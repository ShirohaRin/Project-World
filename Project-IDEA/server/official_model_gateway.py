"""Standalone gateway for server-side official OpenAI-compatible model access."""

from __future__ import annotations

import hmac
import json
import logging
import os
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger("idea.official_model_gateway")
app = FastAPI(title="Official Model Gateway")


_OFFICIAL_MODELS = {
    "gpt-5.6-terra": "GPT_TERRA",
    "gpt-5.6-sol": "GPT_SOL",
    "deepseek-v4-flash": "DEEPSEEK_FLASH",
    "deepseek-v4-pro": "DEEPSEEK_PRO",
}


def _gateway_token() -> str:
    return os.getenv("OFFICIAL_GATEWAY_TOKEN", "").strip()


def _model_prefix(model: str) -> str | None:
    normalized = model.strip().lower()
    if normalized in _OFFICIAL_MODELS:
        return _OFFICIAL_MODELS[normalized]
    return None


def _upstream_config(model: str) -> tuple[str, str, str]:
    prefix = _model_prefix(model)
    if prefix is None:
        raise HTTPException(status_code=400, detail="unsupported model")
    base_url = (
        os.getenv(f"OFFICIAL_{prefix}_API_BASE_URL", "").strip()
        or os.getenv(f"{prefix}_API_BASE_URL", "").strip()
    ).rstrip("/")
    api_key = (
        os.getenv(f"OFFICIAL_{prefix}_API_KEY", "").strip()
        or os.getenv(f"{prefix}_API_KEY", "").strip()
    )
    upstream_model = (
        os.getenv(f"OFFICIAL_{prefix}_MODEL", "").strip()
        or os.getenv(f"{prefix}_LLM_MODEL", "").strip()
        or model
    )
    if not base_url or not api_key:
        raise HTTPException(status_code=503, detail="official model is not configured")
    return base_url, api_key, upstream_model


def _completions_url(base_url: str) -> str:
    return base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"


def _authorized(authorization: str | None) -> bool:
    expected = _gateway_token()
    if not expected or not authorization:
        return False
    scheme, _, token = authorization.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(token, expected)


def _safe_upstream_headers(headers: httpx.Headers) -> dict[str, str]:
    content_type = headers.get("content-type")
    return {"Content-Type": content_type} if content_type else {}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, authorization: str | None = Header(default=None)):
    if not _authorized(authorization):
        raise HTTPException(status_code=401, detail="unauthorized")

    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="request body must be JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="request body must be an object")

    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise HTTPException(status_code=400, detail="model is required")
    base_url, api_key, upstream_model = _upstream_config(model)
    upstream_body = dict(body)
    upstream_body["model"] = upstream_model
    stream = upstream_body.get("stream") is True
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    if not stream:
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(_completions_url(base_url), headers=headers, json=upstream_body)
        except httpx.HTTPError:
            logger.warning("official upstream request failed")
            raise HTTPException(status_code=502, detail="official upstream unavailable") from None
        if response.status_code >= 400:
            logger.warning("official upstream returned HTTP %s", response.status_code)
            return JSONResponse(status_code=response.status_code, content={"error": {"message": "official upstream request failed"}})
        try:
            content = response.json()
        except ValueError:
            logger.warning("official upstream returned invalid JSON")
            raise HTTPException(status_code=502, detail="invalid official upstream response") from None
        return JSONResponse(status_code=response.status_code, content=content)

    async def event_stream() -> AsyncIterator[bytes]:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", _completions_url(base_url), headers=headers, json=upstream_body) as response:
                    if response.status_code >= 400:
                        logger.warning("official streaming upstream returned HTTP %s", response.status_code)
                        yield b'data: {"error":{"message":"official upstream request failed"}}\n\n'
                        return
                    async for chunk in response.aiter_raw():
                        if chunk:
                            yield chunk
        except httpx.HTTPError:
            logger.warning("official streaming upstream request failed")
            yield b'data: {"error":{"message":"official upstream unavailable"}}\n\n'

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("official_model_gateway:app", host=os.getenv("OFFICIAL_GATEWAY_HOST", "127.0.0.1"), port=int(os.getenv("OFFICIAL_GATEWAY_PORT", "8787")))
