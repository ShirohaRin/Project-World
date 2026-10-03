"""论文全索引：按学科与日期增量收割生物医学 / 物理 / 数学与信息 / 人工智能等领域的论文索引。

设计要点：
- 只存索引信息（标题、来源链接、摘要、期刊、日期、来源特征标识），不主动下载全文。
- 采集方式从「按检索词问库」改为「按学科分类 + 日期窗口枚举」：每天取前一天的新论文。
- 四个通道：
  * OpenAlex      —— 跨学科主干，按 primary_topic.field.id 过滤 + 游标翻页；
  * Europe PMC    —— 生物医学纵深，按 FIRST_PDATE 日期窗口 + cursorMark 翻页；
  * bioRxiv/medRxiv —— 生命科学预印本，按日期区间取；
  * arXiv OAI-PMH —— 预印本批量收割，按 archive 集 + 日期区间，官方推荐方式。
- 去重键是各来源自身的特征标识（来源代号 + 源内 ID，另含 DOI / PMCID / arXiv ID）。
- 每日更新由 JobScheduler 驱动，锚定 UTC+8 的 09:00。
- 不做评分：相关性评分留给后续的小参数模型或专门算法。
- 全文按需下载：客户端点击时按登记的开放获取链接取回 PDF（不下 XML）。
"""
from __future__ import annotations

import asyncio
import html
import ipaddress
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse

import httpx

from tool_runtime.permissions import ToolRisk
from tool_runtime.registry import ToolResult

logger = logging.getLogger("idea.literature")

OPENALEX_API = "https://api.openalex.org/works"
EUROPE_PMC_API = "https://www.ebi.ac.uk/europepmc/webservices/rest"
PREPRINT_API = "https://api.biorxiv.org/details"
ARXIV_OAI = "https://oaipmh.arxiv.org/oai"
UNPAYWALL_API = "https://api.unpaywall.org/v2"
PMC_OA_API = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"
TAG_RE = re.compile(r"<[^>]+>")

MAX_PDF_BYTES = 60 * 1024 * 1024
MAX_ABSTRACT_CHARS = 4000
DAILY_INTERVAL_SECONDS = 86400.0
DAILY_JOB_TOOL = "literature.daily_collect"
USER_AGENT = "Project-IDEA-Literature/1.0"
CST = timezone(timedelta(hours=8))
DAILY_HOUR_CST = 9
MAX_PAGES_PER_SOURCE = 30  # 单源单次收割的翻页上限，防止异常膨胀
BLOCKED_HOSTNAMES = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
# 仓库型来源基本不拦服务端抓取；出版商站点经常返回 403，所以把仓库排在前面
REPOSITORY_HOST_HINTS = (
    "ncbi.nlm.nih.gov", "europepmc.org", "arxiv.org", "biorxiv.org", "medrxiv.org",
    "core.ac.uk", "zenodo.org", "osf.io", "hal.science", "hal.archives-ouvertes.fr",
    "europepmc", "repository", "repositor", "escholarship.org", "biorxiv", "medrxiv",
)

# 学科分类：中文标签 -> 各通道的过滤条件（OpenAlex 领域 ID、arXiv archive 集、是否取生命科学预印本）
FIELD_TAXONOMY = {
    "生物医学": {"openalex": [11, 13, 24, 27, 28, 29, 30, 34, 35, 36], "arxiv": ["q-bio"], "preprints": True},
    "物理天文": {"openalex": [31], "arxiv": ["physics", "cond-mat", "astro-ph", "quant-ph", "hep-th", "hep-ph", "hep-ex", "nucl-th", "nucl-ex", "gr-qc"]},
    "数学与信息": {"openalex": [26, 18], "arxiv": ["math", "stat"]},
    "人工智能": {"openalex": [17], "arxiv": ["cs", "eess"]},
    "化学化工": {"openalex": [16, 15]},
    "材料科学": {"openalex": [25]},
    "地球环境": {"openalex": [19, 23]},
    "工程能源": {"openalex": [22, 21]},
    "心理社科": {"openalex": [32]},
}
OPENALEX_SELECT = "id,doi,title,publication_date,type,authorships,primary_location,best_oa_location,open_access,abstract_inverted_index,is_retracted"
PREPRINT_SERVERS = ("biorxiv", "medrxiv")

TIER1_VENUE_HINTS = (
    "nature", "science", "cell", "lancet", "new england journal", "jama", "pnas",
    "proceedings of the national academy", "neuron", "brain", "elife", "bmj",
    "annals of neurology", "molecular psychiatry", "acta neuropathologica",
    "alzheimer's & dementia", "alzheimer's and dementia", "nature aging", "science advances",
    "cell reports", "plos biology", "current biology", "journal of neuroscience",
    "nature communications", "nature medicine", "nature neuroscience", "embo",
)
PREPRINT_HINTS = ("arxiv", "biorxiv", "medrxiv", "research square", "preprint", "ssrn", "osf")


def next_daily_run(now: Optional[float] = None) -> float:
    """下一个 UTC+8 09:00 的时间戳；已过今天 9 点则顺延到明天。"""
    moment = datetime.fromtimestamp(now if now is not None else time.time(), CST)
    target = moment.replace(hour=DAILY_HOUR_CST, minute=0, second=0, microsecond=0)
    if target <= moment:
        target += timedelta(days=1)
    return target.timestamp()


def is_daily_aligned(timestamp: float) -> bool:
    """判断时间戳是否正好落在 UTC+8 的 09:00。"""
    moment = datetime.fromtimestamp(timestamp, CST)
    return moment.hour == DAILY_HOUR_CST and moment.minute == 0


def date_window(days: int = 1, now: Optional[float] = None) -> tuple[str, str]:
    """返回要收割的日期区间（UTC+8）：默认前一天。"""
    today = datetime.fromtimestamp(now if now is not None else time.time(), CST).date()
    start = today - timedelta(days=max(1, int(days)))
    return start.isoformat(), (today - timedelta(days=1)).isoformat()


def _clean_text(value) -> str:
    """去掉 HTML/JATS/arXiv 包装，压缩空白。"""
    text = TAG_RE.sub(" ", str(value or ""))
    return " ".join(html.unescape(text).replace("Abstract:", " ").split())


def _clip(text: str) -> str:
    return str(text or "")[:MAX_ABSTRACT_CHARS]


def _normalize_doi(value) -> str:
    doi = str(value or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi.strip()


def _doi_link(doi: str) -> str:
    return f"https://doi.org/{doi}" if doi else ""


def _authority_for(venue: str, source: str, base: Optional[float] = None) -> float:
    """按期刊层级给来源权威度（仅用于排序，不代表论文质量判断）。"""
    if base is not None:
        return base
    name = (venue or "").strip().lower()
    if not name:
        return 0.35 if source == "ARXIV" else 0.5
    for hint in TIER1_VENUE_HINTS:
        if hint in name:
            return 1.0
    if any(hint in name for hint in PREPRINT_HINTS):
        return 0.45
    if source == "ARXIV":
        return 0.45
    return 0.8


def _title_key(title: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(title or "").lower())[:120]


def _openalex_abstract(inverted) -> str:
    """OpenAlex 的摘要是倒排索引，需要按位置还原。"""
    if not isinstance(inverted, dict) or not inverted:
        return ""
    positioned = []
    for word, positions in inverted.items():
        if isinstance(positions, list):
            positioned.extend((int(position), word) for position in positions if isinstance(position, int))
    positioned.sort(key=lambda pair: pair[0])
    return _clip(" ".join(word for _, word in positioned))


def _public_http_url(value: str) -> bool:
    """只允许抓取公网 http(s) 链接，避免把内网地址当成下载源。"""
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host in BLOCKED_HOSTNAMES or host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved)


class LiteratureService:
    """论文全索引：按学科与日期增量收割、来源标识去重、全文检索与按需 PDF 下载。"""

    def __init__(self, store, llm_client=None, root_dir: str = "", contact_email: str = "", transport=None):
        self.store = store
        self.llm = llm_client  # 索引流程不使用模型，仅为兼容既有调用签名保留
        self.root_dir = Path(root_dir or os.environ.get("IDEA_LITERATURE_ROOT", "literature_index")).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.contact_email = contact_email or os.environ.get("IDEA_LITERATURE_CONTACT_EMAIL", "idea-literature@shiroha-rin.world")
        self.transport = transport  # 仅测试注入：替换 httpx 传输层

    # ------------------------------------------------------------------
    # 基础网络层：带校验与重试（Europe PMC 与 arXiv 偶发返回被截断的响应）
    # ------------------------------------------------------------------

    async def _request_json(self, url: str, params: dict, *, validate=None, attempts: int = 3, timeout: float = 40.0) -> dict:
        last_error = "未知错误"
        for attempt in range(1, attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=8.0), follow_redirects=True, transport=self.transport) as client:
                    response = await client.get(url, params=params, headers={"User-Agent": USER_AGENT})
                    response.raise_for_status()
                    payload = response.json()
                if validate is not None and not validate(payload):
                    last_error = "响应不完整（可能被截断）"
                else:
                    return payload
            except Exception as error:
                last_error = str(error)
            if attempt < attempts:
                await asyncio.sleep(0.8 * attempt)
        raise ValueError(f"请求失败：{last_error}")

    async def _request_text(self, url: str, params: dict = None, *, attempts: int = 3, timeout: float = 60.0) -> str:
        last_error = "未知错误"
        for attempt in range(1, attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=8.0), follow_redirects=True, transport=self.transport) as client:
                    response = await client.get(url, params=params or {}, headers={"User-Agent": USER_AGENT})
                    response.raise_for_status()
                    if response.text:
                        return response.text
                    last_error = "空响应"
            except Exception as error:
                last_error = str(error)
            if attempt < attempts:
                await asyncio.sleep(0.8 * attempt)
        raise ValueError(f"请求失败：{last_error}")

    # ------------------------------------------------------------------
    # 通道一：OpenAlex（跨学科主干）
    # ------------------------------------------------------------------

    async def _harvest_openalex(self, field: str, date_from: str, date_to: str) -> list[dict]:
        field_ids = FIELD_TAXONOMY[field].get("openalex") or []
        if not field_ids:
            return []
        query = "|".join(f"fields/{value}" for value in field_ids)
        items, cursor, pages = [], "*", 0
        while cursor and pages < MAX_PAGES_PER_SOURCE:
            payload = await self._request_json(
                OPENALEX_API,
                {
                    "filter": f"from_publication_date:{date_from},to_publication_date:{date_to},primary_topic.field.id:{query}",
                    "per-page": "200", "cursor": cursor, "select": OPENALEX_SELECT, "mailto": self.contact_email,
                },
                validate=lambda data: isinstance(data, dict) and isinstance(data.get("results"), list),
            )
            for work in payload.get("results", []):
                item = self._openalex_item(work, field)
                if item:
                    items.append(item)
            cursor = (payload.get("meta") or {}).get("next_cursor")
            pages += 1
            if not payload.get("results"):
                break
        logger.info("openalex %s: %d items in %d pages", field, len(items), pages)
        return items

    def _openalex_item(self, work: dict, field: str) -> Optional[dict]:
        doi = _normalize_doi(work.get("doi"))
        work_id = str(work.get("id", "") or "").rsplit("/", 1)[-1]
        external_id = work_id or doi
        title = _clean_text(work.get("title") or work.get("display_name") or "")
        if not external_id or not title:
            return None
        location = work.get("primary_location") or {}
        source_info = location.get("source") or {}
        venue = str(source_info.get("display_name", "") or "").strip()
        best = work.get("best_oa_location") or {}
        open_access = work.get("open_access") or {}
        return {
            "source": "OPENALEX",
            "external_id": external_id,
            "field": field,
            "pmcid": "",
            "doi": doi,
            "url": _doi_link(doi) or str(work.get("id", "") or ""),
            "pdf_url": str(best.get("pdf_url") or open_access.get("oa_url") or "").strip(),
            "title": title,
            "abstract": _openalex_abstract(work.get("abstract_inverted_index")),
            "authors": ", ".join(
                str((authorship.get("author") or {}).get("display_name", "") or "")
                for authorship in (work.get("authorships") or [])[:12]
            ).strip(", "),
            "venue": venue,
            "date": str(work.get("publication_date", "") or "").strip(),
            "license": str(best.get("license") or ""),
            "authority": _authority_for(venue, "OPENALEX"),
            "identifiers": [key for key in (f"openalex:{external_id}".lower(), doi) if key],
        }

    # ------------------------------------------------------------------
    # 通道二：Europe PMC（生物医学纵深）
    # ------------------------------------------------------------------

    async def _harvest_europepmc(self, field: str, date_from: str, date_to: str) -> list[dict]:
        items, cursor, pages = [], "*", 0
        while pages < MAX_PAGES_PER_SOURCE:
            payload = await self._request_json(
                f"{EUROPE_PMC_API}/search",
                {
                    "query": f"FIRST_PDATE:[{date_from} TO {date_to}]", "format": "json",
                    "pageSize": "1000", "resultType": "core", "cursorMark": cursor,
                },
                validate=lambda data: isinstance(data, dict) and "hitCount" in data,
            )
            results = payload.get("resultList", {}).get("result", []) or []
            for record in results:
                item = self._europepmc_item(record, field)
                if item:
                    items.append(item)
            pages += 1
            next_cursor = payload.get("nextCursorMark")
            if not results or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        with_abstract = sum(1 for item in items if item["abstract"])
        logger.info("europepmc %s: %d items in %d pages (%d with abstract)", field, len(items), pages, with_abstract)
        return items

    def _europepmc_item(self, record: dict, field: str) -> Optional[dict]:
        source = str(record.get("source", "") or "").strip()
        external_id = str(record.get("id", "") or "").strip()
        title = _clean_text(record.get("title", ""))
        if not source or not external_id or not title:
            return None
        pmid = external_id if source == "MED" else ""
        pmcid = str(record.get("pmcid", "") or "").strip()
        doi = _normalize_doi(record.get("doi"))
        pdf_url = ""
        for entry in ((record.get("fullTextUrlList") or {}).get("fullTextUrl")) or []:
            if str(entry.get("documentStyle", "")).lower() != "pdf":
                continue
            availability = str(entry.get("availability", "")).lower()
            if availability and "open" not in availability:
                continue
            pdf_url = str(entry.get("url", "") or "").strip()
            if pdf_url:
                break
        license_name = record.get("license") or ""
        if not license_name and isinstance(record.get("licenseInformation"), dict):
            license_name = record["licenseInformation"].get("license", "")
        venue = str(record.get("journalTitle", "") or "").strip()
        if not venue:
            journal = ((record.get("journalInfo") or {}).get("journal") or {})
            venue = str(journal.get("title", "") or "").strip()
        return {
            "source": source,
            "external_id": external_id,
            "field": field,
            "pmcid": pmcid,
            "doi": doi,
            "url": _doi_link(doi) or (f"https://europepmc.org/article/PMC/{pmcid}" if pmcid else f"https://europepmc.org/article/{source}/{external_id}"),
            "pdf_url": pdf_url,
            "title": title,
            "abstract": _clip(_clean_text(record.get("abstractText", ""))),
            "authors": str(record.get("authorString", "") or "").strip(),
            "venue": venue,
            "date": str(record.get("firstPublicationDate", "") or "").strip(),
            "license": str(license_name or ""),
            "authority": _authority_for(venue, source),
            "identifiers": [key for key in (f"med:{pmid}" if pmid else "", f"pmc:{pmcid}".lower() if pmcid else "", doi) if key],
        }

    # ------------------------------------------------------------------
    # 通道三：bioRxiv / medRxiv 预印本
    # ------------------------------------------------------------------

    async def _harvest_preprints(self, field: str, date_from: str, date_to: str) -> list[dict]:
        items = []
        for server in PREPRINT_SERVERS:
            cursor, pages = 0, 0
            while pages < MAX_PAGES_PER_SOURCE:
                payload = await self._request_json(
                    f"{PREPRINT_API}/{server}/{date_from}/{date_to}",
                    {"cursor": str(cursor)},
                    validate=lambda data: isinstance(data, dict) and isinstance(data.get("collection"), list),
                )
                records = payload.get("collection", []) or []
                for record in records:
                    item = self._preprint_item(record, field, server)
                    if item:
                        items.append(item)
                pages += 1
                remaining = payload.get("messages", [{}])[-1].get("total")
                cursor += len(records)
                if not records or not remaining or cursor >= int(remaining):
                    break
        logger.info("preprints %s: %d items", field, len(items))
        return items

    def _preprint_item(self, record: dict, field: str, server: str) -> Optional[dict]:
        doi = _normalize_doi(record.get("doi"))
        title = _clean_text(record.get("title", ""))
        if not doi or not title:
            return None
        version = str(record.get("version", "1") or "1").strip()
        mirror = "biorxiv" if server == "biorxiv" else "medrxiv"
        authors = " ".join(str(record.get("authors", "") or "").replace(";", ",").split()).strip(", ")
        return {
            "source": "PREPRINT",
            "external_id": f"{mirror}:{doi}",
            "field": field,
            "pmcid": "",
            "doi": doi,
            "url": f"https://www.{mirror}.org/content/{doi}v{version}",
            "pdf_url": f"https://www.{mirror}.org/content/{doi}v{version}.full.pdf",
            "title": title,
            "abstract": _clip(_clean_text(record.get("abstract", ""))),
            "authors": authors,
            "venue": str(record.get("category", "") or mirror),
            "date": str(record.get("date", "") or "").strip(),
            "license": str(record.get("license", "") or ""),
            "authority": _authority_for(mirror, "PREPRINT", base=0.45),
            "identifiers": [f"preprint:{mirror}:{doi}", doi],
        }

    # ------------------------------------------------------------------
    # 通道四：arXiv OAI-PMH（官方推荐的批量收割方式）
    # ------------------------------------------------------------------

    async def _harvest_arxiv(self, field: str, date_from: str, date_to: str) -> list[dict]:
        sets = FIELD_TAXONOMY[field].get("arxiv") or []
        items = []
        for archive in sets:
            token = ""
            pages = 0
            while pages < MAX_PAGES_PER_SOURCE:
                params = {"verb": "ListRecords", "metadataPrefix": "arXiv", "set": archive, "from": date_from, "until": date_to}
                if token:
                    params = {"verb": "ListRecords", "resumptionToken": token}
                text = await self._request_text(ARXIV_OAI, params)
                items.extend(self._parse_arxiv_records(text, field, archive))
                token = self._arxiv_resumption_token(text)
                pages += 1
                if not token:
                    break
        logger.info("arxiv %s: %d items", field, len(items))
        return items

    @staticmethod
    def _parse_arxiv_records(text: str, field: str, archive: str) -> list[dict]:
        namespace = {"oai": "http://www.openarchives.org/OAI/2.0/", "arxiv": "http://arxiv.org/OAI/arXiv/"}
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return []
        items = []
        for record in root.findall(".//oai:record", namespace):
            meta = record.find("oai:metadata/arxiv:arXiv", namespace)
            if meta is None:
                continue
            identifier = (meta.findtext("arxiv:id", "", namespace) or "").strip()
            title = _clean_text(meta.findtext("arxiv:title", "", namespace))
            if not identifier or not title:
                continue
            authors = ", ".join(
                " ".join(filter(None, [str(creator.findtext("arxiv:forenames", "", namespace) or "").strip(),
                                       str(creator.findtext("arxiv:keyname", "", namespace) or "").strip()]))
                for creator in meta.findall("arxiv:authors/arxiv:author", namespace)[:12]
            ).strip(", ")
            categories = (meta.findtext("arxiv:categories", "", namespace) or archive).split()
            doi = _normalize_doi(meta.findtext("arxiv:doi", "", namespace))
            items.append({
                "source": "ARXIV",
                "external_id": identifier,
                "field": field,
                "pmcid": "",
                "doi": doi,
                "url": f"https://arxiv.org/abs/{identifier}",
                "pdf_url": f"https://arxiv.org/pdf/{identifier}",
                "title": title,
                "abstract": _clip(_clean_text(meta.findtext("arxiv:abstract", "", namespace))),
                "authors": authors,
                "venue": categories[0] if categories else "arXiv",
                "date": (meta.findtext("arxiv:created", "", namespace) or "")[:10],
                "license": str(meta.findtext("arxiv:license", "", namespace) or ""),
                "authority": _authority_for("arxiv", "ARXIV", base=0.45),
                "identifiers": [f"arxiv:{identifier}".lower()] + ([doi] if doi else []),
            })
        return items

    @staticmethod
    def _arxiv_resumption_token(text: str) -> str:
        namespace = {"oai": "http://www.openarchives.org/OAI/2.0/"}
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return ""
        token = root.findtext(".//oai:resumptionToken", "", namespace)
        return (token or "").strip()

    # ------------------------------------------------------------------
    # 去重与已收录判断
    # ------------------------------------------------------------------

    @staticmethod
    def _dedupe(items: list[dict]) -> list[dict]:
        """跨源去重：同一篇可能同时出现在 OpenAlex、Europe PMC 与预印本通道。"""
        seen, unique = set(), []
        for item in items:
            keys = {str(key).lower() for key in item.get("identifiers", []) if key}
            if item.get("doi"):
                keys.add(str(item["doi"]).lower())
            title_key = _title_key(item.get("title", ""))
            if title_key:
                keys.add(f"title:{title_key}")
            if keys & seen:
                continue
            seen |= keys
            unique.append(item)
        return unique

    @staticmethod
    def _item_identifiers(item: dict) -> set[str]:
        keys = {str(key).lower() for key in item.get("identifiers", []) if key}
        keys.add(f"{str(item.get('source', '')).lower()}:{str(item.get('external_id', '')).lower()}")
        if item.get("doi"):
            keys.add(str(item["doi"]).lower())
        if item.get("pmcid"):
            keys.add(str(item["pmcid"]).lower())
        return keys

    # ------------------------------------------------------------------
    # 收割主流程（不评分、不下载全文）
    # ------------------------------------------------------------------

    async def collect(self, account_id: str, space_id: str, days: int = 1, fields: Optional[list[str]] = None) -> dict:
        """按学科与日期窗口收割论文索引，按来源特征标识跳过已收录项。"""
        date_from, date_to = date_window(days)
        targets = [name for name in (fields or list(FIELD_TAXONOMY)) if name in FIELD_TAXONOMY]
        fetched, source_counts, per_field = [], {}, {}
        for field in targets:
            collected = []
            for name, loader in (
                ("openalex", lambda: self._harvest_openalex(field, date_from, date_to)),
                ("europepmc", lambda: self._harvest_europepmc(field, date_from, date_to)),
                ("preprints", lambda: self._harvest_preprints(field, date_from, date_to)),
                ("arxiv", lambda: self._harvest_arxiv(field, date_from, date_to)),
            ):
                if name == "europepmc" and field != "生物医学":
                    continue  # Europe PMC 只覆盖生物医学
                if name == "preprints" and not FIELD_TAXONOMY[field].get("preprints"):
                    continue
                if name == "arxiv" and not FIELD_TAXONOMY[field].get("arxiv"):
                    continue
                if name == "openalex" and not FIELD_TAXONOMY[field].get("openalex"):
                    continue
                try:
                    items = await loader()
                except Exception as error:  # 单源单学科失败不影响其他
                    logger.warning("literature source %s/%s failed: %s", name, field, error)
                    continue
                source_counts[name] = source_counts.get(name, 0) + len(items)
                collected.extend(items)
            fetched.extend(collected)
            per_field[field] = len(collected)

        known = self.store.literature_identifiers(account_id, space_id)
        records, skipped = [], 0
        for item in self._dedupe(fetched):
            identifiers = self._item_identifiers(item)
            if identifiers & known:
                skipped += 1  # 已收录：按来源特征标识跳过
                continue
            known |= identifiers
            records.append(self.store.upsert_literature(
                account_id, space_id, item["source"], item["external_id"], item.get("pmcid") or "",
                item.get("title", ""), item.get("abstract", ""), None,
                item.get("authors", ""), item.get("venue", ""), item.get("date", ""),
                item.get("license", ""), None, None, "not_requested", "",
                item.get("doi", ""), item.get("venue", ""), item.get("authority"),
                item.get("url", ""), item.get("pdf_url", ""), item.get("field", ""),
            ))
        logger.info("literature index updated: +%d (skipped %d) window %s..%s", len(records), skipped, date_from, date_to)
        return {"count": len(records), "skipped": skipped, "items": records, "sources": source_counts,
                "fields": per_field, "window": [date_from, date_to], "total": self.store.list_literature(account_id, space_id, 1, 0)["total"]}

    # ------------------------------------------------------------------
    # 检索、列表与按需下载
    # ------------------------------------------------------------------

    def list(self, account_id: str, space_id: str, limit: int = 50, offset: int = 0) -> dict:
        return self.store.list_literature(account_id, space_id, limit, offset)

    def search(self, account_id: str, space_id: str, query: str, limit: int = 50, offset: int = 0) -> dict:
        return self.store.search_literature(account_id, space_id, query, limit, offset)

    def field_counts(self, account_id: str, space_id: str) -> dict:
        return self.store.literature_field_counts(account_id, space_id)

    @staticmethod
    def _pdf_filename(record: dict) -> str:
        """生成可读的下载文件名：姓_年份_标题.pdf。"""
        author = str(record.get("authors") or "").split(",")[0].strip()
        surname = author.split()[-1] if author else ""
        year = str(record.get("publication_date") or "")[:4]
        title = " ".join(re.sub(r"[^\w\u4e00-\u9fff \-]+", " ", str(record.get("title") or "")).split())
        prefix = "_".join(part for part in (surname, year) if part)
        name = " ".join(f"{prefix}_{title}".split()) if prefix else title
        name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", name)[:130].strip() or str(record.get("external_id") or record["literature_id"])
        return name if name.lower().endswith(".pdf") else f"{name}.pdf"

    @staticmethod
    def _prefer_repository(urls: list[str]) -> list[str]:
        """仓库型链接排前面，出版商链接排后面（后者常被反爬拦截）。"""
        repository, others = [], []
        for url in urls:
            host = (urlparse(url).hostname or "").lower()
            (repository if any(hint in host for hint in REPOSITORY_HOST_HINTS) else others).append(url)
        return repository + others

    async def _resolve_pdf_candidates(self, record: dict) -> list[str]:
        """按优先级收集 PDF 候选直链：存量链接优先，其次按 DOI / PMCID 现场解析。"""
        candidates: list[str] = []

        def add(url) -> None:
            value = str(url or "").strip()
            if value and value not in candidates:
                candidates.append(value)

        add(record.get("pdf_url"))
        doi = _normalize_doi(record.get("doi"))
        pmcid = str(record.get("pmcid") or "").strip()
        if record.get("source") == "ARXIV" and record.get("external_id"):
            add(f"https://arxiv.org/pdf/{record['external_id']}")
        if doi.startswith("10.1101/"):  # bioRxiv / medRxiv 预印本的直链构造规则
            add(f"https://www.biorxiv.org/content/{doi}v1.full.pdf")
            add(f"https://www.medrxiv.org/content/{doi}v1.full.pdf")
        if doi:
            for url in await self._unpaywall_pdfs(doi):
                add(url)
            add(await self._openalex_pdf(doi))
        add(await self._europepmc_pdf(doi, pmcid))
        add(await self._pmc_oa_pdf(pmcid))
        return [url for url in candidates if _public_http_url(url)]

    async def _unpaywall_pdfs(self, doi: str) -> list[str]:
        """Unpaywall 是公认的开放获取解析服务；返回其记录的多个 PDF 位置（仓库优先）。"""
        try:
            payload = await self._request_json(
                f"{UNPAYWALL_API}/{quote(doi, safe='')}", {"email": self.contact_email},
                validate=lambda data: isinstance(data, dict) and "doi" in data, attempts=2, timeout=25.0,
            )
        except Exception as error:
            logger.info("unpaywall lookup failed for %s: %s", doi, error)
            return []
        found = []
        best = payload.get("best_oa_location") or {}
        if best.get("url_for_pdf"):
            found.append(str(best["url_for_pdf"]))
        for location in payload.get("oa_locations") or []:
            if location.get("url_for_pdf"):
                found.append(str(location["url_for_pdf"]))
        return self._prefer_repository(list(dict.fromkeys(found)))

    async def _openalex_pdf(self, doi: str) -> str:
        try:
            payload = await self._request_json(
                f"{OPENALEX_API}/doi:{quote(doi, safe='')}",
                {"select": "id,best_oa_location,open_access", "mailto": self.contact_email},
                validate=lambda data: isinstance(data, dict) and "id" in data, attempts=2, timeout=25.0,
            )
        except Exception as error:
            logger.info("openalex lookup failed for %s: %s", doi, error)
            return ""
        best = payload.get("best_oa_location") or {}
        return str(best.get("pdf_url") or (payload.get("open_access") or {}).get("oa_url") or "")

    async def _europepmc_pdf(self, doi: str, pmcid: str) -> str:
        query = f'DOI:"{doi}"' if doi else (f"PMCID:{pmcid}" if pmcid else "")
        if not query:
            return ""
        try:
            payload = await self._request_json(
                f"{EUROPE_PMC_API}/search",
                {"query": query, "format": "json", "pageSize": "1", "resultType": "core"},
                validate=lambda data: isinstance(data, dict) and "hitCount" in data, attempts=2, timeout=25.0,
            )
        except Exception as error:
            logger.info("europepmc lookup failed for %s: %s", query, error)
            return ""
        for item in payload.get("resultList", {}).get("result", []) or []:
            for entry in ((item.get("fullTextUrlList") or {}).get("fullTextUrl")) or []:
                if str(entry.get("documentStyle", "")).lower() != "pdf":
                    continue
                availability = str(entry.get("availability", "")).lower()
                if availability and "open" not in availability:
                    continue
                if entry.get("url"):
                    return str(entry["url"])
        return ""

    async def _pmc_oa_pdf(self, pmcid: str) -> str:
        if not re.fullmatch(r"PMC[0-9]+", (pmcid or "").upper()):
            return ""
        try:
            text = await self._request_text(PMC_OA_API, {"id": pmcid.upper()}, attempts=2, timeout=25.0)
        except Exception as error:
            logger.info("pmc oa lookup failed for %s: %s", pmcid, error)
            return ""
        match = re.search(r'<link[^>]*format="pdf"[^>]*href="([^"]+)"', text)
        return match.group(1) if match else ""

    async def _download_pdf(self, candidates: list[str], account_id: str, space_id: str, filename: str, referer: str = "") -> tuple[str, int, str]:
        """按候选顺序尝试下载：只接受公网 http(s)，校验 PDF 头与大小，返回 (路径, 字节数, 生效链接)。"""
        target_root = (self.root_dir / account_id / space_id).resolve()
        target_root.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", filename).strip()[:140] or "paper"
        if not safe_name.lower().endswith(".pdf"):
            safe_name = f"{safe_name}.pdf"
        target = (target_root / safe_name).resolve()
        if target_root != target.parent:
            raise ValueError("下载路径越界")
        temporary = target.with_name(f"{target.name}.part")
        headers = {"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*", "Accept-Language": "en-US,en;q=0.9"}
        if _public_http_url(referer):
            headers["Referer"] = referer
        errors = []
        for url in candidates:
            if not _public_http_url(url):
                continue
            total = 0
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=8.0), follow_redirects=True, transport=self.transport) as client:
                    async with client.stream("GET", url, headers=headers) as response:
                        response.raise_for_status()
                        head = b""
                        with temporary.open("wb") as output:
                            async for chunk in response.aiter_bytes(64 * 1024):
                                if not head:
                                    head = chunk[:5]
                                total += len(chunk)
                                if total > MAX_PDF_BYTES:
                                    raise ValueError("文件超过大小限制")
                                output.write(chunk)
                if not head.startswith(b"%PDF"):
                    raise ValueError("返回内容不是 PDF（可能被出版商拦截）")
            except Exception as error:
                temporary.unlink(missing_ok=True)
                errors.append(f"{urlparse(url).netloc}: {error}")
                logger.info("literature pdf attempt failed for %s: %s", url, error)
                continue
            temporary.replace(target)
            return str(target), total, url
        raise ValueError("；".join(errors[-3:]) or "没有可用的下载链接")

    async def prepare_download(self, account_id: str, space_id: str, literature_id: str) -> Optional[tuple[Path, str]]:
        """按需准备 PDF：已有本地文件直接复用；否则按候选链解析并取回。失败返回 None 并记录原因。"""
        record = self.store.get_literature(account_id, space_id, literature_id)
        if not record:
            return None
        root = (self.root_dir / account_id / space_id).resolve()
        existing = record.get("fulltext_path")
        if existing:
            path = Path(existing).resolve()
            if root == path.parent and path.is_file():
                return path, path.name
        try:
            candidates = await self._resolve_pdf_candidates(record)
        except Exception as error:  # 解析失败不应该让请求炸掉
            logger.warning("literature pdf resolution failed: %s", error)
            candidates = []
        if not candidates:
            logger.info("literature download: no open access pdf for %s (%s)", literature_id, record.get("doi") or record.get("external_id"))
            self.store.set_literature_fulltext_status(literature_id, "no_oa")
            return None
        try:
            path_value, byte_size, used_url = await self._download_pdf(
                candidates, account_id, space_id, self._pdf_filename(record), str(record.get("url") or ""),
            )
        except Exception as error:
            logger.warning("literature download failed for %s: %s", literature_id, error)
            self.store.set_literature_fulltext_status(literature_id, "blocked")
            return None
        self.store.upsert_literature(
            account_id, space_id, record["source"], record["external_id"], record.get("pmcid") or "",
            record["title"], record["abstract"], record.get("relevance_score"),
            record.get("authors", ""), record.get("journal", ""), record.get("publication_date", ""),
            record.get("license"), path_value, byte_size, "downloaded", record.get("scored_direction") or "",
            record.get("doi") or "", record.get("venue") or "", record.get("authority"),
            record.get("url") or "", used_url, record.get("field") or "",
        )
        return Path(path_value).resolve(), Path(path_value).name

    def research_direction(self, account_id: str, space_id: str) -> Optional[str]:
        """研究方向现在只作为可选的检索偏好保留，索引收割不再依赖它。"""
        return self.store.get_research_direction(account_id, space_id)

    def set_research_direction(self, account_id: str, space_id: str, content: str) -> str:
        saved = self.store.set_research_direction(account_id, space_id, content)
        if content.strip():
            self.ensure_daily_job(account_id, space_id)
        return saved

    def ensure_daily_job(self, account_id: str, space_id: str) -> Optional[dict]:
        """幂等创建每日索引作业；已存在时把执行时间对齐到 UTC+8 的 09:00。"""
        job = self.store.find_scheduled_job(account_id, space_id, DAILY_JOB_TOOL)
        if job:
            if not is_daily_aligned(float(job.get("next_run_at") or 0)):
                self.store.reschedule_scheduled_job(job["job_id"], next_daily_run())
            return None
        try:
            return self.store.create_scheduled_job(
                account_id, space_id, "idea", DAILY_JOB_TOOL, {}, DAILY_INTERVAL_SECONDS, first_run_at=next_daily_run(),
            )
        except ValueError as error:
            logger.warning("literature daily job creation failed: %s", error)
            return None

    # ------------------------------------------------------------------
    # 定时任务工具
    # ------------------------------------------------------------------

    async def collect_tool(self, execution_context=None) -> ToolResult:
        if execution_context is None or execution_context.request_context is None:
            return ToolResult(False, "缺少执行上下文", DAILY_JOB_TOOL)
        context = execution_context.request_context
        try:
            result = await self.collect(context.principal.account_id, context.space_id)
        except Exception as error:
            logger.warning("literature index update failed: %s", error)
            return ToolResult(False, f"文献索引更新失败：{error}", DAILY_JOB_TOOL)
        detail = "，".join(f"{name} {count}" for name, count in (result.get("sources") or {}).items())
        fields = "，".join(f"{name} {count}" for name, count in (result.get("fields") or {}).items() if count)
        summary = f"文献索引更新完成（{result['window'][0]}..{result['window'][1]}）：新增 {result['count']} 条，跳过已收录 {result.get('skipped', 0)} 条"
        if detail:
            summary += f"；通道：{detail}"
        if fields:
            summary += f"；学科：{fields}"
        return ToolResult(
            True, summary, DAILY_JOB_TOOL,
            {"count": result["count"], "skipped": result.get("skipped", 0), "total": result.get("total", 0)},
        )


def register_literature_tool(registry, service: LiteratureService) -> None:
    """把每日索引收割注册为可被 JobScheduler 调度的工具。"""
    policy = registry.policy
    policy.TOOL_RISKS[DAILY_JOB_TOOL] = ToolRisk.WRITE
    policy.OWNER_MODULE_TOOLS.add(DAILY_JOB_TOOL)
    registry.register_tool(
        DAILY_JOB_TOOL,
        service.collect_tool,
        {
            "name": DAILY_JOB_TOOL,
            "description": "按学科（生物医学/物理天文/数学与信息/人工智能/化学化工/材料/地球环境/工程能源/心理社科）与日期窗口收割论文索引，按来源特征标识跳过已收录项。",
            "parameters": {"type": "object", "properties": {"days": {"type": "integer", "default": 1}}, "required": []},
        },
    )
