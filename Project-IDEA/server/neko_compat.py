"""N.E.K.O Plugin Run Protocol compatibility client."""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

import httpx


class NekoCompatError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class NekoCompatClient:
    def __init__(self, base_url: str, service_token: str = "", timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def _headers(self, request_id: Optional[str] = None, idempotency_key: Optional[str] = None) -> dict[str, str]:
        headers = {"User-Agent": "IDEA-N.E.K.O-Compat/1.0"}
        if self.service_token:
            headers["Authorization"] = f"Bearer {self.service_token}"
        if request_id:
            headers["X-Request-ID"] = request_id
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    async def _request(self, method: str, path: str, *, request_id: Optional[str] = None, idempotency_key: Optional[str] = None, json_body: Optional[dict] = None, params: Optional[dict] = None) -> Any:
        if not self.enabled:
            raise NekoCompatError(503, "N.E.K.O Plugin Server 未配置")
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=self._headers(request_id, idempotency_key),
                    json=json_body,
                    params=params,
                )
        except httpx.HTTPError as error:
            raise NekoCompatError(503, "N.E.K.O Plugin Server 不可用") from error
        if response.status_code >= 400:
            raise NekoCompatError(response.status_code, "N.E.K.O 请求未成功")
        try:
            return response.json()
        except ValueError as error:
            raise NekoCompatError(502, "N.E.K.O 返回格式无效") from error

    async def health(self, request_id: Optional[str] = None) -> dict:
        try:
            payload = await self._request("GET", "/health", request_id=request_id)
            return {"available": True, "service": "neko-plugin-server", "health": payload}
        except NekoCompatError as error:
            return {"available": False, "service": "neko-plugin-server", "detail": error.detail}

    async def server_info(self, request_id: Optional[str] = None) -> dict:
        return await self._request("GET", "/server/info", request_id=request_id)

    async def plugins(self, request_id: Optional[str] = None) -> dict:
        payload = await self._request("GET", "/plugins", request_id=request_id)
        if isinstance(payload, dict):
            return payload
        return {"plugins": payload if isinstance(payload, list) else []}

    async def create_run(self, payload: dict, request_id: Optional[str] = None, idempotency_key: Optional[str] = None) -> dict:
        return await self._request("POST", "/runs", request_id=request_id, idempotency_key=idempotency_key, json_body=payload)

    async def get_run(self, run_id: str, request_id: Optional[str] = None) -> dict:
        return await self._request("GET", f"/runs/{quote(run_id, safe='')}", request_id=request_id)

    async def cancel_run(self, run_id: str, reason: Optional[str] = None, request_id: Optional[str] = None) -> dict:
        return await self._request("POST", f"/runs/{quote(run_id, safe='')}/cancel", request_id=request_id, json_body={"reason": reason} if reason else {})

    async def export_run(self, run_id: str, after: Optional[str] = None, limit: int = 200, request_id: Optional[str] = None) -> dict:
        params = {"limit": max(1, min(limit, 2000))}
        if after:
            params["after"] = after
        return await self._request("GET", f"/runs/{quote(run_id, safe='')}/export", request_id=request_id, params=params)
