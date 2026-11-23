"""Plain orchestration layer shared by the MCP server and the CLI patrol script.

Keeping business flow here means both entrypoints behave identically; the MCP
tools in ``server.py`` are thin wrappers around an ``PatrolEngine`` instance.
"""

from __future__ import annotations

import re

from . import report as report_mod
from .browser import BrowserSession, AuthExpiredError, UplusError
from .config import Config
from .experiment import ExperimentOperator
from .models import Course, Experiment, HomeworkStatus, HomeworkTask, Question, QuestionType
from .operator import HomeworkOperator, classify_experiment_flag
from .platform import PlatformClient
from . import journal, login_flow, solver, utils


class PatrolEngine:
    def __init__(self, config: Config, session: BrowserSession,
                 client: PlatformClient, operator: HomeworkOperator) -> None:
        self.config = config
        self.session = session
        self.client = client
        self.operator = operator
        self._task_index: dict[str, HomeworkTask] = {}
        # Course-name resolution cache. In live mode (no configured courses),
        # ``course_name`` used to fall through to ``client.get_course`` →
        # ``list_my_courses()`` — a full feed request per call. A patrol /
        # finish_then_review can call it several times per homework, so the feed
        # was re-fetched many times for one course. The cache is filled once per
        # course and never grows beyond the account's courses.
        self._course_names: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def course_name(self, course_id: str) -> str:
        hit = self._course_names.get(course_id)
        if hit:
            return hit
        for c in self.config.courses:
            if c.id == course_id:
                self._course_names[course_id] = c.name
                return c.name
        course = self.client.get_course(course_id)
        name = course.name if course else course_id
        self._course_names[course_id] = name
        return name

    def _iter_courses(self) -> list:
        """Configured courses, or the live account list when none are configured.

        The order is deterministic: configured courses keep the order the user
        wrote in ``courses.json``; the live feed is sorted by name + id because
        the platform does not guarantee a stable order between calls. By default
        expired courses are skipped; ``include_expired_courses=True`` keeps the
        archived view.
        """
        if self.config.courses:
            courses = list(self.config.courses)
        else:
            courses = sorted(self.client.list_my_courses(), key=_course_sort_key)
        if self.config.include_expired_courses:
            return courses
        return [c for c in courses if not utils.is_expired(c.end_ms)]

    def _course_by_id(self, course_id: str) -> Course | None:
        """Find a configured course without an API call, then the live feed."""
        for course in self.config.courses:
            if course.id == course_id:
                return course
        return next(
            (c for c in self.client.list_my_courses() if c.id == course_id),
            None,
        )

    def _register(self, task: HomeworkTask) -> None:
        self._task_index[task.task_id] = task

    def _read_task_page_state(self, task: HomeworkTask) -> str:
        """Read the real submit-button state for one task; return an error text.

        Empty string means the page was read (or not needed). This is the one
        page navigation per open homework, kept inside the current course.
        """
        try:
            page = self.operator.open_homework(task)
            status, progress = self.operator.read_page_state(page)
            if status is not None:
                task.page_status = status
            if progress is not None:
                task.page_progress = progress
        except AuthExpiredError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep the listing alive
            return utils.redact_secrets(str(exc))
        return ""

    @staticmethod
    def _homework_row(course: Course, task: HomeworkTask) -> dict:
        """One JSON-ready homework row, shared by list and course scan."""
        return {
            "course_id": course.id,
            "course_name": course.name,
            "task_id": task.task_id,
            "homework_id": task.homework_id,
            "name": task.name,
            "status": task.status.value,
            "status_label": report_mod.STATUS_LABEL[task.status],
            "deadline_ms": task.end_ms,
            "deadline": utils.deadline_label(task.end_ms),
            "question_count": task.question_count,
            "score": task.score,
            "progress": task.page_progress,
            "page_error": task.raw.get("page_error", ""),
        }

    def resolve_task(self, task_id: str, course_id: str) -> HomeworkTask:
        cached = self._task_index.get(task_id)
        if cached and cached.course_id == course_id:
            return cached
        for task in self.client.list_homeworks(course_id):
            self._register(task)
            if task.task_id == task_id:
                return task
        raise UplusError(f"未找到作业 {task_id}（课程 {course_id}）")

    # ------------------------------------------------------------------ #
    # read-only tools
    # ------------------------------------------------------------------ #
    def check_login(self) -> dict:
        try:
            logged = self.session.is_logged_in()
        except AuthExpiredError:
            logged = False
        return {"logged_in": logged, "base_url": self.config.base_url,
                "message": "登录有效" if logged else
                "登录态已过期：请在插件启动的浏览器窗口中打开 eduplus.net 重新登录，然后重试本工具。"}

    def login(self, owner: str = "", method: str = "", username: str = "",
              password: str = "", school: str = "",
              wait_timeout_ms: int = 120_000) -> dict:
        """Sign in through the three dialogs the user answers, in order.

        Nothing is defaulted: an empty ``owner`` returns dialog 1, an empty
        ``method`` returns dialog 2, and an auto-filled method whose fields are
        still empty returns dialog 3. Only when all three answers are in does
        the tool touch the sign-in form.

        ``method="sms"`` never fills anything: it parks the browser on the 短信
        登录 tab and hands the phone number and verification code to the human.
        ``method="student_id"`` requires ``school`` besides account and password;
        ``method="password"`` needs only the phone/account and password.
        """
        owner_key = login_flow.normalize_owner(owner)
        if not owner_key:
            return login_flow.owner_prompt()
        method_key = login_flow.normalize_method(method)
        if not method_key:
            return login_flow.method_prompt(owner_key)

        if method_key == "sms":
            result = self.session.open_sms_login()
            if result.get("logged_in"):
                # Nothing to hand over: the browser already holds a session.
                return {
                    "logged_in": True,
                    "stage": "done",
                    "owner": owner_key,
                    "method": "sms",
                    "note": result.get("note", "已是登录态，无需重复登录"),
                    "url": result.get("url", ""),
                }
            prompt = login_flow.sms_manual_prompt(result.get("url", ""), owner=owner_key)
            for key in ("tab_selected", "reason", "body_hint"):
                if key in result:
                    prompt[key] = result[key]
            return prompt

        required = (("school", "username", "password") if method_key == "student_id"
                    else ("username", "password"))
        values = {
            "school": (school or "").strip(),
            "username": (username or "").strip(),
            "password": password or "",
        }
        missing = [name for name in required if not values[name]]
        if missing:
            prompt = login_flow.credentials_prompt(method_key, missing)
            prompt["owner"] = owner_key
            return prompt

        # Auto fill never waits the full manual timeout: a failed submit should
        # fall back to a human quickly.
        wait_ms = min(int(wait_timeout_ms), 30_000)
        if method_key == "student_id":
            result = self.session.login_by_student_id(
                values["username"], values["password"], values["school"],
                wait_ms=wait_ms,
            )
        else:
            result = self.session.login_by_password(
                values["username"], values["password"], wait_ms=wait_ms,
            )
        # Strip any accidental credential echo before returning to the model.
        for key in ("username", "password", "school"):
            result.pop(key, None)
        result.update({
            "owner": owner_key,
            "method": method_key,
            "stage": "done" if result.get("logged_in") else "login_failed",
        })
        return result

    def list_courses(self) -> list[dict]:
        return [
            {
                "course_id": c.id,
                "name": c.name,
                "teacher": c.teacher,
                "start": utils.format_ts(c.start_ms),
                "end": utils.format_ts(c.end_ms),
            }
            for c in sorted(self.client.list_my_courses(), key=_course_sort_key)
        ]

    def list_homeworks(self, course_id: str = "", refresh_page_state: bool = False) -> list[dict]:
        targets = [c for c in self._iter_courses() if not course_id or c.id == course_id]
        if course_id and not targets:
            # A named course that does not exist (or is filtered out as expired)
            # must fail loudly, not silently widen the query to every course.
            raise UplusError(
                f"未找到课程 {course_id}（不存在，或已过期被默认过滤）。"
                "请确认课程 ID；如需包含已结课课程，请在配置中设置 include_expired_courses=true。"
            )
        # Do not widen to the live account list here: ``_iter_courses`` already
        # falls back to it only when no course is configured. Widening when
        # every configured course is expired used to silently list unrelated
        # live courses the user never selected for patrol.
        #
        # Course-major and sorted: finish one course's tasks before the next,
        # and order tasks by due date + title so repeated runs read the same.
        rows: list[dict] = []
        for course in targets:
            tasks = sorted(
                self.client.list_homeworks(course.id), key=_homework_sort_key)
            for task in tasks:
                self._register(task)
                if (refresh_page_state
                        and task.status is HomeworkStatus.NOT_SUBMITTED
                        and not task.is_expired()):
                    error = self._read_task_page_state(task)
                    if error:
                        task.raw["page_error"] = error
                rows.append(self._homework_row(course, task))
        return rows

    def list_experiments(self, course_id: str, verify: bool = True) -> list[dict]:
        """List the course's experiments in 实验一/二/… order.

        The list page's badge is not a reliable judge: the live test read
        ``submitted: false`` for every experiment, including one the platform
        itself displayed as 已提交 on its detail page. With ``verify=True``
        each row is re-checked against the detail page (已提交 + 撤回); the
        badge is only kept as a fallback, marked in ``submitted_source``.
        ``verify=False`` reads the list page only — one page load for the whole
        course instead of one per experiment.
        """
        course = self._course_by_id(course_id)
        if course is None:
            raise UplusError(f"未找到课程 {course_id}，请先确认课程 ID")
        experiments = sorted(
            self.operator.read_experiments(course), key=_experiment_sort_key)
        exp_op = self._experiment_operator() if verify else None
        return [
            self._experiment_row(course_id, exp, exp_op, verify)
            for exp in experiments
        ]

    @staticmethod
    def _experiment_row(course_id: str, exp: Experiment,
                        exp_op: ExperimentOperator | None, verify: bool) -> dict:
        """One JSON-ready experiment row, shared by list and course scan."""
        row: dict = {
            "name": exp.name,
            "submitted": exp.submitted,
            "submitted_source": "list_badge",
            "deadline_ms": exp.deadline_ms,
            "deadline": utils.deadline_label(exp.deadline_ms),
            "material": exp.material,
            # Both ids are required by every follow-up experiment tool;
            # without them the caller cannot build the detail route.
            "exp_id": exp.exp_id,
            "stu_exp_id": exp.stu_exp_id,
        }
        if verify and exp_op is not None and exp.exp_id and exp.stu_exp_id:
            try:
                detail = exp_op.read_detail(course_id, exp.exp_id,
                                            exp.stu_exp_id)
                row["status"] = detail.status
                verified = classify_experiment_flag(detail.status, "")
                if verified is None:
                    # The detail page opened but its submission state could
                    # not be read. Keep the list badge rather than
                    # overwriting it with None; say why in the source.
                    row["submitted_source"] = (
                        "list_badge（详情页未给出可识别的提交状态）")
                else:
                    row["submitted"] = verified
                    row["submitted_source"] = "detail_page"
                row["submitted_files"] = list(detail.submitted_files)
            except Exception as exc:  # noqa: BLE001 - keep the list usable
                row["submitted_source"] = f"list_badge（复核失败：{utils.redact_secrets(str(exc))}）"
        return row

    def scan_course(self, course_id: str, *, refresh_page_state: bool = True,
                    verify_experiments: bool = False) -> dict:
        """Read one course end-to-end: all homeworks first, then experiments.

        This is the efficient course-by-course retrieval path. For the named
        course it calls the homework feed once, opens each *unsubmitted*
        homework's answer page once (only when ``refresh_page_state``), then
        opens the experiment list page once. No other course is touched, so the
        browser never jumps between courses; call it for the next course only
        after this one has been read.

        ``verify_experiments=True`` additionally opens every experiment detail
        page to confirm its submitted state; the default ``False`` keeps the
        list badge and costs one page load for the whole course.
        """
        course = self._course_by_id(course_id)
        if course is None:
            raise UplusError(f"未找到课程 {course_id}，请先确认课程 ID")
        tasks = sorted(
            self.client.list_homeworks(course.id), key=_homework_sort_key)
        homeworks: list[dict] = []
        for task in tasks:
            self._register(task)
            if (refresh_page_state
                    and task.status is HomeworkStatus.NOT_SUBMITTED
                    and not task.is_expired()):
                error = self._read_task_page_state(task)
                if error:
                    task.raw["page_error"] = error
            homeworks.append(self._homework_row(course, task))

        experiments = sorted(
            self.operator.read_experiments(course), key=_experiment_sort_key)
        exp_op = self._experiment_operator() if verify_experiments else None
        experiment_rows = [
            self._experiment_row(course.id, exp, exp_op, verify_experiments)
            for exp in experiments
        ]
        submitted_homeworks = sum(
            1 for task in tasks
            if task.status in (HomeworkStatus.SUBMITTED_PENDING,
                               HomeworkStatus.SUBMITTED_GRADED)
        )
        return {
            "course_id": course.id,
            "course_name": course.name,
            "teacher": course.teacher,
            "order": ("homeworks first, then experiments; finish this course "
                      "before reading the next one"),
            "homeworks": homeworks,
            "experiments": experiment_rows,
            "summary": {
                "homework_total": len(homeworks),
                "homework_submitted": submitted_homeworks,
                "homework_outstanding": len(homeworks) - submitted_homeworks,
                "experiments_total": len(experiment_rows),
                "experiments_submitted": sum(
                    1 for row in experiment_rows
                    if row.get("submitted") is True),
                "experiments_outstanding": sum(
                    1 for row in experiment_rows
                    if row.get("submitted") is False),
                "experiments_unknown": sum(
                    1 for row in experiment_rows
                    if row.get("submitted") is None),
            },
        }

    # ------------------------------------------------------------------ #
    # experiments: read-only retrieval
    # ------------------------------------------------------------------ #
    def _experiment_operator(self) -> ExperimentOperator:
        return ExperimentOperator(self.config, self.session)

    def download_experiment_materials(self, course_id: str, exp_id: str,
                                      stu_exp_id: str) -> dict:
        """Download every experiment attachment plus **all** matching 资料 files.

        Read-only against the platform (nothing is uploaded or submitted).
        The 资料 walk descends into folders and every entry matching
        「实验报告模板 / 报告模板 / 实验N」 is downloaded, not just the top
        match — the user's flow downloads all related content. Files land
        under ``reports/experiment_materials/`` so the caller can read the
        requirements locally before authoring the report.
        """
        op = self._experiment_operator()
        detail = op.read_detail(course_id, exp_id, stu_exp_id)
        folder = self.config.report_folder / "experiment_materials"
        downloaded: list[dict] = []
        errors: list[str] = []

        # ---- 1) 实验附件: download every attachment with an href ----
        for attachment in detail.attachments:
            if not attachment.href:
                continue
            filename = attachment.name or "attachment"
            if not re.search(r"\.[A-Za-z0-9]{1,6}$", filename):
                filename += ".bin"
            try:
                path = self.session.download_to(
                    attachment.href, folder / _safe_stem(filename))
            except AuthExpiredError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep downloading others
                errors.append(f"实验附件 {attachment.name}：{utils.redact_secrets(str(exc))}")
                continue
            downloaded.append({"name": attachment.name, "path": str(path),
                               "source": "实验附件"})

        # ---- 2) 实验手册: download every file link on the manual tab ----
        for link in detail.manual_links:
            if not link.href:
                continue
            filename = link.name or "manual"
            if not re.search(r"\.[A-Za-z0-9]{1,6}$", filename):
                filename += ".bin"
            try:
                path = self.session.download_to(
                    link.href, folder / _safe_stem(filename))
            except AuthExpiredError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep downloading others
                errors.append(f"实验手册 {link.name}：{utils.redact_secrets(str(exc))}")
                continue
            downloaded.append({"name": link.name, "path": str(path),
                               "source": "实验手册"})

        # ---- 3) 资料 section: walk (folders included) and download every match ----
        from . import materials
        from .experiment import _template_matcher, _template_priority

        template: dict | None = None
        unavailable_materials: list[str] = []
        matcher = _template_matcher(op._experiment_label(detail.name))
        try:
            entries = materials.walk(
                self.session, course_id, matcher, max_depth=6)
        except AuthExpiredError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep the attachments result
            entries = []
            errors.append(f"资料检索：{exc}")
        entries.sort(key=_template_priority)
        for entry in entries:
            if not entry.href:
                unavailable_materials.append(entry.name)
                continue
            filename = entry.name or "material"
            if not re.search(r"\.[A-Za-z0-9]{1,6}$", filename):
                filename += ".bin"
            try:
                path = self.session.download_to(
                    entry.href, folder / _safe_stem(filename))
            except AuthExpiredError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep downloading others
                errors.append(f"资料 {entry.name}：{utils.redact_secrets(str(exc))}")
                continue
            downloaded.append({"name": entry.name, "path": str(path),
                               "source": "资料"})

        # ---- pick the best template across all downloaded files ----
        # 实验附件 → 实验手册 are checked first (no tree walk involved); the
        # 资料 pick reuses the ``entries`` already walked and downloaded above,
        # so ``find_report_template`` is not called again — it would re-walk the
        # whole 资料 folder tree and re-download the template (the reported
        # double walk / double download).
        template: dict | None = None
        try:
            template = op.find_report_template(
                detail, walk_materials=False)
        except AuthExpiredError:
            raise
        except Exception as exc:  # noqa: BLE001
            errors.append(f"模板选择：{exc}")
        if template is None and entries:
            downloadable = [e for e in entries if e.href]
            if downloadable:
                downloadable.sort(key=_template_priority)
                entry = downloadable[0]
                template = {"name": entry.name, "path": "", "source": "资料"}
                # The file was already downloaded above; report its local path
                # by matching name, so the caller knows where to read it.
                hit = next(
                    (d for d in downloaded
                     if d.get("name") == entry.name and d.get("source") == "资料"),
                    None,
                )
                if hit:
                    template["path"] = hit["path"]
            elif entries:
                entries.sort(key=_template_priority)
                template = {"name": entries[0].name, "path": "",
                            "source": "资料"}

        hidden = [a.name for a in detail.attachments if not a.href]
        hidden_manual = [a.name for a in detail.manual_links if not a.href]
        return {
            "course_id": course_id,
            "exp_id": exp_id,
            "stu_exp_id": stu_exp_id,
            "name": detail.name,
            "material": detail.material,
            "requirements": op.collect_requirements(detail)[:6000],
            "downloaded": downloaded,
            "errors": errors,
            # Tabs whose content could not be read. Previously these failures
            # were swallowed, so an empty ``manual_links`` was ambiguous: no
            # manual on the page, or a manual the tool could not reach.
            "tab_errors": dict(detail.tab_errors),
            "unavailable_attachments": hidden,
            "unavailable_manual_links": hidden_manual,
            "unavailable_materials": unavailable_materials,
            "template": template,
            "template_source": (template or {}).get("source", ""),
            "folder": str(folder),
            "note": (
                "实验附件与资料里所有匹配的实验材料 / 报告模板都已下载到本地；"
                "没有下载链接的条目已列在 unavailable_attachments / "
                "unavailable_materials。"
            ),
        }

    def _homework_question_details(self, task: HomeworkTask) -> list[Question]:
        """Full question details for one homework, API-only (no page open).

        ``inspect_homework`` opens the answer page to read the real button
        state, which the two-phase finish phase does not need — it only needs to
        know which questions are blank and which the caller's answers target.
        Reading the details through the REST layer alone saves one full page
        load per homework in the finish phase.
        """
        return [
            self.client.question_detail(q.question_id)
            for q in self.client.list_questions(task.homework_id)
        ]

    def inspect_homework(self, task_id: str, course_id: str) -> dict:
        task = self.resolve_task(task_id, course_id)
        page = self.operator.open_homework(task)
        status, progress = self.operator.read_page_state(page)
        if status is not None:
            task.page_status = status
        if progress is not None:
            task.page_progress = progress
        final_status = status if status is not None else task.status
        questions = [
            self.client.question_detail(q.question_id)
            for q in self.client.list_questions(task.homework_id)
        ]
        return {
            "course_id": course_id,
            "course_name": self.course_name(course_id),
            "task_id": task.task_id,
            "homework_id": task.homework_id,
            "name": task.name,
            "status": final_status.value,
            "status_label": report_mod.STATUS_LABEL[final_status],
            "progress": progress,
            "deadline": utils.deadline_label(task.end_ms),
            "question_count": len(questions),
            "brief": solver.build_answer_brief(task.name, questions),
        }

    def audit_homework(self, task_id: str, course_id: str,
                       refresh_page_state: bool = True) -> dict:
        """Read-only completeness audit for one homework.

        Reads every question's student-side detail and reports which objective
        questions carry no answer, which multi-selects were submitted with only
        part of the correct options, and which multi-selects cannot be judged
        because neither the platform nor the local journal has an answer yet.
        Nothing is withdrawn, filled or submitted, so it is safe to run against
        an already submitted homework.

        The API status understates: a homework already handed in can still come
        back as ``not_submitted``. ``withdrawable`` therefore follows the page's
        real button state when ``refresh_page_state`` is on, and falls back to the
        API status only if that read fails.
        """
        task = self.resolve_task(task_id, course_id)
        api_status = task.status
        page_state_note = ""
        if refresh_page_state and self.operator is not None:
            try:
                page = self.operator.open_homework(task)
                status, progress = self.operator.read_page_state(page)
                if status is not None:
                    task.page_status = status
                if progress is not None:
                    task.page_progress = progress
            except AuthExpiredError:
                raise
            except Exception as exc:  # page read is best-effort
                page_state_note = f"页面状态读取失败：{exc}，按接口状态判定"

        questions = [
            self.client.question_detail(q.question_id)
            for q in self.client.list_questions(task.homework_id)
        ]
        expected = journal.answers_for(self.config, task.task_id, task.homework_id)
        final_status = task.status
        return {
            "course_id": course_id,
            "course_name": self.course_name(course_id),
            "task_id": task.task_id,
            "homework_id": task.homework_id,
            "name": task.name,
            "status": final_status.value,
            "status_label": report_mod.STATUS_LABEL[final_status],
            "api_status": api_status.value,
            "page_state_note": page_state_note,
            "deadline": utils.deadline_label(task.end_ms),
            "withdrawable": final_status is HomeworkStatus.SUBMITTED_PENDING,
            "journal_answers": len(expected),
            "audit": report_mod.audit_questions(questions, expected_answers=expected),
        }

    def repair_homework(self, task_id: str, course_id: str,
                        answers: dict[str, str] | str | None = None,
                        *, verify_unjudged: bool = False,
                        submit: bool = True) -> dict:
        """Withdraw a submitted homework, fill the gaps, and resubmit it.

        Only a homework the page reports as ``submitted_pending`` can be
        repaired (a graded one cannot be withdrawn). The withdrawal happens
        only after every known gap has an answer: the caller's ``answers``
        first, then the local journal recorded when this tool submitted the
        homework. Existing answers are preserved and re-used, so a withdrawal
        that clears the draft can still be restored.

        ``verify_unjudged=True`` additionally re-answers multi-selects whose
        completeness could not be checked before grading (the platform hides
        its standard answer while a submission is pending). Supplying an answer
        for such a question is what makes the re-check meaningful.
        """
        task = self.resolve_task(task_id, course_id)
        supplied = solver.parse_model_answers(answers or {})
        page = self.operator.open_homework(task)
        status, progress = self.operator.read_page_state(page)
        if status is not None:
            task.page_status = status
        if progress is not None:
            task.page_progress = progress
        final_status = task.status

        result: dict = {
            "course_id": course_id,
            "course_name": self.course_name(course_id),
            "task_id": task.task_id,
            "homework_id": task.homework_id,
            "name": task.name,
            "status": final_status.value,
            "status_label": report_mod.STATUS_LABEL[final_status],
            "withdrawable": final_status is HomeworkStatus.SUBMITTED_PENDING,
            "withdrawn": False,
            "submitted": False,
            "needs_answers": [],
            "errors": [],
            "note": "",
        }
        if final_status is HomeworkStatus.SUBMITTED_GRADED:
            result["note"] = "作业已出分，平台不允许撤回，无法补交"
            return result
        if final_status is not HomeworkStatus.SUBMITTED_PENDING:
            result["note"] = ("页面未显示「撤回作业」，这份作业不是待批改状态；"
                              "如仍未提交，请直接用 uplus_answer_homework 作答")
            return result

        questions = [
            self.client.question_detail(q.question_id)
            for q in self.client.list_questions(task.homework_id)
        ]
        expected = journal.answers_for(self.config, task.task_id, task.homework_id)
        audit = report_mod.audit_questions(questions, expected_answers=expected)
        result["audit"] = audit

        by_order = {q.order: q for q in questions}
        target_ids: set[str] = set()
        targets: list[tuple[str, object, dict]] = []
        for hit in audit["missing"]:
            question = by_order.get(hit["order"])
            if question is not None:
                targets.append(("missing", question, hit))
                target_ids.add(question.question_id)
        for hit in audit["partial"]:
            question = by_order.get(hit["order"])
            if question is not None:
                targets.append(("partial", question, hit))
                target_ids.add(question.question_id)
        if verify_unjudged:
            for hit in audit["unjudged"]:
                question = by_order.get(hit["order"])
                if question is not None:
                    targets.append(("verify", question, hit))
                    target_ids.add(question.question_id)

        for kind, question, hit in targets:
            supplied_answer = (
                supplied.get(question.question_id)
                or supplied.get(str(question.order))
            )
            fallback = expected.get(question.question_id)
            answer = supplied_answer or fallback
            invalid_reason = ""
            # A partial selection or an unverified multi-select must not be
            # resubmitted as-is: that would only repeat the defect. Require a
            # real answer from the caller or the local journal.
            if not answer:
                invalid_reason = "缺少答案，无法补交"
            else:
                try:
                    normalized = solver.normalize_answer(question, answer)
                except (TypeError, ValueError) as exc:
                    invalid_reason = f"答案无法解析：{exc}"
                else:
                    reference = hit.get("reference_answer")
                    if reference and question.type is QuestionType.MULTIPLE:
                        expected_letters = set(utils.normalize_letters(reference))
                        got_letters = set(utils.normalize_letters(normalized))
                        if expected_letters != got_letters:
                            missing = "".join(sorted(expected_letters - got_letters))
                            extra = "".join(sorted(got_letters - expected_letters))
                            detail = []
                            if missing:
                                detail.append(f"仍缺 {missing}")
                            if extra:
                                detail.append(f"多出 {extra}")
                            invalid_reason = (
                                "新答案与标准答案不一致（" + "，".join(detail) + "）"
                            )
            if invalid_reason:
                result["needs_answers"].append({
                    "kind": kind,
                    "question_id": question.question_id,
                    "order": question.order,
                    "type": question.type.label,
                    "stem": question.stem[:120],
                    "my_answer": question.my_answer,
                    "reference_answer": question.reference_answer,
                    "missing_letters": hit.get("missing_letters"),
                    "reason": invalid_reason,
                    "question": question.llm_prompt(),
                })
        if result["needs_answers"]:
            result["note"] = ("撤回前必须先准备以下漏答/漏选题目答案；"
                              "本次未撤回，作业保持已提交状态")
            return result
        if not targets:
            result["note"] = "没有漏答或漏选题目，无需撤回"
            return result

        merged: dict[str, str] = {}
        for question in questions:
            if not question.auto_answerable:
                continue
            answer = (
                supplied.get(question.question_id)
                or supplied.get(str(question.order))
            )
            if answer is None and question.question_id in target_ids:
                answer = expected.get(question.question_id)
            if answer is None:
                answer = question.my_answer
            if answer:
                merged[question.question_id] = answer
        uncovered = [
            question.order for question in questions
            if question.auto_answerable and question.question_id not in merged
        ]
        if uncovered:
            result["needs_answers"] = [
                {"order": order, "reason": "该客观题没有任何答案可供补交"}
                for order in uncovered
            ]
            result["note"] = "仍有客观题没有答案，未撤回，作业保持已提交状态"
            return result

        try:
            result["withdrawal"] = self.operator.withdraw_homework(page, task)
            result["withdrawn"] = result["withdrawal"] == "withdrawn"
        except AuthExpiredError:
            raise
        except Exception as exc:  # noqa: BLE001 - report, never crash the tool
            result["errors"].append(f"撤回失败：{exc}")
            result["note"] = "撤回失败，作业保持原状态"
            return result

        try:
            outcome = self.operator.answer_homework(
                task, merged, submit=submit,
                allow_subjective_skips=True,
                course_name=self.course_name(course_id),
            )
            result["repair"] = outcome
            result["submitted"] = bool(outcome.get("submitted"))
        except AuthExpiredError:
            raise
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"补交失败：{exc}")
            result["note"] = "作业已撤回，但补交未完成，请人工确认后重新提交"
            return result

        try:
            after_questions = [
                self.client.question_detail(q.question_id)
                for q in self.client.list_questions(task.homework_id)
            ]
            after_expected = journal.answers_for(
                self.config, task.task_id, task.homework_id)
            result["audit_after"] = report_mod.audit_questions(
                after_questions, expected_answers=after_expected)
        except AuthExpiredError:
            raise
        except Exception as exc:  # noqa: BLE001 - verification is best-effort
            result["audit_after"] = {"error": str(exc)}

        if result["submitted"]:
            after = result.get("audit_after") or {}
            if after.get("complete"):
                result["note"] = "已撤回、补齐并重新提交；复核未发现漏答或漏选"
                if after.get("unjudged_count"):
                    result["note"] += (
                        f"（另有 {after['unjudged_count']} 道多选因平台尚未公布"
                        "参考答案，最终正确性要等批改后复核）"
                    )
            else:
                result["note"] = "已重新提交，但复核仍有未完成项，请人工检查"
        elif submit:
            result["note"] = "作业已撤回，但未完成重新提交；请检查 errors"
        else:
            result["note"] = "作业已撤回并填写答案，未提交（submit=False）"
        return result

    # ------------------------------------------------------------------ #
    # write tools
    # ------------------------------------------------------------------ #
    def answer_homework(self, task_id: str, course_id: str,
                        answers: dict[str, str] | str, submit: bool = True,
                        force_submit: bool = False) -> dict:
        task = self.resolve_task(task_id, course_id)
        answers = solver.parse_model_answers(answers)
        return self.operator.answer_homework(
            task, answers, submit=submit, force_submit=force_submit,
            course_name=self.course_name(course_id),
        )

    def capture_question_image(self, task_id: str, course_id: str, *,
                               order: int = 0, question_id: str = "",
                               output_path: str = "",
                               full_page: bool = False,
                               occurrence: int = 0) -> dict:
        """Screenshot one question (figure questions included).

        Read-only against the platform: it opens the answer page, navigates to
        the question by stem and saves a PNG of that question's block. Nothing
        is filled or submitted.

        ``occurrence`` selects the n-th exact duplicate when several questions
        share the same stem (mirrors ``fill_question``); 0 is the first copy.
        """
        task = self.resolve_task(task_id, course_id)
        metas = self.client.list_questions(task.homework_id)
        if not metas:
            raise UplusError("该作业没有题目（纯材料/附件作业），无法截取题目图片")
        target = None
        for meta in metas:
            if question_id and meta.question_id == question_id:
                target = meta
                break
            if order and meta.order == order:
                target = meta
                break
        if target is None:
            if not question_id and not order:
                raise UplusError("请提供 order 或 question_id 指定要截图的题目")
            raise UplusError("未找到指定题目，请核对 order / question_id")
        detail = self.client.question_detail(target.question_id)
        page = self.operator.open_homework(task)
        path = self.operator.capture_question(
            page, detail, len(metas), output_path=output_path,
            full_page=full_page, occurrence=occurrence)
        return {
            "task_id": task.task_id,
            "course_id": course_id,
            "question_id": detail.question_id,
            "order": detail.order,
            "type": detail.type.label,
            "stem": detail.stem[:200],
            "images": detail.images,
            "path": str(path),
            "bytes": path.stat().st_size,
            "note": (
                "题目截图已保存。图形题请依据截图作答；"
                "把答案交给 uplus_answer_homework 时用 question_id 作为键。"
            ),
        }

    def upload_attachment(self, task_id: str, course_id: str, file_path: str) -> dict:
        task = self.resolve_task(task_id, course_id)
        return self.operator.upload_attachment(task, file_path)

    # ------------------------------------------------------------------ #
    # patrol
    # ------------------------------------------------------------------ #
    @staticmethod
    def _repair_items(questions: list, audit_result: dict,
                      expected: dict[str, str]) -> list[dict]:
        """Questions a repair call must answer, with their current context."""
        by_order = {q.order: q for q in questions}
        items: list[dict] = []
        for kind in ("missing", "partial", "unjudged"):
            for hit in audit_result.get(kind, []):
                question = by_order.get(hit.get("order"))
                if question is None:
                    continue
                item = {
                    "kind": kind,
                    "order": question.order,
                    "question_id": question.question_id,
                    "type": question.type.label,
                    "stem": question.stem[:120],
                    "current_answer": question.my_answer,
                    "reference_answer": question.reference_answer,
                    "journal_answer": expected.get(question.question_id),
                    "question": question.llm_prompt(),
                }
                for key in ("missing_letters", "reason", "reference_source"):
                    if hit.get(key):
                        item[key] = hit[key]
                item["needs_model_answer"] = not bool(item.get("journal_answer"))
                if kind == "unjudged":
                    # Not a proven defect: the platform simply has not shown
                    # its answer yet. Re-answering it is optional.
                    item["optional"] = True
                items.append(item)
        return items

    def patrol(self, *, auto_answer: bool = False, submit: bool = False,
               audit: bool = True) -> dict:
        snapshot = report_mod.PatrolReport(generated_at_ms=utils.now_ms())
        answer_briefs: list[dict] = []
        repair_briefs: list[dict] = []
        auto_submissions: list[dict] = []
        incomplete: list[dict] = []

        for course in self._iter_courses():
            # Course-major, deterministic order: all of this course's tasks
            # (sorted by due date + title), then its experiments, then the next
            # course. This keeps the browser on one course at a time.
            tasks = sorted(
                self.client.list_homeworks(course.id), key=_homework_sort_key)
            for task in tasks:
                self._register(task)
                # The page button is the authoritative status. Read it for
                # open homeworks as before, and for audited submitted ones so
                # a graded homework is not misreported as withdrawable.
                should_read_page = (
                    (task.status is HomeworkStatus.NOT_SUBMITTED
                     and not task.is_expired())
                    or (audit and task.status in (
                        HomeworkStatus.SUBMITTED_PENDING,
                        HomeworkStatus.SUBMITTED_GRADED,
                    ))
                )
                if should_read_page:
                    try:
                        page = self.operator.open_homework(task)
                        status, progress = self.operator.read_page_state(page)
                        if status is not None:
                            task.page_status = status
                        if progress is not None:
                            task.page_progress = progress
                    except AuthExpiredError:
                        raise
                    except Exception as exc:
                        snapshot.takeover.append({
                            "course": course.name, "title": task.name,
                            "detail": f"页面状态读取失败：{exc}",
                        })
                report_mod.add_homework(snapshot, course, task)

                if audit and task.status in (
                    HomeworkStatus.SUBMITTED_PENDING,
                    HomeworkStatus.SUBMITTED_GRADED,
                ):
                    try:
                        questions = [
                            self.client.question_detail(q.question_id)
                            for q in self.client.list_questions(task.homework_id)
                        ]
                        expected = journal.answers_for(
                            self.config, task.task_id, task.homework_id)
                        audit_result = report_mod.audit_questions(
                            questions, expected_answers=expected)
                    except AuthExpiredError:
                        raise
                    except Exception as exc:  # keep the patrol alive
                        snapshot.takeover.append({
                            "course": course.name, "title": task.name,
                            "detail": f"提交完整性核查失败：{exc}",
                        })
                    else:
                        snapshot.audit_checked += 1
                        snapshot.audit_unjudged += audit_result["unjudged_count"]
                        if not audit_result["complete"]:
                            detail_parts: list[str] = []
                            if audit_result["missing"]:
                                orders = "、".join(
                                    str(m["order"]) for m in audit_result["missing"][:10]
                                )
                                tail = "…" if len(audit_result["missing"]) > 10 else ""
                                detail_parts.append(
                                    f"漏答 {len(audit_result['missing'])} 题"
                                    f"（题号 {orders}{tail}）"
                                )
                            if audit_result["partial"]:
                                porders = "、".join(
                                    str(m["order"]) for m in audit_result["partial"][:10]
                                )
                                ptail = "…" if len(audit_result["partial"]) > 10 else ""
                                detail_parts.append(
                                    f"多选漏选 {len(audit_result['partial'])} 题"
                                    f"（题号 {porders}{ptail}）"
                                )
                            hint = ("，可撤回后补齐再提交"
                                    if task.status is HomeworkStatus.SUBMITTED_PENDING
                                    else "，已出分无法撤回")
                            snapshot.takeover.append({
                                "course": course.name, "title": task.name,
                                "detail": "已提交但" + "；".join(detail_parts) + hint,
                                "deadline": utils.deadline_label(task.end_ms),
                            })
                            incomplete.append({
                                "course_id": course.id,
                                "course_name": course.name,
                                "task_id": task.task_id,
                                "homework_id": task.homework_id,
                                "name": task.name,
                                "status": task.status.value,
                                "withdrawable": task.status is HomeworkStatus.SUBMITTED_PENDING,
                                "missing": audit_result["missing"],
                                "partial": audit_result["partial"],
                                "wrong": audit_result["wrong"],
                                "unjudged": audit_result["unjudged"],
                                "audit": audit_result,
                            })
                            snapshot.incomplete.append({
                                "course": course.name,
                                "name": task.name,
                                "missing_count": len(audit_result["missing"]),
                                "missing_orders": [m["order"] for m in audit_result["missing"]],
                                "partial_count": len(audit_result["partial"]),
                                "partial": audit_result["partial"],
                                "unjudged_count": audit_result["unjudged_count"],
                                "withdrawable": task.status is HomeworkStatus.SUBMITTED_PENDING,
                            })
                        repair_items = self._repair_items(
                            questions, audit_result, expected)
                        if repair_items:
                            withdrawable = (
                                task.status is HomeworkStatus.SUBMITTED_PENDING
                            )
                            if withdrawable:
                                note = ("先答完 needs_model_answer 且非 optional 的题目，再调用 "
                                        "uplus_repair_homework(task_id, course_id, answers) "
                                        "撤回、补齐并重新提交；journal_answer 已有答案的题目"
                                        "无需模型再答。optional=true 的是待批改、平台尚未公布"
                                        "答案的多选，用户要求彻底排除漏选时再作答并传 "
                                        "verify_unjudged=True。")
                            elif task.status is HomeworkStatus.SUBMITTED_GRADED:
                                note = "作业已出分，平台不允许撤回；这些题目仅作结果告知。"
                            else:
                                note = "当前状态无法确认可撤回，请人工核对。"
                            repair_briefs.append({
                                "course_id": course.id,
                                "course_name": course.name,
                                "task_id": task.task_id,
                                "homework_id": task.homework_id,
                                "name": task.name,
                                "status": task.status.value,
                                "withdrawable": withdrawable,
                                "items": repair_items,
                                "note": note,
                            })

                if task.status is HomeworkStatus.NOT_SUBMITTED and not task.is_expired():
                    questions = [
                        self.client.question_detail(q.question_id)
                        for q in self.client.list_questions(task.homework_id)
                    ]
                    objective = [q for q in questions if q.auto_answerable and not q.answered]
                    subjective = [q for q in questions if not q.auto_answerable]
                    if subjective:
                        snapshot.takeover.append({
                            "course": course.name, "title": task.name,
                            "detail": f"含 {len(subjective)} 道主观/编程/附件题，需本人完成",
                            "deadline": utils.deadline_label(task.end_ms),
                        })
                    if objective:
                        rule_answers: dict[str, str] = {}
                        if auto_answer:
                            # Course-specific deterministic rules may plug into
                            # solver.rule_based_answer(); the bundled default
                            # returns None, so this map stays empty unless rules
                            # are configured and human-reviewed.
                            for q in objective:
                                guessed = solver.rule_based_answer(q)
                                if guessed is not None:
                                    rule_answers[q.question_id] = guessed
                        if submit and auto_answer and objective \
                                and len(rule_answers) == len(objective):
                            # Wire the documented contract: only a homework whose
                            # objective questions ALL carry a rule answer goes to
                            # the operator, whose own gate still refuses to submit
                            # when subjective items remain or a fill fails.
                            try:
                                submission = self.operator.answer_homework(
                                    task, rule_answers, submit=True,
                                    course_name=course.name,
                                )
                                auto_submissions.append(submission)
                            except AuthExpiredError:
                                raise
                            except Exception as exc:
                                snapshot.takeover.append({
                                    "course": course.name, "title": task.name,
                                    "detail": f"自动作答/提交失败：{exc}",
                                })
                        answer_briefs.append({
                            "course_id": course.id,
                            "course_name": course.name,
                            "task_id": task.task_id,
                            "homework_id": task.homework_id,
                            "brief": solver.build_answer_brief(task.name, objective),
                            "rule_answers": rule_answers,
                        })
                    if not questions:
                        snapshot.takeover.append({
                            "course": course.name, "title": task.name,
                            "detail": "附件/材料提交题，需本人上传文件",
                            "deadline": utils.deadline_label(task.end_ms),
                        })
                elif task.is_expired() and task.status is HomeworkStatus.NOT_SUBMITTED:
                    snapshot.takeover.append({
                        "course": course.name, "title": task.name,
                        "detail": "已截止，无法补交",
                        "deadline": utils.deadline_label(task.end_ms),
                    })

            try:
                experiments = sorted(
                    self.operator.read_experiments(course),
                    key=_experiment_sort_key,
                )
                for exp in experiments:
                    snapshot.experiments.append(report_mod.ExperimentRow(course.name, exp))
                    # The experiment pipeline is retrieval-only: the patrol
                    # reports what the platform says and hands the rest to the
                    # human. It no longer builds an authoring brief or audits a
                    # submitted report, because nothing here fills or submits.
                    if exp.submitted is False:
                        detail = f"实验未提交，提交物：{exp.material or '实验报告/成果物'}"
                        snapshot.takeover.append({
                            "course": course.name, "title": exp.name,
                            "detail": detail,
                            "deadline": utils.deadline_label(exp.deadline_ms),
                        })
                    elif exp.submitted is None:
                        snapshot.takeover.append({
                            "course": course.name, "title": exp.name,
                            "detail": "实验状态未知，需本人进入实验栏目人工复核",
                            "deadline": utils.deadline_label(exp.deadline_ms),
                        })
            except AuthExpiredError:
                raise
            except Exception as exc:
                snapshot.takeover.append({
                    "course": course.name, "title": "实验栏目",
                    "detail": f"读取失败：{exc}",
                })

        markdown = report_mod.render_markdown(snapshot, warning_hours=self.config.deadline_warning_hours)
        report_path = report_mod.persist(markdown, self.config)

        return {
            "summary": {
                "homework_submitted": snapshot.submitted,
                "homework_pending": snapshot.pending,
                "homework_outstanding": snapshot.outstanding,
                "experiments_submitted": snapshot.submitted_experiments,
                "experiments_outstanding": snapshot.outstanding_experiments,
                "experiments_unknown": snapshot.unknown_experiments,
                "takeover_count": len(snapshot.takeover),
            },
            "audit_summary": {
                "checked": snapshot.audit_checked,
                "incomplete": len(incomplete),
                "unjudged_multiselect": snapshot.audit_unjudged,
            },
            "answer_briefs": answer_briefs,
            "repair_briefs": repair_briefs,
            "auto_submissions": auto_submissions,
            "incomplete_homeworks": incomplete,
            "takeover": snapshot.takeover,
            "report_path": str(report_path),
        }

    # ------------------------------------------------------------------ #
    # two-phase: finish every outstanding homework, then review once
    # ------------------------------------------------------------------ #
    def _collect_outstanding(self, courses: list | None = None) -> list[tuple[Course, HomeworkTask]]:
        """All not-submitted, non-expired homeworks across active courses.

        Course-major and deterministic: a course's tasks are sorted by due date
        + title (same key as ``patrol``), so the finish phase walks them in the
        same stable order the user sees in the report. Expired tasks are left
        out: they cannot be submitted and only slow the finish phase down.

        ``courses`` may be passed in from ``finish_then_review`` so both phases
        share one account-course read; when omitted, the course list is read
        here (as before).
        """
        out: list[tuple[Course, HomeworkTask]] = []
        for course in courses if courses is not None else self._iter_courses():
            tasks = sorted(
                self.client.list_homeworks(course.id), key=_homework_sort_key)
            for task in tasks:
                self._register(task)
                if (task.status is HomeworkStatus.NOT_SUBMITTED
                        and not task.is_expired()):
                    out.append((course, task))
        return out

    def finish_then_review(self, answers: dict[str, dict[str, str]] | None = None,
                           *, submit: bool = False, audit: bool = True,
                           screenshot: bool = False) -> dict:
        """Two-phase homework workflow: finish first, review once afterwards.

        Phase 1 — *finish*. Walk every active course in the fixed patrol order
        and answer each not-submitted, non-expired homework in turn. The caller
        supplies answers as ``{task_id: {question_id: answer}}``; any objective
        question left without an answer is collected, never silently skipped,
        so the review phase can tell exactly what is still outstanding.

        Phase 2 — *review*. Only after the last homework has been attempted do
        we re-read every homework's real page state and run the same
        completeness audit ``patrol`` uses, so the verdict ("还有没有未完成的作
        业") reflects the post-fill state of the whole account rather than a
        per-task snapshot taken mid-fill. ``screenshot=True`` additionally
        captures the answer page of each still-incomplete homework so the model
        (or a human) can see the unfilled boxes before deciding what to do.

        Nothing here changes ``patrol``'s contract: it is a separate orchestration
        for the "answer everything, then check once" flow. Subjective,
        programming and attachment items are never answered and are reported
        under ``takeover`` exactly as ``patrol`` reports them.
        """
        supplied = answers or {}
        finish_log: list[dict] = []
        takeover: list[dict] = []

        # The active course list is read once and shared by both phases. It is
        # the same deterministic, course-major feed ``patrol`` walks; phase 2
        # re-lists each course's *homeworks* (the fill may have submitted some
        # of them, so their state must be re-read) but not the whole account
        # feed.
        courses = self._iter_courses()

        # ---- Phase 1: finish every outstanding homework -------------------- #
        for course, task in self._collect_outstanding(courses=courses):
            task_answers = supplied.get(task.task_id, {})
            try:
                # API-only read (no page open): the finish phase only needs to
                # know which questions are blank and which the caller's answers
                # target. ``inspect_homework`` opens the answer page for the
                # real button state, which is only meaningful once the fill has
                # happened — opening it here would be a wasted full page load
                # (answer_homework and the phase-2 audit open it anyway).
                questions = self._homework_question_details(task)
            except AuthExpiredError:
                raise
            except Exception as exc:  # keep the finish phase going
                takeover.append({
                    "course": course.name, "title": task.name,
                    "phase": "finish",
                    "detail": f"读取题目失败：{exc}",
                    "deadline": utils.deadline_label(task.end_ms),
                })
                continue
            subjective = [q for q in questions if not q.auto_answerable]
            objective_questions = [q for q in questions if q.auto_answerable]
            objective_blank = [q for q in objective_questions if not q.answered]
            if subjective:
                takeover.append({
                    "course": course.name, "title": task.name,
                    "phase": "finish",
                    "detail": f"含 {len(subjective)} 道主观/编程/附件题，需本人完成",
                    "deadline": utils.deadline_label(task.end_ms),
                })
            if not objective_questions:
                # No objective items at all: nothing this tool may fill. A
                # material-only homework and a homework of purely subjective
                # items both belong to the human; neither should reach
                # answer_homework (its page open would be wasted).
                note = ("无客观题（仅含主观/编程/附件题，需本人完成）"
                        if subjective else "无客观题（纯材料/附件作业）")
                finish_log.append({
                    "course_id": course.id, "course_name": course.name,
                    "task_id": task.task_id, "name": task.name,
                    "answered": 0, "submitted": False, "note": note,
                })
                continue
            # The caller's answers may target *already-answered* questions: a
            # partial multi-select that needs AB, a half-filled blank. Those are
            # real gaps this flow exists to close, and answer_homework handles
            # them (diff and refill). Filtering them out here would silently
            # drop the caller's correction.
            objective_qids = {q.question_id for q in objective_questions}
            objective_orders = {str(q.order) for q in objective_questions}
            supplied_for_objective = bool(task_answers) and any(
                str(k) in objective_qids or str(k) in objective_orders
                for k in task_answers)
            if not objective_blank and not supplied_for_objective:
                # Nothing to fill and no correction to apply: skip without
                # opening the page.
                note = ("无客观题待答（已全部作答）"
                        + ("；含主观/编程/附件题需本人完成" if subjective else ""))
                finish_log.append({
                    "course_id": course.id, "course_name": course.name,
                    "task_id": task.task_id, "name": task.name,
                    "answered": 0, "submitted": False, "note": note,
                })
                continue
            if not supplied_for_objective:
                # Blank objective questions exist but the caller gave no answers
                # for this task. Calling answer_homework now would re-open the
                # page and re-fetch every question detail only to skip each one
                # for "缺少模型答案" — a full page load and N API round-trips
                # with zero effect. Report what is needed from the data we
                # already have and leave the homework untouched; the caller
                # supplies answers and re-runs, or finishes it by hand.
                needs = [
                    {"question_id": q.question_id,
                     "order": q.order,
                     "type": q.type.label,
                     "figure": bool(q.images)}
                    for q in objective_blank
                ]
                finish_log.append({
                    "course_id": course.id, "course_name": course.name,
                    "task_id": task.task_id, "homework_id": task.homework_id,
                    "name": task.name,
                    "answered": 0, "objective_total": len(objective_blank),
                    "objective_answered": 0,
                    "submitted": False,
                    "needs_answers": needs,
                    "note": (f"未提供答案，跳过作答：{len(objective_blank)} 道客观题待答。"
                             "先用 uplus_inspect_homework 取审题包，作答后把 "
                             "{task_id: {question_id: answer}} 传入再重跑。"),
                })
                takeover.append({
                    "course": course.name, "title": task.name,
                    "phase": "finish",
                    "detail": (f"{len(objective_blank)} 道客观题未提供答案，未作答"
                               "（先用 inspect 取审题包，作答后重跑 finish_then_review）"),
                    "deadline": utils.deadline_label(task.end_ms),
                    "needs_answers": needs,
                })
                continue
            try:
                outcome = self.operator.answer_homework(
                    task, task_answers, submit=submit,
                    allow_subjective_skips=True,
                    course_name=course.name,
                )
            except AuthExpiredError:
                raise
            except Exception as exc:  # never abort the whole finish phase
                takeover.append({
                    "course": course.name, "title": task.name,
                    "phase": "finish",
                    "detail": f"作答失败：{exc}",
                    "deadline": utils.deadline_label(task.end_ms),
                })
                finish_log.append({
                    "course_id": course.id, "course_name": course.name,
                    "task_id": task.task_id, "homework_id": task.homework_id,
                    "name": task.name,
                    "answered": 0, "submitted": False,
                    "errors": [str(exc)],
                    "note": f"作答失败：{exc}",
                })
                continue
            finish_log.append({
                "course_id": course.id, "course_name": course.name,
                "task_id": task.task_id, "homework_id": task.homework_id,
                "name": task.name,
                "answered": outcome.get("answered", 0),
                "objective_total": outcome.get("objective_total", 0),
                "objective_answered": outcome.get("objective_answered", 0),
                "submitted": bool(outcome.get("submitted")),
                "skipped": outcome.get("skipped", 0),
                "skipped_questions": outcome.get("skipped_questions", []),
                "errors": outcome.get("errors", []),
                "note": outcome.get("note", ""),
            })
            if outcome.get("skipped_questions"):
                for item in outcome["skipped_questions"]:
                    if item.get("subjective") or item.get("blocking") is False:
                        continue
                    takeover.append({
                        "course": course.name, "title": task.name,
                        "phase": "finish",
                        "detail": (f"第{item.get('order')}题未完成："
                                   f"{item.get('reason', '')}"),
                        "deadline": utils.deadline_label(task.end_ms),
                    })

        # ---- Phase 2: review the whole account once ----------------------- #
        review: list[dict] = []
        incomplete_count = 0
        for course in courses:
            tasks = sorted(
                self.client.list_homeworks(course.id), key=_homework_sort_key)
            for task in tasks:
                self._register(task)
                try:
                    result = self.audit_homework(
                        task.task_id, course.id, refresh_page_state=audit)
                except AuthExpiredError:
                    raise
                except Exception as exc:  # keep reviewing the rest
                    takeover.append({
                        "course": course.name, "title": task.name,
                        "phase": "review",
                        "detail": f"复核失败：{exc}",
                        "deadline": utils.deadline_label(task.end_ms),
                    })
                    continue
                audit_block = result.get("audit", {})
                is_incomplete = not audit_block.get("complete", True)
                screenshot_path = ""
                if is_incomplete and screenshot:
                    try:
                        # Full answer-page shot, not a single question capture:
                        # the reviewer needs to see every unfilled box at once.
                        # ``capture_question_image`` cannot do that (it navigates
                        # to one question by stem and requires an order).
                        shot = self.operator.capture_answer_page(task)
                        screenshot_path = str(shot)
                    except AuthExpiredError:
                        raise
                    except Exception:  # screenshot is best-effort
                        screenshot_path = ""
                review.append({
                    "course_id": course.id,
                    "course_name": course.name,
                    "task_id": task.task_id,
                    "homework_id": task.homework_id,
                    "name": task.name,
                    "status": result.get("status"),
                    "status_label": result.get("status_label"),
                    "deadline": result.get("deadline"),
                    "withdrawable": result.get("withdrawable", False),
                    "complete": audit_block.get("complete", True),
                    "missing": audit_block.get("missing", []),
                    "partial": audit_block.get("partial", []),
                    "wrong": audit_block.get("wrong", []),
                    "unjudged": audit_block.get("unjudged", []),
                    "screenshot": screenshot_path,
                })
                if is_incomplete:
                    incomplete_count += 1
                    parts: list[str] = []
                    if audit_block.get("missing"):
                        orders = "、".join(
                            str(m["order"]) for m in audit_block["missing"][:10])
                        tail = "…" if len(audit_block["missing"]) > 10 else ""
                        parts.append(f"漏答 {len(audit_block['missing'])} 题"
                                     f"（题号 {orders}{tail}）")
                    if audit_block.get("partial"):
                        porders = "、".join(
                            str(m["order"]) for m in audit_block["partial"][:10])
                        ptail = "…" if len(audit_block["partial"]) > 10 else ""
                        parts.append(f"多选漏选 {len(audit_block['partial'])} 题"
                                     f"（题号 {porders}{ptail}）")
                    takeover.append({
                        "course": course.name, "title": task.name,
                        "phase": "review",
                        "detail": "；".join(parts) if parts else "复核仍有未完成项",
                        "deadline": utils.deadline_label(task.end_ms),
                        "withdrawable": result.get("withdrawable", False),
                    })

        # Persist a compact Markdown review so the user has a file to open.
        # It is built from the real finish / review / takeover data — an empty
        # PatrolReport would render an empty table — and written under its own
        # ``review_…`` name so a patrol report in the same minute is not
        # overwritten.
        markdown = report_mod.render_finish_review_markdown(
            generated_at_ms=utils.now_ms(),
            finish=finish_log, review=review, takeover=takeover,
        )
        report_path = report_mod.persist_review(markdown, self.config)
        return {
            "phase": "finish_then_review",
            "summary": {
                "finish_attempted": len(finish_log),
                "finish_submitted": sum(1 for f in finish_log if f.get("submitted")),
                "review_total": len(review),
                "review_incomplete": incomplete_count,
                "takeover_count": len(takeover),
            },
            "finish": finish_log,
            "review": review,
            "takeover": takeover,
            "report_path": str(report_path),
            "note": (
                "两阶段流程：第一阶段已尝试完成所有未提交作业，第二阶段统一复核"
                "判断是否仍有未完成。review 中 complete=false 的作业仍需处理；"
                "带 screenshot 的可用 browse_screenshot / uplus_capture_question_image "
                "查看未填写空位后补答，再走 uplus_repair_homework。"
            ),
        }


# --------------------------------------------------------------------------- #
# deterministic retrieval order
# --------------------------------------------------------------------------- #
_CN_DIGITS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9,
}


def _cn_number(text: str) -> int | None:
    """Parse the small Chinese/Arabic number used in an experiment title."""
    token = (text or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if "十" in token:
        head, _, tail = token.partition("十")
        if head and head not in _CN_DIGITS:
            return None
        if tail and tail not in _CN_DIGITS:
            return None
        tens = _CN_DIGITS.get(head, 1) if head else 1
        ones = _CN_DIGITS.get(tail, 0) if tail else 0
        return tens * 10 + ones
    return _CN_DIGITS.get(token)


def experiment_number(name: str) -> int | None:
    """``实验一`` / ``实验3`` -> 1 / 3; None when the title has no number."""
    m = re.search(r"实验\s*([一二三四五六七八九十0-9]+)", name or "")
    return _cn_number(m.group(1)) if m else None


def _course_sort_key(course: Course) -> tuple[str, str]:
    """Stable order for the live course feed (the API order can vary)."""
    return ((course.name or "").strip(), course.id)


def _homework_sort_key(task: HomeworkTask) -> tuple:
    """Due date first, then title/id; tasks without a deadline go last."""
    return (task.end_ms is None, task.end_ms or 0, task.name or "", task.task_id)


def _experiment_sort_key(exp: Experiment) -> tuple:
    """实验一 → 实验二 → … → 实验十, then unnumbered titles by name."""
    number = experiment_number(exp.name)
    return (number is None, number or 0, exp.name or "", exp.exp_id)


def _safe_stem(name: str, fallback: str = "experiment") -> str:
    """A safe local file name for a downloaded material."""
    return utils.safe_filename(name, fallback)
