"""论文全索引：学科收割、日期窗口、来源标识跳过、FTS 检索、每日 09:00 锚定与按需 PDF。

不访问网络：四个通道与 PDF 下载在测试中替换为桩。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from platform_auth import PlatformStore, Principal, RequestContext
from tool_runtime.permissions import ExecutionContext

from literature import (
    CST,
    DAILY_INTERVAL_SECONDS,
    FIELD_TAXONOMY,
    LiteratureService,
    _authority_for,
    _openalex_abstract,
    date_window,
    is_daily_aligned,
    next_daily_run,
)

OPENALEX_WORK = {
    "id": "https://openalex.org/W4400000001",
    "doi": "https://doi.org/10.1000/example",
    "title": "Deep learning for Alzheimer diagnosis",
    "publication_date": "2026-09-20",
    "authorships": [{"author": {"display_name": "Zhang San"}}, {"author": {"display_name": "Li Si"}}],
    "primary_location": {"source": {"display_name": "Nature Neuroscience"}},
    "best_oa_location": {"pdf_url": "https://example.test/paper.pdf", "license": "cc-by"},
    "open_access": {"is_oa": True, "oa_url": "https://example.test/landing"},
    "abstract_inverted_index": {"We": [0], "propose": [1], "a": [2], "model": [3]},
    "is_retracted": False,
}

EPMC_RECORD = {
    "source": "MED",
    "id": "42576492",
    "pmcid": "PMC1234567",
    "doi": "10.1000/epmc",
    "title": "Amyloid beta and hippocampal plasticity",
    "abstractText": "We studied amyloid beta in mice.",
    "authorString": "Wang Wu, Chen Liu",
    "journalInfo": {"journal": {"title": "Journal of Neuroscience"}},
    "firstPublicationDate": "2026-09-19",
    "license": "cc by",
    "fullTextUrlList": {"fullTextUrl": [
        {"documentStyle": "html", "availability": "Open access", "url": "https://example.test/html"},
        {"documentStyle": "pdf", "availability": "Open access", "url": "https://example.test/paper.pdf"},
    ]},
}

PREPRINT_RECORD = {
    "doi": "10.1101/2026.09.19.123456",
    "title": "A preprint about cortical dynamics",
    "authors": "Alice A; Bob B; Carol C",
    "date": "2026-09-19",
    "version": "2",
    "category": "Neuroscience",
    "abstract": "Preprint abstract text.",
    "license": "cc_by",
}

ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/" xmlns:arxiv="http://arxiv.org/OAI/arXiv/">
  <ListRecords>
    <record>
      <header><identifier>oai:arXiv.org:2409.12345</identifier><datestamp>2026-09-20</datestamp></header>
      <metadata>
        <arxiv:arXiv>
          <arxiv:id>2409.12345</arxiv:id>
          <arxiv:created>2026-09-20</arxiv:created>
          <arxiv:title>Scaling laws for sparse attention</arxiv:title>
          <arxiv:abstract>  We study sparse attention scaling.  </arxiv:abstract>
          <arxiv:authors>
            <arxiv:author><arxiv:keyname>Smith</arxiv:keyname><arxiv:forenames>Ann</arxiv:forenames></arxiv:author>
            <arxiv:author><arxiv:keyname>Doe</arxiv:keyname><arxiv:forenames>John</arxiv:forenames></arxiv:author>
          </arxiv:authors>
          <arxiv:categories>cs.LG cs.AI</arxiv:categories>
        </arxiv:arXiv>
      </metadata>
    </record>
    <resumptionToken>token-abc</resumptionToken>
  </ListRecords>
</OAI-PMH>
"""


def make_service(tmp_path, **kwargs):
    store = PlatformStore(str(tmp_path / "platform.db"))
    return store, LiteratureService(store, None, str(tmp_path / "fulltext"), **kwargs)


def make_context(account_id: str, space_id: str) -> ExecutionContext:
    return ExecutionContext(
        request_context=RequestContext("req-1", Principal("p-1", account_id, "owner", "tok"), "dev-1", space_id),
        agent_id="idea",
        is_owner=True,
    )


def sample_item(field="人工智能", source="OPENALEX", external_id="W4400000001", doi="10.1000/example",
                title="Deep learning for Alzheimer diagnosis", abstract="We propose a model for diagnosis."):
    return {
        "source": source, "external_id": external_id, "field": field, "pmcid": "", "doi": doi,
        "url": f"https://doi.org/{doi}" if doi else "", "pdf_url": "", "title": title, "abstract": abstract,
        "authors": "Zhang San", "venue": "Nature", "date": "2026-09-20", "license": "cc-by",
        "authority": 1.0, "identifiers": [f"{source.lower()}:{external_id}".lower()] + ([doi] if doi else []),
    }


def stub_harvesters(service, **by_source):
    """把四个通道换成桩：只返回与所请求学科匹配的条目；未指定的通道返回空。"""
    for name in ("openalex", "europepmc", "preprints", "arxiv"):
        items = by_source.get(name, [])

        def make(source_items):
            async def fake(field="", *args, **kwargs):
                return [dict(item) for item in source_items if item.get("field") in ("", field)]
            return fake

        setattr(service, f"_harvest_{name}", make(items))


# ---------------------------------------------------------------------------
# 日期窗口与学科分类
# ---------------------------------------------------------------------------


def test_date_window_defaults_to_yesterday():
    now = datetime(2026, 9, 21, 10, 0, tzinfo=CST).timestamp()

    assert date_window(1, now=now) == ("2026-09-20", "2026-09-20")
    assert date_window(3, now=now) == ("2026-09-18", "2026-09-20")


def test_taxonomy_covers_requested_fields():
    for name in ("生物医学", "物理天文", "数学与信息", "人工智能", "化学化工", "材料科学", "地球环境"):
        assert name in FIELD_TAXONOMY
        assert FIELD_TAXONOMY[name].get("openalex")
    assert FIELD_TAXONOMY["生物医学"]["preprints"] is True
    assert "cs" in FIELD_TAXONOMY["人工智能"]["arxiv"]


def test_next_daily_run_targets_nine_cst():
    morning = datetime(2026, 9, 22, 7, 30, tzinfo=CST).timestamp()
    afternoon = datetime(2026, 9, 22, 10, 0, tzinfo=CST).timestamp()

    assert datetime.fromtimestamp(next_daily_run(morning), CST) == datetime(2026, 9, 22, 9, 0, tzinfo=CST)
    assert datetime.fromtimestamp(next_daily_run(afternoon), CST) == datetime(2026, 9, 23, 9, 0, tzinfo=CST)


def test_is_daily_aligned():
    assert is_daily_aligned(datetime(2026, 9, 23, 9, 0, tzinfo=CST).timestamp())
    assert not is_daily_aligned(datetime(2026, 9, 23, 20, 18, tzinfo=CST).timestamp())


def test_daily_job_is_anchored_to_nine_cst(tmp_path):
    store, service = make_service(tmp_path)
    service.ensure_daily_job("acc-1", "space-1")

    job = store.find_scheduled_job("acc-1", "space-1", "literature.daily_collect")

    assert job is not None and job["interval_seconds"] == DAILY_INTERVAL_SECONDS
    assert is_daily_aligned(float(job["next_run_at"]))


def test_existing_daily_job_is_realigned(tmp_path):
    store, service = make_service(tmp_path)
    misaligned = datetime(2026, 9, 23, 20, 18, tzinfo=CST).timestamp()
    store.create_scheduled_job("acc-1", "space-1", "idea", "literature.daily_collect", {}, DAILY_INTERVAL_SECONDS, first_run_at=misaligned)

    service.ensure_daily_job("acc-1", "space-1")

    job = store.find_scheduled_job("acc-1", "space-1", "literature.daily_collect")
    assert is_daily_aligned(float(job["next_run_at"]))
    assert len(store.list_scheduled_jobs("acc-1", "space-1")) == 1


# ---------------------------------------------------------------------------
# 各通道解析
# ---------------------------------------------------------------------------


def test_openalex_item_normalization(tmp_path):
    _, service = make_service(tmp_path)

    item = service._openalex_item(OPENALEX_WORK, "人工智能")

    assert item["field"] == "人工智能"
    assert item["external_id"] == "W4400000001"
    assert item["doi"] == "10.1000/example"
    assert item["url"] == "https://doi.org/10.1000/example"
    assert item["pdf_url"] == "https://example.test/paper.pdf"
    assert item["abstract"] == "We propose a model"
    assert item["authors"] == "Zhang San, Li Si"
    assert item["authority"] == 1.0  # Nature Neuroscience 属顶刊档


def test_europepmc_item_normalization(tmp_path):
    _, service = make_service(tmp_path)

    item = service._europepmc_item(EPMC_RECORD, "生物医学")

    assert item["field"] == "生物医学"
    assert item["identifiers"] == ["med:42576492", "pmc:pmc1234567", "10.1000/epmc"]
    assert item["pdf_url"] == "https://example.test/paper.pdf"  # 只取 open access 的 pdf 链接
    assert item["venue"] == "Journal of Neuroscience"  # journalInfo 回退
    assert item["authority"] == 1.0


def test_preprint_item_normalization(tmp_path):
    _, service = make_service(tmp_path)

    item = service._preprint_item(PREPRINT_RECORD, "生物医学", "biorxiv")

    assert item["external_id"] == "biorxiv:10.1101/2026.09.19.123456"
    assert item["url"] == "https://www.biorxiv.org/content/10.1101/2026.09.19.123456v2"
    assert item["pdf_url"].endswith(".full.pdf")
    assert item["authors"] == "Alice A, Bob B, Carol C"
    assert item["authority"] == 0.45


def test_parse_arxiv_records_and_resumption_token(tmp_path):
    _, service = make_service(tmp_path)

    items = service._parse_arxiv_records(ARXIV_XML, "人工智能", "cs")

    assert len(items) == 1
    item = items[0]
    assert item["external_id"] == "2409.12345"
    assert item["title"] == "Scaling laws for sparse attention"
    assert item["abstract"] == "We study sparse attention scaling."
    assert item["authors"] == "Ann Smith, John Doe"
    assert item["venue"] == "cs.LG"
    assert item["pdf_url"] == "https://arxiv.org/pdf/2409.12345"
    assert service._arxiv_resumption_token(ARXIV_XML) == "token-abc"


def test_openalex_abstract_reconstruction():
    assert _openalex_abstract({"Beta": [1], "Amyloid": [0]}) == "Amyloid Beta"
    assert _openalex_abstract(None) == ""


@pytest.mark.parametrize(
    "venue,source,expected",
    [
        ("Nature Neuroscience", "OPENALEX", 1.0),
        ("Journal of Neuroimaging", "MED", 0.8),
        ("bioRxiv", "PREPRINT", 0.45),
        ("", "ARXIV", 0.35),
    ],
)
def test_authority_tiers(venue, source, expected):
    assert _authority_for(venue, source) == expected


# ---------------------------------------------------------------------------
# 收割主流程：写入、学科标注与跳过
# ---------------------------------------------------------------------------


def test_collect_indexes_items_with_field(tmp_path):
    store, service = make_service(tmp_path)
    stub_harvesters(service, openalex=[sample_item(field="人工智能")], arxiv=[sample_item(field="物理天文", source="ARXIV", external_id="2409.1", doi="", title="Scaling laws for sparse attention")])

    result = asyncio.run(service.collect("acc-1", "space-1"))

    assert result["count"] == 2 and result["skipped"] == 0
    assert result["fields"]["人工智能"] == 1
    assert store.literature_field_counts("acc-1", "space-1") == {"人工智能": 1, "物理天文": 1}
    assert result["total"] == 2


def test_second_run_skips_already_indexed(tmp_path):
    store, service = make_service(tmp_path)
    stub_harvesters(service, openalex=[sample_item()])

    first = asyncio.run(service.collect("acc-1", "space-1"))
    second = asyncio.run(service.collect("acc-1", "space-1"))

    assert first["count"] == 1
    assert second["count"] == 0 and second["skipped"] == 1
    assert store.list_literature("acc-1", "space-1")["total"] == 1


def test_collect_dedupes_across_channels(tmp_path):
    store, service = make_service(tmp_path)
    same_doi = sample_item(source="MED", external_id="42576492", doi="10.1000/example", title="Deep learning for Alzheimer diagnosis")
    stub_harvesters(service, openalex=[sample_item()], europepmc=[same_doi])

    result = asyncio.run(service.collect("acc-1", "space-1"))

    assert result["count"] == 1
    assert store.list_literature("acc-1", "space-1")["total"] == 1


def test_collect_isolates_failing_channel(tmp_path):
    store, service = make_service(tmp_path)

    async def broken(*args, **kwargs):
        raise RuntimeError("通道不可用")

    stub_harvesters(service, openalex=[sample_item()])
    service._harvest_europepmc = broken

    result = asyncio.run(service.collect("acc-1", "space-1"))

    assert result["count"] == 1
    assert "europepmc" not in result["sources"] or result["sources"]["europepmc"] == 0


def test_collect_restricts_to_requested_fields(tmp_path):
    store, service = make_service(tmp_path)
    calls = []

    async def record(field, *args, **kwargs):
        calls.append(field)
        return []

    stub_harvesters(service)
    service._harvest_openalex = record

    asyncio.run(service.collect("acc-1", "space-1", fields=["人工智能"]))

    assert calls == ["人工智能"]


# ---------------------------------------------------------------------------
# 全文检索（FTS5）
# ---------------------------------------------------------------------------


def test_search_matches_title_and_abstract(tmp_path):
    store, service = make_service(tmp_path)
    stub_harvesters(service, openalex=[
        sample_item(title="Deep learning for Alzheimer diagnosis"),
        sample_item(external_id="W2", doi="10.1000/other", title="Quantum error correction", abstract="Surface codes and thresholds."),
    ])
    asyncio.run(service.collect("acc-1", "space-1"))

    by_title = service.search("acc-1", "space-1", "Alzheimer")
    by_abstract = service.search("acc-1", "space-1", "thresholds")

    assert by_title["total"] == 1 and by_title["items"][0]["title"].startswith("Deep learning")
    assert by_abstract["total"] == 1 and by_abstract["items"][0]["title"] == "Quantum error correction"


def test_search_is_scoped_to_account_and_space(tmp_path):
    store, service = make_service(tmp_path)
    stub_harvesters(service, openalex=[sample_item()])
    asyncio.run(service.collect("acc-1", "space-1"))

    assert service.search("acc-2", "space-1", "Alzheimer")["total"] == 0
    assert service.search("acc-1", "space-1", "Alzheimer")["total"] == 1
    assert service.search("acc-1", "space-1", "  ")["total"] == 0


# ---------------------------------------------------------------------------
# 网络层重试与按需 PDF 下载
# ---------------------------------------------------------------------------


def test_request_json_retries_truncated_payload(tmp_path):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx.Response(200, json={"version": "6.9"})
        return httpx.Response(200, json={"hitCount": 1, "resultList": {"result": [{"id": "1", "source": "MED"}]}})

    _, service = make_service(tmp_path, transport=httpx.MockTransport(handler))

    payload = asyncio.run(service._request_json(
        "https://example.test/search", {"query": "x"},
        validate=lambda item: isinstance(item, dict) and "hitCount" in item,
    ))

    assert len(seen) == 2 and payload["hitCount"] == 1


def pdf_service(tmp_path, handler):
    store, service = make_service(tmp_path, transport=httpx.MockTransport(handler))
    record = store.upsert_literature(
        "acc-1", "space-1", "OPENALEX", "W1", "", "Deep learning for Alzheimer diagnosis", "摘要", None,
        "Zhang San", "Nature Neuroscience", "2026-09-20", "", None, None, "not_requested", "",
        "10.1000/example", "Nature Neuroscience", 1.0, "https://doi.org/10.1000/example",
        "https://example.test/paper.pdf", "人工智能",
    )
    return store, service, record


def test_pdf_filename_is_readable(tmp_path):
    _, service = make_service(tmp_path)

    name = service._pdf_filename({
        "authors": "Zhang San, Li Si", "publication_date": "2026-09-20",
        "title": "Deep learning for Alzheimer diagnosis", "external_id": "W1", "literature_id": "x",
    })

    assert name == "San_2026_Deep learning for Alzheimer diagnosis.pdf"


def test_prepare_download_fetches_pdf_and_reuses_local_copy(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, headers={"Content-Type": "application/pdf"}, content=b"%PDF-1.7 fake")

    store, service, record = pdf_service(tmp_path, handler)

    path, filename = asyncio.run(service.prepare_download("acc-1", "space-1", record["literature_id"]))
    calls_after_first = len(calls)
    again = asyncio.run(service.prepare_download("acc-1", "space-1", record["literature_id"]))

    assert path.is_file() and path.read_bytes().startswith(b"%PDF")
    assert filename.endswith(".pdf") and "Alzheimer" in filename
    assert store.get_literature("acc-1", "space-1", record["literature_id"])["fulltext_status"] == "downloaded"
    assert len(calls) == calls_after_first  # 第二次直接复用本地文件，不再联网
    assert again[0] == path


def test_prepare_download_falls_back_to_resolved_open_access_link(tmp_path):
    def handler(request):
        url = str(request.url)
        if url.startswith("https://api.unpaywall.org"):
            return httpx.Response(200, json={
                "doi": "10.1000/example",
                "best_oa_location": {"url_for_pdf": "https://oa.test/copy.pdf"},
            })
        if url.startswith("https://example.test/paper.pdf"):
            return httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<html>paywall</html>")
        if url.startswith("https://oa.test/copy.pdf"):
            return httpx.Response(200, headers={"Content-Type": "application/pdf"}, content=b"%PDF-1.7 copy")
        return httpx.Response(404, json={})

    store, service, record = pdf_service(tmp_path, handler)

    path, _ = asyncio.run(service.prepare_download("acc-1", "space-1", record["literature_id"]))

    assert path.read_bytes().startswith(b"%PDF")
    assert store.get_literature("acc-1", "space-1", record["literature_id"])["pdf_url"] == "https://oa.test/copy.pdf"


def test_prepare_download_returns_none_when_links_are_not_pdf(tmp_path):
    def handler(request):
        return httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<html>paywall</html>")

    _, service, record = pdf_service(tmp_path, handler)

    assert asyncio.run(service.prepare_download("acc-1", "space-1", record["literature_id"])) is None


def test_prepare_download_returns_none_without_any_candidate(tmp_path):
    store, service = make_service(tmp_path)
    record = store.upsert_literature(
        "acc-1", "space-1", "CROSSREF", "10.1000/example", "", "论文标题", "摘要", None,
        "", "Nature", "2026-09-20", "", None, None, "not_requested", "",
        "", "Nature", 1.0, "", "", "",
    )

    assert asyncio.run(service.prepare_download("acc-1", "space-1", record["literature_id"])) is None


def test_prefer_repository_orders_repository_first(tmp_path):
    _, service = make_service(tmp_path)

    ordered = service._prefer_repository(["https://academic.oup.com/x.pdf", "https://europepmc.org/y.pdf", "https://arxiv.org/pdf/z"])

    assert ordered == ["https://europepmc.org/y.pdf", "https://arxiv.org/pdf/z", "https://academic.oup.com/x.pdf"]


def test_unpaywall_prefers_repository_locations(tmp_path):
    def handler(request):
        return httpx.Response(200, json={
            "doi": "10.1000/example",
            "best_oa_location": {"url_for_pdf": "https://publisher.test/blocked.pdf"},
            "oa_locations": [{"url_for_pdf": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1/pdf/copy.pdf"}],
        })

    _, service = make_service(tmp_path, transport=httpx.MockTransport(handler))

    candidates = asyncio.run(service._unpaywall_pdfs("10.1000/example"))

    assert candidates[0].startswith("https://www.ncbi.nlm.nih.gov")


def test_resolve_candidates_constructs_preprint_links(tmp_path):
    def handler(request):
        return httpx.Response(404, json={})

    _, service = make_service(tmp_path, transport=httpx.MockTransport(handler))
    record = {"doi": "10.1101/2026.09.19.123456", "source": "MED", "external_id": "42576492", "pmcid": "", "pdf_url": "", "url": ""}

    candidates = asyncio.run(service._resolve_pdf_candidates(record))

    assert "https://www.biorxiv.org/content/10.1101/2026.09.19.123456v1.full.pdf" in candidates
    assert "https://www.medrxiv.org/content/10.1101/2026.09.19.123456v1.full.pdf" in candidates


def test_prepare_download_records_failure_status(tmp_path):
    def html_handler(request):
        return httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<html>paywall</html>")

    store, service, record = pdf_service(tmp_path, html_handler)

    assert asyncio.run(service.prepare_download("acc-1", "space-1", record["literature_id"])) is None
    assert store.get_literature("acc-1", "space-1", record["literature_id"])["fulltext_status"] == "blocked"

    empty = store.upsert_literature(
        "acc-1", "space-1", "CROSSREF", "10.1000/none", "", "另一篇", "摘要", None,
        "", "Nature", "2026-09-20", "", None, None, "not_requested", "", "", "Nature", 1.0, "", "", "",
    )
    assert asyncio.run(service.prepare_download("acc-1", "space-1", empty["literature_id"])) is None
    assert store.get_literature("acc-1", "space-1", empty["literature_id"])["fulltext_status"] == "no_oa"


def test_prepare_download_ignores_internal_urls(tmp_path):
    def handler(request):
        return httpx.Response(200, headers={"Content-Type": "application/pdf"}, content=b"%PDF-1.7 secret")

    store, service = make_service(tmp_path, transport=httpx.MockTransport(handler))
    record = store.upsert_literature(
        "acc-1", "space-1", "OPENALEX", "W2", "", "论文标题", "摘要", None,
        "", "Nature", "2026-09-20", "", None, None, "not_requested", "",
        "", "Nature", 1.0, "", "http://127.0.0.1/paper.pdf", "",
    )

    assert asyncio.run(service.prepare_download("acc-1", "space-1", record["literature_id"])) is None


def test_daily_tool_reports_counts(tmp_path):
    _, service = make_service(tmp_path)
    stub_harvesters(service, openalex=[sample_item()])

    result = asyncio.run(service.collect_tool(make_context("acc-1", "space-1")))

    assert result.success is True
    assert result.metadata["count"] == 1
    assert "文献索引更新完成" in result.output
