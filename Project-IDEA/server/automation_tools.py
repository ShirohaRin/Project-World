"""自动化模块的 Server 端工具注册。

云端 JobScheduler 通过既有 scheduled_jobs 机制执行这些工具：
- automation.course_reminder    课程提醒（幂等，提前 30 分钟）
- automation.rainclass_monitor  雨课堂公告/作业增量监控

未配置必要参数时返回明确提示，不伪造执行。
"""

from __future__ import annotations

import sys
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tool_runtime.permissions import ToolRisk  # noqa: E402
from tool_runtime.registry import ToolResult  # noqa: E402

from modules.automation.checkpoint_store import SqliteCheckpointStore  # noqa: E402
from modules.automation.rainclass_collector import RainClassConfig
from modules.automation.rainclass_ppt import RainClassPptConfig
from modules.automation.rainclass_ppt_notes import MarkdownNoteWriter  # noqa: E402
from modules.automation.course_query import query_courses_from_path  # noqa: E402
from modules.automation.runner import run_course_reminders, run_rainclass_monitor, run_rainclass_ppt_monitor  # noqa: E402
from modules.browser.playwright_runtime import PlaywrightBrowser  # noqa: E402
from modules.external_services.onebot_qq import OneBotQQProvider  # noqa: E402
from modules.external_services.qq import QQMessage  # noqa: E402


def register_automation_tools(registry, config: dict) -> None:
    """把自动化工具注册进 Owner（idea）工具注册表。"""
    automation_cfg = config.get("automation", {}) or {}
    policy = registry.policy

    def _checkpoint() -> SqliteCheckpointStore:
        db_path = automation_cfg.get("checkpoint_db_path", str(PROJECT_ROOT / "memory" / "automation_checkpoints.db"))
        return SqliteCheckpointStore(db_path)

    class _TargetNotifier:
        def __init__(self, provider: OneBotQQProvider, target_id: str) -> None:
            self.provider = provider
            self.target_id = target_id

        async def send(self, content: str) -> None:
            await self.provider.send_text(QQMessage(content=content, target_id=self.target_id))

    def _notifier(target_id: str) -> _TargetNotifier | None:
        qq = automation_cfg.get("qq", {}) or {}
        endpoint = qq.get("endpoint", "")
        if not endpoint:
            return None
        return _TargetNotifier(OneBotQQProvider(endpoint, qq.get("token", "")), target_id or qq.get("target_id", ""))

    async def course_query_tool(week: int = 0, query_date: str = "", course_schedule_path: str = "", execution_context=None) -> ToolResult:
        course_path = course_schedule_path or automation_cfg.get("course_schedule_path", "")
        if not course_path:
            return ToolResult(False, "未配置课程表路径（config.automation.course_schedule_path）", "automation.course_query")
        try:
            courses = query_courses_from_path(course_path, query_date=query_date or None, week=week or None)
        except Exception as error:
            return ToolResult(False, f"课程查询失败: {error}", "automation.course_query")
        return ToolResult(True, json.dumps(courses, ensure_ascii=False), "automation.course_query", {"count": len(courses)})

    async def course_reminder_tool(course_schedule_path: str = "", target_id: str = "", execution_context=None) -> ToolResult:
        if not automation_cfg.get("enabled", False):
            return ToolResult(False, "自动化模块未启用（config.automation.enabled=false）", "automation.course_reminder")
        course_path = course_schedule_path or automation_cfg.get("course_schedule_path", "")
        if not course_path:
            return ToolResult(False, "未配置课程表路径（config.automation.course_schedule_path）", "automation.course_reminder")
        notifier = _notifier(target_id)
        if notifier is None:
            return ToolResult(False, "未配置 QQ 通知端点（config.automation.qq.endpoint）", "automation.course_reminder")
        try:
            sent = await run_course_reminders(course_path, _checkpoint(), notifier)
        except Exception as error:
            return ToolResult(False, f"课程提醒执行失败: {error}", "automation.course_reminder")
        return ToolResult(True, f"课程提醒检查完成，本次发送 {len(sent)} 条", "automation.course_reminder", {"sent": len(sent)})

    async def rainclass_monitor_tool(target_id: str = "", execution_context=None) -> ToolResult:
        if not automation_cfg.get("enabled", False):
            return ToolResult(False, "自动化模块未启用（config.automation.enabled=false）", "automation.rainclass_monitor")
        rainclass = automation_cfg.get("rainclass", {}) or {}
        base_url = rainclass.get("base_url", "") or "https://www.yuketang.cn/v2/web/index"
        course_id = rainclass.get("course_id", "")
        if not base_url:
            return ToolResult(False, "未配置雨课堂 base_url（config.automation.rainclass）", "automation.rainclass_monitor")
        notifier = _notifier(target_id)
        if notifier is None:
            return ToolResult(False, "未配置 QQ 通知端点（config.automation.qq.endpoint）", "automation.rainclass_monitor")
        browser = PlaywrightBrowser(headless=True, storage_state_path=rainclass.get("storage_state_path", ""))
        try:
            await browser.start()
            config = RainClassConfig(base_url=base_url, course_id=course_id)
            new_items = await run_rainclass_monitor(browser, config, _checkpoint(), notifier)
        except Exception as error:
            return ToolResult(False, f"雨课堂监控执行失败: {error}", "automation.rainclass_monitor")
        finally:
            await browser.close()
        titles = "；".join(item.title for item in new_items[:5])
        summary = f"雨课堂检查完成，新增 {len(new_items)} 条" + (f"：{titles}" if titles else "")
        return ToolResult(True, summary, "automation.rainclass_monitor", {"new_items": len(new_items)})

    async def rainclass_ppt_monitor_tool(course_id: str = "", course_name: str = "", note_path: str = "", max_pages: int = 0, execution_context=None) -> ToolResult:
        if not automation_cfg.get("enabled", False):
            return ToolResult(False, "自动化模块未启用（config.automation.enabled=false）", "automation.rainclass_ppt_monitor")
        rainclass = automation_cfg.get("rainclass", {}) or {}
        ppt_cfg = automation_cfg.get("rainclass_ppt", {}) or {}
        selected_course_id = course_id or ppt_cfg.get("course_id", "")
        selected_course_name = course_name or ppt_cfg.get("course_name", "")
        if not selected_course_id and not selected_course_name:
            return ToolResult(False, "请提供 course_id 或 course_name", "automation.rainclass_ppt_monitor")
        note_file = note_path or ppt_cfg.get("note_path", "")
        if not note_file:
            notes_dir = Path(ppt_cfg.get("notes_dir", PROJECT_ROOT / "memory" / "rainclass_notes"))
            safe_name = selected_course_name or selected_course_id
            note_file = str(notes_dir / f"{safe_name}.md")
        browser = PlaywrightBrowser(headless=True, storage_state_path=rainclass.get("storage_state_path", ""))
        try:
            await browser.start()
            ppt_config = RainClassPptConfig(
                base_url=rainclass.get("base_url", "https://www.yuketang.cn/v2/web/index"),
                course_id=selected_course_id,
                course_name=selected_course_name,
                poll_interval_seconds=float(ppt_cfg.get("poll_interval_seconds", 2)),
                settle_samples=int(ppt_cfg.get("settle_samples", 2)),
            )
            slides = await run_rainclass_ppt_monitor(
                browser,
                ppt_config,
                _checkpoint(),
                MarkdownNoteWriter(note_file),
                max_pages=max_pages or None,
            )
        except Exception as error:
            return ToolResult(False, f"雨课堂 PPT 监控执行失败: {error}", "automation.rainclass_ppt_monitor")
        finally:
            await browser.close()
        return ToolResult(True, f"雨课堂 PPT 监控完成，本次记录 {len(slides)} 页：{note_file}", "automation.rainclass_ppt_monitor", {"slides": len(slides), "note_path": note_file})

    for name in ("automation.course_query", "automation.course_reminder", "automation.rainclass_monitor", "automation.rainclass_ppt_monitor"):
        policy.TOOL_RISKS[name] = ToolRisk.WRITE
        policy.OWNER_MODULE_TOOLS.add(name)

    registry.register_tool(
        "automation.course_query",
        course_query_tool,
        {
            "name": "automation.course_query",
            "description": "按周次和日期查询结构化课程表，返回相邻同课合并后的课程列表。",
            "parameters": {"type": "object", "properties": {
                "week": {"type": "integer", "description": "教学周次，缺省为全部周次"},
                "query_date": {"type": "string", "description": "日期 YYYY-MM-DD，缺省为全部日期"},
                "course_schedule_path": {"type": "string", "description": "课程表 JSON 路径，缺省用服务端配置"},
            }, "required": []},
        },
    )
    registry.register_tool(
        "automation.course_reminder",
        course_reminder_tool,
        {
            "name": "automation.course_reminder",
            "description": "发送当前时间点到期的课程提醒（提前 30 分钟，幂等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "course_schedule_path": {"type": "string", "description": "课程表 JSON 路径，缺省用服务端配置"},
                    "target_id": {"type": "string", "description": "接收 QQ 目标 ID，缺省用服务端配置"},
                },
                "required": [],
            },
        },
    )
    registry.register_tool(
        "automation.rainclass_monitor",
        rainclass_monitor_tool,
        {
            "name": "automation.rainclass_monitor",
            "description": "采集雨课堂页面内容并增量通知新增的公告/通知/作业/任务/测验。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_id": {"type": "string", "description": "接收 QQ 目标 ID，缺省用服务端配置"},
                },
                "required": [],
            },
        },
    )
    registry.register_tool(
        "automation.rainclass_ppt_monitor",
        rainclass_ppt_monitor_tool,
        {
            "name": "automation.rainclass_ppt_monitor",
            "description": "自动进入指定雨课堂课程，持续监控放映中的图片型 PPT，并将每次翻页保存到 Markdown。",
            "parameters": {
                "type": "object",
                "properties": {
                    "course_id": {"type": "string", "description": "雨课堂 classroom_id，优先使用"},
                    "course_name": {"type": "string", "description": "课程名称，唯一匹配时可使用"},
                    "note_path": {"type": "string", "description": "Markdown 输出路径，缺省保存到 memory/rainclass_notes"},
                    "max_pages": {"type": "integer", "description": "测试用最大记录页数，0 表示持续运行"},
                },
                "required": [],
            },
        },
    )
