"""Regression tests for the defects the 0.8.19 test suite did not cover.

Every test here pins one reported defect:

1. an identifier's own underscores counted as answer boxes,
2. a value torn apart by a comma and written into the wrong box,
3. a homework graded with zero points reported as "not graded yet",
4. a corrupted answer journal silently wiping every recorded answer,
5. a config file with a typo crashing the MCP server without a hint,
6. a half-filled blank reported as a missing multi-select option,
plus the three small parsing gaps: full-width option letters, 「否」 as a
boolean and a page progress written as "12 / 32".
"""

import pytest

from uplus_mcp import journal, report as report_mod, solver, utils
from uplus_mcp.config import Config, ConfigError, load_config
from uplus_mcp.models import (
    HomeworkStatus,
    HomeworkTask,
    Question,
    QuestionType,
    count_blanks,
)


def _fill_blank(stem: str, answer: str = "", order: int = 1) -> Question:
    return Question(question_id=f"q{order}", order=order,
                    type=QuestionType.FILL_BLANK, stem=stem, my_answer=answer)


# --------------------------------------------------------------------------- #
# 1. an identifier's underscores are not answer boxes
# --------------------------------------------------------------------------- #
def test_count_blanks_ignores_the_underscores_of_an_identifier():
    """``__init__`` is a name, not two answer boxes."""
    assert count_blanks("Python 中__init__方法的作用是____") == 1
    assert count_blanks("__init__ 方法的返回值是____") == 1
    assert count_blanks("变量 my_var 的含义是____") == 1
    # Real markers still count, next to Chinese text and to Latin words.
    assert count_blanks("处理 GET 请求用____注解，处理 POST 请求用____注解。") == 2
    assert count_blanks("通用注解是＿＿＿＿。") == 1


def test_an_identifier_question_accepts_one_answer():
    """The question has one box, so one answer must be enough."""
    question = _fill_blank("Python 中__init__方法的作用是____")
    assert solver.normalize_answer(question, "None") == "None"


# --------------------------------------------------------------------------- #
# 2. a value with a comma is not torn apart
# --------------------------------------------------------------------------- #
def test_split_blanks_keeps_a_comma_inside_one_value():
    assert solver.split_blanks("String, Integer\nParis", 2) == [
        "String, Integer", "Paris",
    ]


def test_split_blanks_still_accepts_comma_separated_answers():
    """The fallback stays: a model that answers "@GetMapping,@PostMapping"
    for two boxes means two boxes."""
    assert solver.split_blanks("@GetMapping,@PostMapping", 2) == [
        "@GetMapping", "@PostMapping",
    ]


def test_split_blanks_does_not_over_split():
    """Three values for two boxes: keep them together, so the count
    mismatch is reported instead of a wrong box being written."""
    assert solver.split_blanks("A, B, C", 2) == ["A, B, C"]


def test_normalize_answer_reports_the_mismatch_instead_of_writing_wrong_boxes():
    question = _fill_blank("第一空____第二空____")
    with pytest.raises(ValueError):
        solver.normalize_answer(question, "A, B, C")


# --------------------------------------------------------------------------- #
# 3. a zero score is a score
# --------------------------------------------------------------------------- #
def test_homework_task_keeps_a_zero_score():
    raw = {"id": "t1", "homeworkId": "h1",
           "homeworkDTO": {"name": "作业", "userScore": 0}}
    task = HomeworkTask.from_api(raw, "c1")
    assert task.score == 0


def test_the_report_shows_zero_instead_of_a_dash():
    snapshot = report_mod.PatrolReport(
        generated_at_ms=0,
        rows=[report_mod.HomeworkRow(
            course_name="课程", task_name="作业",
            status=HomeworkStatus.SUBMITTED_GRADED, deadline_ms=None,
            question_count=3, score=0,
        )],
    )
    markdown = report_mod.render_markdown(snapshot)
    assert "| 0 |" in markdown


# --------------------------------------------------------------------------- #
# 4. a corrupted answer journal keeps its bytes
# --------------------------------------------------------------------------- #
def test_a_corrupt_journal_is_backed_up_instead_of_being_wiped(tmp_path):
    cfg = Config(report_dir=str(tmp_path))
    path = journal.journal_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"t-old": {"answers": {"q1": "A"}}}', encoding="utf-8")
    path.write_text("{broken", encoding="utf-8")

    notes: list[str] = []
    journal.record(cfg, task_id="t2", homework_id="h2", name="作业",
                   answers={"q2": "B"}, notes=notes)

    backups = list(tmp_path.glob("answer_journal.json.corrupt-*.bak"))
    assert backups, "the broken journal must be kept as a backup"
    assert "{broken" in backups[0].read_text(encoding="utf-8")
    # The new submission is recorded and usable.
    assert journal.answers_for(cfg, "t2", "h2") == {"q2": "B"}
    assert notes and "答案簿损坏" in notes[0]


# --------------------------------------------------------------------------- #
# 5. a broken config file names the file and the line
# --------------------------------------------------------------------------- #
def test_load_config_names_the_broken_line(tmp_path):
    cfg_file = tmp_path / "courses.json"
    cfg_file.write_text('{\n  "base_url": "https://x",\n}\n', encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(cfg_file))
    message = str(excinfo.value)
    assert "不是合法 JSON" in message
    assert "第 3 行" in message
    assert str(cfg_file) in message


def test_the_mcp_server_refuses_to_start_on_a_broken_config(
        tmp_path, monkeypatch, capsys):
    import uplus_mcp.server as server

    cfg_file = tmp_path / "courses.json"
    cfg_file.write_text("{oops", encoding="utf-8")
    monkeypatch.setenv("UPLUS_CONFIG", str(cfg_file))
    with pytest.raises(SystemExit) as excinfo:
        server.main()
    assert excinfo.value.code == 2
    assert "不是合法 JSON" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# 6. a half-filled blank is not a missing multi-select option
# --------------------------------------------------------------------------- #
def test_partial_details_tell_blank_gaps_from_missing_options():
    partial = [
        {"order": 3, "type": "多选题", "missing_letters": "D"},
        {"order": 5, "type": "填空题", "missing_blanks": 2},
    ]
    text = report_mod.format_partial_details(partial)
    assert "多选漏选 1 题" in text
    assert "填空漏填 1 题" in text
    assert "缺D" in text
    assert "缺2空" in text


def test_a_half_filled_blank_is_audited_as_a_blank_gap():
    question = _fill_blank("第一空____第二空____", answer="只有一个")
    result = report_mod.audit_questions([question])
    assert result["partial_count"] == 1
    assert result["partial"][0]["missing_blanks"] == 1
    assert result["partial"][0]["type"] == "填空题"


def test_the_patrol_cli_names_a_blank_gap():
    from uplus_mcp import daily_patrol

    item = {
        "course_name": "课程", "name": "作业",
        "missing": [],
        "partial": [{"order": 5, "type": "填空题", "missing_blanks": 2}],
    }
    text = daily_patrol.incomplete_details(item)
    assert "填空漏填 1 题" in text
    assert "多选漏选" not in text


def test_the_report_names_a_blank_gap_in_the_audit_table():
    snapshot = report_mod.PatrolReport(generated_at_ms=0)
    snapshot.incomplete.append({
        "course": "操作系统", "name": "第三章作业",
        "missing_count": 0, "missing_orders": [],
        "partial_count": 1,
        "partial": [{"order": 5, "type": "填空题", "missing_blanks": 2}],
        "withdrawable": False,
    })
    markdown = report_mod.render_markdown(snapshot)
    assert "填空漏填 1 题（题号 5（缺2空））" in markdown
    assert "多选漏选" not in markdown


# --------------------------------------------------------------------------- #
# the three small parsing gaps
# --------------------------------------------------------------------------- #
def test_full_width_option_letters_are_read():
    """A Chinese IME leaves the keyboard in full-width mode by accident."""
    assert utils.normalize_letters("ＡＣ") == "AC"
    assert utils.normalize_letters("Ａ Ｃ") == "AC"


def test_chinese_yes_and_no_are_read_as_flags():
    assert utils.parse_bool("否") is False
    assert utils.parse_bool("是") is True
    assert utils.coerce_bool("否", True) is False


def test_progress_counts_tolerate_spaces():
    from uplus_mcp.engine import _progress_counts

    assert _progress_counts("12 / 32") == (12, 32)
    assert _progress_counts("12/32") == (12, 32)
    assert _progress_counts("nonsense") == (None, None)
