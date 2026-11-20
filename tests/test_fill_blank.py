"""Fill-blank (填空题) support end to end.

A fill-blank question is not one box: the platform renders one answer box per
blank in the stem (``处理 GET 请求用____注解，处理 POST 请求用____注解。``
renders 答案一 / 答案二). These tests cover every half of the feature:

1. counting the blanks in a stem and telling the model how many to return;
2. normalizing a multi-blank model answer into per-box values;
3. filling each box on the page and reading every box back;
4. auditing a half-filled fill-blank instead of calling it complete;
5. re-filling a half-filled fill-blank during the answer flow.
"""

import pytest

from uplus_mcp.browser import UplusError
from uplus_mcp.config import Config
from uplus_mcp.models import HomeworkTask, Question, QuestionType, count_blanks
from uplus_mcp.operator import HomeworkOperator
from uplus_mcp.solver import build_answer_brief, normalize_answer, split_blanks
from uplus_mcp.report import audit_questions
from unittest.mock import Mock


# --------------------------------------------------------------------------- #
# fake page: N answer boxes, each an ordinary input
# --------------------------------------------------------------------------- #
class _Box:
    def __init__(self, value=""):
        self.value = value
        self.fill_calls = 0

    @property
    def first(self):
        return self


class _BlankLocator:
    def __init__(self, page, selector):
        self._page = page
        self._selector = selector

    @property
    def first(self):
        return self

    def count(self):
        if "textarea" in self._selector:
            return len(self._page.boxes)
        return 0

    def nth(self, index):
        return _BlankHandle(self._page.boxes[index])

    def is_visible(self):
        return True


class _BlankHandle:
    def __init__(self, box):
        self._box = box

    def fill(self, value, timeout=None):
        self._box.fill_calls += 1
        self._box.value = value
        return None

    def input_value(self):
        return self._box.value

    def inner_text(self):
        return self._box.value


class _BlankPage:
    """Single-question view with one box per blank, in DOM order."""

    def __init__(self, boxes=2):
        self.boxes = [_Box() for _ in range(boxes)]

    def evaluate(self, script, arg=None):
        return False  # no stem narrowing: the whole page is the scope

    def locator(self, selector):
        return _BlankLocator(self, selector)

    def inner_text(self, selector="body"):
        return "题目正文"


class _StuckHandle:
    """A box whose write never lands: the value stays empty after ``fill``."""

    def __init__(self, box):
        self._box = box

    def fill(self, value, timeout=None):
        self._box.fill_calls += 1
        return None  # the editor swallows it

    def input_value(self):
        return self._box.value

    def inner_text(self):
        return self._box.value


class _RichBoxHandle:
    """A contenteditable rich-text editor: ``fill`` raises, typing lands."""

    def __init__(self, box):
        self._box = box

    def fill(self, value, timeout=None):
        raise Exception("cannot fill a contenteditable element")

    def click(self, timeout=None):
        return None

    def press(self, key):
        if key == "Delete":
            self._box.value = ""
        return None

    def type(self, value, timeout=None):
        self._box.value = value
        return None

    def input_value(self):
        raise Exception("not an input element")

    def inner_text(self):
        return self._box.value


def _operator():
    return HomeworkOperator(Mock(), Mock(), Config())


def _blank_question(stem: str, answer: str = "", order: int = 1) -> Question:
    return Question(
        question_id="q1", order=order, type=QuestionType.FILL_BLANK,
        stem=stem, my_answer=answer or None,
    )


# --------------------------------------------------------------------------- #
# blank counting + the answer brief
# --------------------------------------------------------------------------- #
def test_count_blanks_counts_each_underscore_run():
    assert count_blanks("处理 GET 请求用____注解，处理 POST 请求用____注解。") == 2
    assert count_blanks("用于表示请求映射的通用注解是________。") == 1
    assert count_blanks("没有空的题干") == 0
    assert count_blanks("") == 0
    assert count_blanks(None) == 0


def test_count_blanks_counts_full_width_blanks():
    assert count_blanks("通用注解是＿＿＿＿。") == 1
    assert count_blanks("A是＿＿，B是＿＿。") == 2


def test_count_blanks_ignores_a_single_underscore():
    # ``a_b`` is a variable name, not a blank.
    assert count_blanks("变量 a_b 的含义") == 0


def test_answer_brief_reports_the_blank_count():
    q = _blank_question("GET 用____注解，POST 用____注解。")
    payload = q.llm_prompt()
    assert payload["blank_count"] == 2
    assert "2 个空" in payload["answer_hint"]


def test_answer_brief_asks_for_one_line_per_blank():
    brief = build_answer_brief("作业", [
        _blank_question("GET 用____注解，POST 用____注解。")
    ])
    assert "每空一行" in brief["instructions"]
    # The per-question payload also carries the count the model must match.
    assert brief["questions"][0]["blank_count"] == 2


# --------------------------------------------------------------------------- #
# answer normalization
# --------------------------------------------------------------------------- #
def test_single_blank_returns_plain_text():
    q = _blank_question("通用注解是________。")
    assert normalize_answer(q, "@RequestMapping") == "@RequestMapping"


def test_two_blanks_split_on_newline():
    q = _blank_question("GET 用____注解，POST 用____注解。")
    assert normalize_answer(q, "@GetMapping\n@PostMapping") == "@GetMapping\n@PostMapping"


def test_two_blanks_split_on_chinese_separator():
    q = _blank_question("GET 用____注解，POST 用____注解。")
    assert normalize_answer(q, "@GetMapping；@PostMapping") == "@GetMapping\n@PostMapping"


def test_two_blanks_accept_a_json_array():
    q = _blank_question("GET 用____注解，POST 用____注解。")
    assert normalize_answer(q, '["@GetMapping", "@PostMapping"]') == "@GetMapping\n@PostMapping"


def test_missing_blank_answer_is_rejected():
    q = _blank_question("GET 用____注解，POST 用____注解。")
    with pytest.raises(ValueError, match="2 个空"):
        normalize_answer(q, "@GetMapping")


def test_unknown_blank_count_does_not_split_on_semicolon():
    # Without a stem the separator split must not tear one answer apart.
    assert split_blanks("a;b") == ["a;b"]
    assert split_blanks("a\nb") == ["a", "b"]


def test_split_blanks_drops_empty_entries():
    assert split_blanks("@GetMapping\n\n@PostMapping\n") == ["@GetMapping", "@PostMapping"]


# --------------------------------------------------------------------------- #
# page filling
# --------------------------------------------------------------------------- #
def test_one_blank_goes_into_the_only_box():
    page = _BlankPage(boxes=1)
    out = _operator().fill_question(
        page, _blank_question("通用注解是________。"), "@RequestMapping",
        single_view=True)
    assert out == "@RequestMapping"
    assert [b.value for b in page.boxes] == ["@RequestMapping"]


def test_each_blank_goes_into_its_own_box():
    page = _BlankPage(boxes=2)
    q = _blank_question("GET 用____注解，POST 用____注解。")
    out = _operator().fill_question(
        page, q, "@GetMapping\n@PostMapping", single_view=True)
    assert out == "@GetMapping\n@PostMapping"
    assert [b.value for b in page.boxes] == ["@GetMapping", "@PostMapping"]


def test_a_blank_that_does_not_stick_raises():
    """A write the editor swallows must fail the question, not pass silently:
    the submit gate relies on this error."""
    page = _BlankPage(boxes=2)
    real_nth = _BlankLocator.nth

    def nth(self, index):
        return _StuckHandle(page.boxes[index])

    _BlankLocator.nth = nth
    try:
        q = _blank_question("GET 用____注解，POST 用____注解。")
        with pytest.raises(UplusError, match="写入后页面内容不一致"):
            _operator().fill_question(
                page, q, "@GetMapping\n@PostMapping", single_view=True)
    finally:
        _BlankLocator.nth = real_nth
    # The failing blank raises immediately: box 1 is never touched, and the
    # gate never gets a chance to submit a half-filled question.
    assert page.boxes[0].fill_calls == 2
    assert page.boxes[1].fill_calls == 0


def test_fewer_boxes_than_answers_raises():
    page = _BlankPage(boxes=1)
    q = _blank_question("GET 用____注解，POST 用____注解。")
    with pytest.raises(UplusError, match="只找到 1 个输入框"):
        _operator().fill_question(
            page, q, "@GetMapping\n@PostMapping", single_view=True)


def test_no_box_at_all_raises():
    page = _BlankPage(boxes=0)
    q = _blank_question("通用注解是________。")
    with pytest.raises(UplusError, match="找不到答案输入框"):
        _operator().fill_question(page, q, "@RequestMapping", single_view=True)


def test_a_contenteditable_box_gets_the_answer_typed_into_it():
    """A rich-text editor that refuses ``fill`` must still receive the answer:
    the keyboard fallback types it in instead of silently dropping it."""
    page = _BlankPage(boxes=2)
    # Replace the first box's handle with one that models a contenteditable
    # editor: fill raises, typing lands.
    rich = _BlankPage(boxes=2)
    rich.boxes[0].value = ""
    real_nth = _BlankLocator.nth

    def nth(self, index):
        if index == 0:
            return _RichBoxHandle(rich.boxes[0])
        return real_nth(self, index)

    _BlankLocator.nth = nth
    try:
        q = _blank_question("GET 用____注解，POST 用____注解。")
        out = _operator().fill_question(
            page, q, "@GetMapping\n@PostMapping", single_view=True)
    finally:
        _BlankLocator.nth = real_nth
    assert out == "@GetMapping\n@PostMapping"
    assert rich.boxes[0].value == "@GetMapping"
    assert page.boxes[1].value == "@PostMapping"


# --------------------------------------------------------------------------- #
# live layout: 「答案一/答案二」 textarea boxes with a placeholder hint
# --------------------------------------------------------------------------- #
class _HintBox:
    """A textarea/input handle with the live page's placeholder hint."""

    def __init__(self, placeholder: str, value: str = ""):
        self.placeholder = placeholder
        self.value = value
        self.fill_calls = 0

    def get_attribute(self, name):
        return self.placeholder if name == "placeholder" else None

    def fill(self, value, timeout=None):
        self.fill_calls += 1
        self.value = value

    def input_value(self):
        return self.value

    def inner_text(self):
        return self.value

    def is_visible(self):
        return True


class _HintLocator:
    def __init__(self, page):
        self._page = page

    def count(self):
        return len(self._page.boxes)

    def nth(self, index):
        return self._page.boxes[index]


class _HintPage:
    """Single-question page: a header search field plus the answer textarea."""

    def __init__(self):
        self.boxes = [
            _HintBox("搜索课程"),
            _HintBox("请输入填空项答案"),
        ]

    def locator(self, selector):
        return _HintLocator(self)


def test_header_search_is_not_taken_for_the_blank_slot():
    """The live page labels the answer box 「请输入填空项答案」. A header search
    input in the same scope must not receive the answer instead."""
    page = _HintPage()
    out = _operator().fill_question(
        page, _blank_question("通用注解是________。"), "@RequestMapping",
        single_view=True)
    assert out == "@RequestMapping"
    assert page.boxes[0].value == "" and page.boxes[0].fill_calls == 0
    assert page.boxes[1].value == "@RequestMapping"


def test_blank_boxes_keep_unlabelled_boxes_when_none_have_hints():
    page = _BlankPage(boxes=2)
    assert len(_operator()._blank_boxes(page)) == 2


class _UnlabelledBox(_HintBox):
    def get_attribute(self, name):
        return None


class _UnlabelledSearchPage(_HintPage):
    """A header search box with no placeholder, before the answer textarea."""

    def __init__(self):
        self.boxes = [
            _UnlabelledBox(""),
            _HintBox("请输入填空项答案"),
        ]


def test_unlabelled_search_does_not_take_the_only_blank():
    page = _UnlabelledSearchPage()
    out = _operator().fill_question(
        page, _blank_question("通用注解是________。"), "@RequestMapping",
        single_view=True)
    assert out == "@RequestMapping"
    assert page.boxes[0].value == "" and page.boxes[0].fill_calls == 0
    assert page.boxes[1].value == "@RequestMapping"


class _ScopeLocator:
    def __init__(self, marked):
        self._marked = marked

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self._marked else 0


class _GenericStemPage:
    """Page whose stem is not inside ``.ck-content``.

    The first marker script reports "not found"; the generic walker reports it
    marked the question card, so ``_narrowed_scope`` must fall through to it.
    """

    def __init__(self):
        self.marked = False
        self.scripts: list[str] = []

    def evaluate(self, script, arg=None):
        self.scripts.append(script)
        if "const blocks" in script:
            return False
        if "createTreeWalker" in script:
            self.marked = True
            return True
        return False

    def locator(self, selector):
        return _ScopeLocator(self.marked)


def test_scope_falls_back_to_the_generic_stem_walker():
    page = _GenericStemPage()
    scope = _operator()._narrowed_scope(
        page, "用于表示请求映射的通用注解是________。")
    assert scope is not None
    assert sum("createTreeWalker" in s for s in page.scripts) == 1
    assert not any("const blocks" in s for s in page.scripts[1:])


class _ScriptableStuckHandle(_StuckHandle):
    """Typing is swallowed; the JS value fallback is the only write that lands."""

    def get_attribute(self, name):
        return "请输入填空项答案" if name == "placeholder" else None

    def evaluate(self, script, arg=None):
        self._box.value = arg


def test_script_fallback_writes_a_box_that_swallows_typing():
    page = _BlankPage(boxes=1)
    real_nth = _BlankLocator.nth

    def nth(self, index):
        return _ScriptableStuckHandle(page.boxes[index])

    _BlankLocator.nth = nth
    try:
        out = _operator().fill_question(
            page, _blank_question("通用注解是________。"), "@RequestMapping",
            single_view=True)
    finally:
        _BlankLocator.nth = real_nth
    assert out == "@RequestMapping"
    assert page.boxes[0].value == "@RequestMapping"
    assert page.boxes[0].fill_calls == 2


# --------------------------------------------------------------------------- #
# audit: a half-filled fill-blank is not "complete"
# --------------------------------------------------------------------------- #
def test_audit_flags_a_half_filled_fill_blank():
    q = Question(
        question_id="q1", order=1, type=QuestionType.FILL_BLANK,
        stem="GET 用____注解，POST 用____注解。",
        my_answer="@GetMapping",
        reference_answer="@GetMapping；@PostMapping",
    )
    result = audit_questions([q])
    assert result["partial_count"] == 1
    assert result["partial"][0]["missing_blanks"] == 1
    assert not result["complete"]


def test_audit_accepts_a_fully_filled_fill_blank():
    q = Question(
        question_id="q1", order=1, type=QuestionType.FILL_BLANK,
        stem="GET 用____注解，POST 用____注解。",
        my_answer="@GetMapping\n@PostMapping",
        reference_answer="@GetMapping；@PostMapping",
    )
    result = audit_questions([q])
    assert result["partial_count"] == 0
    assert result["wrong_count"] == 0
    assert result["correct"] == 1
    assert result["complete"]


def test_audit_accepts_a_differently_joined_same_answer():
    """The platform may join the blanks with a separator the model never used;
    a value-by-value comparison must not call that wrong."""
    q = Question(
        question_id="q1", order=1, type=QuestionType.FILL_BLANK,
        stem="GET 用____注解，POST 用____注解。",
        my_answer="@GetMapping\n@PostMapping",
        reference_answer="@GetMapping;@PostMapping",
    )
    result = audit_questions([q])
    assert result["wrong_count"] == 0
    assert result["correct"] == 1


def test_audit_reports_a_wrong_fill_blank():
    q = Question(
        question_id="q1", order=1, type=QuestionType.FILL_BLANK,
        stem="GET 用____注解，POST 用____注解。",
        my_answer="@RequestMapping\n@PostMapping",
        reference_answer="@GetMapping\n@PostMapping",
    )
    result = audit_questions([q])
    assert result["wrong_count"] == 1
    # ``complete`` means "nothing left blank"; a wrong-but-present answer is
    # reported under ``wrong`` and shows up in ``clean`` instead.
    assert result["complete"]
    assert not result["clean"]


# --------------------------------------------------------------------------- #
# answer flow: a half-filled fill-blank is re-filled, not passed off as done
# --------------------------------------------------------------------------- #
class _FlowPage:
    """List-view answer page: no 答题卡 markers, one question at a time."""

    def __init__(self):
        self.url = "https://www.eduplus.net/course/workAnswer/c1/t1/true"
        self.boxes = []
        self.keyboard = Mock()
        self._body = "提交作业"
        self.filled: list[tuple[int, str]] = []

    def _confirm(self):
        self._body = "撤回作业"

    def wait_for_load_state(self, state="load", timeout=None):
        return None

    def inner_text(self, selector="body"):
        return self._body

    def evaluate(self, script, arg=None):
        return False

    def locator(self, selector):
        if "input" in selector or "textarea" in selector or "contenteditable" in selector:
            return _FlowBoxes(self)
        return _NoMatch()

    def get_by_role(self, role, name=None):
        return _Clickable(self._confirm)


class _NoMatch:
    @property
    def first(self):
        return self

    def count(self):
        return 0

    def click(self, timeout=None):
        raise AssertionError("clicked something that should not exist")


class _Clickable:
    def __init__(self, confirm):
        self._confirm = confirm

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def click(self, timeout=None):
        self._confirm()


class _FlowBoxes:
    def __init__(self, page):
        self._page = page

    @property
    def first(self):
        return self

    def count(self):
        return 2

    def nth(self, index):
        return _FlowBox(self._page, index)


class _FlowBox:
    def __init__(self, page, index):
        self._page = page
        self._index = index
        self._value = ""

    def is_visible(self):
        return True

    def fill(self, value, timeout=None):
        self._value = value
        self._page.filled.append((self._index, value))
        return None

    def input_value(self):
        return self._value

    def inner_text(self):
        return self._value


def test_half_filled_fill_blank_is_refilled_during_the_answer_flow(tmp_path):
    """The platform recorded only the first blank. A model answer must refill
    the whole question instead of letting a half-filled one count as done."""
    from unittest.mock import Mock as _Mock

    question = Question(
        question_id="q1", order=1, type=QuestionType.FILL_BLANK,
        stem="GET 用____注解，POST 用____注解。",
        my_answer="@GetMapping", answered=True,
    )
    session = _Mock()
    page = _FlowPage()
    session.open.return_value = page
    session.url_for.return_value = "https://www.eduplus.net/x"
    client = _Mock()
    client.list_questions.return_value = [question]
    client.question_detail.side_effect = lambda qid: question
    op = HomeworkOperator(session, client, Config(report_dir=str(tmp_path)))

    outcome = op.answer_homework(
        HomeworkTask(task_id="t1", homework_id="h1", course_id="c1", name="作业"),
        {"q1": "@GetMapping\n@PostMapping"}, submit=True)

    assert outcome["errors"] == []
    assert outcome["objective_answered"] == outcome["objective_total"]
    assert outcome["can_submit"] is True
    # Both blanks were written, in order.
    assert page.filled == [(0, "@GetMapping"), (1, "@PostMapping")]


def test_half_filled_fill_blank_without_an_answer_is_not_complete(tmp_path):
    """No model answer for a half-filled fill-blank means it cannot be
    verified, so the submit gate must refuse rather than pass it off."""
    from unittest.mock import Mock as _Mock

    question = Question(
        question_id="q1", order=1, type=QuestionType.FILL_BLANK,
        stem="GET 用____注解，POST 用____注解。",
        my_answer="@GetMapping", answered=True,
    )
    session = _Mock()
    page = _FlowPage()
    session.open.return_value = page
    session.url_for.return_value = "https://www.eduplus.net/x"
    client = _Mock()
    client.list_questions.return_value = [question]
    client.question_detail.side_effect = lambda qid: question
    op = HomeworkOperator(session, client, Config(report_dir=str(tmp_path)))

    outcome = op.answer_homework(
        HomeworkTask(task_id="t1", homework_id="h1", course_id="c1", name="作业"),
        {}, submit=True)

    assert outcome["can_submit"] is False
    assert outcome["submitted"] is False
    reasons = " ".join(item["reason"] for item in outcome["skipped_questions"])
    assert "无法补齐剩余空" in reasons
