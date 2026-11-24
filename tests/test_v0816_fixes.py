"""Regression tests for the 2026-11-24 live-read findings (v0.8.16).

The tests use no personal account data and perform no writes to the platform.
They pin page/API reconciliation, login readiness, explicit progress semantics,
and the sign-out receipt without weakening the graded/unjudged audit rules.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from uplus_mcp.browser import BrowserSession
from uplus_mcp.config import Config
from uplus_mcp.engine import PatrolEngine
from uplus_mcp.models import Course, HomeworkStatus, HomeworkTask
from uplus_mcp.operator import HomeworkOperator, extract_page_progress


def _task(task_id: str, *, end_ms: int = 4102444800000) -> HomeworkTask:
    return HomeworkTask(
        task_id=task_id, homework_id="h-" + task_id, course_id="c1",
        name="作业 " + task_id, end_ms=end_ms, submit_task="0",
    )


def _engine(tmp_path, tasks: list[HomeworkTask]):
    course = Course(id="c1", name="示例课程")
    client = Mock()
    client.list_my_courses.return_value = [course]
    client.list_homeworks.return_value = tasks
    client.list_questions.return_value = []
    operator = Mock()
    operator.open_homework.return_value = Mock()
    operator.read_page_state.return_value = (
        HomeworkStatus.SUBMITTED_PENDING, "10/10")
    operator.read_experiments.return_value = []
    return PatrolEngine(
        Config(report_dir=str(tmp_path), courses=[course]),
        Mock(), client, operator), operator


@pytest.mark.parametrize("method,kwargs", [
    ("list_homeworks", {"course_id": "c1", "refresh_page_state": True}),
    ("scan_course", {"course_id": "c1", "refresh_page_state": True}),
    ("patrol", {"audit": False}),
])
def test_every_page_refresh_corrects_api_underreporting(tmp_path, method, kwargs):
    """A non-expired API-unsubmitted task is reconciled with the page button;
    the answer/submit paths remain closed by the deadline gate."""
    engine, operator = _engine(tmp_path, [_task("open")])
    result = getattr(engine, method)(**kwargs)
    operator.open_homework.assert_called_once()
    if method == "patrol":
        assert result["summary"]["homework_submitted"] == 1
        assert result["summary"]["homework_outstanding"] == 0
    else:
        row = result["homeworks"][0] if method == "scan_course" else result[0]
        assert row["status"] == "submitted_pending"
        assert row["api_says_submitted"] is False
        assert row["page_says_submitted"] is True
        assert row["state_conflicts"]


def test_outstanding_phase_refreshes_before_deciding_to_answer(tmp_path):
    """An API-only finish phase used to treat an already-submitted task as
    outstanding. The page button must be read before any answer is attempted."""
    engine, operator = _engine(tmp_path, [_task("t1")])
    engine._homework_question_details = Mock(return_value=[])
    engine.audit_homework = Mock(return_value={
        "status": "submitted_pending", "status_label": "待批改",
        "audit": {"complete": True, "missing": [], "partial": [],
                  "wrong": [], "unjudged": []},
    })
    result = engine.finish_then_review({"t1": {"q1": "A"}})
    operator.answer_homework.assert_not_called()
    assert result["summary"]["finish_attempted"] == 0
    assert result["review"][0]["status"] == "submitted_pending"


def test_progress_unknown_is_explicit_and_not_zero(tmp_path):
    """An unrendered answer card is not evidence of an unanswered homework."""
    engine, operator = _engine(tmp_path, [_task("t1")])
    operator.read_page_state.return_value = (HomeworkStatus.SUBMITTED_PENDING, None)
    row = engine.list_homeworks("c1", refresh_page_state=True)[0]
    assert row["progress"] is None
    assert row["progress_status"] == "unavailable"
    assert row["answered_count"] is None
    assert row["question_total_on_page"] is None


@pytest.mark.parametrize("text,expected", [
    ("答题卡 (10/10) 撤回作业", "10/10"),
    ("答题卡：10 / 10\n撤回作业", "10/10"),
    ("答题进度 3 / 10\n提交作业", "3/10"),
    ("已作答：3/10\n撤回作业", "3/10"),
    ("已完成 3 题，共 10 题\n提交作业", "3/10"),
    ("答题卡 (0/0) 提交作业", None),
    ("课程共 10 章，当前第 3 章", None),
])
def test_progress_layouts_and_unknown(text, expected):
    assert extract_page_progress(text) == expected


def test_read_page_state_reconciles_progress_from_dom():
    class _Page:
        def inner_text(self, selector="body"):
            return "撤回作业\n答题卡"

        def evaluate(self, script):
            assert "getAttribute" in script
            return {"answered": 30, "total": 30}

        def wait_for_load_state(self, *args, **kwargs):
            return None

    operator = HomeworkOperator.__new__(HomeworkOperator)
    status, progress = operator.read_page_state(_Page())
    assert status is HomeworkStatus.SUBMITTED_PENDING
    assert progress == "30/30"


def test_withdrawable_is_unknown_when_refresh_is_disabled(tmp_path):
    """Skipping the page must not claim that a submitted task can be withdrawn."""
    engine, operator = _engine(tmp_path, [_task("t1")])
    task = _task("t1")
    engine._task_index[task.task_id] = task
    engine._homework_question_details = Mock(return_value=[])
    result = engine.audit_homework("t1", "c1", refresh_page_state=False)
    assert result["withdrawable"] is None
    assert result["withdrawable_source"] == "unknown"
    assert result["page_says_submitted"] is None
    operator.open_homework.assert_not_called()


# --------------------------------------------------------------------------- #
# login readiness: token + URL + actual read, not one transient URL snapshot
# --------------------------------------------------------------------------- #
class _RacePage:
    url = "https://www.eduplus.net/workbench/index/school"

    def __init__(self):
        self.token_reads = 0

    def is_closed(self):
        return False

    def title(self):
        return "U+ 工作台"

    def evaluate(self, script, arg=None):
        self.token_reads += 1
        if isinstance(arg, str) and "/api/" in arg:
            return {"status": 200, "body": '{"data":[]}'}
        if "location.replace" in script or "workbench/index/school" in script:
            self.url = "https://www.eduplus.net/workbench/index/school"
            return None
        return "a-token-that-is-not-returned-in-the-test"

    def inner_text(self, selector="body"):
        return "课程 工作台"

    def open(self, path, **kwargs):
        self.url = "https://www.eduplus.net" + path
        return self


def _session(page):
    session = BrowserSession.__new__(BrowserSession)
    session.config = Config()
    session.base_url = "https://www.eduplus.net"
    session._page = page
    session._started = True
    session._token_probed = False
    session.open = page.open
    return session


def test_login_check_waits_for_redirect_back_from_login():
    page = _RacePage()
    session = _session(page)
    page.url = "https://uc.eduplus.net/login"
    # Token just landed; redirecting to the workbench resolves it before the
    # first verdict, instead of declaring the just-logged-in account expired.
    assert session.is_logged_in(timeout_ms=1500) is True


def test_login_check_returns_false_when_no_session_appears(monkeypatch):
    class _NoSession(_RacePage):
        def evaluate(self, script, arg=None):
            if "location.replace" in script:
                self.url = "https://www.eduplus.net/workbench/index/school"
            return ""

    page = _NoSession()
    page.url = "https://uc.eduplus.net/login"
    session = _session(page)
    monkeypatch.setattr("uplus_mcp.browser.time.sleep", lambda _: None)
    assert session.is_logged_in(timeout_ms=100) is False


def test_login_wait_sets_token_probe_for_immediate_followup():
    page = _RacePage()
    session = _session(page)
    assert session.wait_for_session(timeout_ms=100)["logged_in"] is True
    assert session._token_probed is True


def test_api_read_waits_for_a_token_that_lands_after_navigation():
    page = _RacePage()
    session = _session(page)
    # No token on the first probe; it appears when the workbench is reopened.
    def evaluate(script, arg=None):
        if isinstance(arg, str) and "/api/" in arg:
            return {"status": 200, "body": '{"data":[]}'}
        if "localStorage.getItem('AccessToken')" in script:
            page.token_reads += 1
            return "" if page.token_reads == 1 else "a-token"
        return "a-token"
    page.evaluate = evaluate
    opened = []
    session.open = lambda path: opened.append(path) or page
    result = session.api_get("/api/example")
    assert result == {"data": []}
    assert opened == []


# --------------------------------------------------------------------------- #
# explicit sign-out: a receipt, never a silent local-token deletion
# --------------------------------------------------------------------------- #
def test_logout_requires_confirmation():
    session = Mock()
    engine = PatrolEngine(Config(), session, Mock(), Mock())
    result = engine.logout(confirm=False)
    assert result["logged_out"] is False
    assert result["confirmation_required"] is True
    session.logout.assert_not_called()


def test_logout_clears_cached_tasks_only_after_platform_signout():
    session = Mock()
    session.logout.return_value = {
        "logged_out": True, "message": "已退出当前 U+ 账号", "url": "https://example.test/login",
    }
    client = Mock()
    engine = PatrolEngine(Config(), session, client, Mock())
    engine._task_index["t1"] = _task("t1")
    engine._course_names["c1"] = "示例课程"
    result = engine.logout(confirm=True)
    assert result["logged_out"] is True
    assert engine._task_index == {}
    assert engine._course_names == {}
    client.invalidate_courses.assert_called_once_with()
