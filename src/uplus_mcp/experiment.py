"""Experiment retrieval: read requirements and discover/download templates.

Routes captured against eduplus.net on 2026-10-28:
- Experiment list:  /course/course/{course_id}/ExperimentPage?userRole=
- Experiment detail: /course/course/{course_id}/ExperimentPage/expInfo/{exp_id}/stu_experiments/{stu_exp_id}/labsubmit

The detail page has four tabs: 实验提交物 / 实验手册 / 实验视频 / 实验附件.
This module is read-only: it parses the detail page and locates downloadable
materials; it never fills, uploads or submits a report.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PWTimeout

from .browser import BrowserSession, UplusError, AuthExpiredError
from .config import Config
from .models import Course
from . import utils


# The experiment heading is ``实验N <topic>`` where N is a Chinese or an
# Arabic numeral. Matching only 实验一/实验二 left every later experiment
# (实验三 … 实验九) nameless.
# The course name is often glued in front (``人工智能基础-实验3-知识提取实践``),
# so an optional ``…-`` prefix is allowed; a line that carries a colon is a
# field ("实验要求：…"), never a title.
_EXPERIMENT_NAME_RE = re.compile(
    r"^(?:[^：:]{0,30}[-\s])?实验\s*[一二三四五六七八九十0-9]+"
)


def extract_experiment_name(text: str) -> str:
    """First title line of an experiment detail page."""
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or len(stripped) > 60:
            continue
        if "：" in stripped or ":" in stripped:
            continue
        if _EXPERIMENT_NAME_RE.match(stripped):
            return stripped
    # Fallback kept from the earlier heuristic: a topic line that carries no
    # 实验 prefix. It must look like a *title*, not a sentence: requirement
    # lines ("实验要求：掌握进程同步…", "提交物：进程同步实验报告") would
    # otherwise be reported as the experiment's name.
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or len(stripped) > 40:
            continue
        if "：" in stripped or ":" in stripped:
            continue
        if "进程" in stripped:
            return stripped
    return ""


@dataclass
class Attachment:
    """One attachment on the 实验附件 tab."""
    name: str
    href: str = ""          # absolute URL, empty if only a label
    kind: str = "file"       # "file" | "link" | "video"


@dataclass
class ExperimentDetail:
    """Full experiment detail read from the labsubmit page."""
    course_id: str
    exp_id: str
    stu_exp_id: str
    name: str
    status: str = ""               # 已提交 / 未提交 / 待批改
    environment: str = ""          # 实验环境
    material: str = ""              # 提交物要求
    deadline: str = ""              # 截止时间 (display string)
    deadline_ms: Optional[int] = None
    manual_text: str = ""           # 实验手册 (requirements text)
    attachments: list[Attachment] = field(default_factory=list)
    submitted_files: list[str] = field(default_factory=list)
    #: Download links for the files already submitted (captured on the
    #: 实验提交物 tab). The audit uses these to pull the submitted report back
    #: and inspect it; an empty list means the audit can only read page text.
    submitted_links: list[Attachment] = field(default_factory=list)
    #: 「下载」 buttons seen on the 实验提交物 tab. The platform renders the
    #: download control as a <button>, so ``submitted_links`` stays empty even
    #: though a file was submitted; these labels are the evidence of that.
    submitted_download_buttons: list[str] = field(default_factory=list)
    #: Download links found on the 实验手册 tab. The manual is sometimes a
    #: downloadable .docx/.pdf rather than inline text, and that file may carry
    #: the report template or the requirements we need.
    manual_links: list[Attachment] = field(default_factory=list)
    page_url: str = ""
    #: Tabs whose read failed, as ``{tab: reason}``. A tab that cannot be
    #: clicked or read used to be swallowed silently, so ``read_detail``
    #: returned a "successful" detail whose manual text / attachments were
    #: simply missing and the caller could not tell a page with no manual from
    #: a page whose manual could not be reached. Recording it here keeps the
    #: returned structure unchanged while making the gap visible downstream.
    tab_errors: dict[str, str] = field(default_factory=dict)


class ExperimentOperator:
    """Read experiment detail, generate report, upload submission."""

    DETAIL_ROUTE = (
        "/course/course/{course_id}/ExperimentPage/expInfo/{exp_id}"
        "/stu_experiments/{stu_exp_id}/labsubmit"
    )

    def __init__(self, config: Config, session: BrowserSession) -> None:
        self.config = config
        self.session = session

    # ------------------------------------------------------------------ #
    # read detail
    # ------------------------------------------------------------------ #
    def open_detail(self, course_id: str, exp_id: str,
                    stu_exp_id: str) -> Page:
        url = self.DETAIL_ROUTE.format(
            course_id=utils.safe_url_segment(course_id, "course_id"),
            exp_id=utils.safe_url_segment(exp_id, "exp_id"),
            stu_exp_id=utils.safe_url_segment(stu_exp_id, "stu_exp_id"),
        )
        page = self._open_page(url)
        if "login" in page.url.lower():
            raise AuthExpiredError("U+ 登录态已过期，请重新登录。")
        # The detail page can keep a long-polling connection open (ONLYOFFICE
        # manual preview), so "networkidle" may never fire. The page is already
        # navigated; parse what is rendered instead of failing the whole read.
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except PWTimeout:
            pass
        time.sleep(0.5)
        return page

    def _open_page(self, url: str, *, retries: int = 1,
                   timeout_ms: int = 45_000) -> Page:
        """Open a platform page, retrying once on a navigation timeout.

        Some course experiment pages are heavy enough to blow the default
        30-second ``goto`` budget, and the first attempt is the one that
        usually dies while the SPA is still warming up. Retrying beats
        reporting "this course has no experiments".
        """
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return self.session.open(url, timeout_ms=timeout_ms)
            except TypeError:  # a session stub that takes only the URL
                return self.session.open(url)
            except PWTimeout as exc:
                last = exc
                if attempt < retries:
                    time.sleep(1.5)
        raise UplusError(
            f"页面加载超时（已重试 {retries} 次）：{url}；"
            "该课程可能没有实验列表，或平台响应过慢"
        ) from last

    def read_detail(self, course_id: str, exp_id: str,
                    stu_exp_id: str) -> ExperimentDetail:
        page = self.open_detail(course_id, exp_id, stu_exp_id)
        text = page.inner_text("body")

        # basic fields
        name = extract_experiment_name(text)

        detail = ExperimentDetail(
            course_id=course_id, exp_id=exp_id, stu_exp_id=stu_exp_id,
            name=name, page_url=page.url,
        )

        # status badge
        if "已提交" in text:
            detail.status = "已提交"
        elif "未提交" in text or "待提交" in text:
            detail.status = "未提交"

        # environment / material / deadline
        m_env = re.search(r"环境[：:]\s*([^\n]+)", text)
        if m_env:
            detail.environment = m_env.group(1).strip()
        m_mat = re.search(r"提交物[：:]\s*([^\n]+)", text)
        if m_mat:
            detail.material = m_mat.group(1).strip()
        m_dl = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", text)
        if m_dl:
            detail.deadline = m_dl.group(1)
            detail.deadline_ms = utils.parse_cn_datetime(detail.deadline)

        # submitted files
        m_files = re.findall(r"实验报告[：:]\s*([^\n]+?)(?=下载|撤回|$)", text)
        detail.submitted_files = [f.strip() for f in m_files if f.strip()]

        # download links for the submitted file(s), captured while the
        # 实验提交物 tab is still showing (before the tab switches below)
        try:
            links = page.evaluate(r"""
                () => {
                    const out = [];
                    document.querySelectorAll('a').forEach(a => {
                        const t = (a.innerText || '').trim();
                        const href = a.href || '';
                        if (!href) return;
                        if (/下载/.test(t) || /download|attachment|file/i.test(href)) {
                            out.push({name: t || '下载', href});
                        }
                    });
                    return out;
                }
            """) or []
            detail.submitted_links = [
                Attachment(name=str(a.get("name", "")), href=str(a.get("href", "")))
                for a in links if a.get("href")
            ]
            # The live page renders 「下载」 as a <button>, not an <a>, so the
            # href scan finds nothing. Record the buttons so the audit can say
            # "download only available by clicking" instead of claiming there
            # is no submitted file to compare against.
            buttons = page.evaluate(r"""
                () => {
                    const out = [];
                    document.querySelectorAll('button').forEach(b => {
                        const t = (b.innerText || '').trim();
                        if (/下载/.test(t)) out.push(t);
                    });
                    return out;
                }
            """) or []
            detail.submitted_download_buttons = [str(b) for b in buttons]
        except Exception as exc:  # noqa: BLE001 - keep reading the other tabs
            detail.tab_errors["实验提交物"] = utils.redact_secrets(str(exc))

        # read 实验手册 tab — the manual may be inline text *and* carry
        # downloadable files (a .docx handbook, a template, reference code).
        # Both are captured: the text for requirement extraction, the links
        # for template / material download.
        try:
            # ``_click_tab`` already waits for the tab content to load, so an
            # extra fixed sleep here only doubled the per-tab cost (~2s each)
            # without making the read more reliable.
            self._click_tab(page, "实验手册")
            detail.manual_text = page.inner_text("body")
            manual_atts = page.evaluate(r"""
                () => {
                    const out = [];
                    document.querySelectorAll('a').forEach(a => {
                        const t = (a.innerText || '').trim();
                        const href = a.href || '';
                        if (!href || !t || t.length > 200) return;
                        out.push({name: t, href});
                    });
                    return out;
                }
            """) or []
            for a in manual_atts:
                detail.manual_links.append(
                    Attachment(name=str(a.get("name", "")), href=str(a.get("href", "")))
                )
        except Exception as exc:  # noqa: BLE001 - record which tab was unreadable
            detail.tab_errors["实验手册"] = utils.redact_secrets(str(exc))

        # read 实验附件 tab
        try:
            # Same reasoning as 实验手册: ``_click_tab`` waits for the content.
            self._click_tab(page, "实验附件")
            atts = page.evaluate(r"""
                () => {
                    const out = [];
                    document.querySelectorAll('a').forEach(a => {
                        const t = (a.innerText || '').trim();
                        if (t && t.length < 200) {
                            out.push({name: t, href: a.href || ''});
                        }
                    });
                    return out;
                }
            """) or []
            for a in atts:
                detail.attachments.append(
                    Attachment(name=str(a.get("name", "")), href=str(a.get("href", "")))
                )
        except Exception as exc:  # noqa: BLE001 - record which tab was unreadable
            detail.tab_errors["实验附件"] = utils.redact_secrets(str(exc))

        return detail

    def _click_tab(self, page: Page, label: str) -> None:
        """Click a tab label (实验提交物 / 实验手册 / 实验视频 / 实验附件).

        Tab buttons live inside ``.exp-info-tab-btn``; the selected one carries
        ``blue-btn-style``, the others ``btn-style``.
        """
        result = page.evaluate(r"""
            (label) => {
                const btns = document.querySelectorAll('.exp-info-tab-btn span');
                for (const span of btns) {
                    if ((span.innerText || '').trim() === label) {
                        span.click();
                        return true;
                    }
                }
                return false;
            }
        """, label)
        if not result:
            raise UplusError(f"找不到标签页：{label}")
        time.sleep(1.2)  # wait for tab content to load

    # ------------------------------------------------------------------ #
    # extract exp_id / stu_exp_id from list page
    # ------------------------------------------------------------------ #
    def list_experiment_ids(self, course: Course) -> list[dict]:
        """Return [{name, exp_id, stu_exp_id, status, material, deadline}]."""
        safe_cid = utils.safe_url_segment(course.id, "course_id")
        page = self._open_page(
            f"/course/course/{safe_cid}/ExperimentPage?userRole="
        )
        if "login" in page.url.lower():
            raise AuthExpiredError("U+ 登录态已过期，请重新登录。")
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except PWTimeout:
            pass
        time.sleep(1.0)

        rows = page.evaluate(r"""
            () => {
                const out = [];
                document.querySelectorAll('.exp-item').forEach(card => {
                    const link = card.querySelector('a[href*="expInfo"]');
                    const href = link ? link.getAttribute('href') : '';
                    // Same id shape the list reader accepts: hex ids are
                    // common, but uppercase / dashed ids also occur.
                    const m = href.match(/expInfo\/([a-zA-Z0-9_-]+)\/stu_experiments\/([a-zA-Z0-9_-]+)/);
                    const name = card.querySelector('.exp-item-center-name');
                    const flag = card.querySelector('.flag');
                    const text = card.innerText || '';
                    const m_mat = text.match(/提交物[：:]\s*([^\n]+)/);
                    const m_dl = text.match(/(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})/);
                    out.push({
                        name: name ? name.innerText.trim() : '',
                        exp_id: m ? m[1] : '',
                        stu_exp_id: m ? m[2] : '',
                        flag: flag ? (flag.innerText || '').trim() : '',
                        material: m_mat ? m_mat[1].trim() : '',
                        deadline: m_dl ? m_dl[1] : '',
                    });
                });
                return out;
            }
        """)
        return rows or []

    # ------------------------------------------------------------------ #
    # retrieval helpers (read-only: requirements and template discovery)
    # ------------------------------------------------------------------ #
    def collect_requirements(self, detail: ExperimentDetail) -> str:
        """Merge every requirement source into one text block.

        Sources, in order:
        1. ``提交物`` (material) — what the report must contain.
        2. ``实验手册`` (manual) — the main requirement document.
        3. ``实验附件`` attachment names — files that carry additional
           requirements.
        """
        parts: list[str] = []
        if detail.material:
            parts.append(f"提交物：{detail.material}")
        if detail.manual_text:
            parts.append(detail.manual_text)
        if detail.attachments:
            names = "、".join(a.name for a in detail.attachments if a.name)
            if names:
                parts.append(f"实验附件：{names}")
        return "\n\n".join(parts).strip()

    def find_report_template(self, detail: ExperimentDetail,
                             max_depth: int = 6,
                             walk_materials: bool = True) -> Optional[dict]:
        """Locate the best report template across **all** experiment sources.

        Search order (first downloadable match wins): 实验附件 → 实验手册 →
        course 资料 (walked recursively into folders). Downloads the file so
        the caller can read it locally; nothing is filled, uploaded or
        submitted here.

        ``walk_materials=False`` stops after the 附件 / 手册 search and returns
        ``None`` for the 资料 step — the caller (``download_experiment_materials``)
        has already walked and downloaded every matching 资料 file, so a second
        walk here would re-walk the folder tree and re-download the template.
        The caller picks the best 资料 entry from the entries it already holds.

        Returns ``{"name", "path", "source"}`` or ``None`` when no downloadable
        template is found. A missing template is not an error.
        """
        exp_label = self._experiment_label(detail.name)
        dest_dir = self.config.report_folder / "experiment_materials"

        hit = self._best_downloadable_attachment(
            detail.attachments, exp_label, dest_dir)
        if hit:
            hit["source"] = "实验附件"
            return hit

        hit = self._best_downloadable_attachment(
            detail.manual_links, exp_label, dest_dir)
        if hit:
            hit["source"] = "实验手册"
            return hit

        if not walk_materials:
            # The caller already walked 资料 and downloaded every match; it
            # reuses those entries instead of walking the tree again.
            return None

        from . import materials

        matcher = _template_matcher(exp_label)
        try:
            entries = materials.walk(
                self.session, detail.course_id, matcher, max_depth=max_depth)
        except AuthExpiredError:
            raise
        except Exception as exc:  # noqa: BLE001 - 资料 is best-effort
            raise UplusError(f"资料板块检索失败：{exc}") from exc
        if not entries:
            return None
        downloadable = [e for e in entries if e.href]
        downloadable.sort(key=_template_priority)
        if not downloadable:
            entries.sort(key=_template_priority)
            return {"name": entries[0].name, "path": "", "source": "资料"}
        entry = downloadable[0]
        dest = dest_dir / _safe_filename(entry.name)
        try:
            path = materials.download(self.session, entry, dest)
        except AuthExpiredError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise UplusError(f"模板下载失败（{entry.name}）：{exc}") from exc
        return {"name": entry.name, "path": str(path), "source": "资料"}

    def _best_downloadable_attachment(
        self, attachments: list[Attachment], exp_label: str,
        dest_dir: Path) -> Optional[dict]:
        """Pick the best downloadable template-like attachment from a list.

        Used for both 实验附件 and 实验手册 links. Returns
        ``{"name", "path"}`` for the first downloadable match (ranked by
        :func:`_template_priority`), or ``None`` when nothing in the list
        both matches the template name *and* carries a download href.
        """
        label = (exp_label or "").replace(" ", "")
        candidates: list[tuple[int, Attachment]] = []
        for att in attachments:
            if not att.href or not att.name:
                continue
            name = att.name.strip()
            compact = name.replace(" ", "")
            is_template = (
                ("模板" in name and ("报告" in name or "实验" in name))
                or (label and label in compact)
            )
            if not is_template:
                continue
            priority = _template_priority(_NameOnly(name))
            candidates.append((priority, att))
        if not candidates:
            return None
        candidates.sort(key=lambda pair: pair[0])
        _, best = candidates[0]
        dest = dest_dir / _safe_filename(best.name)
        try:
            path = self.session.download_to(best.href, dest)
        except AuthExpiredError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise UplusError(f"模板下载失败（{best.name}）：{exc}") from exc
        return {"name": best.name, "path": str(path)}

    @staticmethod
    def _experiment_label(name: str) -> str:
        """``实验一 进程同步`` -> ``实验一``; falls back to the full name."""
        m = re.match(r"^(实验\s*[一二三四五六七八九十0-9]+)", name or "")
        return m.group(1).replace(" ", "") if m else (name or "")


def _template_matcher(exp_label: str):
    """Build the predicate used to search 资料 for a template.

    Matches either the experiment's own label (``实验一``) or a report
    template name (``实验报告模板``/``报告模板``), so both the per-experiment
    materials and the shared template are found.
    """
    label = (exp_label or "").replace(" ", "")

    def matcher(entry) -> bool:
        name = (entry.name or "").strip()
        if not name:
            return False
        if "模板" in name and ("报告" in name or "实验" in name):
            return True
        if label and label in name.replace(" ", ""):
            return True
        return False

    return matcher


class _NameOnly:
    """Adapter so ``_template_priority`` can rank a bare attachment name."""

    def __init__(self, name: str) -> None:
        self.name = name


def _template_priority(entry) -> int:
    """Rank matches: the explicit 「实验报告模板」 first, then any template,
    then a per-experiment material file."""
    name = entry.name or ""
    compact = name.replace(" ", "")
    if "实验报告模板" in compact:
        return 0
    if "报告模板" in compact:
        return 1
    if "模板" in name and "报告" in name:
        return 2
    if "模板" in name:
        return 3
    return 4


def _safe_filename(name: str, fallback: str = "material") -> str:
    """Turn a platform-provided name into a safe local file name."""
    return utils.safe_filename(name, fallback)


def _missing_sections(body: str, expected: list[str]) -> list[str]:
    """Sections whose heading exists but has no real body text under it.

    ``body`` is the flat text of the whole document (paragraphs and table
    cells, newline-joined). For each expected section the heading is located
    with the same matcher the filler uses, then every line up to the next
    heading is checked: at least one must be real text, not a placeholder.
    Inline text after the heading alias on the same line counts as body.
    A section whose heading is missing entirely also counts as missing — a
    template that never had the section cannot have been filled.
    """
    from .report_generator import (
        canonical_section,
        is_placeholder_text,
        _inline_after_heading,
    )

    lines = body.split("\n")
    headings = [
        (index, canonical_section(line.strip()))
        for index, line in enumerate(lines)
    ]
    headings = [(index, key) for index, key in headings if key]
    # A body sub-heading can reuse a section alias ("一、算法伪代码" resolves to
    # 实验实现). Treating a repeat as a new section boundary ended the first
    # occurrence's body early and reported the section as empty, so only the
    # first sighting of each section counts as its heading.
    seen: set[str] = set()
    first_headings: list[tuple[int, str]] = []
    for index, key in headings:
        if key in seen:
            continue
        seen.add(key)
        first_headings.append((index, key))
    headings = first_headings
    missing: list[str] = []
    for canonical in expected:
        start = next(
            (index for index, key in headings if key == canonical), None)
        if start is None:
            missing.append(canonical)
            continue
        inline = _inline_after_heading(lines[start].strip(), canonical)
        body_lines = [inline] if inline else []
        later = [index for index, _ in headings if index > start]
        end = min(later) if later else len(lines)
        body_lines.extend(
            line.strip() for line in lines[start + 1:end] if line.strip())
        if not body_lines or all(
                is_placeholder_text(line) for line in body_lines):
            missing.append(canonical)
    return missing


def _report_matches_experiment(body: str, detail: ExperimentDetail) -> Optional[bool]:
    """True/False when the report's topic can be judged, ``None`` if not.

    The comparison uses the experiment's *topic* (its name minus the 实验N
    prefix). A report that mentions no part of the topic is almost certainly
    a report for another experiment. When the topic is too short to judge
    (e.g. the page gave no name), the check is skipped rather than guessed —
    "unverifiable" must never be reported as "wrong".
    """
    topic = re.sub(r"^实验\s*[一二三四五六七八九十0-9]*\s*", "", detail.name or "").strip()
    topic = re.sub(r"[^一-鿿A-Za-z0-9]", "", topic)
    if len(topic) < 2:
        return None
    body_key = re.sub(r"\s+", "", body)
    # Any 2-character window of the topic appearing in the report is enough:
    # the topic may be phrased differently but the core words will recur.
    windows = {topic[i:i + 2] for i in range(len(topic) - 1)}
    return any(w and w in body_key for w in windows)
