"""页面题干映射失败时的回归测试。

单题视图上「题干 → 页面位置」映射是防止答错题的唯一机制。映射失败曾
经被写进 ``errors``，而 ``can_submit`` 要求 ``not errors``，于是一道
定位不到的题目会让整份作业无法提交。

映射失败是页面形态问题、不是缺答案：这类题目必须被跳过（不冒险点错
题），但绝不能连带否决其余题目的提交。
"""

import re
import tempfile
from pathlib import Path
from unittest.mock import Mock

from uplus_mcp.config import Config
from uplus_mcp.models import HomeworkTask, Question, QuestionType
from uplus_mcp.operator import HomeworkOperator

_TEST_REPORT_DIR = Path(tempfile.mkdtemp(prefix="uplus-test-mapping-"))


def _task():
    return HomeworkTask(task_id="t1", homework_id="h1", course_id="c1",
                        name="作业", end_ms=None)


def _question(qid, order, stem):
    return Question(
        question_id=qid, order=order, type=QuestionType.SINGLE, stem=stem,
    )


class _Locator:
    """A fake locator that also drives the page's stem walk.

    Clicking the answer-card number moves the page to that position and
    clicking 下一题 advances it, so ``page_stem_positions`` builds a real
    map instead of a one-entry one.
    """

    def __init__(self, page, selector):
        self._page = page
        self._selector = selector

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def click(self, timeout=None):
        text = (self._selector or "").strip()
        # The answer card is addressed as ``//*[normalize-space(text())='N']``.
        match = re.search(r"'(\d+)'", text) or re.fullmatch(r"(\d+)", text)
        if match:
            self._page._index = int(match.group(1))
        elif text == "下一题":
            self._page._index = min(self._page._index + 1, len(self._page._stems))
        elif "提交" in text:
            # The real page repaints to 撤回作业 after a successful submit;
            # ``read_page_state`` must be able to observe that.
            self._page._submitted = True
        return None

    def is_disabled(self):
        return False

    def scroll_into_view_if_needed(self, timeout=None):
        return None

    def get_attribute(self, name):
        return "A" if name == "value" else None

    def is_checked(self):
        # A single-choice read-back must look confirmed, or the fill retries.
        return True

    def inner_text(self):
        return self._page._stems.get(self._page._index, "")


class _SingleQuestionPage:
    """A single-question page whose stem walk only ever yields stem A.

    ``_page_stem`` reads ``.ck-content``, so whatever this page returns for
    that selector is what the position map records.
    """

    def __init__(self, readable_stems):
        self.url = "https://www.eduplus.net/course/workAnswer/c1/t1/true"
        self.keyboard = Mock()
        self._stems = {i + 1: s for i, s in enumerate(readable_stems)}
        self._index = 1
        self._submitted = False

    def inner_text(self, selector="body"):
        # 答题卡 marks the page as a single-question view; the button label is
        # what ``read_page_state`` and the submit verification look for.
        tail = "撤回作业" if self._submitted else "提交作业"
        return f"答题卡 (0/2) {tail}"

    def locator(self, selector):
        return _Locator(self, selector)

    def get_by_role(self, role, name=None):
        return _Locator(self, name or "")

    def wait_for_load_state(self, state="load", timeout=None):
        return None


def _operator(questions, page):
    session = Mock()
    session.open.return_value = page
    session.url_for.return_value = page.url
    client = Mock()
    client.list_questions.return_value = list(questions)
    client.question_detail.side_effect = lambda qid: next(
        q for q in questions if q.question_id == qid)
    return HomeworkOperator(session, client,
                           Config(report_dir=str(_TEST_REPORT_DIR)))


def test_unlocatable_question_is_skipped_but_does_not_veto_submission():
    """The reported symptom: one unmappable question blocked the whole homework.

    q1 is locatable, q2 is not (the page never shows its stem). q1 must still
    be filled and the homework must still submit.
    """
    questions = [
        _question("q1", 1, "以下关于单一职责原则的叙述不正确的是"),
        _question("q2", 2, "以下关于依赖倒置原则的叙述不正确的是"),
    ]
    # The walk records only q1's stem: position 1 carries q1, position 2 stays
    # unreadable, so q2's stem can never be matched.
    page = _SingleQuestionPage(["以下关于单一职责原则的叙述不正确的是", ""])
    op = _operator(questions, page)

    outcome = op.answer_homework(_task(), {"q1": "A", "q2": "B"}, submit=True)

    # q1 is answered...
    assert outcome["answered"] == 1
    assert outcome["objective_answered"] == 1
    # ...q2 is reported as skipped with the mapping reason...
    assert outcome["skipped"] == 1
    assert outcome["unlocated_skips"] == 1
    reasons = " ".join(item["reason"] for item in outcome["skipped_questions"])
    assert "找不到对应题干" in reasons
    # ...and it is NOT an error, so the homework still submits.
    assert outcome["errors"] == []
    assert outcome["blocking_skips"] == 0
    assert outcome["can_submit"] is True
    assert outcome["submitted"] is True


def test_mapping_failure_does_not_write_to_the_wrong_question():
    """A stem mismatch after navigation must skip, never fill blindly.

    The page answers a *different* stem than expected, so nothing may be
    typed — but the already-fillable question still goes through.
    """
    questions = [
        _question("q1", 1, "以下关于单一职责原则的叙述不正确的是"),
        _question("q2", 2, "以下关于依赖倒置原则的叙述不正确的是"),
    ]
    page = _SingleQuestionPage(["以下关于单一职责原则的叙述不正确的是",
                                "完全不相干的另一道题目内容"])
    op = _operator(questions, page)

    outcome = op.answer_homework(_task(), {"q1": "A", "q2": "B"}, submit=True)

    assert outcome["unlocated_skips"] == 1
    assert outcome["errors"] == []
    assert outcome["can_submit"] is True
    assert outcome["submitted"] is True


def test_missing_model_answer_still_blocks_submission():
    """The distinction must not swallow real gaps.

    A question skipped for lacking an answer is blocking work, so the
    homework must still refuse to submit — the new non-blocking class covers
    page-mapping failures only.
    """
    questions = [
        _question("q1", 1, "以下关于单一职责原则的叙述不正确的是"),
        _question("q2", 2, "以下关于依赖倒置原则的叙述不正确的是"),
    ]
    page = _SingleQuestionPage(["以下关于单一职责原则的叙述不正确的是",
                                "以下关于依赖倒置原则的叙述不正确的是"])
    op = _operator(questions, page)

    outcome = op.answer_homework(_task(), {"q1": "A"}, submit=True)

    assert outcome["unlocated_skips"] == 0
    assert outcome["blocking_skips"] == 1
    assert outcome["can_submit"] is False
    assert outcome["submitted"] is False
    assert "缺少模型答案" in outcome["skipped_questions"][0]["reason"]
