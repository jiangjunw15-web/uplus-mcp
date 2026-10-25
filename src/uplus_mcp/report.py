"""Patrol snapshot aggregation and Markdown report rendering."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import Config
from .models import Course, Experiment, HomeworkStatus, HomeworkTask
from . import utils


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

    # ---- takeover list ----
    if report.takeover:
        lines.append("## 需要人工接管")
        lines.append("")
        for item in report.takeover:
            lines.append(f"- **{item.get('course')} / {item.get('title')}**：{item.get('detail')}")
            if item.get("deadline"):
                lines.append(f"  - 截止：{item['deadline']}")
        lines.append("")

    lines.append("> 客观题由巡检流程自动作答；附件上传、实验报告、小组分工简答与编程题需本人完成。")
    return "\n".join(lines)


def persist(markdown: str, config: Config) -> Path:
    folder = config.report_folder
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(tz=utils.CN_TZ).strftime("%Y%m%d_%H%M")
    path = folder / f"patrol_{stamp}.md"
    path.write_text(markdown, encoding="utf-8")
    return path
