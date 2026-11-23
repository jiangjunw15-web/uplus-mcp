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
# cheap button probe (submit / withdraw confirmation loops)
# --------------------------------------------------------------------------- #
class _ProbePage:
    """Fake page answering only the button-label probe script."""

    def __init__(self, labels):
        self._labels = labels
        self.calls = 0

    def evaluate(self, script, arg=None):
        if "撤回作业" in str(script) and "querySelectorAll" in str(script):
            self.calls += 1
            if "撤回作业" in self._labels:
                return "withdrawn"
            if "提交作业" in self._labels:
                return "submit"
            return None
        return None


def _operator():
    from uplus_mcp.operator import HomeworkOperator
    return object.__new__(HomeworkOperator)


def test_probe_submit_button_reads_withdraw_as_pending():
    from uplus_mcp.models import HomeworkStatus
    op = _operator()
    state = op._probe_submit_button(_ProbePage(["撤回作业", "保存草稿"]))
    assert state is HomeworkStatus.SUBMITTED_PENDING


def test_probe_submit_button_reads_submit_as_not_submitted():
    from uplus_mcp.models import HomeworkStatus
    op = _operator()
    state = op._probe_submit_button(_ProbePage(["提交作业", "保存草稿"]))
    assert state is HomeworkStatus.NOT_SUBMITTED


def test_probe_submit_button_returns_none_while_transitioning():
    op = _operator()
    assert op._probe_submit_button(_ProbePage(["保存草稿"])) is None


def test_probe_submit_button_prefers_withdraw_over_submit():
    # Same precedence as classify_page_state: a submitted page that also shows
    # a generic submit control must read as submitted.
    from uplus_mcp.models import HomeworkStatus
    op = _operator()
    state = op._probe_submit_button(_ProbePage(["提交作业", "撤回作业"]))
    assert state is HomeworkStatus.SUBMITTED_PENDING


def test_probe_submit_button_swallows_page_errors():
    class _Broken(_ProbePage):
        def evaluate(self, script, arg=None):
            raise RuntimeError("frame detached")

    op = _operator()
    assert op._probe_submit_button(_Broken([])) is None


# --------------------------------------------------------------------------- #
# stem-walk waits: return as soon as the question has repainted
# --------------------------------------------------------------------------- #
class _StemPage:
    """Fake single-question page whose readable stem changes over time."""

    def __init__(self, stems):
        # ``stems`` is the sequence of values ``.ck-content`` returns; an empty
        # string models the SPA still painting the transition.
        self._stems = list(stems)
        self.reads = 0

    def _ck_content(self):
        # The first locator lookup is the stem container.
        class _Loc:
            def __init__(self, page):
                self._page = page

            @property
            def first(self):
                return self

            def inner_text(self):
                value = self._page._stems[min(
                    self._page.reads, len(self._page._stems) - 1)]
                self._page.reads += 1
                return value

        return _Loc(self)

    def locator(self, selector):
        if selector == ".ck-content":
            return self._ck_content()
        raise AssertionError(f"unexpected selector {selector}")


def test_await_stem_ready_returns_as_soon_as_the_stem_changes():
    """After a navigation the page still shows the previous stem until the SPA
    repaints. The wait must return on the first different stem rather than
    always paying a fixed sleep per question."""
    # First read still shows the old stem, second read shows the new one.
    page = _StemPage(["旧题干", "新题干", "新题干"])
    op = _operator()

    op._await_stem_ready(page, "旧题干", timeout_ms=4000)

    # It returned on the first changed read, not after exhausting the budget.
    assert page.reads == 2


def test_await_stem_ready_tolerates_a_blank_transition_frame():
    page = _StemPage(["", "新题干", "新题干"])
    op = _operator()

    op._await_stem_ready(page, "旧题干", timeout_ms=4000)

    assert page.reads == 2


def test_read_stem_after_transition_retries_a_blank_read():
    """A blank first read (transition animation still running) is retried; the
    result is the first non-empty stem."""
    page = _StemPage(["", "", "题干A", "题干B"])
    op = _operator()

    assert op._read_stem_after_transition(page) == "题干A"
    assert page.reads == 3


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
