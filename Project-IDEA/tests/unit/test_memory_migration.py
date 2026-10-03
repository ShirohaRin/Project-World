from __future__ import annotations

import sqlite3
from pathlib import Path

from Memory.core import AuthorizedMemoryRequest, MemoryLayer
from Memory.migration import migrate_legacy_store
from Memory.service import MemoryService
from Memory.storage import MemoryLayout


def test_migrate_legacy_store_is_idempotent(tmp_path):
    db_path = tmp_path / "idea_memory.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE memory (key TEXT PRIMARY KEY, value TEXT NOT NULL, category TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE audit_log (id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, agent TEXT NOT NULL, action TEXT NOT NULL, detail TEXT);
        """
    )
    connection.execute(
        "INSERT INTO memory VALUES (?, ?, ?, ?, ?)",
        ("legacy-key", "{'preference': '科学阅读'}", "preference", 1720000000, 1720000001),
    )
    connection.commit()
    connection.close()

    service = MemoryService(MemoryLayout(tmp_path / "Memory"))
    request = AuthorizedMemoryRequest(
        caller_id="owner",
        container_id="owner-shiroha-nao",
        allowed_layers=frozenset({MemoryLayer.FACT}),
        allow_raw_content=True,
    )

    assert migrate_legacy_store(db_path, service, request) == {"imported": 1, "skipped": 0, "failed": 0}
    assert migrate_legacy_store(db_path, service, request) == {"imported": 0, "skipped": 1, "failed": 0}
    facts = service.facts.load("owner-shiroha-nao")
    assert len(facts) == 1
    assert facts[0]["metadata"]["legacy_key"] == "legacy-key"
    assert "科学阅读" in facts[0]["text"]
