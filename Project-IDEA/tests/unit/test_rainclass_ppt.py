from __future__ import annotations

import asyncio
from datetime import datetime

from modules.automation.rainclass_ppt import RainClassPptConfig, _last_number, run_rainclass_ppt_monitor
from modules.automation.rainclass_ppt_notes import MarkdownNoteWriter
from modules.automation.checkpoint_store import MemoryCheckpointStore


class _Locator:
    def __init__(self, image: bytes):
        self.image = image

    async def count(self):
        return 1

    async def is_visible(self):
        return True

    async def get_attribute(self, name):
        return "https://example.test/slide/7.png" if name == "src" else None

    async def screenshot(self, type="png"):
        return self.image


class _Page:
    url = "https://example.test/class/7"

    def __init__(self):
        self.calls = 0

    async def goto(self, url, wait_until):
        self.url = url

    async def title(self):
        return "PPT"

    def locator(self, selector):
        self.calls += 1
        return _Locator(b"slide-1")


class _Browser:
    def __init__(self):
        self.page = _Page()


def test_last_number():
    assert _last_number("https://example.test/live/12") == 12
    assert _last_number("https://example.test/live/12?x=1") == 12
    assert _last_number("https://example.test/live") is None


def test_monitor_records_one_stable_slide(tmp_path):
    async def run():
        path = tmp_path / "class.md"
        writer = MarkdownNoteWriter(path)
        config = RainClassPptConfig(
            page_url="https://example.test/live/7",
            course_id="course-1",
            course_name="测试课程",
            settle_samples=2,
            poll_interval_seconds=0.2,
            stop_event=asyncio.Event(),
        )
        slides = await run_rainclass_ppt_monitor(
            _Browser(), config, MemoryCheckpointStore(), writer, max_pages=1
        )
        assert len(slides) == 1
        assert slides[0].slide_index == 7
        assert "测试课程" in path.read_text(encoding="utf-8")
        assert "OCR 识别文本" not in path.read_text(encoding="utf-8")
        assert len(list((tmp_path / "class_assets").glob("*.png"))) == 1

    asyncio.run(run())
