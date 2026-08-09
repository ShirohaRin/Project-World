"""LLM 客户端 — 支持既有提供商与受控 OpenAI 兼容模型配置。"""

import json
import logging
import os
from typing import AsyncIterator, Optional

import httpx

logger = logging.getLogger("idea.llm")

PROVIDERS = {
    "doubao": {"base_url": "https://ark.cn-beijing.volces.com/api/v3", "models": {"Doubao-Seed-2.1-Pro": "doubao-seed-2.1-pro-251015", "Doubao-Seed-2.1-Turbo": "doubao-seed-2.1-turbo-251015", "Doubao-Seed-Code": "doubao-seed-code-251015"}},
    "glm": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "models": {"glm-5.2": "glm-4-plus", "glm-4-flash": "glm-4-flash"}},
    "openai": {"base_url": "https://api.openai.com/v1", "models": {}},
    "custom": {"base_url": "", "models": {}},
}
MODEL_ENV_PREFIXES = {"gpt": "GPT", "deepseek-v4-flash": "DEEPSEEK"}


def selected_model_config(model_key: str) -> Optional[dict]:
    """Return only an allowlisted, complete model configuration from private env."""
    prefix = MODEL_ENV_PREFIXES.get(model_key)
    if not prefix:
        return None
    config = {
        "provider": os.getenv(f"{prefix}_LLM_PROVIDER", "").strip() or "custom",
        "model": os.getenv(f"{prefix}_LLM_MODEL", "").strip(),
        "base_url": os.getenv(f"{prefix}_API_BASE_URL", "").strip().rstrip("/"),
        "api_key": os.getenv(f"{prefix}_API_KEY", "").strip(),
    }
    return config


class LLMClient:
    def __init__(self, config_path: str = None):
        self.providers = {name: {**data, "models": dict(data["models"])} for name, data in PROVIDERS.items()}
        self.api_keys = {"doubao": os.getenv("DOUBAO_API_KEY", ""), "glm": os.getenv("GLM_API_KEY", ""), "openai": os.getenv("OPENAI_API_KEY", ""), "custom": os.getenv("CUSTOM_API_KEY", "")}
        custom_url = os.getenv("CUSTOM_API_BASE_URL", "")
        if custom_url:
            self.providers["custom"]["base_url"] = custom_url.rstrip("/")
        self.default_provider = os.getenv("LLM_PROVIDER", "doubao")
        self.default_model = os.getenv("LLM_MODEL", "Doubao-Seed-2.1-Pro")

    def get_model_id(self, provider: str, model: str) -> str:
        return self.providers.get(provider, {}).get("models", {}).get(model, model)

    def get_base_url(self, provider: str) -> str:
        return self.providers.get(provider, {}).get("base_url", "")

    def _connection(self, model: str, provider: str, llm_model_config: Optional[dict]) -> tuple[str, str, str]:
        if llm_model_config is not None:
            return llm_model_config["model"], llm_model_config["base_url"], llm_model_config["api_key"]
        provider = provider or self.default_provider
        model_name = model or self.default_model
        return self.get_model_id(provider, model_name), self.get_base_url(provider).rstrip("/"), self.api_keys.get(provider, "")

    async def chat(self, messages: list[dict], tools: list[dict] = None, model: str = None, provider: str = None, temperature: float = 0.3, max_tokens: int = 16384, system_prompt: str = None, llm_model_config: Optional[dict] = None) -> dict:
        model_id, base_url, api_key = self._connection(model, provider, llm_model_config)
        if not api_key or not base_url or not model_id:
            return self._fallback_response(messages, "所选模型尚未配置" if llm_model_config is not None else "未配置 LLM API Key")
        req_messages = ([{"role": "system", "content": system_prompt}] if system_prompt else []) + messages
        body = {"model": model_id, "messages": req_messages, "temperature": temperature, "max_tokens": max_tokens}
        if tools:
            body.update({"tools": tools, "tool_choice": "auto"})
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(f"{base_url}/chat/completions", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=body)
            if resp.status_code != 200:
                logger.error("LLM API error: %s — %s", resp.status_code, resp.text[:300])
                return self._fallback_response(messages, f"LLM 调用失败 ({resp.status_code})")
            data = resp.json()
            choice = data["choices"][0]
            msg = choice.get("message", {})
            result = {"content": msg.get("content", ""), "finish_reason": choice.get("finish_reason", "stop"), "usage": data.get("usage", {})}
            if msg.get("tool_calls"):
                result["tool_calls"] = [{"id": tc.get("id", ""), "name": tc["function"]["name"], "arguments": json.loads(tc["function"]["arguments"])} for tc in msg["tool_calls"]]
            return result
        except Exception as error:
            logger.error("LLM error: %s", error, exc_info=True)
            return self._fallback_response(messages, f"LLM 调用异常: {error}")

    async def chat_stream(self, messages: list[dict], tools: list[dict] = None, model: str = None, provider: str = None, temperature: float = 0.3, max_tokens: int = 16384, system_prompt: str = None, llm_model_config: Optional[dict] = None) -> AsyncIterator[dict]:
        response = await self.chat(messages, tools, model, provider, temperature, max_tokens, system_prompt, llm_model_config)
        if response.get("content"):
            yield {"type": "text", "content": response["content"]}
        if response.get("tool_calls"):
            yield {"type": "tool_call", "calls": [{"id": call["id"], "name": call["name"], "arguments": json.dumps(call["arguments"], ensure_ascii=False)} for call in response["tool_calls"]]}
        yield {"type": "done"}

    def _fallback_response(self, messages: list[dict], reason: str) -> dict:
        return {"content": f"⚠️ **LLM 服务不可用**：{reason}", "finish_reason": "fallback", "usage": {}}

    def has_api_key(self) -> bool:
        return any(self.api_keys.values()) or any(selected_model_config(key)["api_key"] for key in MODEL_ENV_PREFIXES)
