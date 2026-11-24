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

import base64
import json
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, TimeoutError as PWTimeout

from .config import Config
from .launcher import (
    attach_help_text,
    default_ports,
    ensure_browser_ready,
)
from . import utils

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
        # Whether ``localStorage.AccessToken`` has already been confirmed on
        # this session's platform-origin tab (see ``api_get``). Re-armed when the
        # tab is moved off the origin, so a fresh tab is always probed once.
        self._token_probed = False

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

    @staticmethod
    def _token_value(page: Page) -> str:
        """Read the token when available; tolerate a boolean probe test double."""
        try:
            value = page.evaluate(
                "() => localStorage.getItem('AccessToken') || ''")
            # Legacy page doubles return bool from this probe. Preserve the
            # presence signal, but never expose a token value in tool results.
            return "present" if isinstance(value, bool) and value else str(value or "")
        except Exception:  # noqa: BLE001 - detached / mid-navigation
            return ""

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
        # Same scheme gate as the raw navigation tools: a caller-supplied
        # ``file://`` URL must be refused, not silently folded into base_url.
        return utils.safe_navigation_url(path, base_url=self.base_url)

    def open(self, path: str, *, timeout_ms: int = 30_000,
             settle: bool = True) -> Page:
        """Open ``path`` in the attached tab and return the page.

        ``settle=True`` (the default) waits a bounded moment for the page to
        quiet down before returning. ``settle=False`` skips that wait for
        callers that immediately do their own readiness check — the homework
        answer page, for instance, is always followed by ``read_page_state``,
        which waits for ``domcontentloaded`` and polls for the submit button.
        Skipping the settle there removes a redundant ~2 s per homework page
        without changing what any caller observes.
        """
        page = self.page
        target = self.url_for(path)
        # Re-navigating to the page the tab is already showing is the single
        # biggest waste in a patrol: a homework's answer page is opened once to
        # read its state, then again to fill it, then again to audit it, and
        # Playwright does not short-circuit ``goto`` to the same URL — each call
        # is a full SPA reload plus a networkidle wait. Skipping the reload when
        # the resolved target equals the current URL (ignoring a trailing hash
        # the SPA may add) turns 2–3 reloads into one, with no behaviour change:
        # the caller still gets the page it asked for, already loaded.
        if self._same_document_url(page.url, target):
            try:
                page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            except PWTimeout:
                pass
            return page
        page.goto(target, wait_until="domcontentloaded",
                  timeout=timeout_ms)
        if settle:
            # Best-effort settle only. ``goto(domcontentloaded)`` already
            # returned; every workflow caller (``_await_page_state``,
            # ``_await_experiment_cards``, the materials walk) does its own
            # readiness wait, so waiting for a full ``networkidle`` here used to
            # cost up to 8 s on every *distinct* page open when the course pack
            # keeps the network busy (analytics, long-poll) and never quietens.
            # A short bounded settle keeps a freshly-opened page from being
            # snapshotted mid-render for the direct ``browse_open`` tool without
            # taxing a whole patrol.
            try:
                page.wait_for_load_state("networkidle", timeout=2000)
            except PWTimeout:
                pass
            time.sleep(0.3)
        # A navigation that lands off the platform origin leaves a tab whose
        # ``localStorage.AccessToken`` is not ours, so the next API call must
        # probe again rather than trust the cached flag.
        if not utils.is_same_origin(page.url, self.base_url):
            self._token_probed = False
        return page
    @staticmethod
    def _same_document_url(current: str, target: str) -> bool:
        """True when ``current`` already shows ``target`` (fragment ignored).

        Only the URL fragment (hash) is ignored: the SPA appends route state as
        a hash on some pages, and a differing hash is still the same document.
        Scheme, host, path and query are compared exactly, because the answer
        route encodes the course/task in its path and query
        (``.../workAnswer/{course}/{task}/true?isPiYue=noPiYue``) — a different
        task is a different path and must still reload.
        """
        if not current or not target:
            return False
        try:
            cur = urlparse(current)
            tgt = urlparse(target)
        except Exception:  # noqa: BLE001 - malformed URL: treat as different
            return False
        return ((cur.scheme, cur.netloc, cur.path, cur.query)
                == (tgt.scheme, tgt.netloc, tgt.path, tgt.query))

    # ------------------------------------------------------------------ #
    # in-page REST reads
    # ------------------------------------------------------------------ #
    def _api_page(self) -> Page:
        """The page the API reads run in, with the token probe done once.

        Every API read must run inside the platform origin so cookies and
        CORS apply the same as a human browsing session. This is the shared
        preamble of :meth:`api_get` and :meth:`api_get_many`.
        """
        page = self.page
        # Ensure the fetch runs inside the platform origin so cookies and CORS
        # apply the same as a human browsing session.
        host = urlparse(self.base_url).netloc
        # Compare the parsed host, not a substring of the whole URL: a page on
        # "www.eduplus.net.evil.example" contains the platform host as text but
        # is a different origin, and the token/cookie read below must happen
        # on the platform origin only.
        try:
            current_host = urlparse(page.url).netloc
        except Exception:  # noqa: BLE001 - closed page / mid-navigation
            current_host = ""
        if current_host != host:
            self.open("/workbench/index/school")
            page = self.page
        # A stale tab may carry no token yet; land on the workbench once before
        # reading localStorage. This is a one-time probe: the fetch script
        # itself re-reads ``localStorage.AccessToken`` on every call, and a
        # token that goes stale mid-patrol is caught by the 401 recovery branch
        # below, so probing on every API call only added one ``evaluate``
        # round-trip per question (30 questions = 30 wasted round-trips). The
        # probe is re-armed whenever the tab is moved off the platform origin.
        if not getattr(self, "_token_probed", False):
            if not self._token_present(page):
                # The SPA writes the token after navigation. Give that write a
                # bounded chance before concluding the session is logged out.
                deadline = time.monotonic() + 4.0
                while time.monotonic() < deadline:
                    time.sleep(0.15)
                    page = self.page
                    if self._token_present(page):
                        break
                if not self._token_present(page):
                    self.open(WORKBENCH_PATH)
                    page = self.page
            # The page-side fetch performs the definitive token/401 check.
            # Cache the completed probe even when the local value is blank so
            # steady-state API reads keep their one-round-trip cost.
            self._token_probed = True
        return page

    #: The in-page fetch used by both the single and the batched API read.
    _API_FETCH_JS = """
    async (url) => {
        const token = localStorage.getItem('AccessToken') || '';
        const headers = {'Accept': 'application/json'};
        if (token) { headers['x-access-token'] = token; }
        const r = await fetch(url, {credentials: 'include', headers: headers});
        const text = await r.text();
        return {status: r.status, body: text};
    }
    """

    def api_get(self, path: str, *, retries: int = 1) -> dict[str, Any]:
        url = self.url_for(path)
        if not utils.is_same_origin(url, self.base_url):
            raise UplusError(
                f"拒绝向非平台域名发送携带凭据的 API 请求：{utils.redact_secrets(url)}"
            )
        page = self._api_page()
        script = self._API_FETCH_JS
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
                    f"页面请求失败（{type(exc).__name__}）："
                    f"{utils.redact_secrets(str(exc))}；"
                    f"当前页面 {utils.redact_secrets(page.url)}"
                ) from exc
            status = int(resp.get("status", 0))
            body = str(resp.get("body", ""))
            # One recovery chance: a stale/login tab can 401 while cookies on
            # another tab are still valid; bounce to the workbench once. Only
            # bounce when a retry attempt is actually left — with
            # ``retries=0`` the old condition consumed the single attempt and
            # the method fell through to ``return {}`` instead of raising.
            if status == 401 and attempt < retries:
                self.open("/workbench/index/school")
                page = self.page
                time.sleep(0.5)
                continue
            if status == 401 or "login" in page.url.lower():
                raise AuthExpiredError(
                    "U+ 登录态已过期，请在浏览器中重新登录后重试。"
                    f"（HTTP {status}，页面 {utils.redact_secrets(page.url)}，"
                    f"响应：{utils.redact_secrets(body[:200])}）"
                )
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                last = {"status": status,
                        "raw": utils.redact_secrets(body[:500])}
                if attempt < retries:
                    time.sleep(1.0)
                    continue
                raise UplusError(
                    f"接口返回非 JSON：{utils.redact_secrets(body[:200])}"
                ) from exc
            if status >= 400:
                last = payload
                if attempt < retries:
                    time.sleep(1.0)
                    continue
                raise UplusError(
                    f"接口 {utils.redact_secrets(url)} 返回 {status}: "
                    f"{utils.redact_secrets(str(payload)[:200])}"
                )
            return payload
        return last or {}

    def api_get_many(self, paths: list[str]) -> list[Any]:
        """Fetch several platform URLs in **one** browser round-trip.

        :meth:`api_get` costs one CDP round-trip per URL: a 30-question
        homework paid 30 sequential round-trips through the browser before a
        single answer could be filled. This issues the very same requests
        *in parallel inside the page* (one ``Promise.all``) and returns the
        parsed payloads in the same order, so the data observed by the caller
        is identical — only the number of round-trips drops from N to 1.

        Error handling is delegated to :meth:`api_get` so the retry and
        recovery behaviour is unchanged: when the batched call itself fails,
        or when any single response is not a plain success, that one URL is
        re-read through the sequential path (which bounces to the workbench on
        a 401 and retries on a bad body). The happy path — every URL returns
        2xx JSON — never takes the fallback.
        """
        if not paths:
            return []
        urls = [self.url_for(path) for path in paths]
        for url in urls:
            if not utils.is_same_origin(url, self.base_url):
                raise UplusError(
                    "拒绝向非平台域名发送携带凭据的 API 请求："
                    f"{utils.redact_secrets(url)}"
                )
        page = self._api_page()
        script = """
        async (urls) => {
            const token = localStorage.getItem('AccessToken') || '';
            const headers = {'Accept': 'application/json'};
            if (token) { headers['x-access-token'] = token; }
            return await Promise.all(urls.map(async (url) => {
                try {
                    const r = await fetch(url, {credentials: 'include', headers});
                    const text = await r.text();
                    return {status: r.status, body: text};
                } catch (e) {
                    return {status: 0, body: '', error: String(e)};
                }
            }));
        }
        """
        try:
            responses = page.evaluate(script, urls)
        except Exception:  # noqa: BLE001 - fall back to the sequential path
            return [self.api_get(path) for path in paths]
        if not isinstance(responses, list) or len(responses) != len(urls):
            return [self.api_get(path) for path in paths]

        payloads: list[Any] = []
        for path, resp in zip(paths, responses, strict=True):
            resp = resp if isinstance(resp, dict) else {}
            status = int(resp.get("status", 0) or 0)
            body = str(resp.get("body", ""))
            if status == 0 and resp.get("error"):
                # The in-page fetch itself failed for this one URL: re-read it
                # through the sequential path, which has the retry logic.
                payloads.append(self.api_get(path))
                continue
            if status == 401 or "login" in page.url.lower():
                # A 401 may be a stale tab rather than an expired session; the
                # sequential path bounces to the workbench and retries once.
                payloads.append(self.api_get(path))
                continue
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payloads.append(self.api_get(path))
                continue
            if status >= 400:
                payloads.append(self.api_get(path))
                continue
            payloads.append(payload)
        return payloads

    # ------------------------------------------------------------------ #
    # binary download (no JSON assumption)
    # ------------------------------------------------------------------ #
    def fetch_bytes(self, url: str, *, timeout_ms: int = 30_000) -> bytes:
        """Fetch a binary resource from the platform as raw bytes.

        Two paths are tried in order so this works for both same-origin
        resources (experiment attachments served from eduplus.net) and
        cross-origin OSS signed links (a different host, where a page-side
        ``fetch`` would be blocked by CORS):

        1. In-page ``fetch`` with the access token and ``credentials:'include'``
           — reuses the signed-in session and works for same-origin URLs.
        2. ``context.request.get`` — Playwright's API request context shares
           the browser cookies but is not subject to CORS, so it reaches a
           cross-origin OSS link the page cannot.

        Both return the response body as bytes; a non-2xx status raises
        ``UplusError`` with the status and a short body excerpt.

        The URL is validated with the same http/https allow-list as page
        navigation before either fetch path runs: an attachment href of
        ``file:///...`` (or another non-web scheme) is refused instead of
        being handed to the browser's request context.
        """
        try:
            url = utils.safe_navigation_url(url, base_url=self.base_url)
        except ValueError as exc:
            raise UplusError(f"拒绝下载非 http/https 链接：{exc}") from exc
        masked_url = utils.redact_secrets(url)
        # Only attach the platform's AccessToken and credentials when the target
        # shares the platform origin; never leak x-access-token to third-party hosts.
        if utils.is_same_origin(url, self.base_url):
            page = self.page
            try:
                result = page.evaluate(r"""
                async ([url, timeoutMs]) => {
                    try {
                        const token = localStorage.getItem('AccessToken') || '';
                        const headers = {};
                        if (token) { headers['x-access-token'] = token; }
                        const ctrl = new AbortController();
                        const id = setTimeout(() => ctrl.abort(), timeoutMs);
                        const r = await fetch(url, {credentials: 'include', headers, signal: ctrl.signal});
                        clearTimeout(id);
                        const buf = await r.arrayBuffer();
                        const bytes = new Uint8Array(buf);
                        let bin = ''; const step = 0x8000;
                        for (let i = 0; i < bytes.length; i += step) {
                            bin += String.fromCharCode.apply(null, bytes.subarray(i, i + step));
                        }
                        return {ok: r.ok, status: r.status, b64: btoa(bin)};
                    } catch (e) {
                        return {ok: false, status: 0, error: String(e)};
                    }
                }
                """, [url, timeout_ms])
                if result and result.get("ok") and result.get("b64"):
                    return base64.b64decode(result["b64"])
            except Exception:  # noqa: BLE001 - page may be detached or cross-origin
                pass
        # Fallback: the APIRequestContext is not bound by CORS and shares
        # the browser cookies, so it can reach a cross-origin OSS link.
        if self._context is not None:
            try:
                resp = self._context.request.get(url, timeout=timeout_ms)
                if resp.ok:
                    return resp.body()
                raise UplusError(
                    f"下载失败：HTTP {resp.status}（{masked_url}），"
                    f"响应：{utils.redact_secrets(resp.text()[:200])}"
                )
            except UplusError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise UplusError(
                    f"下载请求失败：{utils.redact_secrets(str(exc))}"
                    f"（{masked_url}）"
                ) from exc
        raise UplusError(
            f"无法下载 {masked_url}：页内 fetch 失败且无可用请求上下文")

    def download_to(self, url: str, dest: Path) -> Path:
        """Download ``url`` to ``dest`` (confined to the IO roots) and return it.

        ``dest`` is resolved through ``utils.resolve_io_path`` so a confused
        caller cannot write outside the configured directories.
        """
        safe_dest = utils.resolve_io_path(dest, self.config.io_roots)
        data = self.fetch_bytes(url)
        safe_dest.parent.mkdir(parents=True, exist_ok=True)
        safe_dest.write_bytes(data)
        return safe_dest

    # ------------------------------------------------------------------ #
    # login health check
    # ------------------------------------------------------------------ #
    def _session_status(self, page: Page) -> dict[str, Any]:
        """One token/URL snapshot; never persists or echoes the token value."""
        try:
            url = page.url
            token = self._token_value(page)
            logged_in = bool(token) and "login" not in url.lower()
            if logged_in and token == "present":
                # Boolean probe doubles retain the old one-time probe contract.
                self._token_probed = True
            return {
                "logged_in": logged_in,
                "token_len": len(token), "url": url, "title": page.title(),
            }
        except Exception:  # noqa: BLE001 - SPA redirect / detached frame
            return {"logged_in": False, "token_len": 0,
                    "url": getattr(page, "url", ""), "title": ""}

    def is_logged_in(self, *, timeout_ms: int = 8000) -> bool:
        """Wait for a just-finished SPA login instead of judging its first URL.

        Login writes ``AccessToken`` before the redirect has always completed.
        A single workbench navigation can cancel that redirect and race the
        subsequent token read, producing a false "logged out" immediately after
        ``login(stage=done)``. This bounded wait checks token + URL together;
        only a genuine login page triggers one workbench navigation retry.
        """
        deadline = time.monotonic() + max(timeout_ms, 0) / 1000
        bounced = False
        try:
            page = self.page
            if "login" not in page.url.lower():
                self.open(WORKBENCH_PATH)
            while True:
                page = self.page
                status = self._session_status(page)
                if status["logged_in"]:
                    try:
                        # A rendered workbench shell is not proof of a session.
                        # Validate the token/cookie against a protected read too.
                        self.api_get(
                            "/api/course/athena/courses/study?types=Theory%2CTrain",
                            retries=0,
                        )
                    except AuthExpiredError:
                        return False
                    self._token_probed = True
                    return True
                if time.monotonic() >= deadline:
                    return False
                if "login" in page.url.lower() and not bounced:
                    self.open(WORKBENCH_PATH)
                    bounced = True
                time.sleep(0.2)
        except AuthExpiredError:
            return False
        except UplusError:
            raise  # surface attach failures with the help text
        except Exception:  # page closed or permanently mid-navigation
            return False

    # ------------------------------------------------------------------ #
    # sign-out: use the platform's own action, never fabricate a local logout
    # ------------------------------------------------------------------ #
    def logout(self, *, timeout_ms: int = 15_000) -> dict[str, Any]:
        """Click the current profile's 退出登录 and verify the session ends.

        Only this attached profile is acted on. Cookies are never selectively
        edited or deleted, so signing out one U+ profile cannot destroy an
        unrelated browser profile's login. Failure is explicit; localStorage
        removal alone is never called "logged out".
        """
        self.open(WORKBENCH_PATH)
        page = self.page
        initial = self._session_status(page)
        if not initial["logged_in"] and not self._token_present(page):
            try:
                self.api_get(
                    "/api/course/athena/courses/study?types=Theory%2CTrain",
                    retries=0,
                )
            except AuthExpiredError:
                self._token_probed = False
                return {"logged_out": True, "method": "already_logged_out",
                        "message": "当前 profile 没有有效 U+ 登录态，无需退出",
                        "url": utils.redact_secrets(initial["url"])}
            # Cookies may still hold a valid session without localStorage;
            # continue through the real platform sign-out instead of guessing.
        clicked = None
        try:
            clicked = page.evaluate(r"""
        () => {
          const seen = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
          const controls = [...document.querySelectorAll(
            'button, a, [role="button"], .el-dropdown, .user-name, .user-info, .avatar'
          )].filter(seen);
          const exact = (el, text) => (el.innerText || el.textContent || '').trim() === text;
          for (const el of controls) {
            if (exact(el, '退出登录') || exact(el, '退出') || exact(el, '注销')) {
              el.click(); return {action: 'logout'};
            }
          }
          for (const el of controls) {
            const text = (el.innerText || el.getAttribute('aria-label') || '').trim();
            if (el.matches('.el-dropdown, .user-name, .user-info, .avatar') ||
                /个人中心|用户|我的/.test(text)) {
              el.click(); return {action: 'profile_menu'};
            }
          }
          return {action: 'none'};
        }
        """)
        except Exception:  # noqa: BLE001 - report a failed sign-out below
            clicked = None
        if isinstance(clicked, dict) and clicked.get("action") == "profile_menu":
            time.sleep(0.3)
            try:
                logout = page.locator("text=/^\\s*(退出登录|退出|注销)\\s*$/").last
                if logout.count() and logout.is_visible():
                    logout.click(timeout=5000)
            except Exception:  # noqa: BLE001 - keep the failure receipt explicit
                pass
        if not isinstance(clicked, dict) or clicked.get("action") == "none":
            return {"logged_out": False,
                    "message": "未找到平台「退出登录」入口，未删除本地令牌；请手动退出",
                    "url": utils.redact_secrets(page.url)}
        # Element Plus confirmation is part of the real sign-out flow.
        try:
            confirm = page.locator(".el-message-box__btns .el-button--primary")
            if confirm.count() and confirm.is_visible():
                confirm.first.click(timeout=5000)
        except Exception:  # noqa: BLE001 - no confirmation on some layouts
            pass
        deadline = time.monotonic() + max(timeout_ms, 0) / 1000
        while time.monotonic() < deadline:
            time.sleep(0.2)
            status = self._session_status(page)
            if "login" in status["url"].lower() and not self._token_present(page):
                # The protected request proves cookies did not preserve a
                # still-valid session behind a redirect-only sign-out.
                try:
                    self.api_get(
                        "/api/course/athena/courses/study?types=Theory%2CTrain",
                        retries=0,
                    )
                except AuthExpiredError:
                    self._token_probed = False
                    return {"logged_out": True, "method": "platform_ui",
                            "message": "已退出当前浏览器 profile 的 U+ 账号", "url": status["url"]}
                return {"logged_out": False,
                        "message": "平台退出后受保护接口仍可读，未报告退出成功；请手动核对",
                        "url": utils.redact_secrets(status["url"])}
        return {"logged_out": False,
                "message": "未能确认平台退出登录；本地令牌没有被单独删除，请手动点击「退出登录」",
                "url": utils.redact_secrets(page.url)}

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
        has no school field or a choice was made.

        When the typed name matches nothing, this returns False instead of
        clicking the first item: submitting credentials against a wrong school
        is never an acceptable fallback.
        """
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
            return False
        except Exception:  # noqa: BLE001 - remote search may differ per school
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

    #: Labels of the tab that accepts a phone/account plus password.
    _PASSWORD_TAB_TEXTS = ("密码登录", "账号密码登录", "账号登录")

    def _on_password_tab(self, page: Page) -> bool:
        """True when the form already shows the phone/account + password boxes.

        The portal's 学号/工号 tab labels the account box 「请输入学号/工号」 and
        requires a school choice; the 密码登录 tab labels it
        「请输入账号/手机号」 and asks for no school. A phone-number account
        only works on the latter, so the two are told apart by that hint.
        """
        try:
            return bool(page.locator(
                "input[placeholder*='手机' i], input[placeholder*='账号/手机' i]"
            ).count())
        except Exception:  # noqa: BLE001 - layout may differ per portal build
            return False

    def _switch_to_password_tab(self, page: Page) -> bool:
        """Switch the sign-in form to 「密码登录」 when it starts elsewhere.

        Returns True when the form ends up on the password tab (either because
        it already was, or because the tab was clicked); False when no such
        tab could be found, which keeps the caller's old behaviour alive for
        portals that only ship one form.
        """
        if self._on_password_tab(page):
            return True
        for label in self._PASSWORD_TAB_TEXTS:
            try:
                candidates = page.locator(f"text={label}")
                count = min(candidates.count(), 6)
                for index in range(count):
                    item = candidates.nth(index)
                    if not item.is_visible():
                        continue
                    item.click(timeout=4000)
                    time.sleep(0.8)
                    return True
            except Exception:  # noqa: BLE001 - try the next label
                continue
        return False

    def wait_for_session(self, timeout_ms: int = 120_000) -> dict[str, Any]:
        """Poll until the token exists off the login domain; return a status dict.

        The password and account are never echoed back here; only token length,
        URL and title are reported.
        """
        deadline = time.monotonic() + max(timeout_ms, 0) / 1000
        status: dict[str, Any] = {"logged_in": False, "token_len": 0, "url": "", "title": ""}
        while time.monotonic() <= deadline:
            status = self._session_status(self.page)
            if status["logged_in"]:
                # A successful login is the fresh session boundary: the next
                # API read must not bounce back to the previous login tab.
                self._token_probed = True
                return status
            if time.monotonic() >= deadline:
                break
            time.sleep(0.2)
        return status

    def open_login_page(self) -> Page:
        """Land on the portal and open the login form for manual takeover."""
        page = self.open(HOME_PATH)
        self._enter_login_form(page)
        return page

    #: Tab labels of the portal's three sign-in methods.
    LOGIN_TAB_TEXTS = {
        "student_id": ("学号/工号登录", "学号登录", "学号/工号"),
        "sms": ("短信登录", "手机验证码登录", "验证码登录"),
        "password": ("密码登录", "账号密码登录", "账号登录"),
    }

    def switch_login_tab(self, page: Page, method: str, *, wait_ms: int = 20_000) -> bool:
        """Click the sign-in tab that ``method`` needs and prove it took effect.

        The portal opens on 学号/工号登录. ``password`` and ``sms`` live on their
        own tabs, so the caller decides which tab to stand on instead of the
        code defaulting to one method.

        The login page renders its three tab labels late, so this waits for them
        and only reports success once the form underneath actually changed to
        that method's fields — a bare "click did not raise" is not enough.
        """
        labels = self.LOGIN_TAB_TEXTS.get(method, ())
        deadline = time.time() + max(wait_ms, 1) / 1000
        if self._tab_form_matches(page, method):
            return True
        while time.time() < deadline:
            for label in labels:
                try:
                    count = min(page.locator(f"text={label}").count(), 8)
                except Exception:  # noqa: BLE001 - page still rendering
                    count = 0
                for index in range(count):
                    item = page.locator(f"text={label}").nth(index)
                    try:
                        if not item.is_visible():
                            continue
                        own = (item.evaluate("el => el.innerText.trim()") or "")
                        box = item.bounding_box()
                        # Only accept the label itself, not a container that
                        # happens to contain all three labels.
                        if own != label or not box or box["width"] > 260:
                            continue
                        try:
                            item.click(timeout=4000)
                        except Exception:  # noqa: BLE001 - overlay or fade
                            item.evaluate("el => el.click()")
                    except Exception:  # noqa: BLE001 - try the next candidate
                        continue
                    if self._wait_for_tab_form(page, method, timeout_ms=6000):
                        return True
            time.sleep(0.4)
        return False

    #: Placeholders that prove the form really stands on the requested tab.
    _LOGIN_TAB_MARKERS = {
        "student_id": ("请输入学号/工号",),
        "sms": ("请输入手机号", "获取验证码"),
        "password": ("请输入账号/手机号",),
    }

    def _tab_form_matches(self, page: Page, method: str) -> bool:
        """True when the visible form belongs to ``method``'s tab.

        Placeholders are attributes, not body text, so this reads the live
        inputs and buttons instead of ``inner_text``.
        """
        try:
            state = page.evaluate(
                """() => {
                  const seen = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                  return {
                    placeholders: [...document.querySelectorAll('input')]
                      .filter(seen).map(el => el.getAttribute('placeholder') || ''),
                    buttons: [...document.querySelectorAll('button')]
                      .filter(seen).map(el => (el.innerText || '').trim()),
                  };
                }"""
            )
        except Exception:  # noqa: BLE001 - transient mid-navigation
            return False
        placeholders = " ".join(state.get("placeholders") or [])
        buttons = " ".join(state.get("buttons") or [])
        if method == "sms":
            # The password tab also says 账号/手机号, so require the SMS-only cues.
            return "请输入手机号" in placeholders or "获取验证码" in buttons
        return all(m in placeholders for m in self._LOGIN_TAB_MARKERS.get(method, ()))

    def _wait_for_tab_form(self, page: Page, method: str, *,
                           timeout_ms: int = 6000) -> bool:
        deadline = time.time() + max(timeout_ms, 1) / 1000
        while time.time() < deadline:
            if self._tab_form_matches(page, method):
                return True
            time.sleep(0.4)
        return False

    def _session_note(self, page: Page) -> Optional[dict[str, Any]]:
        """A "already signed in" result when the attached browser carries a token."""
        try:
            if not (self._token_present(page) and "login" not in page.url.lower()):
                return None
            token = str(page.evaluate("() => localStorage.getItem('AccessToken') || ''") or "")
            return {"logged_in": True, "note": "已是登录态，无需重复登录",
                    "url": page.url, "token_len": len(token)}
        except Exception:  # noqa: BLE001 - page mid-navigation
            return None

    def _body_hint(self, page: Page, limit: int = 300) -> str:
        """A redacted short body excerpt for failure receipts.

        ``page.inner_text("body")`` can raise when the page is mid-navigation
        or the frame is closing; a login-failure receipt must never turn into a
        raised exception, so the read is guarded and degrades to "".
        """
        try:
            return utils.redact_secrets(page.inner_text("body")[:limit])
        except Exception:  # noqa: BLE001 - mid-navigation / detached frame
            return ""

    def _submit_credentials(self, page: Page, username: str, password: str, *,
                            method: str, wait_ms: int, notes: tuple[str, ...] = ()) -> dict[str, Any]:
        """Fill the account/password boxes, submit, and verify the token."""
        user_box = self._first_visible(page, self._USER_SELECTORS)
        pass_box = self._first_visible(page, self._PASS_SELECTORS)
        if user_box is None or pass_box is None:
            return {
                "logged_in": False, "method": method,
                "reason": "未识别到账号/密码输入框，请改用手动登录",
                "url": utils.redact_secrets(page.url),
                "body_hint": self._body_hint(page),
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
            return {"logged_in": True, "method": method, "note": "自动登录成功",
                    "url": status["url"], "token_len": status["token_len"]}
        reason = "提交后未检测到登录成功，可能是账号密码错误、学校未选对，或需要短信/图形验证码"
        for note in notes:
            reason += f"；{note}"
        return {
            "logged_in": False, "method": method,
            "reason": reason,
            "url": utils.redact_secrets(status["url"]),
            "body_hint": self._body_hint(page),
        }

    def login_by_student_id(self, username: str, password: str, school: str, *,
                            wait_ms: int = 30_000) -> dict[str, Any]:
        """Sign in with 学号/工号 + school + password.

        This tab needs the school chosen first; a school that cannot be matched
        is reported instead of submitting credentials against a wrong school.
        """
        page = self.open(HOME_PATH)
        note = self._session_note(page)
        if note:
            return {"method": "student_id", **note}
        self._enter_login_form(page)
        self.switch_login_tab(page, "student_id")
        school = (school or "").strip()
        if not school:
            return {"logged_in": False, "method": "student_id",
                    "reason": "学号/工号登录需要学校名称", "url": page.url}
        if not self._select_school(page, school):
            return {"logged_in": False, "method": "student_id",
                    "reason": f"学校「{school}」未能在下拉里选中，请确认学校全名",
                    "url": utils.redact_secrets(page.url),
                    "body_hint": utils.redact_secrets(page.inner_text("body")[:300])}
        return self._submit_credentials(page, username, password,
                                        method="student_id", wait_ms=wait_ms)

    def login_by_password(self, username: str, password: str, *,
                          wait_ms: int = 30_000) -> dict[str, Any]:
        """Sign in with 手机号/账号 + password on the 密码登录 tab."""
        page = self.open(HOME_PATH)
        note = self._session_note(page)
        if note:
            return {"method": "password", **note}
        self._enter_login_form(page)
        tab_ok = self.switch_login_tab(page, "password")
        return self._submit_credentials(
            page, username, password, method="password", wait_ms=wait_ms,
            notes=() if tab_ok else ("未能切到密码登录标签页，按当前表单填写",),
        )

    def open_sms_login(self) -> dict[str, Any]:
        """Open the 短信登录 tab and hand over: nothing is filled here.

        The phone number and the verification code are the human's to enter;
        this call only parks the browser on the right tab and reports the URL so
        the caller can finish by hand.
        """
        page = self.open(HOME_PATH)
        note = self._session_note(page)
        if note:
            return {"method": "sms", **note}
        self._enter_login_form(page)
        tab_ok = self.switch_login_tab(page, "sms")
        body = ""
        try:
            body = page.inner_text("body")[:300]
        except Exception:  # noqa: BLE001 - mid-navigation
            pass
        return {
            "logged_in": False, "method": "sms", "manual": True,
            "tab_selected": tab_ok, "url": utils.redact_secrets(page.url),
            "reason": "" if tab_ok else "未能切到短信登录标签页，请在页面上手动选择「短信登录」",
            "body_hint": utils.redact_secrets(body),
        }

    def auto_login(self, username: str, password: str, school: str = "",
                   *, wait_ms: int = 20_000, method: str = "password") -> dict[str, Any]:
        """Backward-compatible entry point: route to the chosen method.

        ``method="student_id"`` needs ``school``; ``method="password"`` ignores it.
        Nothing here picks a method on its own: the caller (``engine.login``)
        only reaches this after the user answered the method dialog.
        """
        if method == "student_id":
            return self.login_by_student_id(username, password, school, wait_ms=wait_ms)
        return self.login_by_password(username, password, wait_ms=wait_ms)

