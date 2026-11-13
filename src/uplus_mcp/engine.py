"""Plain orchestration layer shared by the MCP server and the CLI patrol script.

Keeping business flow here means both entrypoints behave identically; the MCP
tools in ``server.py`` are thin wrappers around an ``PatrolEngine`` instance.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import report as report_mod
from .browser import BrowserSession, AuthExpiredError, UplusError
from .config import Config
from .experiment import ExperimentDetail, ExperimentOperator
from .models import HomeworkStatus, HomeworkTask, QuestionType
from .operator import HomeworkOperator
from .platform import PlatformClient
from . import journal, report_generator, solver, utils


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
                # Both ids are required by every follow-up experiment tool;
                # without them the caller cannot build the detail route.
                "exp_id": e.exp_id,
                "stu_exp_id": e.stu_exp_id,
            }
            for e in experiments
        ]

    # ------------------------------------------------------------------ #
    # experiments: inspect / answer / audit
    # ------------------------------------------------------------------ #
    def _experiment_operator(self) -> ExperimentOperator:
        return ExperimentOperator(self.config, self.session)

    def _student_identity(self, student_name: str = "",
                          student_id: str = "") -> tuple[str, str]:
        """Per-call identity overrides the configured one.

        The configured values live in the gitignored personal config only;
        they are never written to the patrol report or the repo.
        """
        name = (student_name or self.config.student_name or "").strip()
        number = (student_id or self.config.student_id or "").strip()
        return name, number

    @staticmethod
    def _experiment_brief(detail: ExperimentDetail,
                          requirements: str) -> dict:
        """Work out which sections the server fills and which the model must.

        The server copies what the handbook itself states (目的 / 内容 /
        步骤 / 要求); the model supplies the work that cannot be copied —
        the implementation and the analysis. Nothing is invented here.
        """
        derived = report_generator.derive_sections(detail.manual_text or "")
        auto_sections = {
            name: text for name, text in derived.items()
            if name in report_generator.MANUAL_DERIVABLE_SECTIONS
        }
        needed: list[str] = []
        for name in report_generator.MODEL_REQUIRED_SECTIONS:
            if not (auto_sections.get(name) or "").strip():
                needed.append(name)
        return {
            "auto_sections": auto_sections,
            "needed_sections": needed,
            "requirements": requirements,
        }

    def inspect_experiment(self, course_id: str, exp_id: str,
                           stu_exp_id: str, find_template: bool = True) -> dict:
        """Read an experiment and produce the authoring brief.

        The brief is the experiment analogue of ``inspect_homework``: it
        carries the requirements, the downloaded template (searched across
        实验附件 → 实验手册 → 资料, preferring whichever yields a downloadable
        file), and the exact sections the model must write. Nothing is filled,
        uploaded or submitted.
        """
        op = self._experiment_operator()
        detail = op.read_detail(course_id, exp_id, stu_exp_id)
        requirements = op.collect_requirements(detail)
        brief = self._experiment_brief(detail, requirements)
        auto_sections = brief["auto_sections"]
        needed = list(brief["needed_sections"])

        template: dict | None = None
        template_error = ""
        template_description: dict | None = None
        if find_template:
            try:
                template = op.find_report_template(detail)
            except AuthExpiredError:
                raise
            except Exception as exc:  # noqa: BLE001 - 资料 is best-effort
                template_error = str(exc)
            if template and template.get("path"):
                try:
                    template_description = report_generator.describe_template(
                        Path(template["path"]))
                except Exception as exc:  # noqa: BLE001
                    template_error = f"模板解析失败：{exc}"
        if template_description:
            for name in template_description.get("recognised", []):
                canonical = report_generator.canonical_section(name) or name
                if canonical not in auto_sections and canonical not in needed:
                    needed.append(canonical)

        return {
            "course_id": course_id,
            "course_name": self.course_name(course_id),
            "exp_id": exp_id,
            "stu_exp_id": stu_exp_id,
            "name": detail.name,
            "status": detail.status,
            "environment": detail.environment,
            "material": detail.material,
            "deadline": detail.deadline,
            "deadline_label": utils.deadline_label(detail.deadline_ms),
            "submitted_files": detail.submitted_files,
            "attachments": [
                {"name": a.name, "href": a.href, "kind": a.kind}
                for a in detail.attachments
            ],
            "manual_links": [
                {"name": a.name, "href": a.href}
                for a in detail.manual_links
            ],
            "requirements": requirements[:6000],
            "manual_text": (detail.manual_text or "")[:8000],
            "template": template,
            "template_source": (template or {}).get("source", ""),
            "template_error": template_error,
            "template_description": template_description,
            "auto_sections": auto_sections,
            "needed_sections": needed,
            "instructions": (
                "服务端已把实验手册里能直接引用的章节放进 auto_sections（实验目的 / "
                "实验内容 / 实验步骤 / 实验要求），这些会原样填入、不再改写。"
                "请只针对 needed_sections 逐节写出真实内容（实验实现：伪代码与代码；"
                "实验结果：测试结果与复杂度分析；实验总结：关键问题与难点）。"
                "内容必须依据实验要求与附件作答，不要编造与实验无关的内容。"
                "完成后返回 JSON：{\"sections\": {\"实验实现\": \"...\", "
                "\"实验结果\": \"...\", \"实验总结\": \"...\"}}，再调用 "
                "uplus_answer_experiment 完成填充与提交。"
            ),
        }

    def answer_experiment(self, course_id: str, exp_id: str, stu_exp_id: str,
                          sections: dict[str, str] | str,
                          submit: bool = True, student_name: str = "",
                          student_id: str = "",
                          template_path: str = "",
                          force: bool = False) -> dict:
        """Fill the experiment report template, verify it, and submit it.

        The gate mirrors ``answer_homework``: a report is only uploaded when
        every model-required section is present and the read-only
        verification passes (non-empty body, no leftover placeholders, topic
        matches the experiment, compliance checklist clean). A failure
        returns the reasons and submits nothing, so an empty, off-template or
        unverifiable report can never be handed in.

        ``force=True`` keeps the generated file but downgrades the compliance
        checklist to warnings — use it only when the user has decided to
        submit a report that knowingly misses the teacher template.
        """
        op = self._experiment_operator()
        detail = op.read_detail(course_id, exp_id, stu_exp_id)
        supplied = _parse_sections(sections)
        requirements = op.collect_requirements(detail)
        brief = self._experiment_brief(detail, requirements)
        auto_sections = brief["auto_sections"]
        # The caller may override any server-derived section, but the server
        # never overwrites the caller.
        merged: dict[str, str] = dict(auto_sections)
        merged.update(supplied)

        missing = [
            name for name in report_generator.MODEL_REQUIRED_SECTIONS
            if not (merged.get(name) or "").strip()
        ]
        result: dict = {
            "course_id": course_id,
            "course_name": self.course_name(course_id),
            "exp_id": exp_id,
            "stu_exp_id": stu_exp_id,
            "name": detail.name,
            "status": detail.status,
            "auto_sections": sorted(auto_sections),
            "sections": sorted(merged),
            "missing_sections": missing,
            "generated": False,
            "report_path": "",
            "verification": None,
            "submitted": False,
            "can_submit": False,
            "submission": None,
            "note": "",
        }
        if missing:
            result["note"] = (
                "以下章节缺少内容，未生成、未提交（服务端不编造内容）："
                + "、".join(missing)
            )
            return result

        template: dict | None = None
        if template_path:
            safe_template = utils.resolve_io_path(
                template_path, self.config.io_roots, must_exist=True)
            template = {"name": safe_template.name, "path": str(safe_template)}
        else:
            try:
                template = op.find_report_template(detail)
            except AuthExpiredError:
                raise
            except Exception as exc:  # noqa: BLE001 - fall back to generator
                result["note"] = f"资料模板检索失败（{exc}），改用内置报告格式。"
        result["template"] = template

        name, number = self._student_identity(student_name, student_id)
        out_dir = self.config.report_folder / "experiment_reports"
        stem = _safe_stem(detail.name or f"experiment_{exp_id}")
        output = out_dir / f"{stem}.docx"

        try:
            fill_info = self._build_report(
                detail, merged, template, output, name, number)
        except UplusError:
            raise
        except Exception as exc:  # noqa: BLE001
            result["note"] = f"报告生成失败：{exc}"
            return result
        result["generated"] = True
        result["report_path"] = str(output)
        result["fill"] = fill_info

        verification = op.verify_report(
            output, detail,
            expected_sections=list(report_generator.MODEL_REQUIRED_SECTIONS),
            strict_compliance=not force)
        result["verification"] = verification
        result["compliance"] = (verification.get("checks") or {}).get("compliance")
        if not verification["ok"]:
            result["note"] = (
                "报告校验未通过，已拒绝提交：" + verification["reason"]
            )
            return result
        result["can_submit"] = True
        if detail.status == "已提交":
            result["can_submit"] = False
            result["note"] = (
                "该实验在平台上已是「已提交」状态，未重复提交；"
                "如需覆盖，请先撤回再重交。"
            )
            return result
        if not submit:
            result["note"] = "报告已生成并通过校验，按要求未提交（submit=False）。"
            return result

        try:
            submission = op.submit_report(course_id, exp_id, stu_exp_id, output)
        except AuthExpiredError:
            raise
        except Exception as exc:  # noqa: BLE001 - report, never fake success
            result["note"] = f"提交失败：{exc}（报告已保留在本地，可人工复核）"
            return result
        result["submission"] = submission
        result["submitted"] = True
        result["note"] = "报告已填充、校验通过并提交成功（页面已确认）。"
        return result

    def _build_report(self, detail: ExperimentDetail, sections: dict[str, str],
                      template: dict | None, output: Path,
                      student_name: str, student_id: str) -> dict:
        """Fill a downloaded template, or generate then fill a skeleton.

        A template that cannot be opened (a legacy ``.doc`` renamed to
        ``.docx``, a corrupt download, an unsupported layout) falls back to
        the built-in format rather than failing the whole run; the reason is
        reported in ``template_fallback`` so the caller knows why.
        """
        output.parent.mkdir(parents=True, exist_ok=True)
        fallback_reason = ""
        if template and template.get("path"):
            try:
                return report_generator.fill_template(
                    Path(template["path"]), sections, output,
                    student_name=student_name, student_id=student_id)
            except Exception as exc:  # noqa: BLE001 - fall back, do not abort
                fallback_reason = f"模板无法使用（{exc}），已改用内置报告格式"
        # No usable template: build a skeleton whose sections are empty slots,
        # then run the same filler so both paths share verification behaviour.
        skeleton = output.with_name(output.stem + "_skeleton.docx")
        report_generator.generate_report(
            title=detail.name or "实验报告",
            purpose="",
            content="",
            steps=[],
            requirements=[],
            output_path=skeleton,
        )
        try:
            info = report_generator.fill_template(
                skeleton, sections, output,
                student_name=student_name, student_id=student_id)
        finally:
            skeleton.unlink(missing_ok=True)
        if fallback_reason:
            info["template_fallback"] = fallback_reason
        return info

    def audit_experiment(self, course_id: str, exp_id: str, stu_exp_id: str,
                         download: bool = True) -> dict:
        """Read-only completeness audit for one submitted experiment.

        Pulls the submitted report back (when a download link exists) and
        runs the same verification the submit gate uses: empty body, leftover
        placeholders, and a report that talks about a different experiment.
        When the file cannot be downloaded, it falls back to the page's own
        record and says so — an unchecked report is never reported as clean.
        """
        op = self._experiment_operator()
        detail = op.read_detail(course_id, exp_id, stu_exp_id)
        result: dict = {
            "course_id": course_id,
            "course_name": self.course_name(course_id),
            "exp_id": exp_id,
            "stu_exp_id": stu_exp_id,
            "name": detail.name,
            "status": detail.status,
            "deadline": detail.deadline,
            "submitted_files": detail.submitted_files,
            "auditable": False,
            "verification": None,
            "issues": [],
            "note": "",
        }
        submitted = detail.status == "已提交" or bool(detail.submitted_files)
        if not submitted:
            result["note"] = (
                "实验尚未提交，无需巡检；如需作答请先调用 uplus_inspect_experiment"
            )
            return result
        if not detail.submitted_files:
            result["issues"].append("页面未显示任何已提交文件，疑似空提交")
        link = next((item for item in detail.submitted_links if item.href), None)
        if not download or link is None:
            result["note"] = (
                "未取得已提交文件的下载链接，只核对了页面显示信息；"
                "如平台支持下载，请人工打开实验提交物确认内容"
            )
            return result
        try:
            filename = link.name or "submitted"
            if not re.search(r"\.[A-Za-z0-9]{1,5}$", filename):
                filename += ".docx"
            dest = (self.config.report_folder / "experiment_audit"
                    / _safe_stem(filename))
            path = self.session.download_to(link.href, dest)
        except AuthExpiredError:
            raise
        except Exception as exc:  # noqa: BLE001 - audit is best-effort
            result["note"] = f"已提交文件下载失败：{exc}"
            return result
        result["auditable"] = True
        result["downloaded"] = str(path)
        verification = op.verify_report(path, detail)
        result["verification"] = verification
        if not verification["ok"]:
            result["issues"].append(verification["reason"])
        else:
            result["note"] = "已回读已提交报告，未发现空报告、占位符或错交实验。"
        return result

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
                errors.append(f"实验附件 {attachment.name}：{exc}")
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
                errors.append(f"实验手册 {link.name}：{exc}")
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
                errors.append(f"资料 {entry.name}：{exc}")
                continue
            downloaded.append({"name": entry.name, "path": str(path),
                               "source": "资料"})

        # ---- pick the best template across all downloaded files ----
        # The template search already checked 实验附件 → 实验手册 → 资料 in
        # priority order via find_report_template; reuse its pick so the
        # template field is consistent with what answer_experiment would use.
        try:
            template = op.find_report_template(detail)
        except AuthExpiredError:
            raise
        except Exception as exc:  # noqa: BLE001
            errors.append(f"模板选择：{exc}")

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
        experiment_briefs: list[dict] = []
        experiment_audits: list[dict] = []

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
                    can_reach = bool(exp.exp_id and exp.stu_exp_id)
                    if exp.submitted is False:
                        detail_text = (
                            f"实验未提交，提交物：{exp.material or '实验报告/成果物'}"
                        )
                        if can_reach and not utils.is_expired(exp.deadline_ms):
                            # Produce the TODO brief the model answers before
                            # calling uplus_answer_experiment. The patrol does
                            # NOT download the template or submit anything: it
                            # only lists what still needs writing.
                            try:
                                op = self._experiment_operator()
                                exp_detail = op.read_detail(
                                    course.id, exp.exp_id, exp.stu_exp_id)
                                requirements = op.collect_requirements(exp_detail)
                                brief = self._experiment_brief(
                                    exp_detail, requirements)
                                experiment_briefs.append({
                                    "course_id": course.id,
                                    "course_name": course.name,
                                    "exp_id": exp.exp_id,
                                    "stu_exp_id": exp.stu_exp_id,
                                    "name": exp_detail.name or exp.name,
                                    "material": exp_detail.material,
                                    "deadline": utils.deadline_label(
                                        exp.deadline_ms),
                                    "attachments": [
                                        {"name": a.name, "href": a.href}
                                        for a in exp_detail.attachments
                                    ],
                                    "auto_sections": brief["auto_sections"],
                                    "needed_sections": brief["needed_sections"],
                                    "requirements": requirements[:4000],
                                    "note": (
                                        "为 needed_sections 写出真实内容后调用 "
                                        "uplus_answer_experiment(course_id, exp_id, "
                                        "stu_exp_id, sections) 填充、校验并提交；"
                                        "auto_sections 由服务端原样填入。"
                                    ),
                                })
                                if brief["needed_sections"]:
                                    detail_text += (
                                        "；待写章节：" +
                                        "、".join(brief["needed_sections"])
                                    )
                            except AuthExpiredError:
                                raise
                            except Exception as exc:  # noqa: BLE001
                                detail_text += f"；读取实验详情失败：{exc}"
                        snapshot.takeover.append({
                            "course": course.name, "title": exp.name,
                            "detail": detail_text,
                            "deadline": utils.deadline_label(exp.deadline_ms),
                        })
                    elif exp.submitted is None:
                        snapshot.takeover.append({
                            "course": course.name, "title": exp.name,
                            "detail": "实验状态未知，需本人进入实验栏目人工复核",
                            "deadline": utils.deadline_label(exp.deadline_ms),
                        })
                    elif audit and can_reach:
                        # Submitted: run the read-only completeness audit so a
                        # blank or wrong report is surfaced instead of being
                        # silently trusted.
                        try:
                            audit_result = self.audit_experiment(
                                course.id, exp.exp_id, exp.stu_exp_id)
                        except AuthExpiredError:
                            raise
                        except Exception as exc:  # noqa: BLE001
                            snapshot.takeover.append({
                                "course": course.name, "title": exp.name,
                                "detail": f"实验提交完整性核查失败：{exc}",
                                "deadline": utils.deadline_label(exp.deadline_ms),
                            })
                        else:
                            experiment_audits.append(audit_result)
                            if audit_result["issues"]:
                                snapshot.takeover.append({
                                    "course": course.name,
                                    "title": audit_result["name"] or exp.name,
                                    "detail": "已提交实验存在问题：" + "；".join(
                                        audit_result["issues"]),
                                    "deadline": utils.deadline_label(exp.deadline_ms),
                                })
            except AuthExpiredError:
                raise
            except Exception as exc:
                snapshot.takeover.append({
                    "course": course.name, "title": "实验栏目",
                    "detail": f"读取失败：{exc}",
                })

        snapshot.experiment_audits = experiment_audits
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
            "experiment_briefs": experiment_briefs,
            "experiment_audits": experiment_audits,
            "takeover": snapshot.takeover,
            "report_path": str(report_path),
        }


# --------------------------------------------------------------------------- #
# experiment helpers (module-level, used by the engine methods above)
# --------------------------------------------------------------------------- #
def _parse_sections(raw: dict[str, str] | str | None) -> dict[str, str]:
    """Normalize a model response into ``{section_name: text}``.

    Accepts ``{"sections": {...}}``, a bare ``{...}`` map, or a JSON string
    (optionally inside a Markdown fence), mirroring
    ``solver.parse_model_answers`` for the experiment side.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        data = raw
    else:
        text = str(raw).strip()
        if not text:
            return {}
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
        if fenced:
            text = fenced.group(1)
        else:
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end > start:
                text = text[start:end + 1]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {}
    if not isinstance(data, dict):
        return {}
    sections = data.get("sections", data)
    if not isinstance(sections, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in sections.items()
        if value is not None and str(value).strip()
    }


def _safe_stem(name: str, fallback: str = "experiment") -> str:
    """A safe local file name for a report / downloaded material."""
    cleaned = re.sub(r'[<>:"/\|?*\x00-\x1f]', "_", name or "").strip().strip(".")
    return cleaned[:120] or fallback
