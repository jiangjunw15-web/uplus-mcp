"""Regression tests for the five audited defects.

Covers: profile-copy deletion guard, password echo hygiene, submit-gate
counter balance, patrol auto_answer/submit wiring, and the unknown-course
query scope.
"""

from unittest.mock import Mock

import pytest

import uplus_mcp.solver as solver_mod
from uplus_mcp.browse import BrowseTools
from uplus_mcp.config import Config
from uplus_mcp.engine import PatrolEngine
from uplus_mcp.browser import UplusError
from uplus_mcp.launcher import _copy_profile
from uplus_mcp.models import Course, HomeworkTask, Question, QuestionType
from uplus_mcp.operator import HomeworkOperator


# --------------------------------------------------------------------------- #
# P1: profile copy deletion guard
# --------------------------------------------------------------------------- #
def test_copy_profile_refuses_same_directory(tmp_path):
    with pytest.raises(RuntimeError, match="重叠"):
        _copy_profile(tmp_path, tmp_path)


def test_copy_profile_refuses_target_that_is_source_parent(tmp_path):
    src = tmp_path / "real-browser" / "user data"
    src.mkdir(parents=True)
    (src / "Cookies").write_text("real cookies", encoding="utf-8")
    with pytest.raises(RuntimeError, match="重叠"):
        _copy_profile(src, tmp_path)
    assert (src / "Cookies").exists()


def test_copy_profile_refuses_deleting_a_non_profile_target(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "Cookies").write_text("x", encoding="utf-8")
    dst = tmp_path / "my-documents"
    dst.mkdir()
    (dst / "precious.txt").write_text("重要文件", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Local State"):
        _copy_profile(src, dst)
    assert (dst / "precious.txt").exists()


def test_copy_profile_refreshes_a_verified_profile_copy(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "Cookies").write_text("new", encoding="utf-8")
    dst = tmp_path / "uplus-browser-profile"
    dst.mkdir()
    (dst / "Local State").write_text("{}", encoding="utf-8")
    (dst / "stale-file.txt").write_text("old", encoding="utf-8")
    _copy_profile(src, dst)
    assert (dst / "Cookies").read_text(encoding="utf-8") == "new"
    assert not (dst / "stale-file.txt").exists()


def test_copy_profile_creates_missing_target(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "Cookies").write_text("x", encoding="utf-8")
    dst = tmp_path / "fresh"
    _copy_profile(src, dst)
    assert (dst / "Cookies").exists()


# --------------------------------------------------------------------------- #
# P2: password echo hygiene in single-step fill
# --------------------------------------------------------------------------- #
class _FillLocator:
    def __init__(self, password=False):
        self._password = password

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def scroll_into_view_if_needed(self, timeout=5000):
        return None

    def fill(self, value, timeout=8000):
        return None

    def press(self, key):
        return None

    def evaluate(self, expression):
        return self._password


def _browse(page):
    session = Mock()
    session.page = page
    return BrowseTools(session)


class _FillPage:
    url = "https://example.com"
    keyboard = Mock()

    def __init__(self, locator):
        self._locator = locator

    def locator(self, selector):
        return self._locator

    def title(self):
        return "t"


def test_fill_masks_password_field_value():
    page = _FillPage(_FillLocator(password=True))
    result = _browse(page).fill("input[type=password]", "secret-pass")
    assert result["value"] == "••••"
    assert "secret-pass" not in str(result)


def test_fill_returns_plain_value_for_normal_fields():
    page = _FillPage(_FillLocator(password=False))
    result = _browse(page).fill("input[name=q]", "hello")
    assert result["value"] == "hello"


# --------------------------------------------------------------------------- #
# P3: submit-gate counter balance
# --------------------------------------------------------------------------- #
def _question(qid, order, qtype_code, answered=False, my_answer=None):
    return Question(
        question_id=qid, order=order, type=QuestionType(qtype_code),
        stem="题干", answered=answered, my_answer=my_answer,
    )


class _OpLocator:
    def __init__(self, page, on_click=None):
        self._page = page
        self._on_click = on_click

    def count(self):
        return 1

    @property
    def first(self):
        return self

    def nth(self, index):
        return self

    def scroll_into_view_if_needed(self, timeout=None):
        return None

    def click(self, timeout=None):
        if self._on_click:
            self._on_click()


class _OpPage:
    """Fake answer page: body text flips from 提交作业 to 撤回作业 on submit."""

    def __init__(self):
        self.url = "https://www.eduplus.net/course/workAnswer/c1/t1/true"
        self.keyboard = Mock()
        self._body = "答题卡 (0/2) 提交作业"

    def _confirm(self):
        self._body = "撤回作业"

    def wait_for_load_state(self, state="load", timeout=None):
        return None

    def inner_text(self, selector="body"):
        return self._body

    def locator(self, selector):
        return _OpLocator(self)

    def get_by_role(self, role, name=None):
        return _OpLocator(self, on_click=self._confirm)

    def get_by_text(self, text, exact=False):
        return _OpLocator(self)


def _operator(questions, page=None):
    session = Mock()
    page = page or _OpPage()
    session.open.return_value = page
    session.url_for.return_value = page.url
    client = Mock()
    client.list_questions.return_value = list(questions)
    client.question_detail.side_effect = lambda qid: next(
        q for q in questions if q.question_id == qid)
    op = HomeworkOperator(session, client, Config())
    return op, page


def _task():
    return HomeworkTask(task_id="t1", homework_id="h1", course_id="c1",
                        name="作业", end_ms=None)


def test_answer_homework_refuses_submit_when_question_list_is_empty():
    op, page = _operator([])
    outcome = op.answer_homework(_task(), {"1": "A"}, submit=True)
    assert outcome["can_submit"] is False
    assert "题目列表为空" in outcome["note"]
    assert outcome["submitted"] is False
    assert "提交作业" in page.inner_text()  # submit button was never pressed


def test_answer_homework_counts_previously_answered_objectives_in_total():
    # q1 already answered on the platform; q2/q3 must be filled now.
    questions = [
        _question("q1", 1, QuestionType.SINGLE, answered=True, my_answer="A"),
        _question("q2", 2, QuestionType.SINGLE),
        _question("q3", 3, QuestionType.SINGLE),
    ]
    op, page = _operator(questions)
    outcome = op.answer_homework(_task(), {"q2": "B", "q3": "C"}, submit=True)
    assert outcome["objective_total"] == 3
    assert outcome["objective_answered"] == 3
    assert outcome["can_submit"] is True
    assert outcome["submitted"] is True


def test_answer_homework_still_refuses_when_subjective_questions_remain():
    questions = [
        _question("q1", 1, QuestionType.SINGLE),
        _question("q2", 2, QuestionType.SHORT_ANSWER),
    ]
    op, page = _operator(questions)
    outcome = op.answer_homework(_task(), {"q1": "A"}, submit=True)
    assert outcome["skipped"] == 1
    assert outcome["can_submit"] is False
    assert outcome["submitted"] is False


def test_answer_homework_fresh_all_answered_still_submits():
    questions = [_question("q1", 1, QuestionType.SINGLE),
                 _question("q2", 2, QuestionType.SINGLE)]
    op, page = _operator(questions)
    outcome = op.answer_homework(_task(), {"q1": "A", "q2": "B"}, submit=True)
    assert outcome["can_submit"] is True
    assert outcome["submitted"] is True


# --------------------------------------------------------------------------- #
# P4: patrol auto_answer/submit wiring
# --------------------------------------------------------------------------- #
def _patrol_engine(tmp_path, questions):
    client = Mock()
    client.list_my_courses.return_value = [
        Course(id="c1", name="课程", end_ms=4102444800000)]  # year 2100
    client.list_homeworks.return_value = [
        HomeworkTask(task_id="t1", homework_id="h1", course_id="c1",
                     name="作业", end_ms=4102444800000, question_count=1)]
    client.list_questions.return_value = list(questions)
    client.question_detail.side_effect = lambda qid: next(
        q for q in questions if q.question_id == qid)
    operator = Mock()
    operator.read_page_state.return_value = (None, None)
    operator.read_experiments.return_value = []
    operator.open_homework.return_value = Mock()
    operator.answer_homework.return_value = {
        "task_id": "t1", "submitted": True, "note": "提交成功，待老师批改"}
    cfg = Config(report_dir=str(tmp_path))
    return PatrolEngine(cfg, Mock(), client, operator), operator


def test_patrol_default_never_answers_or_submits(tmp_path):
    questions = [_question("q1", 1, QuestionType.SINGLE)]
    engine, operator = _patrol_engine(tmp_path, questions)
    result = engine.patrol(auto_answer=False, submit=False)
    operator.answer_homework.assert_not_called()
    assert result["auto_submissions"] == []
    assert len(result["answer_briefs"]) == 1


def test_patrol_submit_without_rule_answers_stays_hands_off(tmp_path):
    # auto_answer+submit are wired, but the bundled rule hook returns None,
    # so no homework may reach the operator without a verified answer.
    questions = [_question("q1", 1, QuestionType.SINGLE)]
    engine, operator = _patrol_engine(tmp_path, questions)
    result = engine.patrol(auto_answer=True, submit=True)
    operator.answer_homework.assert_not_called()
    assert result["auto_submissions"] == []


def test_patrol_submit_runs_when_rules_cover_all_objectives(tmp_path, monkeypatch):
    questions = [_question("q1", 1, QuestionType.SINGLE)]
    engine, operator = _patrol_engine(tmp_path, questions)
    monkeypatch.setattr(solver_mod, "rule_based_answer", lambda q: "A")
    result = engine.patrol(auto_answer=True, submit=True)
    operator.answer_homework.assert_called_once()
    args, kwargs = operator.answer_homework.call_args
    assert args[1] == {"q1": "A"}
    assert kwargs["submit"] is True
    assert result["auto_submissions"][0]["submitted"] is True


def test_patrol_submit_skips_partial_rule_coverage(tmp_path, monkeypatch):
    questions = [_question("q1", 1, QuestionType.SINGLE),
                 _question("q2", 2, QuestionType.SINGLE)]
    engine, operator = _patrol_engine(tmp_path, questions)
    monkeypatch.setattr(solver_mod, "rule_based_answer",
                        lambda q: "A" if q.question_id == "q1" else None)
    result = engine.patrol(auto_answer=True, submit=True)
    operator.answer_homework.assert_not_called()
    assert result["auto_submissions"] == []


# --------------------------------------------------------------------------- #
# P5: unknown course id must not widen the query
# --------------------------------------------------------------------------- #
def test_list_homeworks_unknown_course_id_raises_instead_of_scanning_all():
    client = Mock()
    client.list_my_courses.return_value = [Course(id="c1", name="课")]
    engine = PatrolEngine(Config(), Mock(), client, Mock())
    with pytest.raises(UplusError, match="未找到课程 no-such-id"):
        engine.list_homeworks("no-such-id")
    client.list_homeworks.assert_not_called()


def test_list_homeworks_expired_course_id_raises_under_default_filter():
    client = Mock()
    client.list_my_courses.return_value = [
        Course(id="old", name="旧课", end_ms=1000)]  # long expired
    engine = PatrolEngine(Config(), Mock(), client, Mock())
    with pytest.raises(UplusError, match="include_expired_courses"):
        engine.list_homeworks("old")
    client.list_homeworks.assert_not_called()


def test_list_homeworks_without_course_id_still_lists_all_active():
    client = Mock()
    client.list_my_courses.return_value = [Course(id="c1", name="课")]
    client.list_homeworks.return_value = []
    engine = PatrolEngine(Config(), Mock(), client, Mock())
    rows = engine.list_homeworks("")
    client.list_homeworks.assert_called_once_with("c1")
    assert rows == []
