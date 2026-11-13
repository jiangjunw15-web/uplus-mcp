"""Course 资料 (materials/resources) section: discover, walk, download.

The U+ platform's course home page has a left-side navigation bar with
entries like 课程 / 作业 / 实验 / 资料 / 资源. The 资料 section is not
documented in the API mapping (no route, no DOM contract) and its exact
layout varies per course. This module discovers the entry by matching the
visible text, then walks the folder tree and downloads matching files.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urljoin

from playwright.sync_api import Page, TimeoutError as PWTimeout

from .browser import BrowserSession, UplusError, AuthExpiredError
from .config import Config
from . import utils


# --------------------------------------------------------------------------- #
# data model
# --------------------------------------------------------------------------- #

@dataclass
class MaterialEntry:
    """One item in a 资料 folder listing."""
    name: str
    href: str = ""          # absolute or relative URL, empty for a folder
    kind: str = "file"      # "file" | "folder"
    size: str = ""          # human-readable size, if shown
    #: When the entry is a folder, its sub-items are filled by the caller
    #: after ``walk`` recurses into it.
    children: list["MaterialEntry"] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# entry discovery
# --------------------------------------------------------------------------- #

#: Chinese labels that may appear on the left-side navigation entry for the
#: course 资料 / 资源 section. The first match wins.
_MATERIAL_ENTRY_LABELS = [
    "资料",
    "资源",
    "素材",
    "文件",
    "课程资料",
    "课程资源",
    "教学资料",
    "学习资料",
]

#: CSS selectors that may hold the left-side navigation links. The real
#: layout is unknown, so we try the most common patterns.
_NAV_SELECTORS = [
    ".el-menu-item",
    ".el-sub-menu__title",
    ".nav-item",
    ".sidebar-item",
    ".left-nav a",
    ".course-nav a",
    "#leftNav a",
    ".el-menu a",
    ".el-aside a",
]


def _find_material_entry(page: Page) -> Optional[str]:
    """Return the href of the 资料 entry, or ``None`` if not found.

    The search is done in two stages:

    1. Try each known navigation selector and look for a link whose text
       matches one of ``_MATERIAL_ENTRY_LABELS``.
    2. Fall back to a global text scan: any ``<a>`` whose visible text
       matches the labels is a candidate.

    Returns the *absolute* URL (resolved against the page origin) so the
    caller can ``page.goto`` it directly.
    """
    for selector in _NAV_SELECTORS:
        try:
            links = page.locator(selector)
            count = min(links.count(), 40)
            for i in range(count):
                a = links.nth(i)
                text = (a.inner_text() or "").strip()
                if not text:
                    continue
                # Match the first label that appears in the text.
                for label in _MATERIAL_ENTRY_LABELS:
                    if label in text:
                        href = a.get_attribute("href") or ""
                        if href:
                            # Resolve relative hrefs against the *page* URL,
                            # not its parent: ``/course/course/c1/MaterialPage``
                            # must not become ``/course/course/MaterialPage``.
                            return urljoin(page.url, href)
        except Exception:  # noqa: BLE001 - selector may not match this layout
            continue
    # Global fallback: any <a> on the page.
    try:
        result = page.evaluate(r"""
        () => {
            const labels = ["资料", "资源", "素材", "文件", "课程资料", "课程资源", "教学资料", "学习资料"];
            const links = document.querySelectorAll('a');
            for (const a of links) {
                const text = (a.innerText || '').trim();
                for (const label of labels) {
                    if (text.includes(label)) {
                        return a.href || '';
                    }
                }
            }
            return '';
        }
        """)
        if result:
            return str(result)
    except Exception:  # noqa: BLE001
        pass
    return None


# --------------------------------------------------------------------------- #
# folder listing
# --------------------------------------------------------------------------- #

def list_folder(page: Page) -> list[MaterialEntry]:
    """Return the items on the current 资料 folder page.

    The layout is unknown, so we try two strategies:

    1. A table / list of rows where each row has a clickable name and an
       optional size column. The row is a ``<tr>`` or a ``<div>`` with a
       ``.name`` / ``.title`` child.
    2. A global scan of ``<a>`` and ``<div>`` elements whose text looks
       like a file or folder name.

    Items whose name ends with a known folder extension (e.g. ``/`` in the
    name, or the text "文件夹" / "目录") are classified as folders.
    """
    # Strategy 1: known table/list selectors.
    table_selectors = [
        ".el-table__row",
        ".file-row",
        ".folder-row",
        ".el-table tbody tr",
        ".el-table-row",
        ".resource-row",
        ".material-row",
    ]
    for selector in table_selectors:
        try:
            rows = page.locator(selector)
            count = rows.count()
            if count == 0:
                continue
            out: list[MaterialEntry] = []
            for i in range(count):
                row = rows.nth(i)
                # Try to find the clickable name.
                name_el = row.locator("a, [role='button'], .name, .title").first
                if not name_el.count():
                    continue
                name = (name_el.inner_text() or "").strip()
                if not name:
                    continue
                href = name_el.get_attribute("href") or ""
                # Size column: look for a sibling or child with a size-like text.
                size = ""
                for cell in row.locator("td, span, div").all():
                    cell_text = (cell.inner_text() or "").strip()
                    if re.search(r"\d+\s*(KB|MB|GB|B)$", cell_text, re.I):
                        size = cell_text
                        break
                kind = "folder" if (not href and re.search(r"[\\/]|文件夹|目录|folder", name, re.I)) else "file"
                out.append(MaterialEntry(name=name, href=href, kind=kind, size=size))
            if out:
                return out
        except Exception:  # noqa: BLE001
            continue

    # Strategy 2: global scan of <a> elements.
    try:
        items = page.evaluate(r"""
        () => {
            const out = [];
            const seen = new Set();
            const links = document.querySelectorAll('a');
            for (const a of links) {
                const text = (a.innerText || '').trim();
                if (!text || text.length > 200 || seen.has(text)) continue;
                seen.add(text);
                const href = a.href || '';
                const isFolder = !href || /folder|dir|目录|文件夹/i.test(text);
                out.push({name: text, href, kind: isFolder ? 'folder' : 'file', size: ''});
            }
            return out;
        }
        """) or []
        return [
            MaterialEntry(
                name=item["name"],
                href=item.get("href", ""),
                kind=item.get("kind", "file"),
                size=item.get("size", ""),
            )
            for item in items
        ]
    except Exception:  # noqa: BLE001
        return []


# --------------------------------------------------------------------------- #
# recursive walk
# --------------------------------------------------------------------------- #

def walk(
    session: BrowserSession,
    course_id: str,
    matcher: Callable[[MaterialEntry], bool],
    max_depth: int = 6,
) -> list[MaterialEntry]:
    """Walk the 资料 folder tree and return every entry that matches.

    The walk starts at the 资料 entry on the course home page and descends
    into every folder until ``max_depth`` or until no more folders are
    found. Entries are deduplicated by ``(name, href)``.

    Args:
        session: The attached browser session.
        course_id: The course whose 资料 section to walk.
        matcher: A predicate that returns ``True`` for entries the caller
            wants to keep (e.g. ``lambda e: '实验' in e.name or '模板' in e.name``).
        max_depth: Maximum recursion depth (avoids infinite loops on
            circular folder structures).

    Returns:
        All matching entries, in the order they were first seen.
    """
    seen: set[tuple[str, str]] = set()
    results: list[MaterialEntry] = []

    def _walk(page: Page, depth: int) -> None:
        if depth >= max_depth:
            return
        parent_url = page.url
        entries = list_folder(page)
        for entry in entries:
            key = (entry.name, entry.href)
            if key in seen:
                continue
            seen.add(key)
            if matcher(entry):
                results.append(entry)
            if entry.kind != "folder":
                continue
            try:
                # Navigate into the folder and recurse.
                if entry.href:
                    page.goto(urljoin(parent_url, entry.href),
                              wait_until="domcontentloaded")
                else:
                    # No href: the folder is a clickable row.
                    row = page.locator(f"text={re.escape(entry.name)}").first
                    if not row.count():
                        continue
                    row.click(timeout=5000)
                time.sleep(0.6)
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except PWTimeout:
                    pass
                _walk(page, depth + 1)
            except (PWTimeout, UplusError):
                # If the folder cannot be entered, skip it and move on.
                pass
            finally:
                # Return to this level's listing so the next sibling folder is
                # read from the right page. A click-navigated folder gives no
                # reliable history entry, so the parent URL is re-opened
                # explicitly in that case.
                restored = page.url == parent_url
                if not restored:
                    try:
                        page.go_back(wait_until="domcontentloaded")
                        time.sleep(0.3)
                    except (PWTimeout, UplusError):
                        pass
                    restored = page.url == parent_url
                if not restored or not entry.href:
                    try:
                        page.goto(parent_url, wait_until="domcontentloaded")
                        time.sleep(0.3)
                    except (PWTimeout, UplusError):
                        pass

    # Start from the course home page.
    home = session.open(f"/course/course/{course_id}")
    entry_href = _find_material_entry(home)
    if not entry_href:
        return results
    folder_page = session.open(entry_href)
    try:
        folder_page.wait_for_load_state("networkidle", timeout=8000)
    except PWTimeout:
        pass
    time.sleep(0.6)
    _walk(folder_page, 0)
    return results


# --------------------------------------------------------------------------- #
# download
# --------------------------------------------------------------------------- #

def download(
    session: BrowserSession,
    entry: MaterialEntry,
    dest: Path,
    timeout_ms: int = 60_000,
) -> Path:
    """Download a material entry to ``dest``.

    If ``entry.href`` is a real URL, the download is done via
    ``session.download_to`` (which handles both same-origin and cross-origin
    resources). If the entry has no href (a folder rendered as a clickable
    row), the function clicks the row and uses Playwright's ``expect_download``
    to capture the resulting file.

    Args:
        session: The attached browser session.
        entry: The material entry to download.
        dest: The destination path (confined to the IO roots).
        timeout_ms: How long to wait for a download to start.

    Returns:
        The resolved destination path.
    """
    safe_dest = utils.resolve_io_path(dest, session.config.io_roots)
    if entry.href:
        return session.download_to(entry.href, safe_dest)
    # No href: click the row and expect a download.
    page = session.page
    try:
        with page.expect_download(timeout=timeout_ms) as dl_info:
            row = page.locator(f"text={re.escape(entry.name)}").first
            if not row.count():
                raise UplusError(f"找不到可下载的条目：{entry.name}")
            row.click(timeout=5000)
        dl = dl_info.value
        # ``Download.save_as`` returns None; the destination is what we chose.
        dl.save_as(str(safe_dest))
        return safe_dest
    except PWTimeout:
        raise UplusError(f"下载超时（{entry.name}），请检查该条目是否可下载")
