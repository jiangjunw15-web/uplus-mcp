"""Plain orchestration layer shared by the MCP server and the CLI patrol script.

Keeping business flow here means both entrypoints behave identically; the MCP
tools in ``server.py`` are thin wrappers around an ``PatrolEngine`` instance.
"""

from __future__ import annotations

from . import report as report_mod
from .browser import BrowserSession, AuthExpiredError, UplusError
from .config import Config
from .models import HomeworkStatus, HomeworkTask, QuestionType
from .operator import HomeworkOperator
from .platform import PlatformClient
from . import journal, solver, utils


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
            # Auto fill never waits the full manual timeout: a failed submit
            # should fall back to a human quickly, so the wait is capped.
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
                               full_page: bool = False) -> dict:
        """Screenshot one question (figure questions included).

        Read-only against the platform: it opens the answer page, navigates to
        the question by stem and saves a PNG of that question's block. Nothing
        is filled or submitted.
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
            full_page=full_page)
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
            for task in self.client.list_homeworks(course.id):
                self._register(task)
                # The page button is the authoritative status. Read it for
                # open homeworks as before, and for audited submitted ones so
                # a graded homework is not misreported as withdrawable.
                should_read_page = task.status is HomeworkStatus.NOT_SUBMITTED or (
                    audit and task.status in (
                        HomeworkStatus.SUBMITTED_PENDING,
                        HomeworkStatus.SUBMITTED_GRADED,
                    )
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
