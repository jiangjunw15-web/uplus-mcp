"""Browser session that ATTACHES to the user's own desktop browser via CDP.

The server reuses the user's signed-in browser session rather than owning a
bundled kernel: ``launcher`` starts Edge or Chrome on a debug port using a
dedicated profile copy (Chromium 136+ ignores ``--remote-debugging-port`` on
the default profile directory), then this module attaches over the DevTools
protocol and works inside the existing U+ tab. Cookies and the SPA token are
reused as a human browsing session would use them; no login snapshot is
persisted.

Prerequisite: a debug-port browser on the configured CDP ports; call
``browse_launch_edge`` once, or let ``config.auto_launch_browser`` bring it up
automatically.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, TimeoutError as PWTimeout

from .config import Config
from .launcher import (
    attach_help_text,
    default_ports,
    ensure_browser_ready,
)

ATTACH_HELP = attach_help_text()

# Portal landing: an unauthenticated bounce redirects to the uc.eduplus.net
# login domain; a signed-in session renders the workbench SPA.
HOME_PATH = "/home/index"
WORKBENCH_PATH = "/workbench/index/school"


class UplusError(RuntimeError):
    """Base error for U+ integration failures."""


class AuthExpiredError(UplusError):
    """Login state is missing or expired; a human must sign in again."""


class BrowserSession:
    """CDP-attach session over the user's own desktop browser."""

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
        ports = default_ports(self.config.cdp_port)
        # Prefer an already-open debug port; otherwise try to bring a browser up.
        ensure_browser_ready(
            port=ports[0],
            auto_launch=self.config.auto_launch_browser,
            profile_root=self.config.profile_dir or None,
            kind=self.config.browser,
        )
        last_exc: Optional[Exception] = None
        for port in ports:
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
        """Reuse an open U+ tab, preferring the workbench/home SPA that carries
        the token; skip stale ``/login`` redirect tabs that have no session."""
        host = urlparse(self.base_url).netloc
        same_host: list[Page] = []
        for page in self._context.pages:
            try:
                url = page.url
                if "login" in url.lower():
                    continue
                if urlparse(url).netloc == host:
                    same_host.append(page)
            except Exception:  # noqa: BLE001 - transient during startup
                continue
        if same_host:
            # Prefer workbench/home tabs over course sub-pages.
            for page in same_host:
                if "/workbench/" in page.url or "/home/" in page.url:
                    return page
            return same_host[0]
        # Reuse an existing blank tab so we do not pile up tabs.
        for page in self._context.pages:
            try:
                if page.url in ("about:blank", ""):
                    return page
            except Exception:  # noqa: BLE001
                continue
        return self._context.new_page()

    @staticmethod
    def _token_present(page: Page) -> bool:
        try:
            return bool(page.evaluate("() => localStorage.getItem('AccessToken') || ''"))
        except Exception:  # noqa: BLE001 - detached / mid-navigation
            return False

    @property
    def page(self) -> Page:
        if not self._started:
            self.start()
        if self._page is None or self._page.is_closed():
            self._page = self._pick_page()
        return self._page

    def close(self) -> None:
        # Detach only: the user's browser keeps running untouched.
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
        # A stale tab may carry no token yet; land on the workbench once before
        # reading localStorage.
        if not self._token_present(page):
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
            try:
                resp = page.evaluate(script, url)
            except Exception as exc:  # navigation race / detached frame / Failed to fetch
                if attempt < retries:
                    self.open("/workbench/index/school")
                    page = self.page
                    time.sleep(0.8)
                    continue
                raise UplusError(
                    f"页面请求失败（{type(exc).__name__}）：{exc}；当前页面 {page.url}"
                ) from exc
            status = int(resp.get("status", 0))
            body = str(resp.get("body", ""))
            # One recovery chance: a stale/login tab can 401 while cookies on
            # another tab are still valid; bounce to the workbench once.
            if status == 401 and attempt == 0:
                self.open("/workbench/index/school")
                page = self.page
                time.sleep(0.5)
                continue
            if status == 401 or "login" in page.url.lower():
                raise AuthExpiredError(
                    "U+ 登录态已过期，请在浏览器中重新登录后重试。"
                    f"（HTTP {status}，页面 {page.url}，响应：{body[:200]}）"
                )
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
            if "login" in self.page.url.lower():
                return False
            # The SPA hydration order can leave an empty body on first navigation;
            # a non-empty AccessToken proves the session before we inspect text.
            if self._token_present(self.page):
                return True
            content = self.page.inner_text("body")
            # A signed-in workbench renders course cards and the user name block.
            return "课程" in content and ("工作台" in content)
        except AuthExpiredError:
            return False
        except UplusError:
            raise  # surface attach failures with the help text
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # sign-in: manual takeover or assisted auto fill
    # ------------------------------------------------------------------ #
    # Account box: match placeholder/name only. The school el-select also renders
    # a text input, so a blanket ``input[type='text']:first`` fallback would hit
    # the wrong field and must stay out of this tuple.
    _USER_SELECTORS = (
        "input[name*='user' i]", "input[name*='account' i]",
        "input[name*='loginName' i]", "input[name*='username' i]",
        "input[placeholder*='账号' i]", "input[placeholder*='用户名' i]",
        "input[placeholder*='学号' i]", "input[placeholder*='工号' i]",
    )
    _PASS_SELECTORS = (
        "input[type='password']", "input[name*='pass' i]",
        "input[placeholder*='密码' i]",
    )
    _LOGIN_BUTTON_SELECTORS = (
        "button.el-button--primary:not(.is-disabled):has-text('登录')",
        "button[type='submit']:not(.is-disabled)",
        "button:has-text('登录'):not(.is-disabled)",
        "input[type='submit']", ".login-btn",
    )

    def _first_visible(self, page: Page, selectors: tuple[str, ...]):
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() and locator.is_visible():
                    return locator
            except Exception:  # noqa: BLE001 - selector may not match this layout
                continue
        return None

    def _enter_login_form(self, page: Page) -> None:
        """The portal home page only shows a ``登录/注册`` entry; click it to
        bounce to the uc.eduplus.net login form when we are not already there."""
        if "login" in page.url.lower():
            return
        try:
            entry = page.locator(".login-button").first
            if entry.count() and entry.is_visible():
                entry.click(timeout=5000)
                try:
                    page.wait_for_url("**/login**", timeout=8000)
                except PWTimeout:
                    pass
                time.sleep(0.8)
        except Exception:  # noqa: BLE001 - entry may already be the form
            pass

    def _select_school(self, page: Page, school: str) -> bool:
        """Fill the optional school remote-search el-select; True when the form
        has no school field or a choice was made."""
        if not school:
            return True
        select = page.locator(".el-select").first
        if not (select.count() and select.is_visible()):
            return True
        try:
            select.click(timeout=4000)
            time.sleep(0.5)
            select.locator("input").first.fill(school)
            time.sleep(1.0)
            match = page.locator(".el-select-dropdown__item", has_text=school).first
            if match.count():
                match.click(timeout=4000)
                return True
            first = page.locator(".el-select-dropdown__item").first
            if first.count():
                first.click(timeout=4000)
                return True
        except Exception:  # noqa: BLE001 - remote search may differ per school
            return False
        return False

    def _click_login_button(self, page: Page) -> bool:
        for selector in self._LOGIN_BUTTON_SELECTORS:
            try:
                locator = page.locator(selector).first
                if locator.count() and locator.is_visible():
                    locator.click(timeout=4000)
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def wait_for_session(self, timeout_ms: int = 120_000) -> dict[str, Any]:
        """Poll until the token exists off the login domain; return a status dict.

        The password and account are never echoed back here; only token length,
        URL and title are reported.
        """
        deadline = time.time() + timeout_ms / 1000
        status: dict[str, Any] = {"logged_in": False, "token_len": 0, "url": "", "title": ""}
        while time.time() < deadline:
            page = self.page
            try:
                url = page.url
                token = str(page.evaluate("() => localStorage.getItem('AccessToken') || ''") or "")
                status = {
                    "logged_in": bool(token) and "login" not in url.lower(),
                    "token_len": len(token),
                    "url": url,
                    "title": page.title(),
                }
                if status["logged_in"]:
                    return status
            except Exception:  # noqa: BLE001 - detached frame / mid-navigation
                pass
            time.sleep(1.0)
        return status

    def open_login_page(self) -> Page:
        """Land on the portal and open the login form for manual takeover."""
        page = self.open(HOME_PATH)
        self._enter_login_form(page)
        return page

    def auto_login(self, username: str, password: str, school: str = "",
                   *, wait_ms: int = 20_000) -> dict[str, Any]:
        """Fill the sign-in form and submit, then verify the session token.

        Credentials live only for this call: they are not persisted to config,
        reports or logs, and the returned status never echoes them back.
        """
        page = self.open(HOME_PATH)
        if self._token_present(page) and "login" not in page.url.lower():
            return {"logged_in": True, "mode": "auto", "note": "已是登录态，无需重复登录",
                    "url": page.url, "token_len": len(
                        str(page.evaluate("() => localStorage.getItem('AccessToken') || ''") or ""))}

        self._enter_login_form(page)
        school_ok = self._select_school(page, school.strip())
        user_box = self._first_visible(page, self._USER_SELECTORS)
        pass_box = self._first_visible(page, self._PASS_SELECTORS)
        if user_box is None or pass_box is None:
            return {
                "logged_in": False, "mode": "auto",
                "reason": "未识别到账号/密码输入框，请改用手动登录",
                "url": page.url,
                "body_hint": page.inner_text("body")[:300],
            }
        user_box.fill(username)
        pass_box.fill(password)
        if not self._click_login_button(page):
            try:
                pass_box.press("Enter")
            except Exception:  # noqa: BLE001
                pass
        time.sleep(1.0)
        status = self.wait_for_session(timeout_ms=wait_ms)
        if status["logged_in"]:
            return {"logged_in": True, "mode": "auto", "note": "自动登录成功",
                    "url": status["url"], "token_len": status["token_len"]}
        reason = "提交后未检测到登录成功，可能是账号密码错误、学校未选对，或需要短信/图形验证码"
        if not school_ok:
            reason += "；学校下拉未能自动选中，请改用手动登录"
        return {
            "logged_in": False, "mode": "auto",
            "reason": reason,
            "url": status["url"],
            "body_hint": page.inner_text("body")[:300],
        }
