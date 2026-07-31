"""公开学术元数据来源适配器。

适配器只调用来源公开的 API，并产出元数据、落地页和 OA 指示信息；不获取
全文、不解析付费页面，也不尝试规避访问限制。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass(frozen=True)
class HarvestContext:
    since: date
    limit: int
    settings: dict[str, Any]


class SourceAdapter(ABC):
    """将一个公开索引映射为统一的元数据记录。"""

    name: str

    @abstractmethod
    def collect(self, context: HarvestContext) -> Iterable[dict[str, Any]]:
        """返回来源原始标识唯一的标准化记录。"""


def get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request_headers = {"User-Agent": "paper-watcher/2.0 (metadata research tool)"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def clean_doi(value: str | None) -> str:
    return (value or "").lower().removeprefix("https://doi.org/").removeprefix("doi:").strip()


def author_names(authorships: list[dict[str, Any]]) -> list[str]:
    return [item.get("author", {}).get("display_name", "") for item in authorships if item.get("author", {}).get("display_name")]


class OpenAlexAdapter(SourceAdapter):
    """OpenAlex 是默认主来源，支持按日期全量初始化与游标增量。"""

    name = "openalex"

    def collect(self, context: HarvestContext) -> Iterable[dict[str, Any]]:
        filter_value = f"from_publication_date:{context.since.isoformat()}"
        params = {
            "filter": filter_value,
            "per-page": min(context.limit, 200),
            "cursor": context.settings.get("cursor", "*"),
            "select": "id,doi,title,publication_date,authorships,abstract_inverted_index,primary_location,open_access,ids,type,cited_by_count,updated_date",
        }
        email = context.settings.get("mailto")
        if email:
            params["mailto"] = email
        payload = get_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params))
        for work in payload.get("results", []):
            inverted = work.get("abstract_inverted_index") or {}
            positions = sorted((position, token) for token, values in inverted.items() for position in values)
            abstract = " ".join(token for _, token in positions)
            location = work.get("primary_location") or {}
            oa = work.get("open_access") or {}
            source_id = work.get("id", "").rsplit("/", 1)[-1]
            yield {
                "source_id": source_id,
                "doi": clean_doi(work.get("doi")),
                "arxiv_id": (work.get("ids") or {}).get("arxiv", "").rsplit("/", 1)[-1],
                "title": work.get("title") or "",
                "abstract": abstract,
                "authors": author_names(work.get("authorships") or []),
                "published": work.get("publication_date") or "",
                "landing_url": location.get("landing_page_url") or work.get("doi") or work.get("id", ""),
                "oa_status": "open" if oa.get("is_oa") else "closed_or_unknown",
                "oa_url": oa.get("oa_url") or "",
                "work_type": work.get("type") or "",
                "updated": work.get("updated_date") or "",
                "citation_count": work.get("cited_by_count"),
            }


class CrossrefAdapter(SourceAdapter):
    name = "crossref"

    def collect(self, context: HarvestContext) -> Iterable[dict[str, Any]]:
        params = {
            "filter": f"from-update-date:{context.since.isoformat()}",
            "rows": min(context.limit, 1000),
            "select": "DOI,title,abstract,author,published,URL,type,update-to,indexed,is-referenced-by-count",
            "sort": "updated",
            "order": "asc",
        }
        email = context.settings.get("mailto")
        if email:
            params["mailto"] = email
        payload = get_json("https://api.crossref.org/works?" + urllib.parse.urlencode(params))
        for item in payload.get("message", {}).get("items", []):
            parts = item.get("published", {}).get("date-parts", [[]])[0]
            published = "-".join(str(x) for x in parts)
            authors = [" ".join(filter(None, (a.get("given"), a.get("family")))) for a in item.get("author", [])]
            doi = clean_doi(item.get("DOI"))
            yield {
                "source_id": doi,
                "doi": doi,
                "arxiv_id": "",
                "title": (item.get("title") or [""])[0],
                "abstract": item.get("abstract") or "",
                "authors": [a for a in authors if a],
                "published": published,
                "landing_url": item.get("URL") or f"https://doi.org/{doi}",
                "oa_status": "unknown",
                "oa_url": "",
                "work_type": item.get("type") or "",
                "updated": (item.get("indexed") or {}).get("date-time", ""),
                "citation_count": item.get("is-referenced-by-count"),
            }


class SemanticScholarAdapter(SourceAdapter):
    name = "semantic_scholar"

    def collect(self, context: HarvestContext) -> Iterable[dict[str, Any]]:
        query = context.settings.get("query", "")
        if not query:
            return []
        params = {"query": query, "limit": min(context.limit, 100), "fields": "paperId,title,abstract,authors,publicationDate,externalIds,url,openAccessPdf,publicationTypes,citationCount"}
        headers = {"x-api-key": context.settings["api_key"]} if context.settings.get("api_key") else {}
        payload = get_json("https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(params), headers)
        return ({
            "source_id": item["paperId"], "doi": clean_doi((item.get("externalIds") or {}).get("DOI")),
            "arxiv_id": (item.get("externalIds") or {}).get("ArXiv", ""), "title": item.get("title") or "",
            "abstract": item.get("abstract") or "", "authors": [a.get("name", "") for a in item.get("authors") or []],
            "published": item.get("publicationDate") or "", "landing_url": item.get("url") or "",
            "oa_status": "open" if (item.get("openAccessPdf") or {}).get("url") else "unknown",
            "oa_url": (item.get("openAccessPdf") or {}).get("url", ""), "work_type": ",".join(item.get("publicationTypes") or []),
            "updated": "", "citation_count": item.get("citationCount"),
        } for item in payload.get("data", []))


class CoreAdapter(SourceAdapter):
    name = "core"

    def collect(self, context: HarvestContext) -> Iterable[dict[str, Any]]:
        api_key = context.settings.get("api_key")
        query = context.settings.get("query", "")
        if not api_key or not query:
            return []
        params = {"q": query, "limit": min(context.limit, 100), "fromPublishedDate": context.since.isoformat()}
        payload = get_json("https://api.core.ac.uk/v3/search/works?" + urllib.parse.urlencode(params), {"Authorization": f"Bearer {api_key}"})
        return ({
            "source_id": str(item.get("id", "")), "doi": clean_doi(item.get("doi")), "arxiv_id": item.get("arxivId") or "",
            "title": item.get("title") or "", "abstract": item.get("abstract") or "", "authors": item.get("authors") or [],
            "published": item.get("publishedDate") or "", "landing_url": item.get("sourceFulltextUrls", [""])[0] if item.get("sourceFulltextUrls") else item.get("downloadUrl") or "",
            "oa_status": "open" if item.get("downloadUrl") or item.get("sourceFulltextUrls") else "unknown", "oa_url": item.get("downloadUrl") or "",
            "work_type": item.get("type") or "", "updated": item.get("updatedDate") or "", "citation_count": None,
        } for item in payload.get("results", []))


class EuropePmcAdapter(SourceAdapter):
    name = "europe_pmc"

    def collect(self, context: HarvestContext) -> Iterable[dict[str, Any]]:
        query = context.settings.get("query", "")
        date_query = f"FIRST_PDATE:[{context.since.isoformat()} TO *]"
        params = {"query": f"({query}) AND {date_query}" if query else date_query, "format": "json", "pageSize": min(context.limit, 1000), "resultType": "core"}
        payload = get_json("https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(params))
        for item in payload.get("resultList", {}).get("result", []):
            doi = clean_doi(item.get("doi"))
            pmcid = item.get("pmcid", "")
            is_oa = item.get("isOpenAccess") == "Y"
            yield {
                "source_id": item.get("id", ""), "doi": doi, "arxiv_id": "", "title": item.get("title") or "",
                "abstract": item.get("abstractText") or "", "authors": (item.get("authorString") or "").split(", "),
                "published": item.get("firstPublicationDate") or item.get("pubYear", ""),
                "landing_url": f"https://europepmc.org/article/{item.get('source', 'MED')}/{item.get('id', '')}",
                "oa_status": "open" if is_oa else "closed_or_unknown", "oa_url": f"https://europepmc.org/articles/{pmcid}" if is_oa and pmcid else "",
                "work_type": item.get("pubType") or "", "updated": item.get("lastUpdateDate") or "", "citation_count": item.get("citedByCount"),
            }


class ArxivAdapter(SourceAdapter):
    name = "arxiv"

    def collect(self, context: HarvestContext) -> Iterable[dict[str, Any]]:
        query = context.settings.get("query", "all:*")
        params = urllib.parse.urlencode({"search_query": query, "start": 0, "max_results": context.limit, "sortBy": "submittedDate", "sortOrder": "descending"})
        request = urllib.request.Request("https://export.arxiv.org/api/query?" + params, headers={"User-Agent": "paper-watcher/2.0 (metadata research tool)"})
        with urllib.request.urlopen(request, timeout=60) as response:
            root = ET.fromstring(response.read())
        for entry in root.findall(ATOM + "entry"):
            arxiv_id = entry.findtext(ATOM + "id", "").rstrip("/").split("/")[-1]
            yield {
                "source_id": arxiv_id, "doi": "", "arxiv_id": arxiv_id,
                "title": " ".join(entry.findtext(ATOM + "title", "").split()),
                "abstract": " ".join(entry.findtext(ATOM + "summary", "").split()),
                "authors": [a.findtext(ATOM + "name", "") for a in entry.findall(ATOM + "author")],
                "published": entry.findtext(ATOM + "published", ""), "landing_url": entry.findtext(ATOM + "id", ""),
                "oa_status": "open", "oa_url": "", "work_type": "preprint", "updated": entry.findtext(ATOM + "updated", ""), "citation_count": None,
            }


ADAPTERS: dict[str, type[SourceAdapter]] = {
    cls.name: cls for cls in (OpenAlexAdapter, CrossrefAdapter, SemanticScholarAdapter, CoreAdapter, EuropePmcAdapter, ArxivAdapter)
}
