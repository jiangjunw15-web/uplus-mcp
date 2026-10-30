"""Shared text, HTML and time helpers for the U+ MCP server."""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

CN_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")

# Only ordinary web navigation is allowed. Everything else (file://, ftp://,
# data:, javascript:, blob:, ...) is refused so a page under a model's control
# can never turn a navigation step into a local-file read.
ALLOWED_URL_SCHEMES = ("http", "https")


def safe_navigation_url(raw: str, *, base_url: str = "") -> str:
    """Validate a URL before the session navigates to it.

    Accepts only ``http``/``https``. A relative path is resolved against
    ``base_url`` so internal routes keep working; an absolute URL keeps its own
    host. Raises ``ValueError`` for any other scheme (notably ``file://``) or a
    malformed URL, so the caller surfaces the refusal instead of navigating.
    """
    candidate = (raw or "").strip()
    if not candidate:
        raise ValueError("导航 URL 不能为空")
    parsed = urlparse(candidate)
    if not parsed.scheme:
        if not base_url:
            raise ValueError(f"相对路径需要 base_url：{candidate!r}")
        parsed = urlparse(
            f"{base_url.rstrip('/')}{candidate if candidate.startswith('/') else '/' + candidate}"
        )
    if parsed.scheme not in ALLOWED_URL_SCHEMES:
        raise ValueError(
            f"拒绝导航到 {parsed.scheme or '未知'}:// 链接："
            "只允许 http/https 网页，防止读取本地文件（file://）等非网页内容。"
        )
    if not parsed.netloc:
        raise ValueError(f"URL 缺少主机名：{candidate!r}")
    return candidate if urlparse(candidate).scheme else parsed.geturl()


def strip_html(html: Optional[str]) -> str:
    """Strip tags, decode common entities and collapse whitespace.

    U+ question stems and option contents arrive as fragments of HTML such as
    ``<p>先来先服务</p>`` or ``<p>开放性&nbsp;</p>``.
    """
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ")
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


_TOKEN_KEY_RE = re.compile(
    r'(?i)("?[a-z0-9_-]*(?:token|secret|password|api[_-]?key|authorization)"?\s*[=:]\s*)'
    r'"?([^",\s}]{6,})"?'
)


def redact_secrets(text: Optional[str]) -> str:
    """Mask credential-looking key/value pairs before text reaches the model
    context or an error message (platform API bodies may carry tokens)."""
    if not text:
        return ""
    return _TOKEN_KEY_RE.sub(r"\1***", text)


def now_ms() -> int:
    return int(time.time() * 1000)


def format_ts(ms: Optional[int]) -> str:
    """Epoch milliseconds -> ``YYYY-MM-DD HH:MM`` in Asia/Shanghai."""
    if not ms:
        return "-"
    dt = datetime.fromtimestamp(ms / 1000, tz=CN_TZ)
    return dt.strftime("%Y-%m-%d %H:%M")


def deadline_label(ms: Optional[int], reference_ms: Optional[int] = None) -> str:
    """Human-friendly countdown or overdue label for a deadline."""
    if not ms:
        return "未设置截止时间"
    reference = reference_ms or now_ms()
    delta_ms = ms - reference
    if delta_ms <= 0:
        return "已截止"
    hours = delta_ms / 3_600_000
    if hours >= 48:
        days = hours / 24
        return f"剩余 {days:.1f} 天（{format_ts(ms)} 截止）"
    return f"剩余 {hours:.1f} 小时（{format_ts(ms)} 截止）"


def is_expired(ms: Optional[int], reference_ms: Optional[int] = None) -> bool:
    if not ms:
        return False
    return ms <= (reference_ms or now_ms())


def normalize_letters(raw: Optional[str]) -> str:
    """Normalize a multiple-choice answer into a sorted uppercase letter string.

    ``"b,acd"`` / ``" A C D "`` -> ``"ACD"``.
    """
    if not raw:
        return ""
    letters = re.findall(r"[A-Za-z]", raw.upper())
    return "".join(sorted(set(letters)))


def normalize_bool(raw: Optional[str]) -> Optional[bool]:
    """Parse a U+ judge answer into a real bool.

    API stores ``true`` / ``false`` strings; page buttons read ``正确`` / ``错误``.
    """
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if text in ("true", "1", "yes", "y", "正确", "对", "√", "t"):
        return True
    if text in ("false", "0", "no", "n", "错误", "错", "×", "f"):
        return False
    return None


def question_stem_number(order: Optional[int]) -> str:
    return f"第 {order} 题" if order else "未知题号"


# --------------------------------------------------------------------------- #
# file-tool path confinement
# --------------------------------------------------------------------------- #
def resolve_io_path(raw: str | os.PathLike, allowed_roots: Iterable[Path],
                    *, must_exist: bool = False) -> Path:
    """Resolve a model-supplied path and confine it to the configured roots.

    ``..`` segments, ``~`` and symlinks are collapsed via ``Path.resolve()``
    before the containment check, so ``reports/../../.ssh/id_rsa`` cannot slip
    through. Raises ``PermissionError`` with a hint on how to extend the roots
    when the path escapes every allowed root; ``FileNotFoundError`` when
    ``must_exist`` is set and the file is absent.
    """
    candidate = Path(raw).expanduser()
    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:
        raise PermissionError(f"路径无法解析：{raw!r}") from exc
    roots: list[Path] = []
    for root in allowed_roots:
        try:
            roots.append(Path(root).expanduser().resolve(strict=False))
        except OSError:  # noqa: PERF203 - an unreadable root simply cannot match
            continue
    if roots and not any(resolved == root or root in resolved.parents
                         for root in roots):
        shown = "、".join(str(root) for root in roots)
        raise PermissionError(
            f"路径 {resolved} 不在允许的目录内（{shown}）。"
            "文件读写只允许配置的 allowed_io_paths / UPLUS_ALLOWED_PATHS 目录，"
            "如需使用其它目录请把它加入配置后重试。"
        )
    if must_exist and not resolved.is_file():
        raise FileNotFoundError(f"文件不存在: {resolved}")
    return resolved
