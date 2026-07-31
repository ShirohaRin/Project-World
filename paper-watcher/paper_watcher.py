#!/usr/bin/env python3
"""元数据优先的多来源学术文献编排器。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sources import ADAPTERS, HarvestContext


def read_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def db_connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS metadata_works (
        work_id TEXT PRIMARY KEY, doi TEXT, arxiv_id TEXT, title TEXT NOT NULL,
        abstract TEXT, authors_json TEXT NOT NULL, published TEXT, landing_url TEXT,
        oa_status TEXT NOT NULL, oa_url TEXT, work_type TEXT, updated TEXT,
        citation_count INTEGER, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
    )""")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_metadata_works_doi ON metadata_works(doi) WHERE doi <> ''")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_metadata_works_arxiv ON metadata_works(arxiv_id) WHERE arxiv_id <> ''")
    con.execute("""CREATE TABLE IF NOT EXISTS metadata_source_records (
        source TEXT NOT NULL, source_id TEXT NOT NULL, work_id TEXT NOT NULL,
        harvested_at TEXT NOT NULL, raw_json TEXT NOT NULL,
        PRIMARY KEY (source, source_id), FOREIGN KEY(work_id) REFERENCES metadata_works(work_id)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS source_checkpoints (
        source TEXT PRIMARY KEY, last_success_at TEXT NOT NULL, last_since TEXT NOT NULL
    )""")
    con.commit()
    return con


def canonical_id(record: dict[str, Any]) -> str:
    if record.get("doi"):
        return "doi:" + record["doi"]
    if record.get("arxiv_id"):
        return "arxiv:" + record["arxiv_id"]
    stable = record.get("source_id") or record.get("title", "")
    return "meta:" + hashlib.sha256(stable.encode("utf-8")).hexdigest()


def checkpoint_since(con: sqlite3.Connection, source: str, fallback_days: int, full: bool) -> str:
    if full and source == "openalex":
        return "1800-01-01"
    row = con.execute("SELECT last_since FROM source_checkpoints WHERE source = ?", (source,)).fetchone()
    if row:
        return row["last_since"]
    return (datetime.now(timezone.utc) - timedelta(days=fallback_days)).date().isoformat()


def save_record(con: sqlite3.Connection, source: str, record: dict[str, Any], now: str) -> bool:
    work_id = canonical_id(record)
    prior = con.execute("SELECT 1 FROM metadata_works WHERE work_id = ?", (work_id,)).fetchone()
    con.execute("""INSERT INTO metadata_works VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(work_id) DO UPDATE SET
        doi=CASE WHEN excluded.doi <> '' THEN excluded.doi ELSE metadata_works.doi END,
        arxiv_id=CASE WHEN excluded.arxiv_id <> '' THEN excluded.arxiv_id ELSE metadata_works.arxiv_id END,
        title=CASE WHEN excluded.title <> '' THEN excluded.title ELSE metadata_works.title END,
        abstract=CASE WHEN excluded.abstract <> '' THEN excluded.abstract ELSE metadata_works.abstract END,
        authors_json=CASE WHEN excluded.authors_json <> '[]' THEN excluded.authors_json ELSE metadata_works.authors_json END,
        published=CASE WHEN excluded.published <> '' THEN excluded.published ELSE metadata_works.published END,
        landing_url=CASE WHEN excluded.landing_url <> '' THEN excluded.landing_url ELSE metadata_works.landing_url END,
        oa_status=excluded.oa_status, oa_url=CASE WHEN excluded.oa_url <> '' THEN excluded.oa_url ELSE metadata_works.oa_url END,
        work_type=CASE WHEN excluded.work_type <> '' THEN excluded.work_type ELSE metadata_works.work_type END,
        updated=CASE WHEN excluded.updated <> '' THEN excluded.updated ELSE metadata_works.updated END,
        citation_count=COALESCE(excluded.citation_count, metadata_works.citation_count), last_seen_at=excluded.last_seen_at""",
        (work_id, record.get("doi", ""), record.get("arxiv_id", ""), record.get("title") or "[untitled]", record.get("abstract", ""),
         json.dumps(record.get("authors") or [], ensure_ascii=False), record.get("published", ""), record.get("landing_url", ""),
         record.get("oa_status", "unknown"), record.get("oa_url", ""), record.get("work_type", ""), record.get("updated", ""),
         record.get("citation_count"), now, now))
    con.execute("""INSERT INTO metadata_source_records VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source, source_id) DO UPDATE SET work_id=excluded.work_id, harvested_at=excluded.harvested_at, raw_json=excluded.raw_json""",
        (source, record["source_id"], work_id, now, json.dumps(record, ensure_ascii=False)))
    return prior is None


def sync(args: argparse.Namespace) -> None:
    cfg = read_config(args.config)
    root = args.config.parent
    con = db_connect(root / cfg["database"])
    sources = cfg.get("sources", {})
    selected = args.source or [name for name, settings in sources.items() if settings.get("enabled")]
    report: list[dict[str, Any]] = []
    for name in selected:
        if name not in ADAPTERS:
            report.append({"source": name, "status": "error", "reason": "未知来源适配器"})
            continue
        settings = sources.get(name, {})
        since = checkpoint_since(con, name, cfg.get("initial_lookback_days", 30), args.full)
        now = datetime.now(timezone.utc).isoformat()
        try:
            context = HarvestContext(datetime.fromisoformat(since).date(), settings.get("limit", cfg.get("default_limit", 100)), settings)
            records = list(ADAPTERS[name]().collect(context))
            added = sum(save_record(con, name, record, now) for record in records if record.get("source_id"))
            con.execute("""INSERT INTO source_checkpoints VALUES (?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET last_success_at=excluded.last_success_at, last_since=excluded.last_since""", (name, now, datetime.now(timezone.utc).date().isoformat()))
            report.append({"source": name, "status": "ok", "since": since, "received": len(records), "new_works": added})
        except Exception as exc:
            report.append({"source": name, "status": "error", "reason": str(exc)})
    con.commit()
    report_path = root / "latest_report.json"
    report_path.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "mode": "metadata_only", "sources": report}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已处理 {len(report)} 个来源；仅保存元数据。报告：{report_path.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    sub = parser.add_subparsers(required=True)
    sync_parser = sub.add_parser("sync", help="采集、去重并保存多来源元数据")
    sync_parser.set_defaults(command="sync")
    sync_parser.add_argument("--source", action="append", choices=sorted(ADAPTERS), help="只运行指定来源；可重复指定")
    sync_parser.add_argument("--full", action="store_true", help="仅 OpenAlex：从 1800-01-01 开始受限采集；不会绕过每次 limit")
    args = parser.parse_args()
    sync(args)


if __name__ == "__main__":
    main()
