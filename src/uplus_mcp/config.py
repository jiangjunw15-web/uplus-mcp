"""Runtime configuration for the U+ MCP server.

Configuration file layout mirrors ``config/courses.example.json``. Missing files
fall back to the bundled default course list so the server can start in read-only
mode without a personal config.

All machine-specific values (debug port, browser engine, dedicated profile
directory) resolve through config file → environment variable → safe default, so
the package runs on Windows, macOS and Linux without a hard-coded path.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .models import Course


# src/uplus_mcp/config.py -> project root = parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "courses.json"
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config" / "courses.example.json"

# Supported browser engines for CDP attach / auto launch.
BROWSER_AUTO = "auto"
BROWSER_EDGE = "edge"
BROWSER_CHROME = "chrome"
BROWSER_CHROMIUM = "chromium"
BROWSER_CHOICES = (BROWSER_AUTO, BROWSER_EDGE, BROWSER_CHROME, BROWSER_CHROMIUM)

DEFAULT_CDP_PORT = 9333


@dataclass
class Config:
    base_url: str = "https://www.eduplus.net"
    nav_timeout_ms: int = 30_000
    action_timeout_ms: int = 15_000
    deadline_warning_hours: int = 72
    report_dir: str = "reports"
    courses: list[Course] = field(default_factory=list)
    # ---- browser / CDP session -------------------------------------- #
    # When no CDP port is listening, let the server launch a browser against a
    # dedicated profile copy automatically (Edge 136+ forbids the debug flag on
    # the default profile directory, so a dedicated copy is required).
    auto_launch_browser: bool = True
    # auto / edge / chrome / chromium. ``auto`` picks whichever installed browser
    # is found first; ``chromium`` falls back to the Playwright bundled kernel.
    browser: str = BROWSER_AUTO
    # Preferred CDP port; a free fallback port is tried when this one is busy.
    cdp_port: int = DEFAULT_CDP_PORT
    # Dedicated browser profile directory; empty means "use the platform default
    # under the user home" (see launcher.dedicated_profile_dir).
    profile_dir: str = ""
    # When False (default), expired courses are skipped during patrol. Set
    # True to keep an archived view of finished courses in the report.
    include_expired_courses: bool = False
    # Roots the file-touching tools may read from or write to (report output,
    # screenshots, uploads). A path outside every root is refused, so a
    # confused or injected instruction cannot reach ~/.ssh, browser profiles
    # or arbitrary system files. Defaults to the report folder plus the system
    # temp dir; extend via config key ``allowed_io_paths`` or the
    # ``UPLUS_ALLOWED_PATHS`` environment variable (os.pathsep-separated).
    allowed_io_paths: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Keep the safe default available for directly-constructed configs
        # (tests, embedders) as well as for load_config().
        if not self.allowed_io_paths:
            self.allowed_io_paths = [self.report_dir, tempfile.gettempdir()]

    # ---- path resolution helpers ------------------------------------ #
    def resolve(self, raw: str) -> Path:
        p = Path(raw)
        return p if p.is_absolute() else (PROJECT_ROOT / p)

    @property
    def report_folder(self) -> Path:
        return self.resolve(self.report_dir)

    @property
    def io_roots(self) -> list[Path]:
        return [self.resolve(p) for p in self.allowed_io_paths]

    @property
    def auto_launch_edge(self) -> bool:
        """Deprecated alias kept for older config files and callers."""
        return self.auto_launch_browser


def _default_courses() -> list[Course]:
    """Bundled fallback: empty on purpose.

    Personal course ids and teacher names must live in the gitignored
    ``config/courses.json``; when it is absent the engine falls back to the
    signed-in account's live course list from the platform API.
    """
    return []


def _normalize_browser(raw: str) -> str:
    value = str(raw or BROWSER_AUTO).strip().lower()
    return value if value in BROWSER_CHOICES else BROWSER_AUTO


def _allowed_io_paths(data: dict, report_dir: str) -> list[str]:
    """File-tool roots: config key / env override / safe default.

    The default is the report folder plus the system temp dir, which keeps the
    documented flow (generate a report, then submit it) working out of the box
    while refusing reads of SSH keys, browser profiles and other sensitive
    trees. An explicitly configured list replaces the default entirely.
    """
    raw = data.get("allowed_io_paths")
    env = os.environ.get("UPLUS_ALLOWED_PATHS")
    if env is not None and env.strip():
        raw = [part for part in env.split(os.pathsep) if part.strip()]
    if isinstance(raw, str):
        raw = [raw]
    if not raw or not isinstance(raw, list):
        raw = [report_dir, tempfile.gettempdir()]
    return [str(part) for part in raw if str(part).strip()]


def load_config(path: Optional[str] = None) -> Config:
    """Load config from ``path``, the ``UPLUS_CONFIG`` env var, or defaults.

    Personal ``config/courses.json`` is gitignored; copy the example to start.
    Environment variables override the file for machine-specific settings:
    ``UPLUS_CDP_PORT``, ``UPLUS_BROWSER``, ``UPLUS_PROFILE_DIR``,
    ``UPLUS_ALLOWED_PATHS`` (file-tool root allow-list).
    """
    raw_path = path or os.environ.get("UPLUS_CONFIG")
    config_file: Optional[Path] = None
    if raw_path:
        config_file = Path(raw_path)
        if not config_file.is_absolute():
            config_file = PROJECT_ROOT / config_file
    elif DEFAULT_CONFIG_PATH.exists():
        config_file = DEFAULT_CONFIG_PATH

    data: dict = {}
    if config_file and config_file.exists():
        data = json.loads(config_file.read_text(encoding="utf-8"))

    # Legacy config files used ``auto_launch_edge``; map it onto the generic
    # ``auto_launch_browser`` flag so old files keep working.
    auto_launch = data.get("auto_launch_browser", data.get("auto_launch_edge", True))
    cdp_port = data.get("cdp_port", DEFAULT_CDP_PORT)
    browser_raw = data.get("browser", data.get("browser_engine", BROWSER_AUTO))
    report_dir = str(data.get("report_dir", "reports"))

    cfg = Config(
        base_url=str(data.get("base_url", "https://www.eduplus.net")).rstrip("/"),
        nav_timeout_ms=int(data.get("nav_timeout_ms", 30_000)),
        action_timeout_ms=int(data.get("action_timeout_ms", 15_000)),
        deadline_warning_hours=int(data.get("deadline_warning_hours", 72)),
        report_dir=report_dir,
        auto_launch_browser=bool(auto_launch),
        browser=_normalize_browser(browser_raw),
        cdp_port=int(cdp_port),
        profile_dir=str(data.get("profile_dir", "")),
        include_expired_courses=bool(data.get("include_expired_courses", False)),
        allowed_io_paths=_allowed_io_paths(data, report_dir),
    )

    # Environment overrides for per-machine settings.
    env_port = os.environ.get("UPLUS_CDP_PORT")
    if env_port and env_port.strip().isdigit():
        cfg.cdp_port = int(env_port)
    env_browser = os.environ.get("UPLUS_BROWSER")
    if env_browser:
        cfg.browser = _normalize_browser(env_browser)
    env_profile = os.environ.get("UPLUS_PROFILE_DIR")
    if env_profile:
        cfg.profile_dir = env_profile

    raw_courses = data.get("courses") or []
    if raw_courses:
        cfg.courses = [
            Course(
                id=str(c.get("id", "")),
                name=str(c.get("name", "")),
                teacher=str(c.get("teacher", "")),
            )
            for c in raw_courses
            if c.get("id")
        ]
    else:
        cfg.courses = _default_courses()
    return cfg
