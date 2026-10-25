"""Browser session that ATTACHES to the user's own logged-in Edge via CDP.

This module never launches a browser and never owns a profile: the student
opens Edge themselves, signs into U+ normally, and every tool call attaches
to that live browser over the DevTools protocol and works inside the existing
U+ tab. Because the session is the user's real one, the platform's redirect
bounce never triggers.

Prerequisite (one-time per Edge session): start Edge with the debug port —

    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" --remote-debugging-port=9333

(Fully exit Edge first, otherwise the flag is ignored by the running instance.)
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, TimeoutError as PWTimeout

from .config import Config
from .launcher import CDP_PORTS, ATTACH_HELP_TEXT, ensure_browser_ready

ATTACH_HELP = ATTACH_HELP_TEXT


class UplusError(RuntimeError):
    """Base error for U+ integration failures."""


class AuthExpiredError(UplusError):
    """Login state is missing or expired; a human must sign in again."""


class BrowserSession:
    """CDP-attach session over the user's own Edge browser."""

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
        # Prefer an already-open debug port; otherwise try to bring Edge up.
        ensure_browser_ready(port=CDP_PORTS[0], auto_launch=self.config.auto_launch_edge)
        last_exc: Optional[Exception] = None
        for port in CDP_PORTS:
            try:
                self._browser = self._pw.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{port}", timeout=5000
                )
                break
            except Exception as exc:
                last_exc = exc
        if self._browser is None:
            raise UplusError(f"{ATTACH_HELP}\n（连接失败原因: {last_exc}）")
        self._context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        self._context.set_default_navigation_timeout(self.config.nav_timeout_ms)
        self._context.set_default_timeout(self.config.action_timeout_ms)
        self._page = self._pick_page()
        self._started = True

    def _pick_page(self) -> Page:
        """Reuse the open U+ tab when present, otherwise open a new tab in the
        user's browser (their login cookies apply automatically)."""
        host = urlparse(self.base_url).netloc
        for page in self._context.pages:
            try:
                if urlparse(page.url).netloc == host:
                    return page
            except Exception:  # noqa: BLE001 - transient during startup
                continue
        # Reuse an existing blank tab so we do not pile up tabs.
        for page in self._context.pages:
            try:
                if page.url in ("about:blank", ""):
                    return page
            except Exception:  # noqa: BLE001
                continue
        return self._context.new_page()

    @property
    def page(self) -> Page:
        if not self._started:
            self.start()
        if self._page is None or self._page.is_closed():
            self._page = self._pick_page()
        return self._page

    def save_state(self) -> Path:
        """No-op kept for API compatibility: login lives in the user's Edge."""
        from pathlib import Path
        return Path(".")

    def close(self) -> None:
        # Detach only: the user's Edge browser keeps running untouched.
        try:
            if self._browser:
                self._browser.close()
        finally:
            self._pw.stop()
            self._started = False
            self._browser = None
            self._context = None
            self._page = None

    # ------------------------------------------------------------------ #
    # navigation
    # ------------------------------------------------------------------ #
    def url_for(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}{path if path.startswith('/') else '/' + path}"

    def open(self, path: str) -> Page:
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
        page = self.page
        # Ensure the fetch runs inside the platform origin so cookies and CORS
        # apply the same as a human browsing session.
        host = urlparse(self.base_url).netloc
        if host not in page.url:
            self.open("/workbench/index/school")
            page = self.page
        url = self.url_for(path)
        script = """
        async (url) => {
            const token = localStorage.getItem('AccessToken') || '';
            const headers = {'Accept': 'application/json'};
            if (token) { headers['x-access-token'] = token; }
            const r = await fetch(url, {credentials: 'include', headers: headers});
            const text = await r.text();
            return {status: r.status, body: text};
        }
        """
        last: Optional[dict[str, Any]] = None
        for attempt in range(retries + 1):
            resp = page.evaluate(script, url)
            status = int(resp.get("status", 0))
            body = str(resp.get("body", ""))
            if status == 401 or "login" in page.url.lower():
                raise AuthExpiredError("U+ 登录态已过期，请在浏览器中重新登录后重试。")
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
        except AuthExpiredError:
            return False
        except UplusError:
            raise  # surface attach failures with the help text
        except Exception:
            return False
