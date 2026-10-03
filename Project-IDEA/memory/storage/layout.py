from __future__ import annotations

import json
import re
from pathlib import Path


_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,96}$")

VIEW_FILES = (
    "facts.json",
    "facts_archive.json",
    "reflections.json",
    "persona.json",
    "persona_corrections.json",
    "recent.json",
    "surfaced.json",
    "settings.json",
)
SYSTEM_FILES = (
    "time_indexed.db",
    "events.ndjson",
    "events_applied.json",
    "outbox.ndjson",
    "cursors.json",
)
LOCAL_CACHE_FILES = (
    "local_cache.json",
    "upload_staging.ndjson",
)
ALLOWED_FILES = VIEW_FILES + SYSTEM_FILES + LOCAL_CACHE_FILES


class MemoryLayout:
    """NEKO 风格的角色隔离文件布局。"""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def character(self, name: str) -> Path:
        if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
            raise ValueError("角色标识只能包含字母、数字、点、下划线和连字符")
        path = (self.root / name).resolve()
        if path.parent != self.root:
            raise ValueError("角色路径越界")
        path.mkdir(exist_ok=True)
        return path

    def file(self, name: str, filename: str) -> Path:
        if Path(filename).name != filename or filename not in ALLOWED_FILES:
            raise ValueError("不允许访问该 Memory 文件")
        return self.character(name) / filename

    def archive(self, name: str) -> Path:
        path = self.character(name) / "archive"
        path.mkdir(exist_ok=True)
        return path

    def initialize_character(self, name: str) -> Path:
        directory = self.character(name)
        for filename in VIEW_FILES:
            path = directory / filename
            if not path.exists():
                path.write_text("{}\n", encoding="utf-8")
        for filename in ("events.ndjson", "outbox.ndjson"):
            (directory / filename).touch(exist_ok=True)
        for filename, value in (("events_applied.json", {"last_applied_event_id": None}), ("cursors.json", {}), ("local_cache.json", {"cloud_revision": None, "items": []})):
            path = directory / filename
            if not path.exists():
                path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (directory / "upload_staging.ndjson").touch(exist_ok=True)
        return directory
