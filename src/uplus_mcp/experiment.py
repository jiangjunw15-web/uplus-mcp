"""Experiment automation: read requirements, generate report, upload submit.

Routes captured against eduplus.net on 2026-10-28:
- Experiment list:  /course/course/{course_id}/ExperimentPage?userRole=
- Experiment detail: /course/course/{course_id}/ExperimentPage/expInfo/{exp_id}/stu_experiments/{stu_exp_id}/labsubmit

The detail page has four tabs: 实验提交物 / 实验手册 / 实验视频 / 实验附件.
Submission is a single file upload (usually .docx 实验报告).
"""

from __future__ import annotations

import datetime
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page

from .browser import BrowserSession, UplusError, AuthExpiredError
from .config import Config
from .models import Course


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
    attachments: list[str] = field(default_factory=list)
    submitted_files: list[str] = field(default_factory=list)
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
        page = self.session.open(url)
        if "login" in page.url.lower():
            raise AuthExpiredError("U+ 登录态已过期，请重新登录。")
        page.wait_for_load_state("networkidle", timeout=8000)
        time.sleep(0.5)
        return page

    def read_detail(self, course_id: str, exp_id: str,
                    stu_exp_id: str) -> ExperimentDetail:
        page = self.open_detail(course_id, exp_id, stu_exp_id)
        text = page.inner_text("body")

        # basic fields
        name = ""
        m = text.split("\n")
        for i, line in enumerate(m):
            if line.strip().startswith("实验") and "进程" in line or "实验一" in line or "实验二" in line:
                name = line.strip()
                break

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
        import re
        m_env = re.search(r"环境[：:]\s*([^\n]+)", text)
        if m_env:
            detail.environment = m_env.group(1).strip()
        m_mat = re.search(r"提交物[：:]\s*([^\n]+)", text)
        if m_mat:
            detail.material = m_mat.group(1).strip()
        m_dl = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", text)
        if m_dl:
            detail.deadline = m_dl.group(1)
            try:
                dt = datetime.datetime.strptime(detail.deadline, "%Y-%m-%d %H:%M")
                detail.deadline_ms = int(dt.timestamp() * 1000)
            except ValueError:
                pass

        # submitted files
        m_files = re.findall(r"实验报告[：:]\s*([^\n]+?)(?=下载|撤回|$)", text)
        detail.submitted_files = [f.strip() for f in m_files if f.strip()]

        # read 实验手册 tab
        try:
            self._click_tab(page, "实验手册")
            time.sleep(1.0)
            detail.manual_text = page.inner_text("body")
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
                        if (t && t.length < 200) out.push(t);
                    });
                    return out;
                }
            """)
            detail.attachments = atts or []
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
        page = self.session.open(
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
        from . import utils
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
        time.sleep(2.0)

        # find and click submit button
        result = page.evaluate(r"""
            () => {
                const all = document.querySelectorAll('button, a, div');
                for (const el of all) {
                    const t = (el.innerText || '').trim();
                    if ((t === '提交' || t === '确认提交' || t === '上传提交') && el.offsetParent !== null) {
                        el.click();
                        return true;
                    }
                }
                return false;
            }
        """)

        if not result:
            raise UplusError("未找到提交按钮")

        time.sleep(3.0)
        return {
            "uploaded": True,
            "file": safe_path.name,
            "page_url": page.url,
        }
