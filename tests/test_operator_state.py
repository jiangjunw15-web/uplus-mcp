"""Pure-function tests for page-state and experiment-flag classification.

These rules decide what the real answer page / experiment card says; they must
not depend on a live browser session.
"""

from uplus_mcp.models import HomeworkStatus
from uplus_mcp.operator import classify_experiment_flag, classify_page_state


# --------------------------------------------------------------------------- #
# homework page state
# --------------------------------------------------------------------------- #
def test_page_state_submitted_pending_when_withdraw_button_shown():
    text = "第三章作业 答题卡 (32/32) 撤回作业 保存草稿"
    assert classify_page_state(text) is HomeworkStatus.SUBMITTED_PENDING


def test_page_state_not_submitted_when_submit_button_shown():
    text = "上传PPT 答题卡 (0/1) 提交作业 保存草稿"
    assert classify_page_state(text) is HomeworkStatus.NOT_SUBMITTED


def test_page_state_graded_by_reference_answer_keyword():
    assert classify_page_state("参考答案 第 1 题") is HomeworkStatus.SUBMITTED_GRADED


def test_page_state_graded_by_total_score_keyword():
    assert classify_page_state("总分：100 分") is HomeworkStatus.SUBMITTED_GRADED


def test_page_state_graded_by_per_question_score_keyword():
    assert classify_page_state("第 2 题 得分：4 分") is HomeworkStatus.SUBMITTED_GRADED


def test_page_state_unknown_when_no_keyword_matches():
    # Unrecognised text must return None so callers keep the REST status
    # instead of assuming "not submitted".
    assert classify_page_state("课程章节 公告 小U学伴") is None
    assert classify_page_state("") is None


def test_page_state_withdraw_takes_priority_over_submit_text():
    # A submitted page must not be misread as open because the body also
    # mentions a generic submit action elsewhere in the layout.
    text = "撤回作业 提交作业 保存草稿"
    assert classify_page_state(text) is HomeworkStatus.SUBMITTED_PENDING


# --------------------------------------------------------------------------- #
# experiment badge
# --------------------------------------------------------------------------- #
def test_experiment_flag_text_wins_over_class():
    # A freshly mounted card may still carry the default gray class before the
    # API state paints; visible text must win.
    assert classify_experiment_flag("未提交", "flag gray") is False
    assert classify_experiment_flag("已提交", "flag red") is True


def test_experiment_flag_class_fallback_when_text_missing():
    assert classify_experiment_flag(None, "flag gray") is True
    assert classify_experiment_flag("", "flag red") is False


def test_experiment_flag_unknown_when_both_missing():
    assert classify_experiment_flag(None, "") is None
    assert classify_experiment_flag(None, "flag") is None


def test_experiment_flag_recognises_pending_and_not_started_text():
    assert classify_experiment_flag("待提交", "flag gray") is False
    assert classify_experiment_flag("未开始", "flag red") is False
