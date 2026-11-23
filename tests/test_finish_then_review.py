"""Two-phase homework workflow: finish every outstanding homework, then review.

``finish_then_review`` separates the "answer everything" and "check once"
concerns that ``patrol`` interleaves per task. These tests pin the two-phase
contract: phase 1 answers every not-submitted, non-expired homework in the
fixed course-major order; phase 2 re-audits the whole account afterwards and
reports what is still incomplete. Expired and already-submitted tasks are
never answered in phase 1, and the review verdict reflects the post-fill state.
"""

from __future__ import annotations

from unittest.mock import Mock

from uplus_mcp.config import Config
from uplus_mcp.engine import PatrolEngine
from uplus_mcp.models import Course, HomeworkTask


def _task(task_id: str, name: str, end_ms, submit_task=None,
          course_id="c1") -> HomeworkTask:
    return HomeworkTask(
        task_id=task_id,
        homework_id="h-" + task_id,
        course_id=course_id,
        name=name,
        end_ms=end_ms,
        submit_task=submit_task,
    )


def _question(qid: str, order: int, *, qtype: str = "单选题",
              answered: bool = False) -> dict:
    """A bare question dict shaped like ``solver.build_answer_brief`` output."""
    return {
        "question_id": qid,
        "order": order,
        "type": qtype,
        "stem": f"第{order}题",
        "answered": answered,
    }


def _finish_engine(tmp_path, *, tasks=None, audit_complete=True,
                   answers_per_task=None):
    """Build an engine whose inspect/audit/answer calls are fully mocked.

    ``tasks`` is the homework list returned for course c1. ``audit_complete``
    drives the phase-2 audit result for every task. ``answers_per_task`` lets a
    test assert the answers handed to ``answer_homework`` per task_id.
    """
    course = Course(id="c1", name="课程一")
    cfg = Config(report_dir=str(tmp_path), courses=[course])
    client = Mock()
    client.list_my_courses.return_value = [course]
    client.list_homeworks.return_value = tasks or []
    client.list_questions.return_value = []

    operator = Mock()
    operator.open_homework.return_value = Mock()
    operator.read_page_state.return_value = (None, None)
    operator.read_experiments.return_value = []
    # Phase 1 fills return a minimal successful outcome.
    operator.answer_homework.return_value = {
        "answered": 1, "objective_total": 1, "objective_answered": 1,
        "submitted": True, "skipped": 0,
        "skipped_questions": [], "errors": [], "note": "已提交",
    }

    engine = PatrolEngine(cfg, Mock(), client, operator)

    # inspect_homework drives the brief; audit_homework drives phase 2.
    def fake_inspect(task_id, course_id):
        return {
            "course_id": course_id, "task_id": task_id,
            "brief": {"questions": [_question("q1", 1)]},
        }

    def fake_audit(task_id, course_id, refresh_page_state=True):
        return {
            "course_id": course_id, "task_id": task_id,
            "status": "not_submitted", "status_label": "未提交",
            "deadline": "", "withdrawable": False,
            "audit": {"complete": audit_complete, "missing": [],
                      "partial": [], "wrong": [], "unjudged": []},
        }

    engine.inspect_homework = Mock(side_effect=fake_inspect)
    engine.audit_homework = Mock(side_effect=fake_audit)
    engine.capture_question_image = Mock(
        return_value={"path": "reports/shot.png"})
    return engine, client, operator


def test_phase1_answers_only_outstanding_homeworks_in_order(tmp_path):
    """Expired and already-submitted tasks are skipped; the rest are answered
    in due-date + title order."""
    future = 4102444800000  # year 2100
    tasks = [
        _task("late", "晚交作业", future + 2000),
        _task("early", "早交作业", future + 1000),
        _task("done", "已交作业", future + 1500, submit_task="1"),
        _task("expired", "过期作业", 1),  # end_ms in the past -> expired
    ]
    engine, client, operator = _finish_engine(tmp_path, tasks=tasks)
    # Every outstanding task carries an answer, so all of them reach the fill.
    answers = {"early": {"q1": "A"}, "late": {"q1": "B"}}

    result = engine.finish_then_review(answers, submit=True)

    # Only the two outstanding, non-expired tasks reached the answer phase.
    answered = [f["task_id"] for f in result["finish"]]
    assert answered == ["early", "late"]
    assert result["summary"]["finish_attempted"] == 2
    assert result["summary"]["finish_submitted"] == 2


def test_phase2_reviews_after_finish_and_reports_incomplete(tmp_path):
    """The review runs over every task after phase 1, and an incomplete audit
    surfaces in summary.review_incomplete."""
    tasks = [_task("t1", "作业一", 4102444800000)]
    engine, _, _ = _finish_engine(
        tmp_path, tasks=tasks, audit_complete=False)

    result = engine.finish_then_review()

    assert result["summary"]["review_total"] == 1
    assert result["summary"]["review_incomplete"] == 1
    assert result["review"][0]["complete"] is False
    # The takeover carries the review phase tag so the caller can tell which
    # phase surfaced it.
    phases = {t["phase"] for t in result["takeover"]}
    assert "review" in phases


def test_screenshot_captures_incomplete_homework_pages(tmp_path):
    """``screenshot=True`` captures the answer page of each still-incomplete
    homework so the model can see the unfilled boxes."""
    tasks = [_task("t1", "作业一", 4102444800000)]
    engine, _, _ = _finish_engine(
        tmp_path, tasks=tasks, audit_complete=False)

    result = engine.finish_then_review(screenshot=True)

    assert result["review"][0]["screenshot"] == "reports/shot.png"
    engine.capture_question_image.assert_called_once()


def test_subjective_questions_go_to_takeover_not_answered(tmp_path):
    """A homework with subjective items is reported under takeover (finish
    phase) and never sent to answer_homework."""
    course = Course(id="c1", name="课程一")
    cfg = Config(report_dir=str(tmp_path), courses=[course])
    client = Mock()
    client.list_my_courses.return_value = [course]
    client.list_homeworks.return_value = [_task("t1", "作业一", 4102444800000)]
    client.list_questions.return_value = []
    operator = Mock()
    operator.open_homework.return_value = Mock()
    operator.read_page_state.return_value = (None, None)
    operator.read_experiments.return_value = []
    operator.answer_homework.return_value = {
        "answered": 0, "objective_total": 0, "objective_answered": 0,
        "submitted": False, "skipped": 0, "skipped_questions": [],
        "errors": [], "note": "",
    }
    engine = PatrolEngine(cfg, Mock(), client, operator)
    engine.inspect_homework = Mock(return_value={
        "brief": {"questions": [
            _question("q1", 1, qtype="编程题"),
            _question("q2", 2, qtype="简答题"),
        ]},
    })
    engine.audit_homework = Mock(return_value={
        "status": "not_submitted", "status_label": "未提交",
        "deadline": "", "withdrawable": False,
        "audit": {"complete": True, "missing": [], "partial": [],
                  "wrong": [], "unjudged": []},
    })
    engine.capture_question_image = Mock(return_value={"path": ""})

    result = engine.finish_then_review()

    # Subjective homework lands in takeover under the finish phase; the answer
    # operator is not called for a homework with only subjective items.
    finish_takeover = [t for t in result["takeover"]
                       if t.get("phase") == "finish"]
    assert any("主观" in t["detail"] or "编程" in t["detail"]
               for t in finish_takeover)
    operator.answer_homework.assert_not_called()


def test_finish_phase_does_not_abort_on_one_failure(tmp_path):
    """A failed answer for one homework must not stop the rest of phase 1."""
    future = 4102444800000
    tasks = [
        _task("t1", "作业一", future + 1000),
        _task("t2", "作业二", future + 2000),
    ]
    engine, client, operator = _finish_engine(tmp_path, tasks=tasks)
    operator.answer_homework.side_effect = [
        RuntimeError("network blip"),
        {"answered": 1, "objective_total": 1, "objective_answered": 1,
         "submitted": True, "skipped": 0, "skipped_questions": [],
         "errors": [], "note": "已提交"},
    ]
    answers = {"t1": {"q1": "A"}, "t2": {"q1": "B"}}

    result = engine.finish_then_review(answers)

    # Both tasks were attempted; the failure is reported, the second succeeded.
    assert result["summary"]["finish_attempted"] == 2
    assert result["summary"]["finish_submitted"] == 1
    failure = next(t for t in result["takeover"]
                   if t.get("phase") == "finish" and "作答失败" in t["detail"])
    assert failure["title"] == "作业一"


def test_missing_answers_skip_page_open_and_report_needs(tmp_path):
    """A homework with blank objective questions but no supplied answers is
    reported with the questions it needs — and answer_homework is never called,
    so no page is opened and no question detail is fetched for nothing."""
    tasks = [_task("t1", "作业一", 4102444800000)]
    engine, client, operator = _finish_engine(tmp_path, tasks=tasks)

    result = engine.finish_then_review()  # no answers at all

    operator.answer_homework.assert_not_called()
    entry = result["finish"][0]
    assert entry["objective_total"] == 1
    assert [n["order"] for n in entry["needs_answers"]] == [1]
    # The still-needed questions are surfaced on the takeover list too, so the
    # caller can answer them and re-run without a second inspection.
    finish_takeover = [t for t in result["takeover"] if t.get("phase") == "finish"]
    assert any("needs_answers" in t for t in finish_takeover)
