"""A 编程题 that is really a 填空题 sentence.

Some course packs (2024级框架技术与应用开发 among them) return the blank
sentence 「用于表示请求映射的通用注解是 ______」 and 「处理 GET 请求用____注解，
处理 POST 请求用____注解」 under ``qsnType=6`` (编程题), yet the answer page
renders them as 填空题 with 答案一/答案二 input boxes. Trusting the code alone
handed those two questions to the student and left the homework unsubmittable.

These tests pin the stem-based reclassification and make sure genuine
programming prompts are still refused:
"""

from unittest.mock import Mock

from uplus_mcp.config import Config
from uplus_mcp.models import Question, QuestionType, count_blanks, is_blank_style
from uplus_mcp.operator import HomeworkOperator
from uplus_mcp.report import audit_questions


def _raw(qtype: int, stem: str) -> dict:
    return {
        "id": "q-blank",
        "orderNumber": 19,
        "qsnType": qtype,
        "everyScore": 5.0,
        "titleText": stem,
        "options": [],
    }


# --------------------------------------------------------------------------- #
# stem judgement
# --------------------------------------------------------------------------- #
def test_single_blank_sentence_is_blank_style():
    assert is_blank_style("用于表示请求映射的通用注解是 ____________________。")
    assert is_blank_style("处理 GET 请求用______注解，处理 POST 请求用______注解。")


def test_code_prompt_with_blanks_is_not_blank_style():
    stem = "补全下面的代码：\npublic class A {\n  public void run() { ____ }\n}"
    assert not is_blank_style(stem)


def test_plain_sentence_without_blanks_is_not_blank_style():
    assert not is_blank_style("请编写一个 Spring MVC 控制器")
    assert count_blanks("请编写一个 Spring MVC 控制器") == 0


# --------------------------------------------------------------------------- #
# model reclassification
# --------------------------------------------------------------------------- #
def test_program_typed_blank_sentence_becomes_fill_blank():
    q = Question.from_detail(_raw(6, "用于表示请求映射的通用注解是 ______。"))
    assert q.type is QuestionType.FILL_BLANK
    assert q.auto_answerable is True
    assert q.llm_prompt()["blank_count"] == 1


def test_two_blank_program_sentence_asks_for_two_lines():
    q = Question.from_detail(_raw(6, "处理 GET 请求用____注解，处理 POST 请求用____注解。"))
    payload = q.llm_prompt()
    assert payload["blank_count"] == 2
    assert "2 个空" in payload["answer_hint"]


def test_real_program_question_stays_program():
    q = Question.from_detail(_raw(6, "补全代码：\npublic void run() { ______ }"))
    assert q.type is QuestionType.PROGRAM
    assert q.auto_answerable is False


def test_program_question_with_options_is_not_reclassified():
    raw = _raw(6, "通用注解是 ______")
    raw["options"] = [{"optionCode": "A", "optionContent": "@RequestMapping"}]
    q = Question.from_detail(raw)
    assert q.type is QuestionType.PROGRAM


def test_short_answer_with_blanks_stays_human_work():
    # Only 编程题(6) is reclassified; 简答题 keeps the human boundary.
    q = Question.from_detail(_raw(5, "谈谈 ______ 的含义"))
    assert q.type is QuestionType.SHORT_ANSWER
    assert q.auto_answerable is False


# --------------------------------------------------------------------------- #
# filling + auditing a reclassified question
# --------------------------------------------------------------------------- #
class _Box:
    def __init__(self):
        self.value = ""


class _Handle:
    def __init__(self, box):
        self._box = box

    def fill(self, value, timeout=None):
        self._box.value = value

    def input_value(self):
        return self._box.value

    def inner_text(self):
        return self._box.value


class _Locator:
    def __init__(self, page):
        self._page = page

    @property
    def first(self):
        return self

    def count(self):
        return len(self._page.boxes)

    def nth(self, index):
        return _Handle(self._page.boxes[index])

    def is_visible(self):
        return True


class _Page:
    def __init__(self, boxes):
        self.boxes = [_Box() for _ in range(boxes)]

    def evaluate(self, script, arg=None):
        return False

    def locator(self, selector):
        return _Locator(self)

    def inner_text(self, selector="body"):
        return "题目正文"


def test_reclassified_question_fills_one_box_per_blank():
    q = Question.from_detail(_raw(6, "处理 GET 请求用____注解，处理 POST 请求用____注解。"))
    page = _Page(boxes=2)
    out = HomeworkOperator(Mock(), Mock(), Config()).fill_question(
        page, q, "@GetMapping\n@PostMapping", single_view=True,
    )
    assert out == "@GetMapping\n@PostMapping"
    assert [b.value for b in page.boxes] == ["@GetMapping", "@PostMapping"]


def test_audit_counts_half_filled_reclassified_blank_as_partial():
    q = Question.from_detail(_raw(6, "处理 GET 请求用____注解，处理 POST 请求用____注解。"))
    q.my_answer = "@GetMapping"
    result = audit_questions([q])
    assert result["complete"] is False
    assert result["partial"]
    assert result["partial"][0]["missing_blanks"] == 1


def test_audit_accepts_the_platform_at_join_for_two_blanks():
    # The live record for a submitted two-blank answer is one string joined by
    # a run of "@": "@GetMapping@@@@PostMapping". It must count as two boxes.
    q = Question.from_detail(_raw(6, "处理 GET 请求用____注解，处理 POST 请求用____注解。"))
    q.my_answer = "@GetMapping@@@@PostMapping"
    result = audit_questions([q])
    assert result["complete"] is True
    assert not result["partial"]


def test_split_blanks_handles_the_platform_at_join():
    from uplus_mcp.solver import split_blanks
    assert split_blanks("@GetMapping@@@@PostMapping", 2) == ["@GetMapping", "PostMapping"]
    # A single-blank answer keeps its "@" intact.
    assert split_blanks("@RequestMapping", 1) == ["@RequestMapping"]

