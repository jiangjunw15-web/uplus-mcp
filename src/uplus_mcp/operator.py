"""Page-level UI operations on the U+ answer pages.

The REST layer (``platform.py`` reads data; this layer drives the real answer
page to fill objective questions, verify answers, submit homework and upload
attachments. Page truth overrides API status: the button label
(``提交作业`` vs ``撤回作业``) is the authoritative submission state.
"""

from __future__ import annotations

import datetime
import re
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PWTimeout, Locator

from .browser import BrowserSession, UplusError, AuthExpiredError
from .config import Config
from .models import (
    Course,
    Experiment,
    HomeworkStatus,
    HomeworkTask,
    Question,
    QuestionType,
)
from .platform import PlatformClient
from . import solver, utils


class HomeworkOperator:
    ANSWER_ROUTE = "/course/workAnswer/{course_id}/{task_id}/true?isPiYue=noPiYue"
    EXPERIMENT_ROUTE = "/course/course/{course_id}/ExperimentPage?userRole="

    def __init__(self, session: BrowserSession, client: PlatformClient, config: Config) -> None:
        self.session = session
        self.client = client
        self.config = config

    # ------------------------------------------------------------------ #
    # navigation
    # ------------------------------------------------------------------ #
    def open_homework(self, task: HomeworkTask) -> Page:
        url = self.session.url_for(
            self.ANSWER_ROUTE.format(course_id=task.course_id, task_id=task.task_id)
        )
        page = self.session.open(url)
        if "login" in page.url.lower():
            raise AuthExpiredError("U+ 登录态已过期，请重新登录。")
        return page

    # ------------------------------------------------------------------ #
    # page truth: button label + progress
    # ------------------------------------------------------------------ #
    def read_page_state(self, page: Page) -> tuple[HomeworkStatus, Optional[str]]:
        text = page.inner_text("body")
        progress = None
        m = re.search(r"答题卡\s*\((\d+\s*/\s*\d+)\)", text)
        if m:
            progress = m.group(1).replace(" ", "")
        if "撤回作业" in text:
            # Graded vs pending is refined by the client score detail; treated
            # as submitted pending until a score appears in the homework DTO.
            return HomeworkStatus.SUBMITTED_PENDING, progress
        if "提交作业" in text:
            return HomeworkStatus.NOT_SUBMITTED, progress
        return HomeworkStatus.NOT_SUBMITTED, progress

    # ------------------------------------------------------------------ #
    # question filling
    # ------------------------------------------------------------------ #
    def _option_locator(self, page: Page, code: str) -> Locator:
        """Locate the clickable option row for a letter like ``A``."""
        radio = page.locator(f"label.el-radio:has-text(\"{code}、\")")
        if radio.count():
            return radio.first
        checkbox = page.locator(f"label.el-checkbox:has-text(\"{code}、\")")
        if checkbox.count():
            return checkbox.first
        # Fallback: text node "A、" and its clickable ancestor row.
        return page.locator(f"text={code}、").first

    def _click_option(self, page: Page, code: str) -> None:
        locator = self._option_locator(page, code)
        locator.scroll_into_view_if_needed(timeout=4000)
        locator.click(timeout=6000)
        time.sleep(0.25)

    def _answer_judge(self, page: Page, value: bool) -> None:
        target = "正确" if value else "错误"
        btn = page.get_by_text(target, exact=True)
        if btn.count() == 0:
            btn = page.locator(f"label:has-text(\"{target}\")")
        btn.first.click(timeout=6000)
        time.sleep(0.25)

    def _answer_blank(self, page: Page, text: str) -> None:
        box = page.locator("textarea").first
        if box.count() == 0:
            box = page.locator(
                "input[type=text], input[type=search], input[type=number]"
            ).first
        box.fill(text, timeout=6000)

    def navigate_to_question(self, page: Page, order: int) -> None:
        if order <= 1:
            return
        card = page.locator(
            f"xpath=//*[normalize-space(text())='{order}']"
        ).first
        try:
            card.click(timeout=4000)
            time.sleep(0.3)
        except PWTimeout:
            pass

    def fill_question(self, page: Page, question: Question, answer: str) -> str:
        """Fill one question on the current page; return the normalized answer."""
        normalized = solver.normalize_answer(question, answer)
        qtype = question.type
        if qtype is QuestionType.SINGLE:
            self._click_option(page, normalized)
            return normalized
        if qtype is QuestionType.MULTIPLE:
            current = set(utils.normalize_letters(question.my_answer or ""))
            target = set(normalized)
            # Deselect removed choices first, then select target choices.
            for old in sorted(current - target):
                self._click_option(page, old)
            for new in sorted(target - current):
                self._click_option(page, new)
            return normalized
        if qtype is QuestionType.JUDGE:
            self._answer_judge(page, normalized == "true")
            return normalized
        if qtype is QuestionType.FILL_BLANK:
            self._answer_blank(page, normalized)
            return normalized
        raise UplusError(f"题型 {qtype.label} 不支持自动作答")

    def read_current_my_answer(self, page: Page) -> Optional[str]:
        try:
            text = page.locator("text=我的答案").first.locator("xpath=following-sibling::*[1]").inner_text()
            return text.strip() or None
        except PWTimeout:
            return None

    # ------------------------------------------------------------------ #
    # full homework flow
    # ------------------------------------------------------------------ #
    def answer_homework(
        self,
        task: HomeworkTask,
        answers: dict[str, str],
        *,
        submit: bool = True,
        force_submit: bool = False,
        course_name: str = "",
    ) -> dict:
        """Open a homework, fill provided objective answers, verify and submit.

        ``answers`` maps ``question_id`` (or question order number as string) to
        the normalized answer. Unsupported questions are collected in ``skipped``;
        already-submitted homework is left untouched. Submission only happens when
        every objective question was answered without errors, unless
        ``force_submit=True``.
        """
        outcome: dict = {
            "task_id": task.task_id,
            "name": task.name,
            "course_name": course_name,
            "submitted": False,
            "answered": 0,
            "skipped": 0,
            "objective_total": 0,
            "objective_answered": 0,
            "can_submit": False,
            "skipped_questions": [],
            "errors": [],
            "note": "",
        }
        page = self.open_homework(task)
        status, progress = self.read_page_state(page)
        task.page_status = status
        task.page_progress = progress

        if status is HomeworkStatus.SUBMITTED_PENDING or status is HomeworkStatus.SUBMITTED_GRADED:
            outcome["note"] = "作业已提交，未重复作答"
            return outcome

        questions = self.client.list_questions(task.homework_id)
        outcome["total"] = len(questions)

        for meta in questions:
            detail = self.client.question_detail(meta.question_id)
            self.navigate_to_question(page, detail.order)
            if detail.answered and detail.my_answer:
                outcome["answered"] += 1
                if detail.auto_answerable:
                    outcome["objective_answered"] += 1
                continue
            if not detail.auto_answerable:
                outcome["skipped"] += 1
                outcome["skipped_questions"].append(
                    {
                        "question_id": detail.question_id,
                        "order": detail.order,
                        "type": detail.type.label,
                        "stem": detail.stem[:120],
                        "reason": "主观题/编程题/附件题，需本人完成",
                    }
                )
                continue
            outcome["objective_total"] += 1
            raw_answer = answers.get(detail.question_id) or answers.get(str(detail.order))
            if raw_answer is None:
                outcome["skipped"] += 1
                outcome["skipped_questions"].append(
                    {
                        "question_id": detail.question_id,
                        "order": detail.order,
                        "type": detail.type.label,
                        "stem": detail.stem[:120],
                        "reason": "缺少模型答案，待人工/LLM 补充",
                    }
                )
                continue
            try:
                normalized = self.fill_question(page, detail, raw_answer)
                detail.my_answer = normalized
                detail.answered = True
                outcome["answered"] += 1
                outcome["objective_answered"] += 1
            except (UplusError, PWTimeout, ValueError) as exc:
                outcome["errors"].append(f"第{detail.order}题作答失败: {exc}")

        outcome["can_submit"] = (
            not outcome["errors"]
            and outcome["skipped"] == 0
            and outcome["objective_answered"] == outcome["objective_total"]
        )
        if submit and (outcome["can_submit"] or force_submit):
            self.submit_homework(page, task, outcome)
        else:
            reasons = []
            if outcome["skipped"]:
                reasons.append(f"{outcome['skipped']} 题未完成")
            if outcome["errors"]:
                reasons.append(f"{len(outcome['errors'])} 题作答出错")
            outcome["note"] = "存在未完成题目，已暂缓提交：" + "，".join(reasons) if reasons else "已填写，等待提交"
        return outcome

    def submit_homework(self, page: Page, task: HomeworkTask, outcome: dict) -> None:
        submit_btn = page.get_by_role("button", name="提交作业")
        if submit_btn.count() == 0:
            outcome["note"] = "未找到提交按钮"
            return
        submit_btn.first.click()
        time.sleep(0.8)
        # Confirmation dialog: "作业已经全部做完，是否提交?"
        confirm = page.locator(".el-message-box__btns .el-button--primary")
        try:
            if confirm.count():
                confirm.first.click(timeout=4000)
            else:
                page.get_by_role("button", name="确定").first.click(timeout=4000)
        except PWTimeout:
            outcome["errors"].append("提交确认弹窗未出现")
            return
        # Verify the button switched to 撤回作业.
        for _ in range(10):
            time.sleep(0.6)
            state, progress = self.read_page_state(page)
            if state in (HomeworkStatus.SUBMITTED_PENDING, HomeworkStatus.SUBMITTED_GRADED):
                task.page_status = state
                task.page_progress = progress
                outcome["submitted"] = True
                outcome["note"] = "提交成功，待老师批改"
                return
        outcome["errors"].append("提交后页面状态未变为「撤回作业」，请人工复核")

    # ------------------------------------------------------------------ #
    # attachment upload
    # ------------------------------------------------------------------ #
    def upload_attachment(self, task: HomeworkTask, file_path: str) -> dict:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"附件不存在: {file_path}")
        page = self.open_homework(task)
        status, _ = self.read_page_state(page)
        if status in (HomeworkStatus.SUBMITTED_PENDING, HomeworkStatus.SUBMITTED_GRADED):
            return {"task_id": task.task_id, "uploaded": False, "note": "作业已提交，如需修改请先撤回"}
        file_input = page.locator("input[type=file]").last
        file_input.set_input_files(str(path))
        time.sleep(1.5)
        return {
            "task_id": task.task_id,
            "uploaded": True,
            "file": str(path),
            "note": "附件已上传，仍需在作答页点击「提交作业」完成提交",
        }

    # ------------------------------------------------------------------ #
    # experiments
    # ------------------------------------------------------------------ #
    def read_experiments(self, course: Course) -> list[Experiment]:
        page = self.session.open(self.EXPERIMENT_ROUTE.format(course_id=course.id))
        if "login" in page.url.lower():
            raise AuthExpiredError("U+ 登录态已过期，请重新登录。")
        time.sleep(1.0)
        js = r"""
        () => {
          const cards = [...document.querySelectorAll('div')].filter(d =>
            /^实验[一二三四五六七八九十0-9]/.test((d.innerText||'').trim()) &&
            d.innerText.includes('截止'));
          const out = [];
          const seen = new Set();
          cards.forEach(c => {
            const title = (c.innerText.split('\n')[0]||'').trim();
            if (seen.has(title)) return;
            seen.add(title);
            let parent = c, badge = null, material = '', deadline = '';
            for (let i=0;i<4 && parent;i++){
              const b = [...parent.querySelectorAll('*')].find(e => e.children.length===0 &&
                ['已提交','未提交','待提交','未开始'].includes((e.innerText||'').trim()));
              if (b) { badge = b.innerText.trim(); break; }
              parent = parent.parentElement;
            }
            const text = c.innerText;
            const m1 = text.match(/提交物[：:]\s*([^\n]+)/); if (m1) material = m1[1].trim();
            const m2 = text.match(/(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})/); if (m2) deadline = m2[1];
            out.push({name: title, badge, material, deadline, raw: text.slice(0,200)});
          });
          return out;
        }
        """
        raw_list = page.evaluate(js) or []
        experiments: list[Experiment] = []
        for raw in raw_list:
            deadline_ms = None
            if raw.get("deadline"):
                try:
                    dt = datetime.datetime.strptime(raw["deadline"], "%Y-%m-%d %H:%M")
                    deadline_ms = int(dt.timestamp() * 1000)
                except ValueError:
                    deadline_ms = None
            experiments.append(
                Experiment(
                    name=raw["name"],
                    submitted=raw["badge"] == "已提交",
                    deadline_ms=deadline_ms,
                    material=raw.get("material", ""),
                    raw_text=raw.get("raw", ""),
                )
            )
        return experiments
