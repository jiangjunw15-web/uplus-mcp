"""Experiment automation: read requirements, generate report, upload submit.

Routes captured against eduplus.net on 2026-10-28:
- Experiment list:  /course/course/{course_id}/ExperimentPage?userRole=
- Experiment detail: /course/course/{course_id}/ExperimentPage/expInfo/{exp_id}/stu_experiments/{stu_exp_id}/labsubmit

The detail page has four tabs: 实验提交物 / 实验手册 / 实验视频 / 实验附件.
Submission is a single file upload (usually .docx 实验报告).
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
from . import compliance, utils


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
    #: Download links found on the 实验手册 tab. The manual is sometimes a
    #: downloadable .docx/.pdf rather than inline text, and that file may carry
    #: the report template or the requirements we need.
    manual_links: list[Attachment] = field(default_factory=list)
    page_url: str = ""


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
            course_id=course_id, exp_id=exp_id, stu_exp_id=stu_exp_id
        )
        page = self._open_page(url)
        if "login" in page.url.lower():
            raise AuthExpiredError("U+ 登录态已过期，请重新登录。")
        page.wait_for_load_state("networkidle", timeout=8000)
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
        except Exception:
            pass

        # read 实验手册 tab — the manual may be inline text *and* carry
        # downloadable files (a .docx handbook, a template, reference code).
        # Both are captured: the text for requirement extraction, the links
        # for template / material download.
        try:
            self._click_tab(page, "实验手册")
            time.sleep(1.0)
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
        except Exception:
            pass

        # read 实验附件 tab
        try:
            self._click_tab(page, "实验附件")
            time.sleep(0.8)
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
        except Exception:
            pass

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
        page = self._open_page(
            f"/course/course/{course.id}/ExperimentPage?userRole="
        )
        if "login" in page.url.lower():
            raise AuthExpiredError("U+ 登录态已过期，请重新登录。")
        page.wait_for_load_state("networkidle", timeout=8000)
        time.sleep(1.0)

        rows = page.evaluate(r"""
            () => {
                const out = [];
                document.querySelectorAll('.exp-item').forEach(card => {
                    const link = card.querySelector('a[href*="expInfo"]');
                    const href = link ? link.getAttribute('href') : '';
                    const m = href.match(/expInfo\/([a-f0-9]+)\/stu_experiments\/([a-f0-9]+)/);
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
    # upload submission
    # ------------------------------------------------------------------ #
    def upload_report(self, course_id: str, exp_id: str, stu_exp_id: str,
                      file_path: Path) -> dict:
        """Upload a .docx report file to the experiment submission page."""
        safe_path = utils.resolve_io_path(
            file_path, self.config.io_roots, must_exist=True)
        page = self.open_detail(course_id, exp_id, stu_exp_id)

        # make sure we're on 实验提交物 tab
        self._click_tab(page, "实验提交物")
        time.sleep(0.5)

        # find file input
        file_input = page.locator('input[type="file"]').first
        if not file_input.count():
            raise UplusError("页面未找到文件上传输入框")

        file_input.set_input_files(str(safe_path))
        # The uploader converts the file asynchronously. The 「提交」 button is
        # visible the whole time but a click while the page still says
        # 「格式转换中...」 silently does nothing — that is what produced
        # "clicked 提交 但未生效". Wait for 「转换成功」 before clicking.
        if not self._wait_converted(page, timeout=60.0):
            raise UplusError(
                "文件上传后 60 秒仍未显示「转换成功」，请人工复核（文件可能过大或格式不支持）"
            )

        # The uploader converts the file first (aaaa.docx 转换成功) and only
        # then exposes the 「提交」 button. Clicking a div that merely contains
        # the text 提交 does nothing, so the click is restricted to real
        # buttons and the submission is polled until the badge flips.
        if not self._click_submit(page):
            raise UplusError("未找到可点击的「提交」按钮（文件可能仍在转换）")

        registered = self._wait_submitted(page, timeout=15.0)
        if not registered:
            raise UplusError(
                "已点击「提交」但页面未出现「已提交」状态，提交未生效，请人工复核"
            )
        return {
            "uploaded": True,
            "file": safe_path.name,
            "page_url": page.url,
        }

    # ------------------------------------------------------------------ #
    # submission primitives
    # ------------------------------------------------------------------ #
    def _click_submit(self, page: Page) -> bool:
        """Click the real 「提交」 button and accept a confirmation dialog.

        Returns ``False`` when no visible button carries that exact label —
        which happens while the upload is still converting.
        """
        clicked = page.evaluate(r"""
            () => {
                const els = Array.from(document.querySelectorAll('button'));
                const hit = els.find(e => {
                    const t = (e.innerText || '').trim();
                    return (t === '提交' || t === '确认提交') && e.offsetParent !== null;
                });
                if (!hit) return false;
                hit.click();
                return true;
            }
        """)
        if not clicked:
            return False
        time.sleep(1.5)
        # Some courses ask for confirmation before the submission registers.
        try:
            box = page.locator(".el-message-box, .el-dialog").first
            if box.count() and box.is_visible():
                ok_btn = box.locator(
                    "button:has-text('确定'), button:has-text('确认')").first
                if ok_btn.count():
                    ok_btn.click(timeout=3000)
                    time.sleep(1.0)
        except Exception:  # noqa: BLE001 - a missing dialog is normal
            pass
        return True

    def _wait_converted(self, page: Page, timeout: float = 60.0) -> bool:
        """Poll until the uploader reports 「转换成功」 for the new file.

        Right after ``set_input_files`` the page shows 「格式转换中...」 for a
        few seconds. A 「提交」 click during that window is accepted by the UI
        but never registers, so the conversion has to finish first.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                text = page.inner_text("body")
            except Exception:  # noqa: BLE001 - page may be re-rendering
                time.sleep(1.0)
                continue
            if "转换中" in text:
                time.sleep(1.0)
                continue
            if "转换成功" in text:
                return True
            time.sleep(1.0)
        return False

    def _wait_submitted(self, page: Page, timeout: float = 15.0) -> bool:
        """Poll the page until it shows the submitted badge and 撤回 button."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                text = page.inner_text("body")
            except Exception:  # noqa: BLE001 - page may be reloading
                time.sleep(1.0)
                continue
            if "已提交" in text and "撤回" in text:
                return True
            time.sleep(1.0)
        return False

    def submit_report(self, course_id: str, exp_id: str, stu_exp_id: str,
                      file_path: Path) -> dict:
        """Upload a report and **verify the submission actually registered**.

        The plain ``upload_report`` returns ``uploaded: True`` as soon as the
        submit button is clicked, exactly like the homework path before it
        learned to read the page back. The submission can silently fail (the
        uploader rejects the file, a confirm dialog appears, the request 500s),
        and reporting success then is worse than reporting failure.

        This method re-reads the page after submitting and only reports
        success when the page shows the file among the submitted files or the
        status badge switched to 已提交. Otherwise it raises ``UplusError``
        so the caller never reports a phantom submission.
        """
        result = self.upload_report(course_id, exp_id, stu_exp_id, file_path)
        page = self.open_detail(course_id, exp_id, stu_exp_id)
        time.sleep(1.0)
        text = page.inner_text("body")
        file_name = Path(file_path).name
        # Only the submitted badge plus a 撤回 button (or this file appearing
        # in the submitted list) proves the submission registered. The page
        # shows "实验报告：xxx.docx 转换成功" right after an upload, so
        # treating "the file name appears somewhere" as success — which this
        # method used to do — reports a phantom submission.
        listed = [
            f.strip()
            for f in re.findall(r"实验报告[：:]\s*([^\n]+?)(?=下载|撤回|$)", text)
            if f.strip()
        ]
        confirmed = "已提交" in text and (
            "撤回" in text or any(file_name == f for f in listed)
        )
        if not confirmed:
            raise UplusError(
                "提交后页面未显示已提交状态或已上传文件，"
                "提交可能未生效，请人工复核（未返回假成功）"
            )
        result["confirmed"] = True
        result["page_url"] = page.url
        return result

    # ------------------------------------------------------------------ #
    # requirements collection
    # ------------------------------------------------------------------ #
    def collect_requirements(self, detail: ExperimentDetail) -> str:
        """Merge every requirement source into one text block.

        Sources, in order:
        1. ``提交物`` (material) — what the report must contain.
        2. ``实验手册`` (manual) — the main requirement document.
        3. ``实验附件`` attachment names — files that carry additional
           requirements (the user said requirements usually live in the
           manual and the attachments).

        Returns the concatenated text, with each source on its own block.
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
                             max_depth: int = 6) -> Optional[dict]:
        """Locate the best report template across **all** experiment sources.

        The user's requirement: check everything — 实验附件, 实验手册
        (downloadable files on the manual tab), and the course 资料 section
        (recursing into folders) — and prefer whichever source actually
        yields a downloadable template file.

        Search order (first downloadable match wins):

        1. **实验附件** — attachments on the 实验附件 tab whose name matches
           「实验报告模板 / 报告模板 / 实验N」 and carry a download href.
        2. **实验手册** — downloadable files on the 实验手册 tab (the manual
           is sometimes a .docx that *is* the template, or carries one).
        3. **资料** — the course 资料 section, walked recursively into
           folders, matching the same names.

        Within each source, entries are ranked by :func:`_template_priority`
        (an explicit「实验报告模板」beats a bare「模板」beats a per-experiment
        file). A source that has a matching name but no download href is
        noted but skipped — the caller wants a file it can actually open.

        Returns ``{"name", "path", "source"}`` with ``path`` pointing at the
        downloaded file, or ``None`` when no downloadable template is found
        in any source. A missing template is not an error: the caller falls
        back to the built-in report generator.
        """
        exp_label = self._experiment_label(detail.name)
        dest_dir = self.config.report_folder / "experiment_materials"

        # ---- 1) 实验附件 ----
        hit = self._best_downloadable_attachment(
            detail.attachments, exp_label, dest_dir)
        if hit:
            hit["source"] = "实验附件"
            return hit

        # ---- 2) 实验手册 (downloadable files on the manual tab) ----
        hit = self._best_downloadable_attachment(
            detail.manual_links, exp_label, dest_dir)
        if hit:
            hit["source"] = "实验手册"
            return hit

        # ---- 3) 资料 (recursive folder walk) ----
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
        # Prefer a downloadable file; rank by template priority.
        downloadable = [e for e in entries if e.href]
        downloadable.sort(key=_template_priority)
        if not downloadable:
            # Report the best match's name even though it has no href, so the
            # caller can tell the user where the template is but that it must
            # be downloaded manually.
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
            # Match: a template name, or this experiment's own label.
            is_template = (
                ("模板" in name and ("报告" in name or "实验" in name))
                or (label and label in compact)
            )
            if not is_template:
                continue
            priority = _template_priority(
                _NameOnly(name))  # reuse the ranking function
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

    # ------------------------------------------------------------------ #
    # report verification (the inspection gate)
    # ------------------------------------------------------------------ #
    def verify_report(self, path: Path, detail: ExperimentDetail,
                      expected_sections: list[str] | None = None,
                      strict_compliance: bool = True) -> dict:
        """Read-only inspection of a report before it may be submitted.

        Mirrors the homework audit: it refuses an empty or wrong submission
        rather than uploading a blank document.

        Checks:
        * the file exists, is a non-trivial size, and has readable body text
          (a .docx that opens but is blank is still "empty");
        * no placeholder text remains（"（此处填写…）"、"待填写"、"TODO"…）;
        * when ``expected_sections`` is given, each named section has body
          text (a heading with nothing under it is empty);
        * the report shares at least one topic word with the experiment's
          name/requirements — a report written for a *different* experiment
          is the document analogue of answering the wrong question;
        * the report passes the compliance checklist (teacher template cover,
          experiment-name field, screenshots when the manual demands them,
          manual requirement coverage, AI-sounding meta talk, repetition,
          layout). ``strict_compliance=False`` downgrades those findings to
          warnings instead of blocking.

        Returns ``{"ok": bool, "reason": str, "checks": {...}}``. ``ok`` is
        ``False`` with a filled ``reason`` when the caller must refuse to
        submit.
        """
        from .report_generator import extract_body_text, is_placeholder_text

        safe = utils.resolve_io_path(path, self.config.io_roots, must_exist=True)
        checks: dict = {}

        # 1) non-trivial size first: a stub file is "empty" whether or not it
        #    happens to be a parseable document.
        checks["bytes"] = safe.stat().st_size
        if safe.stat().st_size < 1024:
            return {"ok": False, "reason": "报告文件过小（<1KB），疑似空文件", "checks": checks}

        # 2) readable, non-trivial body
        try:
            body = extract_body_text(safe)
        except Exception as exc:  # noqa: BLE001 - corrupt/unsupported file
            return {"ok": False, "reason": f"无法读取报告内容：{exc}", "checks": checks}
        checks["chars"] = len(body)
        if len(body.strip()) < 30:
            return {"ok": False, "reason": "报告正文有效内容不足，疑似空报告", "checks": checks}

        # 3) leftover placeholders
        leftovers = [
            line.strip() for line in body.split("\n")
            if line.strip() and is_placeholder_text(line.strip())
        ]
        checks["placeholders"] = leftovers[:10]
        if leftovers:
            shown = "、".join(leftovers[:3])
            return {
                "ok": False,
                "reason": f"报告仍残留未填写占位内容（{shown}…），拒绝提交",
                "checks": checks,
            }

        # 4) expected sections have body
        if expected_sections:
            missing = _missing_sections(body, expected_sections)
            checks["missing_sections"] = missing
            if missing:
                return {
                    "ok": False,
                    "reason": "以下章节没有正文内容：" + "、".join(missing),
                    "checks": checks,
                }

        # 5) report topic matches the experiment
        matches = _report_matches_experiment(body, detail)
        checks["topic_match"] = matches
        if matches is False:
            return {
                "ok": False,
                "reason": (
                    f"报告内容与实验「{detail.name}」不匹配"
                    "（未出现实验主题关键词），疑似交错了实验，拒绝提交"
                ),
                "checks": checks,
            }

        # 6) compliance checklist — 「能交」和「交了会被挑毛病」是两件事
        try:
            requirements = self.collect_requirements(detail)
        except Exception:  # noqa: BLE001 - the checklist is best-effort here
            requirements = ""
        audit = compliance.check_report(
            safe, manual_text=detail.manual_text or "", requirements=requirements)
        checks["compliance"] = audit.as_dict()
        if strict_compliance and not audit.ok:
            first = audit.blocking[0]
            more = len(audit.blocking) - 1
            reason = f"合规核查未通过（{first.title}）：{first.detail}"
            if more > 0:
                reason += f"；另有 {more} 项硬伤"
            return {"ok": False, "reason": reason, "checks": checks}

        return {"ok": True, "reason": "", "checks": checks}

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
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
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "").strip().strip(".")
    return cleaned[:120] or fallback


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
