from __future__ import annotations

import ast
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import AuthorizedMemoryRequest, MemoryLayer
from .service import MemoryService


def migrate_legacy_store(
    db_path: str | Path,
    service: MemoryService,
    request: AuthorizedMemoryRequest,
) -> dict[str, int]:
    """将旧 MemoryStore 的键值记录导入为带来源标记的 Fact。"""
    if MemoryLayer.FACT not in request.allowed_layers:
        raise PermissionError("迁移请求必须允许写入 Fact 层")

    imported = skipped = failed = 0
    connection = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT key, value, category, created_at, updated_at FROM memory ORDER BY updated_at, key"
        ).fetchall()
    finally:
        connection.close()

    for key, value, category, created_at, updated_at in rows:
        fact_id = f"legacy_{_stable_id(str(key))}"
        if any(item.get("id") == fact_id for item in service.facts.load(request.container_id)):
            skipped += 1
            continue
        content = _legacy_content(value)
        if not content:
            failed += 1
            continue
        service.add_fact(request, {
            "id": fact_id,
            "text": content,
            "category": str(category or "general"),
            "created_at": _timestamp(created_at),
            "updated_at": _timestamp(updated_at),
            "metadata": {
                "legacy_source": "server/memory/idea_memory.db",
                "legacy_key": str(key),
                "legacy_category": str(category or "general"),
                "migration_version": "legacy-keystore-v1",
            },
        })
        imported += 1
    return {"imported": imported, "skipped": skipped, "failed": failed}


def _legacy_content(value: Any) -> str:
    if not isinstance(value, str):
        return str(value).strip()
    text = value.strip()
    if not text:
        return ""
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return text
    if isinstance(parsed, (dict, list)):
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True)
    return str(parsed).strip()


def _timestamp(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return datetime.now(timezone.utc).isoformat()


def _stable_id(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


__all__ = ["migrate_legacy_store"]
