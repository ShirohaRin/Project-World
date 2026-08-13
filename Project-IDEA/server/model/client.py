"""
LLM 客户端 — 支持豆包(Doubao)、智谱(GLM)、OpenAI 兼容 API
"""

import json
import logging
import os
import time
import urllib.request
from typing import AsyncIterator, Optional

import httpx

logger = logging.getLogger("idea.llm")

# ---------------------------------------------------------------------------
# LLM 提供商配置
# ---------------------------------------------------------------------------
PROVIDERS = {
    "doubao": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "models": {
            "Doubao-Seed-2.1-Pro": "doubao-seed-2.1-pro-251015",
            "Doubao-Seed-2.1-Turbo": "doubao-seed-2.1-turbo-251015",
            "Doubao-Seed-Code": "doubao-seed-code-251015",
        },
    },
    "glm": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": {
            "glm-5.2": "glm-4-plus",
            "glm-4-flash": "glm-4-flash",
        },
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "models": {},
    },
    "custom": {
        "base_url": "",
        "models": {},
    },
}

MODEL_ENV_PREFIXES = {"gpt": "GPT", "deepseek-v4-flash": "DEEPSEEK"}


def selected_model_config(model_key: str) -> Optional[dict]:
    """Return the allowlisted model configuration from environment variables."""
    prefix = MODEL_ENV_PREFIXES.get(model_key)
    if not prefix:
        return None
    return {
        "provider": os.getenv(f"{prefix}_LLM_PROVIDER", "").strip() or "custom",
        "model": os.getenv(f"{prefix}_LLM_MODEL", "").strip(),
        "base_url": os.getenv(f"{prefix}_API_BASE_URL", "").strip().rstrip("/"),
        "api_key": os.getenv(f"{prefix}_API_KEY", "").strip(),
    }


class LLMClient:
    """统一的 LLM 调用客户端，支持多提供商 + Function Calling"""

    def __init__(self, config_path: str = None):
        self.providers = PROVIDERS.copy()

        # 从环境变量加载 API Key
        self.api_keys = {
            "doubao": os.getenv("DOUBAO_API_KEY", ""),
            "glm": os.getenv("GLM_API_KEY", ""),
            "openai": os.getenv("OPENAI_API_KEY", ""),
            "custom": os.getenv("CUSTOM_API_KEY", ""),
        }

        # 从环境变量加载自定义端点
        custom_url = os.getenv("CUSTOM_API_BASE_URL", "")
        if custom_url:
            self.providers["custom"]["base_url"] = custom_url

        self.default_provider = os.getenv("LLM_PROVIDER", "doubao")
        self.default_model = os.getenv("LLM_MODEL", "Doubao-Seed-2.1-Pro")

    def _connection(self, model: str, provider: str, llm_model_config: Optional[dict]) -> tuple[str, str, str]:
        if llm_model_config is not None:
            return llm_model_config["model"], llm_model_config["base_url"], llm_model_config["api_key"]
        provider = provider or self.default_provider
        model_name = model or self.default_model
        return self.get_model_id(provider, model_name), self.get_base_url(provider).rstrip("/"), self.api_keys.get(provider, "")

    def get_model_id(self, provider: str, model: str) -> str:
        """获取提供商内部的模型 ID"""
        return self.providers.get(provider, {}).get("models", {}).get(model, model)

    def get_base_url(self, provider: str) -> str:
        return self.providers.get(provider, {}).get("base_url", "")

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] = None,
        model: str = None,
        provider: str = None,
        temperature: float = 0.3,
        max_tokens: int = 16384,
        system_prompt: str = None,
        llm_model_config: Optional[dict] = None,
    ) -> dict:
        """
        单次 LLM 调用（非流式）
        返回: {"content": str, "tool_calls": list | None, "finish_reason": str, "usage": dict}
        """
        model_id, base_url, api_key = self._connection(model, provider, llm_model_config)
        if not api_key or not base_url or not model_id:
            return self._fallback_response(messages, "所选模型尚未配置" if llm_model_config is not None else "未配置 LLM API Key")
        url = f"{base_url}/chat/completions"

        # 构建请求消息列表
        req_messages = []
        if system_prompt:
            req_messages.append({"role": "system", "content": system_prompt})
        req_messages.extend(messages)

        body = {
            "model": model_id,
            "messages": req_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                if resp.status_code != 200:
                    logger.error(f"LLM API error: {resp.status_code} — {resp.text[:300]}")
                    return self._fallback_response(messages, f"LLM 调用失败 ({resp.status_code})")

                data = resp.json()
                choice = data["choices"][0]
                msg = choice.get("message", {})

                result = {
                    "content": msg.get("content", ""),
                    "finish_reason": choice.get("finish_reason", "stop"),
                    "usage": data.get("usage", {}),
                }

                # 处理 function calling
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    result["tool_calls"] = [
                        {
                            "id": tc.get("id", ""),
                            "name": tc["function"]["name"],
                            "arguments": json.loads(tc["function"]["arguments"]),
                        }
                        for tc in tool_calls
                    ]

                return result

        except Exception as e:
            logger.error(f"LLM error: {e}", exc_info=True)
            return self._fallback_response(messages, f"LLM 调用异常: {str(e)}")

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] = None,
        model: str = None,
        provider: str = None,
        temperature: float = 0.3,
        max_tokens: int = 16384,
        system_prompt: str = None,
        llm_model_config: Optional[dict] = None,
    ) -> AsyncIterator[dict]:
        """
        流式 LLM 调用
        Yields: { "type": "text" | "tool_call" | "done", "content": str, ... }
        """
        model_id, base_url, api_key = self._connection(model, provider, llm_model_config)
        if not api_key or not base_url or not model_id:
            fallback = self._fallback_response(messages, "所选模型尚未配置" if llm_model_config is not None else "未配置 LLM API Key")
            yield {"type": "text", "content": fallback["content"]}
            yield {"type": "done"}
            return
        url = f"{base_url}/chat/completions"

        req_messages = []
        if system_prompt:
            req_messages.append({"role": "system", "content": system_prompt})
        req_messages.extend(messages)

        body = {
            "model": model_id,
            "messages": req_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST", url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                ) as resp:
                    # 累积 tool_call 内容
                    tool_call_acc = {}
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            fc = delta.get("tool_calls")

                            if fc:
                                for tc in fc:
                                    idx = tc.get("index", 0)
                                    if idx not in tool_call_acc:
                                        tool_call_acc[idx] = {"id": tc.get("id", ""), "name": "", "arguments": ""}
                                    if "function" in tc:
                                        if "name" in tc["function"]:
                                            tool_call_acc[idx]["name"] = tc["function"]["name"]
                                        if "arguments" in tc["function"]:
                                            tool_call_acc[idx]["arguments"] += tc["function"]["arguments"]
                            elif delta.get("content"):
                                yield {"type": "text", "content": delta["content"]}
                        except json.JSONDecodeError:
                            continue

                    # 如果累积了 tool calls，发出
                    if tool_call_acc:
                        yield {"type": "tool_call", "calls": list(tool_call_acc.values())}

                    yield {"type": "done"}

        except Exception as e:
            logger.error(f"Stream error: {e}", exc_info=True)
            yield {"type": "text", "content": f"\n\n⚠️ LLM 流式调用异常: {str(e)}"}
            yield {"type": "done"}

    def _fallback_response(self, messages: list[dict], reason: str) -> dict:
        """LLM 不可用时的降级响应"""
        last_msg = messages[-1]["content"] if messages else ""
        return {
            "content": (
                f"⚠️ **LLM 服务不可用**：{reason}\n\n"
                f"请检查以下配置：\n"
                f"- 环境变量 `DOUBAO_API_KEY` 或 `GLM_API_KEY` 是否设置\n"
                f"- 网络是否能访问 LLM API 端点\n\n"
                f"同时，我可以用**模板模式**为你提供基础的结构化回复。"
            ),
            "finish_reason": "fallback",
            "usage": {},
        }

    def has_api_key(self) -> bool:
        """检查是否配置了任何 API Key"""
        return any(v for v in self.api_keys.values()) or any(selected_model_config(key)["api_key"] for key in MODEL_ENV_PREFIXES)
