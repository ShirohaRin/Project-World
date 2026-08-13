"""
IDEA-ProgramWorldAdminister — 世界项目主管智能体
"""

import logging
import time

logger = logging.getLogger("idea.pwa")


class ProgramWorldAdminister:
    def __init__(self, memory, config: dict):
        self.memory = memory
        self.config = config

    async def respond(self, message: str, history: list) -> str:
        """
        被 IDEA 调度时的主要入口。
        根据消息内容判断应该生成项目计划、风险评估还是进度报告。
        """
        self.memory.log_audit("PWA", "respond", message[:100])
        msg_lower = message.lower()

        # 判断请求类型
        if any(w in msg_lower for w in ["风险", "risk"]):
            plan = await self.create_project_plan(
                project_name=self._extract_project_name(message),
                objectives=message,
            )
            risk = await self.risk_assessment(message)

            return (
                "好的，让我来理一下……\n\n"
                f"## 项目计划\n\n"
                f"**目标**：{plan['objectives'][:200]}\n\n"
                f"**里程碑规划**：\n"
                + "\n".join(f"- {m['name']} → {m['deliverable']}" for m in plan.get("timeline", {}).get("milestones", []))
                + f"\n\n## 风险登记册\n\n"
                + "\n".join(f"- ⚠️ **{r['risk']}** | 概率:{r['probability']} | 影响:{r['impact']} | 应对:{r['mitigation']}" for r in risk.get("risk_register", []))
                + f"\n\n## 下一步\n\n"
                + "\n".join(f"- {s}" for s in plan.get("next_steps", []))
                + "\n\n这里有个风险我们需要注意——需求变更是最常见也最难控制的变量。建议在项目启动前就建立变更控制流程。Done."
            )

        if any(w in msg_lower for w in ["进度", "report", "进展", "status"]):
            report = await self.progress_report(
                project_name=self._extract_project_name(message),
            )
            return (
                f"## 进度报告 — {report['project_name']}\n\n"
                f"报告日期：{report['report_date']}\n\n"
                f"| 指标 | 状态 |\n"
                f"|------|------|\n"
                f"| 完成度 | {report['status']['percent_complete']} |\n"
                f"| 已完成 | {report['status']['completed']} |\n"
                f"| 阻塞项 | {report['status']['blockers']} |\n\n"
                + "\n".join(f"- → {s}" for s in report.get("next_steps", []))
                + "\n\n保持节奏，不要慌。做完一个 milestone 再庆祝。"
            )

        # 默认：创建完整项目计划
        plan = await self.create_project_plan(
            project_name=self._extract_project_name(message),
            objectives=message,
        )
        return (
            "好的，让我来理一下……\n\n"
            f"## 项目计划：{plan['project_name']}\n\n"
            f"**目标**：{plan['objectives'][:200]}\n\n"
            f"### 时间线与里程碑\n\n"
            + "\n".join(f"- **{m['name']}** → {m['deliverable']}" for m in plan.get("timeline", {}).get("milestones", []))
            + f"\n\n### 风险提示\n\n"
            + "\n".join(f"- ⚠️ **{r['risk']}**：{r['mitigation']}" for r in plan.get("risk_register", []))
            + f"\n\n### 下一步\n\n"
            + "\n".join(f"- {s}" for s in plan.get("next_steps", []))
            + "\n\nDone."
        )

    async def create_project_plan(self, project_name: str, objectives: str, constraints: str = "") -> dict:
        self.memory.log_audit("PWA", "create_plan", project_name)

        plan = {
            "project_name": project_name,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "objectives": objectives,
            "constraints": constraints or "未指定",
            "timeline": {
                "estimated_duration": "待评估",
                "milestones": [
                    {"name": "项目启动", "deliverable": "项目章程 / Kickoff 会议"},
                    {"name": "需求确认", "deliverable": "需求文档 + Scope 确认"},
                    {"name": "方案设计", "deliverable": "技术方案 / 设计文档"},
                    {"name": "执行阶段 1", "deliverable": "MVP 或核心功能"},
                    {"name": "执行阶段 2", "deliverable": "完整功能集"},
                    {"name": "测试 & 验收", "deliverable": "测试报告 + 验收签字"},
                    {"name": "交付 & 复盘", "deliverable": "最终交付物 + 复盘报告"},
                ],
            },
            "risk_register": [
                {"risk": "需求变更", "probability": "高", "impact": "高", "mitigation": "建立变更控制流程，冻结核心需求"},
                {"risk": "资源不足", "probability": "中", "impact": "高", "mitigation": "提前锁定关键资源 + 备选方案"},
                {"risk": "技术不确定性", "probability": "中", "impact": "中", "mitigation": "技术预研 + 快速原型验证"},
                {"risk": "沟通失效", "probability": "低", "impact": "中", "mitigation": "每周同步 + 日报机制"},
            ],
            "next_steps": [
                "确认项目范围和优先级排序",
                "组建项目团队并分配角色",
                "制定详细排期（精确到周）",
                "启动 Kickoff 会议",
                "建立项目看板和沟通渠道",
            ],
        }

        self.memory.store(f"plan:{project_name}", str(plan), category="project")
        return plan

    async def risk_assessment(self, context: str, risk_categories: list = None) -> dict:
        self.memory.log_audit("PWA", "risk_assessment", context[:100])
        categories = risk_categories or ["技术风险", "进度风险", "资源风险", "外部风险"]

        return {
            "context": context,
            "assessment_date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "risk_register": [
                {"risk": cat, "probability": "中", "impact": "高", "mitigation": "制定" + cat + "应急预案"}
                for cat in categories
            ],
        }

    async def progress_report(self, project_name: str, milestone: str = "", completed_tasks: str = "", blockers: str = "") -> dict:
        self.memory.log_audit("PWA", "progress_report", project_name)
        return {
            "project_name": project_name,
            "report_date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "milestone": milestone or "当前",
            "status": {
                "percent_complete": completed_tasks and "部分完成" or "起始阶段",
                "completed": completed_tasks or "待列出",
                "blockers": blockers or "暂无",
            },
            "next_steps": ["推进当前阶段任务", "更新任务看板"],
        }

    def _extract_project_name(self, message: str) -> str:
        """从消息中提取项目名"""
        for keyword in ["项目", "project", "关于", "名称"]:
            idx = message.find(keyword)
            if idx >= 0 and len(message) > idx + 10:
                return message[idx:idx + 30].replace("\n", " ")
        return "IDEA-Project-" + time.strftime("%m%d-%H%M")
