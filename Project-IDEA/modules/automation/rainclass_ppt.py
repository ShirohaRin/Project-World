"""雨课堂图片型 PPT 的翻页检测与增量记录。"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .checkpoint_store import MemoryCheckpointStore, SqliteCheckpointStore
from .rainclass_ppt_notes import MarkdownNoteWriter, RainClassSlide


_OCR_ENGINE: Any | None = None


def _ocr_text(image_bytes: bytes) -> str:
    global _OCR_ENGINE

    if _OCR_ENGINE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError:
            return ""
        _OCR_ENGINE = RapidOCR()

    try:
        result, _ = _OCR_ENGINE(image_bytes)
    except Exception:
        return ""

    if not result:
        return ""
    return "\n".join(
        str(item[1]).strip()
        for item in result
        if isinstance(item, (list, tuple)) and len(item) > 1 and str(item[1]).strip()
    )


@dataclass(frozen=True)
class RainClassPptConfig:
    base_url: str
    course_id: str = ""
    course_name: str = ""
    session_id: str = ""
    page_url: str = ""
    poll_interval_seconds: float = 2.0
    settle_samples: int = 2
    image_selector: str = "img"
    stop_event: asyncio.Event | None = None


@dataclass(frozen=True)
class PptPageState:
    page_url: str
    image_url: str
    image_key: str
    slide_index: int | None
    image_bytes: bytes

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.image_key.encode("utf-8")).hexdigest()


async def run_rainclass_ppt_monitor(
    browser: Any,
    config: RainClassPptConfig,
    checkpoint: MemoryCheckpointStore | SqliteCheckpointStore,
    note_writer: MarkdownNoteWriter,
    max_pages: int | None = None,
) -> list[RainClassSlide]:
    """持续监控放映页，检测到新图片后保存一次。"""
    page = getattr(browser, "page", None)
    if page is None:
        raise RuntimeError("雨课堂 PPT 监控需要 Playwright 页面实例")

    await page.goto(config.base_url, wait_until="domcontentloaded")
    if await _login_expired(page):
        raise RuntimeError("雨课堂登录态已失效，请重新登录并更新 storage_state")
    course = await _find_course(page, config)
    page_url = config.page_url or course["url"]
    await page.goto(page_url, wait_until="domcontentloaded")
    if await _login_expired(page):
        raise RuntimeError("雨课堂登录态已失效，请重新登录并更新 storage_state")
    await _open_ppt_entry(page)

    course_id = config.course_id or course["classroom_id"]
    course_name = config.course_name or course["name"]
    recorded: list[RainClassSlide] = []
    last_fingerprint = ""
    pending_key = ""
    pending_count = 0

    while True:
        state = await _read_page_state(page, config)
        if state is not None:
            if state.image_key == pending_key:
                pending_count += 1
            else:
                pending_key = state.image_key
                pending_count = 1

            if pending_count >= max(1, config.settle_samples) and state.fingerprint != last_fingerprint:
                checkpoint_key = f"rainclass-ppt:{course_id}:{config.session_id}:{state.fingerprint}"
                if not await checkpoint.known(checkpoint_key):
                    slide = RainClassSlide(
                        course_id=course_id,
                        course_name=course_name,
                        session_id=config.session_id,
                        slide_id=state.image_key,
                        slide_index=state.slide_index,
                        discovered_at=datetime.now().astimezone(),
                        image_path=_image_name(len(recorded) + 1, state.image_bytes),
                        image_url=state.image_url,
                        page_url=state.page_url,
                    )
                    await note_writer.append_slide(
                        slide,
                        state.image_bytes,
                        _ocr_text(state.image_bytes),
                    )
                    await checkpoint.remember(checkpoint_key)
                    recorded.append(slide)
                    if max_pages is not None and len(recorded) >= max_pages:
                        return recorded
                last_fingerprint = state.fingerprint

        stop_event = config.stop_event
        if stop_event is not None and stop_event.is_set():
            return recorded
        await asyncio.sleep(max(0.2, config.poll_interval_seconds))


async def _read_page_state(page: Any, config: RainClassPptConfig) -> PptPageState | None:
    image = await _largest_visible_image(page, config.image_selector)
    if image is None:
        return None

    image_url = str(await image.get_attribute("src") or await image.get_attribute("currentSrc") or "")
    page_url = page.url
    if not image_url and not page_url:
        return None

    image_bytes = await image.screenshot(type="png")
    image_key = hashlib.sha256(image_bytes).hexdigest()

    return PptPageState(
        page_url=page_url,
        image_url=image_url,
        image_key=image_key,
        slide_index=_last_number(page_url),
        image_bytes=image_bytes,
    )


async def _largest_visible_image(page: Any, selector: str) -> Any | None:
    images = page.locator(selector)
    count = await images.count()
    selected = None
    selected_area = 0
    for index in range(count):
        candidate = images.nth(index)
        if not await candidate.is_visible():
            continue
        box = await candidate.bounding_box()
        if not box:
            continue
        area = box["width"] * box["height"]
        if area > selected_area:
            selected = candidate
            selected_area = area
    return selected


async def _login_expired(page: Any) -> bool:
    return "login" in page.url.lower() or "登录" in (await page.title())


async def _find_course(page: Any, config: RainClassPptConfig) -> dict[str, str]:
    from .rainclass_collector import _load_student_courses

    courses = await _load_student_courses(page)
    if config.course_id:
        for course in courses:
            if course["classroom_id"] == config.course_id:
                return course
    if config.course_name:
        matches = [course for course in courses if config.course_name in course["name"]]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError(f"匹配到多门雨课堂课程，请改用 course_id：{config.course_name}")
    raise RuntimeError("未找到指定雨课堂课程，请提供正确的 course_id 或 course_name")


async def _open_ppt_entry(page: Any) -> None:
    labels = ("课堂", "直播", "放映", "正在上课", "进入课堂")
    for label in labels:
        locator = page.get_by_text(label, exact=True).first
        if await locator.count() and await locator.is_visible():
            try:
                await locator.click(timeout=3000)
                await page.wait_for_timeout(1000)
                return
            except Exception:
                continue
    # 入口也可能是带 href 的图片或按钮，尝试从链接语义定位。
    links = page.locator("a,button")
    for index in range(await links.count()):
        link = links.nth(index)
        if not await link.is_visible():
            continue
        text = (await link.inner_text()).strip()
        href = str(await link.get_attribute("href") or "")
        if any(label in f"{text} {href}" for label in labels):
            try:
                await link.click(timeout=3000)
                await page.wait_for_timeout(1000)
                return
            except Exception:
                continue


def _last_number(value: str) -> int | None:
    match = re.search(r"(\d+)(?:/?)$", value.split("?", 1)[0])
    return int(match.group(1)) if match else None


def _image_name(index: int, image_bytes: bytes) -> str:
    digest = hashlib.sha256(image_bytes).hexdigest()[:12]
    return f"slide-{index:03d}-{digest}.png"
