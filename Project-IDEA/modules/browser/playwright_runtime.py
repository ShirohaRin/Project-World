"""Playwright 浏览器执行器（本地/云端通用）。

可选依赖：playwright。未安装时调用 start() 会给出明确提示。
storage_state 用于持久化登录态：云端 Worker 可用它复用已保存的雨课堂会话。
"""

from __future__ import annotations

from pathlib import Path

from .contract.protocols import BrowserSnapshot


class PlaywrightBrowser:
    def __init__(self, headless: bool = True, storage_state_path: str | None = None, timeout_ms: int = 30_000) -> None:
        self.headless = headless
        self.storage_state_path = Path(storage_state_path) if storage_state_path else None
        self.timeout_ms = timeout_ms
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    @property
    def page(self):
        return self._require_page()

    async def start(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as error:
            raise RuntimeError("未安装 playwright。请执行 pip install playwright 并运行 playwright install chromium") from error
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        if self.storage_state_path is not None and self.storage_state_path.exists():
            self._context = await self._browser.new_context(storage_state=str(self.storage_state_path))
        else:
            self._context = await self._browser.new_context()
        self._page = await self._context.new_page()
        self._page.set_default_timeout(self.timeout_ms)

    async def navigate(self, url: str) -> BrowserSnapshot:
        page = self._require_page()
        await page.goto(url, wait_until="domcontentloaded")
        return await self.snapshot()

    async def snapshot(self) -> BrowserSnapshot:
        page = self._require_page()
        text = (await page.inner_text("body")) or ""
        return BrowserSnapshot(url=page.url, title=(await page.title()), text=text)

    async def click(self, selector: str) -> None:
        await self._require_page().click(selector)

    async def type_text(self, selector: str, text: str) -> None:
        await self._require_page().fill(selector, text)

    async def screenshot(self) -> bytes:
        return await self._require_page().screenshot()

    async def save_storage_state(self, path: str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        await self._context.storage_state(path=str(target))

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._page = self._context = self._browser = self._playwright = None

    def _require_page(self):
        if self._page is None:
            raise RuntimeError("浏览器尚未启动，请先调用 start()")
        return self._page
