"""雨课堂 PPT 常驻监控进程。"""

from __future__ import annotations

import argparse
import asyncio
import signal
from pathlib import Path
from typing import Any

from modules.automation.checkpoint_store import SqliteCheckpointStore
from modules.automation.rainclass_ppt import RainClassPptConfig, run_rainclass_ppt_monitor
from modules.automation.rainclass_ppt_notes import MarkdownNoteWriter
from modules.browser.playwright_runtime import PlaywrightBrowser


def _load_config(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as error:
        raise RuntimeError("PPT 监控需要 PyYAML") from error
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


async def _run(args: argparse.Namespace) -> None:
    config = _load_config(Path(args.config))
    automation = config.get("automation", {}) or {}
    rainclass = automation.get("rainclass", {}) or {}
    ppt = automation.get("rainclass_ppt", {}) or {}
    course_id = args.course_id or ppt.get("course_id", "")
    course_name = args.course_name or ppt.get("course_name", "")
    if not course_id and not course_name:
        raise RuntimeError("请通过参数或 config.automation.rainclass_ppt 配置 course_id/course_name")

    notes_dir = Path(ppt.get("notes_dir", "./memory/rainclass_notes"))
    safe_name = course_name or course_id
    note_path = Path(args.note_path) if args.note_path else notes_dir / f"{safe_name}.md"
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:
            pass

    browser = PlaywrightBrowser(
        headless=True,
        storage_state_path=rainclass.get("storage_state_path", ""),
    )
    try:
        await browser.start()
        await run_rainclass_ppt_monitor(
            browser,
            RainClassPptConfig(
                base_url=rainclass.get("base_url", "https://www.yuketang.cn/v2/web/index"),
                course_id=course_id,
                course_name=course_name,
                poll_interval_seconds=float(ppt.get("poll_interval_seconds", 2)),
                settle_samples=int(ppt.get("settle_samples", 2)),
                stop_event=stop_event,
            ),
            SqliteCheckpointStore(automation.get("checkpoint_db_path", "./memory/automation_checkpoints.db")),
            MarkdownNoteWriter(note_path),
        )
    finally:
        await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="雨课堂 PPT 常驻监控")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--course-id", default="")
    parser.add_argument("--course-name", default="")
    parser.add_argument("--note-path", default="")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
