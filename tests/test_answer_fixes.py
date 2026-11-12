"""Regression tests for the multi-select / skipped-question fixes.

Covers:
1. answer normalization never turns explanation text into option letters;
2. duplicate page stems map to distinct positions and are consumed once;
3. navigation to page position 1 actually clicks (the old early return left
   the page on the last question, filling answers into the wrong question);
4. multi-select filling reconciles with the *page's* checked boxes instead of
   toggling already-checked boxes off (the reported "漏选");
5. a multi-select that cannot reach its target raises, so the submit gate
   refuses an incomplete answer.
"""

import re

import pytest

from uplus_mcp.browser import UplusError
from uplus_mcp.config import Config
from uplus_mcp.models import Option, Question, QuestionType
from uplus_mcp.operator import HomeworkOperator, stem_matches, stem_position
from uplus_mcp.solver import normalize_answer


# --------------------------------------------------------------------------- #
# answer normalization
# --------------------------------------------------------------------------- #
def _multi(*codes: str) -> Question:
    return Question(
        question_id="q1", order=1, type=QuestionType.MULTIPLE, stem="题干",
        options=[Option(code=code, content=f"选项{code}") for code in codes],
    )


def test_multiple_answer_ignores_english_explanation():
    q = _multi("A", "B", "C", "D")
    assert normalize_answer(q, "AC because A and C are correct") == "AC"


def test_multiple_answer_ignores_chinese_explanation():
    q = _multi("A", "B", "C", "D")
    assert normalize_answer(q, "A、C（B 中说法不成立）") == "AC"
    assert normalize_answer(q, "答案是 A、C") == "AC"


def test_multiple_answer_drops_letters_outside_the_options():
    q = _multi("A", "B", "C", "D")
    assert normalize_answer(q, "ACDZ") == "ACD"


def test_single_answer_keeps_the_leading_letter():
    q = Question(
        question_id="q1", order=1, type=QuestionType.SINGLE, stem="题干",
        options=[Option(code=code, content="x") for code in "ABCD"],
    )
    assert normalize_answer(q, "C，因为A和B都不对") == "C"


# --------------------------------------------------------------------------- #
# duplicate stems
# --------------------------------------------------------------------------- #
def test_duplicate_stems_resolve_to_distinct_positions():
    positions = {"同样的题干文本": 1, "同样的题干文本::2": 5}
    used: set[int] = set()
    assert stem_position(positions, "同样的题干文本", used) == 1
    assert stem_position(positions, "同样的题干文本", used) == 5
    assert stem_position(positions, "同样的题干文本", used) is None
    assert used == {1, 5}


def test_stem_position_consumes_into_the_caller_supplied_set():
    """Regression: ``used or set()`` silently replaced an empty caller set,
    so the first consumed position never reached the caller."""
    used: set[int] = set()
    assert stem_position({"题干文本内容": 3}, "题干文本内容", used) == 3
    assert used == {3}


def test_stem_matches_accepts_page_stem_with_appended_options():
    api_stem = "以下关于依赖倒置原则的叙述不正确的是（ ）"
    page_stem = "以下关于依赖倒置原则的叙述不正确的是A依赖倒置原则的简称是DIP"
    assert stem_matches(page_stem, api_stem)


def test_stem_matches_rejects_a_different_question_with_the_same_opening():
    a = "以下关于单一职责原则的叙述不正确的是A单一职责原则提高了类之间的耦合性"
    b = "以下关于依赖倒置原则的叙述不正确的是A依赖倒置原则的简称是DIP"
    assert not stem_matches(a, b)


# --------------------------------------------------------------------------- #
# navigation must actually click, then verify the stem
# --------------------------------------------------------------------------- #
class _NavLocator:
    def __init__(self, page, selector):
        self._page = page
        self._selector = selector

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def click(self, timeout=None):
        match = re.search(r"='(\d+)'\]", self._selector)
        if match:
            self._page.current = int(match.group(1))

    def inner_text(self):
        return self._page.stems.get(self._page.current, "")


class _NavPage:
    def __init__(self, stems):
        self.stems = stems
        self.current = 0
        self.clicked = []

    def locator(self, selector):
        return _NavLocator(self, selector)


def _operator() -> HomeworkOperator:
    return HomeworkOperator(session=None, client=None, config=Config())


def test_navigate_to_position_one_still_clicks_the_card():
    page = _NavPage({1: "第一道题的题干内容", 2: "第二道题的题干内容"})
    page.current = 2  # the stem walk leaves the page on the last question
    op = _operator()
    assert op.navigate_to_question(page, 1, "第一道题的题干内容") is True
    assert page.current == 1


def test_navigate_refuses_a_stem_mismatch():
    page = _NavPage({1: "第一道题的题干内容", 2: "第二道题的题干内容"})
    page.current = 2
    op = _operator()
    assert op.navigate_to_question(page, 2, "第一道题的题干内容") is False


def test_navigate_proceeds_when_the_page_stem_is_unreadable():
    class _UnreadablePage:
        def locator(self, selector):
            class _L:
                @property
                def first(self):
                    return self

                def click(self, timeout=None):
                    return None

                def inner_text(self):
                    raise RuntimeError("no such element")

            return _L()

    assert _operator().navigate_to_question(_UnreadablePage(), 2, "题干") is True


# --------------------------------------------------------------------------- #
# multi-select page-state reconciliation
# --------------------------------------------------------------------------- #
class _Box:
    def __init__(self, value, checked):
        self.value = value
        self.checked = checked


class _BoxHandle:
    def __init__(self, box):
        self._box = box

    def get_attribute(self, name):
        return self._box.value if name == "value" else None

    def is_checked(self):
        return self._box.checked


class _MultiLocator:
    BOX_LIST = "input[type=checkbox], input[type=radio]"
    CHECK_VALUE = re.compile(r'input\[type=checkbox\]\[value="([A-Z])"\]')

    def __init__(self, page, selector):
        self._page = page
        self._selector = selector

    @property
    def first(self):
        return self

    def count(self):
        if self._selector == self.BOX_LIST:
            return len(self._page.boxes)
        if self.CHECK_VALUE.search(self._selector):
            return 1
        return 0

    def nth(self, index):
        return _BoxHandle(list(self._page.boxes.values())[index])

    def scroll_into_view_if_needed(self, timeout=None):
        return None

    def click(self, timeout=None):
        match = self.CHECK_VALUE.search(self._selector)
        if match:
            self._page.click_box(match.group(1))


class _MultiPage:
    """List-view page fake: one page, four checkbox options, no stem scope."""

    def __init__(self, checked: str, stuck: str = ""):
        self.boxes = {code: _Box(code, code in checked) for code in "ABCD"}
        self.clicks: list[str] = []
        self._stuck = stuck  # letter whose click never sticks

    def evaluate(self, script, arg=None):
        return False  # no scoping marker: operate on the whole fake page

    def locator(self, selector):
        return _MultiLocator(self, selector)

    def click_box(self, code):
        self.clicks.append(code)
        if code == self._stuck:
            return
        self.boxes[code].checked = not self.boxes[code].checked


def _multi_question(my_answer: str) -> Question:
    return Question(
        question_id="q1", order=1, type=QuestionType.MULTIPLE, stem="题干",
        my_answer=my_answer,
        options=[Option(code=code, content="x") for code in "ABCD"],
    )


def test_multiselect_only_ticks_missing_options_and_keeps_checked_ones():
    """Page already has A,B checked; target A,B,C,D must click only C and D.
    Clicking A or B again (the old API-delta logic) would uncheck them."""
    page = _MultiPage("AB")
    out = _operator().fill_question(
        page, _multi_question("AB"), "ABCD", single_view=False)
    assert out == "ABCD"
    assert all(page.boxes[code].checked for code in "ABCD")
    assert page.clicks == ["C", "D"]


def test_multiselect_uses_the_page_state_not_the_api_state():
    """The API says AB but the page already has ABC checked (unsaved edit).
    Re-clicking C would uncheck it; only D may be clicked."""
    page = _MultiPage("ABC")
    _operator().fill_question(page, _multi_question("AB"), "ABCD",
                              single_view=False)
    assert page.boxes["C"].checked
    assert page.clicks == ["D"]


def test_multiselect_deselects_choices_outside_the_target():
    page = _MultiPage("ABD")
    _operator().fill_question(page, _multi_question("ABD"), "AB",
                              single_view=False)
    assert page.boxes["D"].checked is False
    assert page.clicks == ["D"]


def test_multiselect_raises_when_the_page_cannot_reach_the_target():
    """A click that never sticks must fail the question, not pass silently:
    the submit gate relies on this error to refuse an incomplete answer."""
    page = _MultiPage("", stuck="C")
    with pytest.raises(UplusError, match="选项未能全部选中"):
        _operator().fill_question(page, _multi_question(""), "AC",
                                  single_view=False)
