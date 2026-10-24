"""Playwright browser session with persisted U+ login state.

The session reuses a real browser context so cookies, the SPA token and any
client-side interceptors stay exactly the same as a human operator's session.
Read-only REST calls run through an in-page ``fetch`` so they inherit that
session automatically; page writes are performed on the answer page itself by
``operator.py``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, TimeoutError as PWTimeout

from .config import Config


class UplusError(RuntimeError):
    """Base error for U+ integration failures."""


class AuthExpiredError(UplusError):
    """Login state is missing or expired; a human must sign in again."""


class BrowserSession:
    def __init__(self, config: Config, *, keep_open: bool = False) -> None:
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self._pw = sync_playwright().start()
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._keep_open = keep_open
        self._started = False

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._started:
            return
        self._browser = self._pw.chromium.launch(
            headless=self.config.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        state_path = str(self.config.storage_state_file)
        state: Optional[dict[str, Any]] = None
        if Path(state_path).exists():
            try:
                state = json.loads(Path(state_path).read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                state = None
        self._context = self._browser.new_context(
            storage_state=state,
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
        )
        self._context.set_default_navigation_timeout(self.config.nav_timeout_ms)
        self._context.set_default_timeout(self.config.action_timeout_ms)
        self._page = self._context.new_page()
        self._started = True

    @property
    def page(self) -> Page:
        if not self._page:
            raise UplusError("Browser session is not started")
        return self._page

    def save_state(self) -> Path:
        if not self._context:
            raise UplusError("Browser session is not started")
        self.config.storage_state_file.parent.mkdir(parents=True, exist_ok=True)
        self._context.storage_state(path=str(self.config.storage_state_file))
        return self.config.storage_state_file

    def close(self) -> None:
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
        finally:
            self._pw.stop()
            self._started = False
            self._context = None
            self._browser = None
            self._page = None

    # ------------------------------------------------------------------ #
    # navigation
    # ------------------------------------------------------------------ #
    def url_for(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}{path if path.startswith('/') else '/' + path}"

    def open(self, path: str) -> Page:
        self.start()
        page = self.page
        page.goto(self.url_for(path), wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except PWTimeout:
            pass
        time.sleep(0.6)
        return page

    # ------------------------------------------------------------------ #
    # in-page REST reads
    # ------------------------------------------------------------------ #
    def api_get(self, path: str, *, retries: int = 1) -> dict[str, Any]:
        self.start()
        # Ensure the fetch runs inside the platform origin so cookies and CORS
        # apply the same as a human browsing session.
        host = urlparse(self.base_url).netloc
        if host not in self.page.url:
            self.open("/workbench/index/school")
        url = self.url_for(path)
        script = """
        async (url) => {
            const r = await fetch(url, {credentials: 'include', headers: {'Accept': 'application/json'}});
            const text = await r.text();
            return {status: r.status, body: text};
        }
        """
        last: Optional[dict[str, Any]] = None
        for attempt in range(retries + 1):
            resp = self.page.evaluate(script, url)
            status = int(resp.get("status", 0))
            body = str(resp.get("body", ""))
            if status == 401 or "login" in self.page.url.lower():
                raise AuthExpiredError("U+ 登录态已过期，请重新登录后再运行巡检。")
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                last = {"status": status, "raw": body[:500]}
                if attempt < retries:
                    time.sleep(1.0)
                    continue
                raise UplusError(f"接口返回非 JSON：{body[:200]}")
            if status >= 400:
                last = payload
                if attempt < retries:
                    time.sleep(1.0)
                    continue
                raise UplusError(f"接口 {url} 返回 {status}: {str(payload)[:200]}")
            return payload
        return last or {}

    # ------------------------------------------------------------------ #
    # login health check
    # ------------------------------------------------------------------ #
    def is_logged_in(self) -> bool:
        try:
            self.open("/workbench/index/school")
            content = self.page.inner_text("body")
            if "login" in self.page.url.lower():
                return False
            # A signed-in workbench renders course cards and the user name block.
            return "课程" in content and ("工作台" in content)
        except Exception:
            return False
