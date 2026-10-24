import pytest

from uplus_mcp.models import Question, QuestionType
from uplus_mcp import solver


def _question(qtype: QuestionType, qid: str = "q1") -> Question:
    return Question(question_id=qid, order=1, type=qtype)


def test_parse_model_answers_nested_dict():
    out = solver.parse_model_answers({"answers": {"q1": "C", "q2": "true"}})
    assert out == {"q1": "C", "q2": "true"}


def test_parse_model_answers_flat_dict():
    out = solver.parse_model_answers({"q1": "ABD"})
    assert out == {"q1": "ABD"}


def test_parse_model_answers_fenced_json_string():
    text = '```json\n{"answers": {"q1": "false"}}\n```'
    assert solver.parse_model_answers(text) == {"q1": "false"}


def test_parse_model_answers_invalid_returns_empty():
    assert solver.parse_model_answers("not json") == {}
    assert solver.parse_model_answers(None) == {}


def test_normalize_single_choice():
    assert solver.normalize_answer(_question(QuestionType.SINGLE), "c. 操作系统") == "C"


def test_normalize_multiple_choice_sorts_and_dedupes():
    assert solver.normalize_answer(_question(QuestionType.MULTIPLE), "b, a, d") == "ABD"


def test_normalize_judge_true_false():
    assert solver.normalize_answer(_question(QuestionType.JUDGE), "正确") == "true"
    assert solver.normalize_answer(_question(QuestionType.JUDGE), "false") == "false"
    with pytest.raises(ValueError):
        solver.normalize_answer(_question(QuestionType.JUDGE), "maybe")


def test_normalize_fill_blank_keeps_text():
    assert solver.normalize_answer(_question(QuestionType.FILL_BLANK), "  进程  ") == "进程"


def test_rule_based_answer_defaults_to_none():
    assert solver.rule_based_answer(_question(QuestionType.SINGLE)) is None
