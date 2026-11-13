"""Patrol snapshot aggregation and Markdown report rendering."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import Config
from .models import (
    Course,
    Experiment,
    HomeworkStatus,
    HomeworkTask,
    Question,
    QuestionType,
)
from . import solver, utils


STATUS_LABEL = {
    HomeworkStatus.SUBMITTED_GRADED: "已出分",
    HomeworkStatus.SUBMITTED_PENDING: "已提交待批改",
    HomeworkStatus.NOT_SUBMITTED: "未提交",
    HomeworkStatus.NO_HOMEWORK: "未布置",
}


@dataclass
class HomeworkRow:
    course_name: str
    task_name: str
    status: HomeworkStatus
    deadline_ms: Optional[int]
    question_count: int
    score: Optional[float]
    note: str = ""


@dataclass
class ExperimentRow:
    course_name: str
    experiment: Experiment


@dataclass
class PatrolReport:
    generated_at_ms: int
    rows: list[HomeworkRow] = field(default_factory=list)
    experiments: list[ExperimentRow] = field(default_factory=list)
    takeover: list[dict] = field(default_factory=list)
    incomplete: list[dict] = field(default_factory=list)
    #: One entry per submitted experiment the optional audit inspected. Each
    #: entry is the dict returned by ``engine.audit_experiment``: empty
    #: reports, leftover placeholders and wrong-experiment submissions land in
    #: its ``issues`` list.
    experiment_audits: list[dict] = field(default_factory=list)
    #: How many submitted homeworks the optional audit inspected, and how many
    #: answered multi-select questions it could not verify because the platform
    #: had not published the reference answer yet (still awaiting grading).
    audit_checked: int = 0
    audit_unjudged: int = 0

    # ------------------------------------------------------------------ #
    # statistics
    # ------------------------------------------------------------------ #
    @property
    def submitted(self) -> int:
        return sum(
            1 for r in self.rows if r.status in (HomeworkStatus.SUBMITTED_GRADED, HomeworkStatus.SUBMITTED_PENDING)
        )

    @property
    def pending(self) -> int:
        return sum(1 for r in self.rows if r.status is HomeworkStatus.SUBMITTED_PENDING)

    @property
    def outstanding(self) -> int:
        return sum(1 for r in self.rows if r.status is HomeworkStatus.NOT_SUBMITTED)

    @property
    def submitted_experiments(self) -> int:
        return sum(1 for e in self.experiments if e.experiment.submitted is True)

    @property
    def outstanding_experiments(self) -> int:
        return sum(1 for e in self.experiments if e.experiment.submitted is False)

    @property
    def unknown_experiments(self) -> int:
        return sum(1 for e in self.experiments if e.experiment.submitted is None)


def add_homework(report: PatrolReport, course: Course, task: HomeworkTask, *, note: str = "") -> None:
    report.rows.append(
        HomeworkRow(
            course_name=course.name,
            task_name=task.name,
            status=task.status,
            deadline_ms=task.end_ms,
            question_count=task.question_count,
            score=task.score,
            note=note,
        )
    )


def _entry(question: Question, **extra) -> dict:
    entry = {
        "order": question.order,
        "type": question.type.label,
        "stem": question.stem[:80],
        "my_answer": question.my_answer,
    }
    entry.update(extra)
    return entry


def _normalized(question: Question, raw: Optional[str]) -> Optional[str]:
    """Normalize one answer the same way the answer path does; None if invalid."""
    if not raw:
        return None
    try:
        return solver.normalize_answer(question, raw)
    except (TypeError, ValueError):
        return None


def audit_questions(questions: list[Question],
                    expected_answers: Optional[dict[str, str]] = None) -> dict:
    """Read-only completeness check over one homework's questions.

    A question counts as answered only when it carries a non-empty
    ``userAnswer`` (``my_answer``). A bare ``hwAnswerId`` or ``isAnswer`` flag
    no longer suffices: the platform creates an answer row when the student
    merely opens the question, even with no option selected (see
    ``models._is_answered``). The audit needs no page interaction and never
    withdraws a submission. Subjective / programming / attachment items are
    reported separately and do not make the homework "incomplete".

    Multi-select answers are also checked against the platform's standard
    answer (``answer``) when it is available. That field is only sent after
    grading; an answer that is a *proper subset* of the standard answer is a
    "漏选" (``partial``), while letters outside it are ``wrong``. When the
    platform hides the answer (still awaiting grading), a locally recorded
    answer from :mod:`uplus_mcp.journal` is used instead, so homework submitted
    through this tool can be checked before it is graded. A multi-select with
    neither source is listed under ``unjudged`` instead of being silently
    reported as complete. ``complete`` therefore means "no blank question and
    no partial multi-select"; ``clean`` additionally requires every checked
    answer to be correct.
    """
    objective = [q for q in questions if q.auto_answerable]
    missing = [q for q in objective if not q.my_answer]
    partial: list[dict] = []
    wrong: list[dict] = []
    unjudged: list[dict] = []
    correct = 0
    reference_known = 0

    expected_answers = expected_answers or {}
    for q in objective:
        if not q.my_answer:
            continue
        journal_answer = (expected_answers.get(q.question_id) or "").strip()
        reference = (q.reference_answer or "").strip()
        reference_source = "platform" if reference else ""
        if not reference and q.type is QuestionType.MULTIPLE and journal_answer:
            reference = journal_answer
            reference_source = "journal"
        if reference:
            reference_known += 1
            if q.type is QuestionType.MULTIPLE:
                selected = set(utils.normalize_letters(q.my_answer))
                expected_letters = set(utils.normalize_letters(reference))
                if not expected_letters:
                    unjudged.append(_entry(
                        q, reason="标准答案无法解析为选项字母，未能核对漏选",
                        reference_answer=reference, reference_source=reference_source,
                    ))
                elif selected == expected_letters:
                    correct += 1
                elif selected and selected < expected_letters:
                    partial.append(_entry(
                        q,
                        reference_answer=reference,
                        reference_source=reference_source,
                        missing_letters="".join(sorted(expected_letters - selected)),
                    ))
                else:
                    extra = "".join(sorted(selected - expected_letters))
                    absent = "".join(sorted(expected_letters - selected))
                    reason = "多选了标准答案之外的选项"
                    if absent:
                        reason += f"，同时漏选 {absent}"
                    wrong.append(_entry(
                        q,
                        reference_answer=reference,
                        reference_source=reference_source,
                        extra_letters=extra,
                        missing_letters=absent,
                        reason=reason,
                    ))
            else:
                got = _normalized(q, q.my_answer)
                want = _normalized(q, reference)
                if got is None or want is None:
                    unjudged.append(_entry(
                        q, reason="答案或标准答案无法解析，未能核对",
                        reference_answer=reference, reference_source=reference_source,
                    ))
                elif got == want:
                    correct += 1
                else:
                    wrong.append(_entry(
                        q, reference_answer=reference,
                        reference_source=reference_source,
                        reason="与标准答案不一致",
                    ))
        elif q.is_correct is not None:
            # Graded, but the endpoint did not expose the answer text. The
            # boolean still tells right from wrong; it cannot tell a partial
            # multi-select from a wrongly selected one.
            reference_known += 1
            if q.is_correct:
                correct += 1
            else:
                reason = "判分不正确"
                if q.type is QuestionType.MULTIPLE:
                    reason += "（平台未给出标准答案，无法区分漏选与错选）"
                wrong.append(_entry(q, reason=reason))
        elif q.type is QuestionType.MULTIPLE:
            # Pending grading: the student-side detail carries no ``answer``,
            # and the answer page only shows "我的答案". A non-empty selection
            # can still be missing options, so it must not pass silently.
            unjudged.append(_entry(
                q, reason="平台未公布参考答案（待批改），本地也没有提交时的答案记录，"
                          "无法核对是否漏选",
            ))

    issue_count = len(missing) + len(partial)
    return {
        "total": len(questions),
        "objective": len(objective),
        "subjective": len(questions) - len(objective),
        "answered": len(objective) - len(missing),
        "missing": [
            {"order": q.order, "type": q.type.label, "stem": q.stem[:80]}
            for q in missing
        ],
        "partial": partial,
        "wrong": wrong,
        "unjudged": unjudged,
        "correct": correct,
        "reference_known": reference_known,
        "missing_count": len(missing),
        "partial_count": len(partial),
        "wrong_count": len(wrong),
        "unjudged_count": len(unjudged),
        "issue_count": issue_count,
        "complete": issue_count == 0,
        "clean": issue_count == 0 and not wrong,
        "verified": not unjudged,
    }


def _deadline_text(ms: Optional[int], now: int, warning_hours: int, outstanding: bool) -> str:
    label = utils.deadline_label(ms, now)
    if not outstanding or not ms or ms <= now:
        return label
    hours = (ms - now) / 3_600_000
    if hours < warning_hours:
        return f"{label} ⚠️ 临近截止"
    return label


def render_markdown(report: PatrolReport, *, warning_hours: int = 72) -> str:
    now = report.generated_at_ms
    ts = datetime.datetime.fromtimestamp(now / 1000, tz=utils.CN_TZ).strftime("%Y-%m-%d %H:%M")
    lines: list[str] = []
    lines.append(f"# U+ 平台巡检报告（{ts}）")
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    lines.append(f"- 作业：已提交 {report.submitted} 项（待批改 {report.pending} 项），未完成 {report.outstanding} 项")
    exp_line = (f"- 实验：已提交 {report.submitted_experiments} 项，未完成 {report.outstanding_experiments} 项")
    if report.unknown_experiments:
        exp_line += f"，状态未知 {report.unknown_experiments} 项"
    lines.append(exp_line)
    lines.append("")

    # ---- homework table ----
    lines.append("## 作业明细")
    lines.append("")
    lines.append("| 课程 | 作业 | 状态 | 截止 | 题量 | 得分 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for r in report.rows:
        deadline = _deadline_text(
            r.deadline_ms, now, warning_hours,
            outstanding=r.status is HomeworkStatus.NOT_SUBMITTED,
        )
        score = "-" if r.score is None else f"{r.score}"
        qcount = "-" if not r.question_count else str(r.question_count)
        lines.append(
            f"| {r.course_name} | {r.task_name} | {STATUS_LABEL[r.status]} | "
            f"{deadline} | {qcount} | {score} |"
        )
    lines.append("")

    # ---- experiments ----
    if report.experiments:
        lines.append("## 实验明细")
        lines.append("")
        lines.append("| 课程 | 实验 | 状态 | 截止 | 提交物 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for row in report.experiments:
            e = row.experiment
            if e.submitted is True:
                state = "已提交"
            elif e.submitted is False:
                state = "未提交"
            else:
                state = "状态未知"
            deadline = _deadline_text(
                e.deadline_ms, now, warning_hours, outstanding=e.submitted is False
            )
            lines.append(
                f"| {row.course_name} | {e.name} | {state} | "
                f"{deadline} | {e.material or '-'} |"
            )
        lines.append("")

    # ---- completeness audit ----
    if report.incomplete or report.audit_checked:
        lines.append("## 提交完整性核查")
        lines.append("")
        if report.incomplete:
            lines.append("| 课程 | 作业 | 漏答 | 多选漏选 | 题号 | 可否撤回 |")
            lines.append("| --- | --- | --- | --- | --- | --- |")
            for item in report.incomplete:
                orders = "、".join(str(n) for n in item.get("missing_orders", [])[:12])
                if len(item.get("missing_orders", [])) > 12:
                    orders += "…"
                partial_parts: list[str] = []
                for hit in item.get("partial", [])[:12]:
                    miss = hit.get("missing_letters")
                    partial_parts.append(
                        f"{hit.get('order')}" + (f"（缺{miss}）" if miss else "")
                    )
                if len(item.get("partial", [])) > 12:
                    partial_parts.append("…")
                if partial_parts:
                    orders = "；".join(
                        part for part in (orders, "漏选 " + "、".join(partial_parts)) if part
                    )
                withdrawable = "可撤回" if item.get("withdrawable") else "已出分不可撤回"
                lines.append(
                    f"| {item.get('course', '')} | {item.get('name', '')} | "
                    f"{item.get('missing_count', 0)} | {item.get('partial_count', 0)} | "
                    f"{orders or '-'} | {withdrawable} |"
                )
            lines.append("")
        else:
            lines.append("本次核查的已提交作业没有漏答或漏选。")
            lines.append("")
        if report.audit_unjudged:
            lines.append(
                f"> 另有 {report.audit_unjudged} 道多选题因平台尚未公布参考答案"
                "（待批改）无法核对是否漏选；批改后重新运行核查即可判定。"
            )
            lines.append("")

    # ---- experiment completeness audit ----
    if report.experiment_audits:
        lines.append("## 实验提交完整性核查")
        lines.append("")
        lines.append("| 课程 | 实验 | 状态 | 文件取回 | 核查结果 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for item in report.experiment_audits:
            fetched = "已回读" if item.get("auditable") else "仅页面"
            if item.get("issues"):
                outcome = "；".join(item["issues"])
            elif item.get("auditable"):
                outcome = "未发现空报告、残留占位符或错交实验"
            else:
                outcome = item.get("note") or "未能取回已提交文件"
            lines.append(
                f"| {item.get('course_name', '')} | {item.get('name', '')} | "
                f"{item.get('status', '')} | {fetched} | {outcome} |"
            )
        lines.append("")

    # ---- takeover list ----
    if report.takeover:
        lines.append("## 需要人工接管")
        lines.append("")
        for item in report.takeover:
            lines.append(f"- **{item.get('course')} / {item.get('title')}**：{item.get('detail')}")
            if item.get("deadline"):
                lines.append(f"  - 截止：{item['deadline']}")
        lines.append("")

    lines.append(
        "> 客观题由巡检流程自动作答；实验报告可用 uplus_inspect_experiment → "
        "uplus_answer_experiment 生成：服务端只引用实验手册原文，实验实现/结果分析/"
        "总结由模型产出，填充后必须通过空报告、占位符、错交实验三道校验才会提交；"
        "附件上传、小组分工简答与编程题仍需本人完成。"
    )
    return "\n".join(lines)


def persist(markdown: str, config: Config) -> Path:
    folder = config.report_folder
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(tz=utils.CN_TZ).strftime("%Y%m%d_%H%M")
    path = folder / f"patrol_{stamp}.md"
    path.write_text(markdown, encoding="utf-8")
    return path
