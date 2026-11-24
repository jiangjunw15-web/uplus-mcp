"""跨账号同题号答案复用（本地答案簿）的离线测试。

The platform assigns ``question_id`` per question, so a question bank answered
on one account shares ids with another account's copy. These tests pin that
reuse stays a *hint*: it is applied only when the caller has no answer, the
caller's answer always wins, disabled by ``reuse=False``, and never overrides
the graded/unjudged audit rules.
"""

from __future__ import annotations

import json
from unittest.mock import Mock

from uplus_mcp import journal
from uplus_mcp.config import Config
from uplus_mcp.engine import PatrolEngine
from uplus_mcp.models import Course, HomeworkStatus, HomeworkTask, Option, Question, QuestionType


TASK = HomeworkTask(
    task_id="t-new", homework_id="h-new", course_id="c1",
    name="作业3", end_ms=4102444800000, submit_task="0",
)


def Q(order, qtype, answer=None, answered=False):
    return Question(
        question_id=f"q{order}", order=order, type=qtype, stem=f"第{order}题",
        options=[Option(code="A", content="甲"), Option(code="B", content="乙"),
                 Option(code="C", content="丙"), Option(code="D", content="丁")],
        my_answer=answer, answered=answered,
    )


def _cfg(tmp_path):
    return Config(report_dir=str(tmp_path), courses=[Course(id="c1", name="示例课程")])


def _seed(tmp_path, payload):
    path = journal.journal_path(_cfg(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _engine(tmp_path):
    client = Mock()
    client.list_homeworks.return_value = [TASK]
    operator = Mock()
    operator.open_homework.return_value = Mock()
    operator.read_page_state.return_value = (HomeworkStatus.SUBMITTED_PENDING, None)
    operator.answer_homework.return_value = {"submitted": True, "answered": 2}
    operator.withdraw_homework.return_value = "withdrawn"
    return PatrolEngine(_cfg(tmp_path), Mock(), client, operator), operator


def test_journal_path_env_override(tmp_path, monkeypatch):
    """A shared journal file is what makes cross-account reuse visible when
    every account keeps its own working directory."""
    shared = tmp_path / "shared" / "answers.json"
    monkeypatch.setenv("UPLUS_JOURNAL_PATH", str(shared))
    cfg = _cfg(tmp_path)
    assert journal.journal_path(cfg) == shared
    monkeypatch.delenv("UPLUS_JOURNAL_PATH")
    assert journal.journal_path(cfg) == cfg.report_folder / journal.FILENAME


# --------------------------------------------------------------------------- #
# journal index
# --------------------------------------------------------------------------- #

def test_answer_index_newest_wins_and_skips_blank_answers(tmp_path):
    _seed(tmp_path, {
        "t-old": {"homework_id": "h-old", "name": "作业1", "recorded_at_ms": 100,
                  "answers": {"q1": "A", "q2": "true", "q3": "  "}},
        "t-mid": {"homework_id": "h-mid", "name": "作业3", "recorded_at_ms": 200,
                  "answers": {"q1": "ACD", "q2": "false"}},
    })
    index = journal.answer_index(_cfg(tmp_path))
    assert index["q1"]["answer"] == "ACD"
    assert index["q1"]["task_id"] == "t-mid"
    assert index["q2"]["answer"] == "false"
    assert "q3" not in index  # blank answers are never recorded
    assert journal.answers_by_question_id(_cfg(tmp_path), ["q2"]) == {"q2": "false"}
    assert journal.answers_by_question_id(_cfg(tmp_path))["q1"] == "ACD"


# --------------------------------------------------------------------------- #
# answer_homework
# --------------------------------------------------------------------------- #

def test_answer_homework_fills_blank_questions_from_another_account(tmp_path):
    _seed(tmp_path, {
        "t-old": {"homework_id": "h-old", "name": "作业3", "recorded_at_ms": 200,
                  "answers": {"q1": "ACD", "q2": "A"}},
    })
    engine, operator = _engine(tmp_path)
    engine._homework_question_details = Mock(return_value=[
        Q(1, QuestionType.MULTIPLE),                       # blank -> reused
        Q(2, QuestionType.SINGLE, answer="B", answered=True),  # caller's answer wins
    ])
    result = engine.answer_homework("t-new", "c1", {"q2": "B"}, submit=True)

    sent = operator.answer_homework.call_args[0][1]
    assert sent == {"q1": "ACD", "q2": "B"}
    assert result["reused_questions"][0]["question_id"] == "q1"
    assert result["reused_questions"][0]["source_task_id"] == "t-old"
    assert "复用" in result["reuse_note"]


def test_answer_homework_reuse_can_be_disabled(tmp_path):
    _seed(tmp_path, {
        "t-old": {"homework_id": "h-old", "name": "作业3", "recorded_at_ms": 200,
                  "answers": {"q1": "ACD"}},
    })
    engine, operator = _engine(tmp_path)
    engine._homework_question_details = Mock(return_value=[Q(1, QuestionType.MULTIPLE)])
    result = engine.answer_homework("t-new", "c1", {"q2": "B"}, submit=False, reuse=False)

    sent = operator.answer_homework.call_args[0][1]
    assert sent == {"q2": "B"}
    assert "reused_questions" not in result


def test_answer_homework_order_keyed_caller_answer_blocks_reuse(tmp_path):
    """An answer keyed by question order must not be duplicated as an id key."""
    _seed(tmp_path, {
        "t-old": {"homework_id": "h-old", "name": "作业3", "recorded_at_ms": 200,
                  "answers": {"q3": "ACD"}},
    })
    engine, operator = _engine(tmp_path)
    engine._homework_question_details = Mock(return_value=[Q(3, QuestionType.MULTIPLE)])
    result = engine.answer_homework("t-new", "c1", {"3": "AB"}, submit=False)

    sent = operator.answer_homework.call_args[0][1]
    assert sent == {"3": "AB"}
    assert "reused_questions" not in result


def test_answer_homework_reuse_failure_does_not_block_caller_answers(tmp_path):
    engine, operator = _engine(tmp_path)
    engine._homework_question_details = Mock(side_effect=RuntimeError("接口读取失败"))
    result = engine.answer_homework("t-new", "c1", {"q1": "A"}, submit=False)

    sent = operator.answer_homework.call_args[0][1]
    assert sent == {"q1": "A"}
    assert "跨账号答案复用未生效" in result["reuse_note"]


# --------------------------------------------------------------------------- #
# repair_homework
# --------------------------------------------------------------------------- #

def test_repair_homework_falls_back_to_cross_account_answers(tmp_path):
    _seed(tmp_path, {
        "t-old": {"homework_id": "h-old", "name": "作业3", "recorded_at_ms": 200,
                  "answers": {"q1": "ACD", "q2": "true"}},
    })
    engine, operator = _engine(tmp_path)
    engine._homework_question_details = Mock(return_value=[
        Q(1, QuestionType.MULTIPLE),
        Q(2, QuestionType.JUDGE),
    ])
    result = engine.repair_homework("t-new", "c1")

    assert result["withdrawn"] is True
    assert result["submitted"] is True
    assert not result["needs_answers"]
    assert {item["question_id"] for item in result["reused_questions"]} == {"q1", "q2"}
    sent = operator.answer_homework.call_args[0][1]
    assert sent == {"q1": "ACD", "q2": "true"}


def test_repair_homework_reuse_off_requires_answers(tmp_path):
    _seed(tmp_path, {
        "t-old": {"homework_id": "h-old", "name": "作业3", "recorded_at_ms": 200,
                  "answers": {"q1": "ACD"}},
    })
    engine, operator = _engine(tmp_path)
    engine._homework_question_details = Mock(return_value=[Q(1, QuestionType.MULTIPLE)])
    result = engine.repair_homework("t-new", "c1", reuse=False)

    operator.withdraw_homework.assert_not_called()
    assert result["withdrawn"] is False
    assert result["needs_answers"]
