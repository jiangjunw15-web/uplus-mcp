"""Runtime configuration for the U+ MCP server.

Configuration file layout mirrors ``config/courses.example.json``. Missing files
fall back to the bundled default course list so the server can start in read-only
mode without a personal config.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .models import Course


# src/uplus_mcp/config.py -> project root = parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "courses.json"
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config" / "courses.example.json"


@dataclass
class Config:
    base_url: str = "https://www.eduplus.net"
    headless: bool = True
    nav_timeout_ms: int = 30_000
    action_timeout_ms: int = 15_000
    deadline_warning_hours: int = 72
    storage_state_path: str = "storage_state.json"
    browser_profile_path: str = "browser_profile"
    report_dir: str = "reports"
    courses: list[Course] = field(default_factory=list)

    # ---- path resolution helpers ------------------------------------ #
    def resolve(self, raw: str) -> Path:
        p = Path(raw)
        return p if p.is_absolute() else (PROJECT_ROOT / p)

    @property
    def storage_state_file(self) -> Path:
        return self.resolve(self.storage_state_path)

    @property
    def profile_dir(self) -> Path:
        return self.resolve(self.browser_profile_path)

    @property
    def report_folder(self) -> Path:
        return self.resolve(self.report_dir)


def _default_courses() -> list[Course]:
    """Bundled fallback: empty on purpose.

    Personal course ids and teacher names must live in the gitignored
    ``config/courses.json``; when it is absent the engine falls back to the
    signed-in account's live course list from the platform API.
    """
    return []


def load_config(path: Optional[str] = None) -> Config:
    """Load config from ``path``, the ``UPLUS_CONFIG`` env var, or defaults.

    Personal ``config/courses.json`` is gitignored; copy the example to start.
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

    cfg = Config(
        base_url=str(data.get("base_url", "https://www.eduplus.net")).rstrip("/"),
        headless=bool(data.get("headless", True)),
        nav_timeout_ms=int(data.get("nav_timeout_ms", 30_000)),
        action_timeout_ms=int(data.get("action_timeout_ms", 15_000)),
        deadline_warning_hours=int(data.get("deadline_warning_hours", 72)),
        storage_state_path=str(data.get("storage_state_path", "storage_state.json")),
        browser_profile_path=str(data.get("browser_profile_path", "browser_profile")),
        report_dir=str(data.get("report_dir", "reports")),
    )

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
