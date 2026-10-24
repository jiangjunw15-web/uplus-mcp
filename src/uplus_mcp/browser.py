"""Browser session for U+ that drives the REAL Edge via CDP attach.

Architecture: this module launches the user's own Edge binary with only
Edge-official flags (``--user-data-dir`` + ``--remote-debugging-port``) on a
dedicated profile dir, then attaches Playwright over CDP. Playwright injects
no command-line flags at all, so Edge never shows the "unsupported
command-line flag" warning bar and never bounces U+ login back to the entry
page. Whatever the student signs into stays in ``browser_profile/`` forever.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, BrowserContext, Page, TimeoutError as PWTimeout

from .config import Config

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

CDP_PORT = 9333


def _kill_edges_on_profile(profile_dir: Path) -> None:
    """Kill Edge processes whose command line carries OUR profile dir only."""
    try:
        out = subprocess.run(
            ["wmic", "process", "where", "name='msedge.exe'", "get", "ProcessId,CommandLine", "/format:list"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return
    marker = str(profile_dir).lower()
    current_pid = {str(pid) for pid in []}
    for block in out.stdout.split("\n\n"):
        if marker not in block.lower():
            continue
        pid = ""
        for line in block.splitlines():
            if line.startswith("ProcessId="):
                pid = line.split("=", 1)[1].strip()
        if pid.isdigit():
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, timeout=10)


class UplusError(RuntimeError):
    """Base error for U+ integration failures."""


class AuthExpiredError(UplusError):
    """Login state is missing or expired; a human must sign in again."""


class BrowserSession:
    """Real Edge session over CDP with a persistent U+ profile."""

    def __init__(self, config: Config, *, keep_open: bool = False) -> None:
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self._pw = sync_playwright().start()
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._proc: Optional[subprocess.Popen] = None
        self._keep_open = keep_open
        self._started = False

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def _edge_binary(self) -> str:
        for path in EDGE_CANDIDATES:
            if Path(path).exists():
                return path
        raise UplusError("未找到 Microsoft Edge，请确认已安装。")

    def start(self) -> None:
        if self._started:
            return
        profile_dir = self.config.profile_dir
        profile_dir.mkdir(parents=True, exist_ok=True)
        # Headless Edge on this machine keeps its debug port closed (security
        # policy), so always run headed; the login window must be visible anyway.
        # Launch with ONLY Edge-official flags: no warning bar, no exit-21.
        try:
            self._browser = self._pw.chromium.connect_over_cdp(
                f"http://127.0.0.1:{CDP_PORT}", timeout=3000
            )
        except Exception:
            # A leftover Edge holding the profile dir makes new launches hand
            # off and exit without opening the debug port; clear it first.
            _kill_edges_on_profile(profile_dir)
            exe = self._edge_binary()
            self._proc = subprocess.Popen(
                [
                    exe,
                    f"--user-data-dir={profile_dir}",
                    f"--remote-debugging-port={CDP_PORT}",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.time() + 30
            last_exc: Optional[Exception] = None
            while time.time() < deadline:
                try:
                    self._browser = self._pw.chromium.connect_over_cdp(
                        f"http://127.0.0.1:{CDP_PORT}", timeout=2000
                    )
                    break
                except Exception as exc:
                    last_exc = exc
                    time.sleep(0.5)
            else:
                raise UplusError(f"连接 Edge 调试端口失败: {last_exc}")
        # CDP attach: Edge's own real UA/fingerprints stay untouched.
        self._context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        self._context.set_default_navigation_timeout(self.config.nav_timeout_ms)
        self._context.set_default_timeout(self.config.action_timeout_ms)
        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()
        self._started = True

    @property
    def page(self) -> Page:
        if not self._page:
            raise UplusError("Browser session is not started")
        return self._page

    def save_state(self) -> Path:
        """No-op kept for API compatibility: the real Edge profile already
        holds the session persistently."""
        return self.config.profile_dir

    def close(self) -> None:
        # Detach only: the real Edge process (and its saved login state) keeps
        # running so the profile stays reusable across sessions.
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
