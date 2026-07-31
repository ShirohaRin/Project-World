"""
Agent Runner — LLM + 工具调用的执行循环

这是整个系统的"引擎"：接收消息 → LLM 推理 → 工具调用 → 汇总结果
"""

import json
import logging
from typing import AsyncIterator, Optional

from llm.client import LLMClient
from tools.registry import ToolRegistry, ToolResult

logger = logging.getLogger("idea.runner")

# 最大迭代次数（防止 LLM 无限循环调用工具）
MAX_TOOL_ROUNDS = 10


class AgentRunner:
    """
    Agent 执行循环。

    流程:
    1. 将用户消息 + 历史 + 系统提示词发给 LLM
    2. 如果 LLM 返回文本 → 直接输出
    3. 如果 LLM 返回 tool_call → 执行工具 → 把结果发回 LLM → 重复 2-3
    4. 达到最大轮次或 LLM 停止 → 返回最终结果
    """

    def __init__(self, llm: LLMClient, tools: ToolRegistry, system_prompt: str,
                 model: str = None, provider: str = None):
        self.llm = llm
        self.tools = tools
        self.system_prompt = system_prompt
        self.model = model
        self.provider = provider

    async def run(
        self,
        user_message: str,
        history: list[dict] = None,
        stream: bool = False,
    ) -> dict:
        """
        执行 Agent 循环（非流式）
        返回: {"reply": str, "tool_calls_log": list, "iterations": int}
        """
        messages = self._build_messages(user_message, history)
        tool_schemas = self.tools.get_all_schemas()
        tool_calls_log = []
        iterations = 0

        while iterations < MAX_TOOL_ROUNDS:
            iterations += 1
            logger.info(f"Agent round {iterations}")

            response = await self.llm.chat(
                messages=messages,
                tools=tool_schemas,
                model=self.model,
                provider=self.provider,
                system_prompt=self.system_prompt,
            )

            # 如果 LLM 返回了文本 → 最终输出
            if response.get("content"):
                return {
                    "reply": response["content"],
                    "tool_calls_log": tool_calls_log,
                    "iterations": iterations,
                    "usage": response.get("usage", {}),
                }

            # 如果 LLM 要求调用工具
            tool_calls = response.get("tool_calls", [])
            if not tool_calls:
                # 没有 content 也没有 tool_call → 可能出错
                logger.warning(f"No content and no tool_calls. Finish: {response.get('finish_reason')}")
                return {
                    "reply": response.get("content", "（模型未返回有效响应）"),
                    "tool_calls_log": tool_calls_log,
                    "iterations": iterations,
                }

            # 执行工具调用
            for tc in tool_calls:
                tool_name = tc["name"]
                args = tc["arguments"]
                result = await self._execute_tool(tool_name, args)
                tool_calls_log.append({
                    "name": tool_name,
                    "args": args,
                    "result": result.output[:1000] if result.success else f"ERROR: {result.output[:500]}",
                    "success": result.success,
                })

                # 将工具结果追加到消息列表
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tc.get("id", "call_0"),
                        "type": "function",
                        "function": {"name": tool_name, "arguments": json.dumps(args, ensure_ascii=False)},
                    }],
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", "call_0"),
                    "content": result.output,
                })

        # 达到最大迭代
        return {
            "reply": f"已达到最大工具调用轮次 ({MAX_TOOL_ROUNDS})。以下是已执行的操作：\n\n" +
                     "\n".join(f"- **{tc['name']}**: {'✅' if tc['success'] else '❌'}" for tc in tool_calls_log),
            "tool_calls_log": tool_calls_log,
            "iterations": iterations,
        }

    async def run_stream(
        self,
        user_message: str,
        history: list[dict] = None,
    ) -> AsyncIterator[dict]:
        """
        执行 Agent 循环（流式输出）
        Yields: {"type": "text"|"tool_call"|"tool_result"|"done", "content": str, ...}
        """
        messages = self._build_messages(user_message, history)
        tool_schemas = self.tools.get_all_schemas()
        tool_calls_log = []
        iterations = 0

        while iterations < MAX_TOOL_ROUNDS:
            iterations += 1
            logger.info(f"Agent stream round {iterations}")

            # 流式调用 LLM
            has_tool_call = False
            async for chunk in self.llm.chat_stream(
                messages=messages,
                tools=tool_schemas,
                model=self.model,
                provider=self.provider,
                system_prompt=self.system_prompt,
            ):
                if chunk["type"] == "text":
                    yield {"type": "text", "content": chunk["content"]}
                elif chunk["type"] == "tool_call":
                    has_tool_call = True
                    # 执行每个 tool call
                    for tc in chunk.get("calls", []):
                        try:
                            args = json.loads(tc["arguments"]) if isinstance(tc["arguments"], str) else tc["arguments"]
                        except json.JSONDecodeError:
                            args = {}

                        tool_name = tc["name"]
                        yield {"type": "tool_call", "name": tool_name, "args": args}

                        result = await self._execute_tool(tool_name, args)
                        tool_calls_log.append({
                            "name": tool_name,
                            "args": args,
                            "success": result.success,
                        })

                        yield {
                            "type": "tool_result",
                            "name": tool_name,
                            "success": result.success,
                            "output": result.output[:2000],
                        }

                        # 追加到消息列表
                        messages.append({
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{
                                "id": tc.get("id", "call_0"),
                                "type": "function",
                                "function": {"name": tool_name, "arguments": json.dumps(args, ensure_ascii=False)},
                            }],
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", "call_0"),
                            "content": result.output,
                        })
                elif chunk["type"] == "done":
                    pass

            if not has_tool_call:
                # 没有工具调用，LLM 直接回复了文本 → 结束
                break

        yield {
            "type": "done",
            "iterations": iterations,
            "tool_calls_count": len(tool_calls_log),
        }

    def _build_messages(self, user_message: str, history: list[dict] = None) -> list[dict]:
        """构建发给 LLM 的消息列表"""
        messages = []
        if history:
            for h in history[-20:]:  # 最近 20 条历史
                role = h.get("role", "user")
                content = h.get("content", "")
                if role in ("user", "assistant"):
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_message})
        return messages

    async def _execute_tool(self, tool_name: str, args: dict) -> ToolResult:
        """执行单个工具调用"""
        func = self.tools.get_tool(tool_name)
        if not func:
            return ToolResult(False, f"未知工具: {tool_name}", tool_name)

        try:
            return await func(**args)
        except TypeError as e:
            return ToolResult(False, f"参数错误: {e}", tool_name)
        except Exception as e:
            logger.error(f"Tool {tool_name} error: {e}", exc_info=True)
            return ToolResult(False, f"执行异常: {e}", tool_name)
