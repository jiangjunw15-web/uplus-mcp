"""Shared text, HTML and time helpers for the U+ MCP server."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from bs4 import BeautifulSoup

CN_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


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
