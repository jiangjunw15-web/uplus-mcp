"""Tests for the v0.8.14 fixes.

Covers:
1. ``_repair_items`` rewrites the unjudged reason for a graded homework so the
   per-item reason no longer contradicts the brief-level note.
2. ``build_answer_brief`` sorts questions by order regardless of API order.
3. ``_homework_row`` exposes ``api_says_submitted`` / ``page_says_submitted``
   / ``state_conflicts`` so list_homeworks surfaces the reconciliation.
4. ``read_page_state`` drops a 0/0 progress as unusable.
5. ``HomeworkTask.from_api`` reads the score from multiple candidate fields.
6. ``add_homework`` populates ``state_conflicts`` on the row.
"""

from __future__ import annotations

from unittest.mock import Mock

from uplus_mcp.config import Config
from uplus_mcp.engine import PatrolEngine
from uplus_mcp.models import (
    Course,
    HomeworkStatus,
    HomeworkTask,
    Option,
    Question,
    QuestionType,
)
from uplus_mcp.report import PatrolReport, add_homework, render_markdown
from uplus_mcp.solver import build_answer_brief


def Q(order, qtype, answer=None, answered=False, stem="题干",
      reference=None):
    return Question(
        question_id=f"q{order}",
        order=order,
        type=qtype,
        stem=stem,
        options=[Option(code="A", content="甲"), Option(code="B", content="乙"),
                 Option(code="C", content="丙"), Option(code="D", content="丁")],
        my_answer=answer,
        answered=answered,
        reference_answer=reference,
    )


# --------------------------------------------------------------------------- #
# Fix 2: graded homework's unjudged reason does not say "待批改"
# --------------------------------------------------------------------------- #
def test_repair_items_graded_homework_rewrites_unjudged_reason():
    """A graded homework's unjudged item must not say '待批改'."""
    questions = [Q(1, QuestionType.MULTIPLE, "A", True)]
    # Simulate an unjudged hit (no reference answer, no journal).
    audit_result = {
        "missing": [],
        "partial": [],
        "wrong": [],
        "unjudged": [{"order": 1, "type": "多选题", "stem": "题干",
                       "reason": "平台未公布参考答案（待批改），本地也没有提交时的答案记录，无法核对是否漏选"}],
        "unjudged_count": 1,
        "complete": False,
    }
    items = PatrolEngine._repair_items(
        questions, audit_result, {}, graded=True)
    assert items[0]["optional"] is True
    assert "待批改" not in items[0]["reason"]
    assert "已出分" in items[0]["reason"]


def test_repair_items_pending_homework_keeps_unjudged_reason():
    """A pending homework's unjudged reason should keep the original text."""
    questions = [Q(1, QuestionType.MULTIPLE, "A", True)]
    audit_result = {
        "missing": [],
        "partial": [],
        "wrong": [],
        "unjudged": [{"order": 1, "type": "多选题", "stem": "题干",
                       "reason": "平台未公布参考答案（待批改），无法核对是否漏选"}],
        "unjudged_count": 1,
        "complete": False,
    }
    items = PatrolEngine._repair_items(
        questions, audit_result, {}, graded=False)
    assert "待批改" in items[0]["reason"]


# --------------------------------------------------------------------------- #
# Fix 5c: build_answer_brief sorts questions by order
# --------------------------------------------------------------------------- #
def test_build_answer_brief_sorts_by_order():
    """Questions arriving in API order 3,2,1 should be brief-ordered 1,2,3."""
    questions = [
        Q(3, QuestionType.SINGLE, stem="第三题"),
        Q(1, QuestionType.SINGLE, stem="第一题"),
        Q(2, QuestionType.SINGLE, stem="第二题"),
    ]
    brief = build_answer_brief("测试作业", questions)
    orders = [q["order"] for q in brief["questions"]]
    assert orders == [1, 2, 3]


def test_build_answer_brief_already_sorted_stays_sorted():
    questions = [
        Q(1, QuestionType.SINGLE),
        Q(2, QuestionType.SINGLE),
        Q(3, QuestionType.SINGLE),
    ]
    brief = build_answer_brief("测试", questions)
    orders = [q["order"] for q in brief["questions"]]
    assert orders == [1, 2, 3]


# --------------------------------------------------------------------------- #
# Fix 3: _homework_row exposes reconciliation fields
# --------------------------------------------------------------------------- #
def test_homework_row_exposes_state_conflict_fields():
    """list_homeworks rows carry api_says_submitted / page_says_submitted / state_conflicts."""
    course = Course(id="c1", name="课程一")
    task = HomeworkTask(
        task_id="t1", homework_id="h1", course_id="c1", name="作业一",
        submit_task="0",  # API says not submitted
    )
    task.raw["_api_status_value"] = HomeworkStatus.NOT_SUBMITTED.value
    # Page says submitted (the button shows 撤回作业).
    task.page_status = HomeworkStatus.SUBMITTED_PENDING
    row = PatrolEngine._homework_row(course, task)
    assert row["api_says_submitted"] is False
    assert row["page_says_submitted"] is True
    assert len(row["state_conflicts"]) == 1
    assert "接口显示未提交" in row["state_conflicts"][0]


def test_homework_row_no_conflict_when_states_agree():
    course = Course(id="c1", name="课程一")
    task = HomeworkTask(
        task_id="t1", homework_id="h1", course_id="c1", name="作业一",
        submit_task="1",
    )
    task.raw["_api_status_value"] = HomeworkStatus.SUBMITTED_PENDING.value
    task.page_status = HomeworkStatus.SUBMITTED_PENDING
    row = PatrolEngine._homework_row(course, task)
    assert row["api_says_submitted"] is True
    assert row["page_says_submitted"] is True
    assert row["state_conflicts"] == []


# --------------------------------------------------------------------------- #
# Fix 5d: 0/0 progress is dropped
# --------------------------------------------------------------------------- #
def test_read_page_state_drops_zero_zero_progress():
    """A 0/0 progress means the answer card did not load; report None."""
    from uplus_mcp.operator import HomeworkOperator
    page = Mock()
    # Body text with a 0/0 answer card and a submit button.
    page.inner_text.return_value = "答题卡(0/0)\n提交作业"
    operator = HomeworkOperator.__new__(HomeworkOperator)
    status, progress = operator.read_page_state(page)
    assert progress is None


def test_read_page_state_keeps_real_progress():
    from uplus_mcp.operator import HomeworkOperator
    page = Mock()
    page.inner_text.return_value = "答题卡(32/32)\n撤回作业"
    operator = HomeworkOperator.__new__(HomeworkOperator)
    status, progress = operator.read_page_state(page)
    assert progress == "32/32"


# --------------------------------------------------------------------------- #
# Fix 4: from_api reads score from multiple candidate fields
# --------------------------------------------------------------------------- #
def test_from_api_reads_score_from_dto_user_score():
    raw = {
        "id": "t1", "homeworkId": "h1", "endTime": None,
        "homeworkDTO": {"name": "作业一", "userScore": 96},
    }
    task = HomeworkTask.from_api(raw, "c1")
    assert task.score == 96


def test_from_api_reads_score_from_top_level():
    raw = {
        "id": "t1", "homeworkId": "h1", "endTime": None,
        "homeworkDTO": {"name": "作业一"},
        "userScore": 97,
    }
    task = HomeworkTask.from_api(raw, "c1")
    assert task.score == 97


def test_from_api_reads_score_from_score_field():
    raw = {
        "id": "t1", "homeworkId": "h1", "endTime": None,
        "homeworkDTO": {"name": "作业一", "score": 88},
    }
    task = HomeworkTask.from_api(raw, "c1")
    assert task.score == 88


def test_from_api_score_none_when_absent():
    raw = {
        "id": "t1", "homeworkId": "h1", "endTime": None,
        "homeworkDTO": {"name": "作业一"},
    }
    task = HomeworkTask.from_api(raw, "c1")
    assert task.score is None


# --------------------------------------------------------------------------- #
# Fix 3: add_homework populates state_conflicts on the report row
# --------------------------------------------------------------------------- #
def test_add_homework_populates_state_conflicts(tmp_path):
    report = PatrolReport(generated_at_ms=0)
    course = Course(id="c1", name="课程一")
    task = HomeworkTask(
        task_id="t1", homework_id="h1", course_id="c1", name="作业一",
        submit_task="0",
    )
    task.raw["_api_status_value"] = HomeworkStatus.NOT_SUBMITTED.value
    task.page_status = HomeworkStatus.SUBMITTED_PENDING
    add_homework(report, course, task)
    assert len(report.rows) == 1
    assert len(report.rows[0].state_conflicts) == 1


def test_render_markdown_includes_conflict_section():
    report = PatrolReport(generated_at_ms=0)
    course = Course(id="c1", name="课程一")
    task = HomeworkTask(
        task_id="t1", homework_id="h1", course_id="c1", name="作业一",
        submit_task="0",
    )
    task.raw["_api_status_value"] = HomeworkStatus.NOT_SUBMITTED.value
    task.page_status = HomeworkStatus.SUBMITTED_PENDING
    add_homework(report, course, task)
    markdown = render_markdown(report)
    assert "页面状态与接口状态差异" in markdown
    assert "接口显示未提交" in markdown
