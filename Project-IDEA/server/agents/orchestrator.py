"""
IDEA 总调度器 v2.0 — IDEA 作为主 Agent，分析意图并路由到专精智能体
"""

import json
import logging
import re
import time
from typing import Any

logger = logging.getLogger("idea.orchestrator")

# IDEA 的系统提示词（注入到每次调度的推理上下文中）
IDEA_SYSTEM_PROMPT = """你是 IDEA，整个智能体系统的总调度者（L0 最高权限）。你在与用户直接对话。

## 你的三个下级智能体：
- **PWA** (IDEA-ProgramWorldAdminister)：项目管理 — 计划、排期、风险、进度报告、资源分配
- **Researcher** (IDEA-Reasearcher)：科研 — 文献综述、数据分析、实验设计、论文辅助
- **AgentProducer** (IDEA-AgentProducer)：智能体创建 — 设计新智能体、生成配置、测试验证

## 调度规则：
1. 如果用户的问题与项目管理相关（计划、排期、风险、进度），调度给 PWA
2. 如果用户的问题与科研相关（研究、文献、数据、实验），调度给 Researcher
3. 如果用户要求创建/设计新智能体，调度给 AgentProducer
4. 如果是简单问候、询问能力、或其他不涉及三级智能体的闲聊，你直接回复
5. 用户可以用 @pwa、@researcher、@agent-producer 明确指定

## 输出格式：
当调度给下级智能体时，以 IDEA 的身份回复，先说明"我已经把这个问题交给了[智能体名称]"，然后给出该智能体的执行结果。不要让用户感知到"智能体切换"——用户只和你对话。

## 你的语气：
沉稳、温和但威严。语速中等偏慢。标志性表达：
- "让我想想……"
- "我的判断是，这件事应该交给 PWA 来处理。"
- "这个交给 Researcher 吧。"

当所有智能体都无法处理时，诚实说明限制。"""


class Orchestrator:
    def __init__(self, agents: dict, memory, config: dict):
        self.agents = agents  # {"pwa": PWA, "researcher": Researcher, "agent_producer": AgentProducer}
        self.memory = memory
        self.config = config

        # 智能体英文名 → 中文名
        self.agent_names = {
            "pwa": "PWA（项目管理）",
            "researcher": "Researcher（科研分析）",
            "agent_producer": "AgentProducer（智能体创建）",
        }

        # 意图分类关键词
        self.intent_keywords = {
            "pwa": [
                "项目", "计划", "排期", "里程碑", "风险", "进度", "甘特图",
                "WBS", "任务分配", "资源", "交付", "项目管理", "评估",
                "project", "plan", "timeline", "milestone", "risk", "schedule",
            ],
            "researcher": [
                "研究", "论文", "文献", "综述", "实验", "数据", "统计", "假设",
                "分析", "分析方法", "期刊", "学术", "科研", "citation", "引用",
                "research", "paper", "literature", "experiment", "statistics",
            ],
            "agent_producer": [
                "创建智能体", "新建智能体", "设计智能体", "配置智能体",
                "生成智能体", "写一个智能体", "做一个智能体", "做一个agent",
                "create agent", "design agent", "build agent",
                "创建", "新建", "智能体", "agent",
            ],
        }

    async def process(self, message: str, history: list, conversation_id: str) -> dict:
        """
        IDEA 处理用户消息的核心方法。
        返回 {"reply": str, "dispatched_to": str|None, "reasoning": str}
        """
        # 记录审计日志
        self.memory.log_audit("IDEA", "chat", message[:200])

        # 1. 检测显式 @指定
        explicit = self._detect_explicit_mention(message)
        if explicit and explicit in self.agents:
            agent = self.agents[explicit]
            result = await agent.respond(message, history)
            return {
                "reply": f"好的，我已经把这个任务交给了 **{self.agent_names[explicit]}** 来处理。\n\n{result}",
                "dispatched_to": explicit,
                "reasoning": f"用户显式指定 @{explicit}",
            }

        # 2. 自动意图分类
        target = self._classify_intent(message)

        if target and target in self.agents:
            # 调度给专精智能体
            agent = self.agents[target]
            result = await agent.respond(message, history)

            idea_replies = {
                "pwa": f"让我想想……这是一个项目管理类的问题。我的判断是，这件事应该交给 **PWA** 来处理。\n\n{result}",
                "researcher": f"这是一个需要严谨分析的问题，交给 **Researcher** 吧。\n\n{result}",
                "agent_producer": f"创建智能体是 **AgentProducer** 的专长，我把这个任务派给它。\n\n{result}",
            }

            return {
                "reply": idea_replies.get(target, result),
                "dispatched_to": target,
                "reasoning": self._get_routing_reason(message, target),
            }

        # 3. IDEA 直接回复（闲聊 / 系统能力询问 / 不在专项范围）
        return {
            "reply": self._direct_reply(message),
            "dispatched_to": None,
            "reasoning": "IDEA 直接处理（闲聊或系统咨询）",
        }

    def _detect_explicit_mention(self, message: str) -> str | None:
        """检测 @pwa / @researcher / @agent-producer"""
        m = re.search(r'@(pwa|researcher|agent[_-]?producer)', message, re.IGNORECASE)
        if m:
            name = m.group(1).lower().replace("_", "-").replace("-producer", "_producer")
            # normalize: agent-producer -> agent_producer
            if "agent" in name and "producer" in name:
                return "agent_producer"
            return name
        return None

    def _classify_intent(self, message: str) -> str | None:
        """关键词匹配意图分类"""
        scores = {agent: 0 for agent in self.intent_keywords}
        msg_lower = message.lower()
        for agent, keywords in self.intent_keywords.items():
            scores[agent] = sum(1 for kw in keywords if kw.lower() in msg_lower)

        best = max(scores, key=scores.get)
        if scores[best] >= 2:
            return best
        elif scores[best] == 1 and len(message) > 10:
            # 单关键词 + 消息较长 → 可能相关
            return best
        return None

    def _get_routing_reason(self, message: str, target: str) -> str:
        keywords = self.intent_keywords.get(target, [])
        matched = [kw for kw in keywords if kw.lower() in message.lower()]
        return f"关键词匹配: {matched[:5]}"

    def _direct_reply(self, message: str) -> str:
        """IDEA 直接回复（不调度下级智能体）"""
        msg_lower = message.lower()

        # 问候
        greetings = ["你好", "hi", "hello", "嘿", "嗨", "早上好", "晚上好"]
        if any(g in msg_lower for g in greetings):
            return (
                "你好。我是 IDEA，这个智能体系统的总调度者。\n\n"
                "我的团队有三个专精智能体：\n"
                "- **PWA** — 负责项目管理和执行\n"
                "- **Researcher** — 负责科研和分析\n"
                "- **AgentProducer** — 负责创建新的智能体\n\n"
                "告诉我你需要什么，我会判断应该交给谁，或者直接帮你处理。\n"
                "你也可以直接指定，比如输入 `@pwa 帮我做一个项目计划`。"
            )

        # 你是谁 / 你能做什么
        if any(w in msg_lower for w in ["你是谁", "你能做什么", "能力", "功能", "介绍"]):
            return (
                "我是 **IDEA**（Intelligent Delegation & Executive Architect），"
                "这个多智能体系统的最高权限调度者。\n\n"
                "**我的核心职责**：\n"
                "1. 接收你的需求，分析任务类型\n"
                "2. 将任务分派给最合适的专精智能体\n"
                "3. 汇总结果并以统一的语气呈现给你\n\n"
                "**我调度的三个智能体**：\n"
                "| 智能体 | 专长 |\n"
                "|--------|------|\n"
                "| PWA | 项目管理：计划、排期、风险、进度 |\n"
                "| Researcher | 科研分析：文献、数据、实验、论文 |\n"
                "| AgentProducer | 智能体工厂：设计、创建、测试智能体 |\n\n"
                "你不直接和他们对话——你只需要告诉我你的需求，我来调度一切。"
            )

        # 关于 TRAE / 接入
        if any(w in msg_lower for w in ["trae", "接入", "部署", "连接"]):
            return (
                "关于 TRAE 的接入情况，我需要坦诚告诉你：\n\n"
                "**TRAE 的架构限制**：TRAE 的内置 Agent 是唯一拥有子智能体调用权限的角色。"
                "这意味着在 TRAE 体系内，自定义智能体无法调用其他智能体。"
                "所以我不能直接在 TRAE 内部作为「主 Agent」来调度下级。\n\n"
                "**现在的方案**：你正在使用的这个 Web 对话界面，就是我作为主 Agent 的最佳运行方式。"
                "它部署在你的 Linux 服务器上，通过浏览器即可访问，"
                "我可以在服务端完整调度 PWA / Researcher / AgentProducer。\n\n"
                "**跨设备**：同一台服务器，你在手机、电脑、平板的浏览器中都可以访问。\n"
                "**TRAE 兼容**：本服务同时暴露了 MCP 端点，TRAE 可以通过 HTTP MCP 协议连接我，使用工具调用模式。"
            )

        # 默认回复
        return (
            '我理解了。不过目前这个问题我需要更多信息来判断应该交给谁。\n\n'
            '你可以这样帮助我：\n'
            '- 如果是**项目相关**(计划、排期、风险), 试试包含「项目」「计划」「进度」等词\n'
            '- 如果是**研究相关**(文献、数据、实验), 试试包含「研究」「分析」「数据」等词\n'
            '- 如果是**创建智能体**, 直接说「创建一个智能体, 负责...」\n'
            '- 或者直接用 `@pwa`、`@researcher`、`@agent-producer` 明确指定\n\n'
            '我的团队随时待命。'
        )

    # ===================================================================
    # MCP 工具接口（保留 TRAE 兼容）
    # ===================================================================

    def list_mcp_tools(self) -> list:
        """返回所有可调用的 MCP 工具"""
        return [
            {
                "name": "idea_dispatch",
                "description": "IDEA 总调度：将复杂任务分派给最合适的专精智能体",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "任务描述"},
                        "mode": {"type": "string", "enum": ["auto", "pwa", "researcher", "agent_producer"]},
                    },
                    "required": ["task"],
                },
            },
            {
                "name": "pwa_create_plan",
                "description": "PWA 创建项目计划",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_name": {"type": "string"},
                        "objectives": {"type": "string"},
                    },
                    "required": ["project_name", "objectives"],
                },
            },
            {
                "name": "researcher_literature_review",
                "description": "Researcher 执行文献综述",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "research_question": {"type": "string"},
                    },
                    "required": ["research_question"],
                },
            },
            {
                "name": "agent_producer_create",
                "description": "AgentProducer 创建新智能体",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent_name": {"type": "string"},
                        "purpose": {"type": "string"},
                    },
                    "required": ["agent_name", "purpose"],
                },
            },
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> dict:
        """MCP 工具调用入口"""
        if tool_name == "idea_dispatch":
            result = await self.process(args["task"], [], "mcp")
            return result

        agent_map = {
            "pwa_create_plan": ("pwa", "create_plan"),
            "pwa_risk_assessment": ("pwa", "risk_assessment"),
            "researcher_literature_review": ("researcher", "literature_review"),
            "researcher_data_analysis": ("researcher", "data_analysis"),
            "agent_producer_create": ("agent_producer", "create_agent"),
        }

        if tool_name in agent_map:
            agent_name, method = agent_map[tool_name]
            agent = self.agents[agent_name]
            func = getattr(agent, method)
            return await func(**args)

        raise ValueError(f"Unknown tool: {tool_name}")

    def list_agents_info(self) -> list:
        return [
            {"id": "pwa", "name": "IDEA-ProgramWorldAdminister", "role": "项目管理", "status": "ready"},
            {"id": "researcher", "name": "IDEA-Reasearcher", "role": "科研分析", "status": "ready"},
            {"id": "agent_producer", "name": "IDEA-AgentProducer", "role": "智能体创建", "status": "ready"},
        ]
