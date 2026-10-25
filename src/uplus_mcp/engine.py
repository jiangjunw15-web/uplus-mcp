"""Plain orchestration layer shared by the MCP server and the CLI patrol script.

Keeping business flow here means both entrypoints behave identically; the MCP
tools in ``server.py`` are thin wrappers around an ``PatrolEngine`` instance.
"""

from __future__ import annotations

from . import report as report_mod
from .browser import BrowserSession, AuthExpiredError, UplusError
from .config import Config
from .models import HomeworkStatus, HomeworkTask
from .operator import HomeworkOperator
from .platform import PlatformClient
from . import solver, utils


class PatrolEngine:
    def __init__(self, config: Config, session: BrowserSession,
                 client: PlatformClient, operator: HomeworkOperator) -> None:
        self.config = config
        self.session = session
        self.client = client
        self.operator = operator
        self._task_index: dict[str, HomeworkTask] = {}

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def course_name(self, course_id: str) -> str:
        hit = next((c.name for c in self.config.courses if c.id == course_id), "")
        if not hit:
            course = self.client.get_course(course_id)
            hit = course.name if course else course_id
        return hit

    def _iter_courses(self) -> list:
        """Configured courses, or the live account list when none are configured.

        By default expired courses are skipped during patrol; set
        ``include_expired_courses=True`` to keep the archived view.
        """
        courses = self.config.courses or self.client.list_my_courses()
        if self.config.include_expired_courses:
            return courses
        return [c for c in courses if not utils.is_expired(c.end_ms)]

    def _register(self, task: HomeworkTask) -> None:
        self._task_index[task.task_id] = task

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

    def login(self, mode: str = "manual", username: str = "",
              password: str = "", school: str = "",
              wait_timeout_ms: int = 120_000) -> dict:
        """Sign in through one of two agreed flows.

        ``manual`` opens the portal and waits for the human to finish signing in
        in the attached browser window. ``auto`` fills the school selector, account
        and password after the model asked the user for them; credentials are
        used only for this call and are never persisted or echoed back.
        """
        mode = (mode or "manual").strip().lower()
        if mode == "auto":
            if not username.strip() or not password.strip():
                return {"logged_in": False, "mode": "auto",
                        "reason": "自动登录前必须先向用户询问账号与密码；缺少凭据时改用手动登录。"}
            result = self.session.auto_login(
                username.strip(), password, school.strip(),
                wait_ms=min(int(wait_timeout_ms), 30_000),
            )
            # Strip any accidental credential echo before returning to the model.
            result.pop("username", None)
            result.pop("password", None)
            result.pop("school", None)
            return result

        self.session.open_login_page()
        status = self.session.wait_for_session(timeout_ms=int(wait_timeout_ms))
        if status["logged_in"]:
            return {"logged_in": True, "mode": "manual",
                    "note": "手动登录成功", "url": status["url"],
                    "token_len": status["token_len"]}
        return {"logged_in": False, "mode": "manual",
                "reason": "等待超时：未检测到登录成功，请在浏览器窗口完成登录后重试。",
                "url": status["url"]}

    def list_courses(self) -> list[dict]:
        return [
            {
                "course_id": c.id,
                "name": c.name,
                "teacher": c.teacher,
                "start": utils.format_ts(c.start_ms),
                "end": utils.format_ts(c.end_ms),
            }
            for c in self.client.list_my_courses()
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
        if not targets:
            targets = self.client.list_my_courses()
        rows: list[dict] = []
        for course in targets:
            for task in self.client.list_homeworks(course.id):
                self._register(task)
                if refresh_page_state and task.status is HomeworkStatus.NOT_SUBMITTED:
                    try:
                        page = self.operator.open_homework(task)
                        status, progress = self.operator.read_page_state(page)
                        if status is not None:
                            task.page_status = status
                        if progress is not None:
                            task.page_progress = progress
                    except AuthExpiredError:
                        raise
                    except Exception as exc:  # keep listing alive
                        task.raw["page_error"] = str(exc)
                rows.append({
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
                })
        return rows

    def list_experiments(self, course_id: str) -> list[dict]:
        course = next(
            (c for c in self.client.list_my_courses() if c.id == course_id), None
        )
        if course is None:
            raise UplusError(f"未找到课程 {course_id}，请先确认课程 ID")
        experiments = self.operator.read_experiments(course)
        return [
            {
                "name": e.name,
                "submitted": e.submitted,
                "deadline_ms": e.deadline_ms,
                "deadline": utils.deadline_label(e.deadline_ms),
                "material": e.material,
            }
            for e in experiments
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

    def upload_attachment(self, task_id: str, course_id: str, file_path: str) -> dict:
        task = self.resolve_task(task_id, course_id)
        return self.operator.upload_attachment(task, file_path)

    # ------------------------------------------------------------------ #
    # patrol
    # ------------------------------------------------------------------ #
    def patrol(self, *, auto_answer: bool = False, submit: bool = False) -> dict:
        snapshot = report_mod.PatrolReport(generated_at_ms=utils.now_ms())
        answer_briefs: list[dict] = []
        auto_submissions: list[dict] = []

        for course in self._iter_courses():
            for task in self.client.list_homeworks(course.id):
                self._register(task)
                if task.status is HomeworkStatus.NOT_SUBMITTED:
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
                experiments = self.operator.read_experiments(course)
                for exp in experiments:
                    snapshot.experiments.append(report_mod.ExperimentRow(course.name, exp))
                    if exp.submitted is False:
                        snapshot.takeover.append({
                            "course": course.name, "title": exp.name,
                            "detail": f"实验未提交，提交物：{exp.material or '实验报告/成果物'}",
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
            "answer_briefs": answer_briefs,
            "auto_submissions": auto_submissions,
            "takeover": snapshot.takeover,
            "report_path": str(report_path),
        }
