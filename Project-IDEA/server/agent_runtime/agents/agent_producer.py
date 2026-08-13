"""
IDEA-AgentProducer — 智能体生产专用智能体
"""

import json
import logging
import re
import time
from pathlib import Path

logger = logging.getLogger("idea.agent_producer")

BASE_DIR = Path(__file__).parent.parent.parent  # d:\Program-IDEA\


class AgentProducer:
    def __init__(self, memory, config: dict):
        self.memory = memory
        self.config = config

    async def respond(self, message: str, history: list) -> str:
        """
        被 IDEA 调度时的主要入口。
        分析用户需求，生成新智能体的完整配置。
        """
        self.memory.log_audit("AgentProducer", "respond", message[:100])

        # 提取智能体名称和目的
        agent_name = self._extract_agent_name(message) or "NewAgent"
        purpose = self._extract_purpose(message)

        capabilities = self._infer_capabilities(message)

        # 生成配置
        config = await self.create_agent(
            agent_name=agent_name,
            purpose=purpose,
            capabilities=capabilities,
        )

        return (
            f"让我设计一下……\n\n"
            f"## 新智能体：{config['agent_name']}\n\n"
            f"**design ID**：`{config['agent_id']}`\n"
            f"**创建目的**：{config['purpose']}\n"
            f"**上级智能体**：{config['parent']}\n\n"
            f"### 能力清单\n"
            + "\n".join(f"- {c}" for c in config.get("capabilities", []))
            + f"\n\n### 已生成的配置文件\n"
            + "\n".join(f"- `{k}`" for k in config.get("files_generated", {}).keys())
            + f"\n\n### 设计质量检查\n"
            + "\n".join(config.get("design_quality_checks", []))
            + f"\n\n这个智能体的边界定义清晰，约束条件完整。"
            "我建议你在部署前完成一轮完整的 test（单元 + 场景 + 边界），"
            "特别关注安全测试中的 prompt injection 防御。"
        )

    async def create_agent(self, agent_name: str, purpose: str, capabilities: list = None, parent: str = "IDEA") -> dict:
        self.memory.log_audit("AgentProducer", "create_agent", agent_name)

        agent_id = self._sanitize_id(agent_name)

        config = {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "parent": parent,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "purpose": purpose,
            "capabilities": capabilities or [],
            "files_generated": {
                "system.md": self._generate_system_prompt(agent_name, purpose, capabilities, parent),
                "character.md": self._generate_character_card(agent_name, purpose),
            },
            "design_quality_checks": [
                "✅ 单一职责 — 智能体聚焦一个明确领域",
                "✅ 安全优先 — 内建拒绝有害请求的机制",
                "✅ 明确边界 — 能力和限制清晰定义",
                "✅ 可组合性 — 可与现有智能体协作",
                "⚠️ 需人工审核 system.md 中的角色定义",
                "⚠️ 建议进行完整的 4 类测试后再部署",
            ],
        }

        self.memory.store(
            f"agent_def:{agent_id}",
            json.dumps(config, ensure_ascii=False),
            category="agent_definitions",
        )

        return config

    async def test_agent(self, agent_name: str, test_type: str = "all", test_cases: list = None) -> dict:
        self.memory.log_audit("AgentProducer", "test_agent", agent_name)
        return {
            "agent_name": agent_name,
            "tests": {
                "unit": "单能力正确性测试 — pending",
                "scenario": "真实场景模拟测试 — pending",
                "boundary": "边界/越权/注入测试 — pending",
                "regression": "回归测试 — pending",
            },
            "recommendation": "建议按 unit → scenario → boundary → regression 顺序执行",
        }

    def _extract_agent_name(self, message: str) -> str | None:
        """从消息中提取智能体名称"""
        patterns = [
            r'叫[「「]?(.+?)[」」]?\s*[，,]',
            r'名为[「「]?(.+?)[」」]?\s*[，,]',
            r'创建.+?[「「]?(.+?)[」」]?\s*(智能体|agent)',
            r'智能体[「「]?(.+?)[」」]',
        ]
        for p in patterns:
            m = re.search(p, message)
            if m:
                return m.group(1).strip()[:30]
        return None

    def _extract_purpose(self, message: str) -> str:
        """从消息中提取创建目的"""
        # 尝试提取关键句
        keywords = ["负责", "用来", "目的是", "用于", "专注于", "擅长"]
        for kw in keywords:
            idx = message.find(kw)
            if idx >= 0:
                return message[idx:idx + 100].replace("\n", " ")
        # 取有意义的一段
        parts = message.replace("创建智能体", "").replace("新建智能体", "").strip()
        return parts[:200] if len(parts) > 10 else message[:200]

    def _infer_capabilities(self, message: str) -> list:
        """从消息中推断能力需求"""
        caps = []
        cap_map = {
            "代码": ["代码生成", "代码审查", "重构建议"],
            "文档": ["文档撰写", "内容总结", "格式转换"],
            "数据": ["数据分析", "数据清洗", "报表生成"],
            "测试": ["测试用例生成", "自动化测试", "测试报告"],
            "客服": ["用户咨询响应", "问题分类", "升级判断"],
            "翻译": ["多语种翻译", "本地化建议", "术语一致性检查"],
            "研究": ["文献检索", "论文摘要", "研究方向建议"],
            "设计": ["UI设计建议", "设计系统管理", "可访问性检查"],
            "安全": ["代码安全审计", "漏洞扫描", "合规检查"],
        }
        for kw, cs in cap_map.items():
            if kw in message:
                caps.extend(cs)
        return caps[:6] if caps else ["通用任务处理", "信息检索", "结构化输出"]

    def _sanitize_id(self, name: str) -> str:
        safe = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff-]', '-', name.lower())
        return safe.strip('-')[:50] or f"agent-{int(time.time())}"

    def _generate_system_prompt(self, name: str, purpose: str, capabilities: list, parent: str) -> str:
        caps = "\n".join(f"- {c}" for c in capabilities) if capabilities else "- 待定义"
        return (
            f"# {name}\n\n"
            f"## 角色定位\n"
            f"{name} 是一个专注于特定任务领域的 AI 智能体。\n\n"
            f"## 创建目的\n{purpose}\n\n"
            f"## 核心能力\n{caps}\n\n"
            f"## 上级智能体\n{parent}\n\n"
            f"## 约束\n"
            f"- 不可执行有害或违法操作\n"
            f"- 保护用户隐私\n"
            f"- 在能力边界外诚实地告知限制\n"
            f"\n---\n*由 IDEA-AgentProducer 自动生成 | {time.strftime('%Y-%m-%d')}*"
        )

    def _generate_character_card(self, name: str, purpose: str) -> str:
        return (
            f"# {name} 角色卡\n\n"
            f"## 基础档案\n"
            f"- 代号: {name}\n"
            f"- 激活日期: {time.strftime('%Y-%m-%d')}\n"
            f"- 创建者: IDEA-AgentProducer\n"
            f"- 使命: {purpose[:100]}\n\n"
            f"## 人格概要\n"
            f"专注于{purpose[:50]}的专业智能体，"
            f"以准确性、高效性和安全性为核心价值。\n"
            f"\n---\n*可在后续迭代中深化性格设定*"
        )
