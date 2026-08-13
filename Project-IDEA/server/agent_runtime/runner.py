"""Agent Runner — LLM + policy-aware tool calling loop."""

import json
import logging
from typing import AsyncIterator, Optional

from model.client import LLMClient
from tool_runtime.permissions import ExecutionContext
from tool_runtime.registry import ToolRegistry, ToolResult

logger = logging.getLogger("idea.runner")
MAX_TOOL_ROUNDS = 10


def _summarize_args(args: dict) -> dict:
    """截断工具参数摘要用于内部日志与调度信息，避免把大块内容写入日志。"""
    if not isinstance(args, dict):
        return {}
    return {key: (str(value)[:120] if value is not None else None) for key, value in args.items()}


class AgentRunner:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, system_prompt: str, model: str = None, provider: str = None):
        self.llm = llm
        self.tools = tools
        self.system_prompt = system_prompt
        self.model = model
        self.provider = provider

    async def run(self, user_message: str, history: list[dict] = None, stream: bool = False, llm_model_config: Optional[dict] = None, execution_context: Optional[ExecutionContext] = None) -> dict:
        messages = self._build_messages(user_message, history)
        tool_schemas = self.tools.schemas_for(execution_context)
        tool_calls_log = []
        for iterations in range(1, MAX_TOOL_ROUNDS + 1):
            response = await self.llm.chat(messages=messages, tools=tool_schemas, model=self.model, provider=self.provider, system_prompt=self.system_prompt, llm_model_config=llm_model_config)
            if response.get("content"):
                return {"reply": response["content"], "tool_calls_log": tool_calls_log, "iterations": iterations, "usage": response.get("usage", {})}
            tool_calls = response.get("tool_calls", [])
            if not tool_calls:
                return {"reply": response.get("content", "（模型未返回有效响应）"), "tool_calls_log": tool_calls_log, "iterations": iterations}
            for call in tool_calls:
                name, args = call["name"], call["arguments"]
                result = await self._execute_tool(name, args, execution_context)
                tool_calls_log.append({"name": name, "success": result.success, "decision": result.metadata.get("decision"), "reason": result.metadata.get("reason"), "args": _summarize_args(args)})
                messages.extend([
                    {"role": "assistant", "content": None, "tool_calls": [{"id": call.get("id", "call_0"), "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]},
                    {"role": "tool", "tool_call_id": call.get("id", "call_0"), "content": result.output},
                ])
        return {"reply": f"已达到最大工具调用轮次 ({MAX_TOOL_ROUNDS})。", "tool_calls_log": tool_calls_log, "iterations": MAX_TOOL_ROUNDS}

    async def run_stream(self, user_message: str, history: list[dict] = None, llm_model_config: Optional[dict] = None, execution_context: Optional[ExecutionContext] = None) -> AsyncIterator[dict]:
        messages = self._build_messages(user_message, history)
        tool_schemas = self.tools.schemas_for(execution_context)
        tool_calls_log = []
        iterations = 0
        while iterations < MAX_TOOL_ROUNDS:
            iterations += 1
            has_tool_call = False
            async for chunk in self.llm.chat_stream(messages=messages, tools=tool_schemas, model=self.model, provider=self.provider, system_prompt=self.system_prompt, llm_model_config=llm_model_config):
                if chunk["type"] == "text":
                    yield {"type": "text", "content": chunk["content"]}
                elif chunk["type"] == "tool_call":
                    has_tool_call = True
                    for call in chunk.get("calls", []):
                        try:
                            args = json.loads(call["arguments"]) if isinstance(call["arguments"], str) else call["arguments"]
                        except json.JSONDecodeError:
                            args = {}
                        name = call["name"]
                        yield {"type": "tool_call", "name": name}
                        result = await self._execute_tool(name, args, execution_context)
                        tool_calls_log.append({"name": name, "success": result.success, "decision": result.metadata.get("decision"), "reason": result.metadata.get("reason"), "args": _summarize_args(args)})
                        yield {"type": "tool_result", "name": name, "success": result.success, "output": result.output[:2000], "decision": result.metadata.get("decision"), "reason": result.metadata.get("reason")}
                        messages.extend([
                            {"role": "assistant", "content": None, "tool_calls": [{"id": call.get("id", "call_0"), "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]},
                            {"role": "tool", "tool_call_id": call.get("id", "call_0"), "content": result.output},
                        ])
            if not has_tool_call:
                break
        yield {"type": "done", "iterations": iterations, "tool_calls_count": len(tool_calls_log)}

    @staticmethod
    def _build_messages(user_message: str, history: list[dict] = None) -> list[dict]:
        messages = [{"role": item.get("role", "user"), "content": item.get("content", "")} for item in (history or [])[-20:] if item.get("role", "user") in {"user", "assistant"}]
        messages.append({"role": "user", "content": user_message})
        return messages

    async def _execute_tool(self, tool_name: str, args: dict, execution_context: Optional[ExecutionContext]) -> ToolResult:
        return await self.tools.execute(tool_name, args, execution_context)
