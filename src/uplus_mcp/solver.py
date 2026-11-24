"""Answer preparation and model-answer normalization.

The MCP server never keeps a private question bank: it hands structured
question bundles to the MCP client model (Claude, Cursor agent, Doubao agent,
etc.), receives normalized answers back, and lets ``operator.py`` fill the page.
A small rule hook exists for future course-specific fallbacks but returns
``None`` by default.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from .models import Question, QuestionType, count_blanks
from . import utils


def build_answer_brief(homework_name: str, questions: list[Question]) -> dict:
    """Structured payload an MCP client model should use to answer."""
    # Sort by question order so the brief always lists questions 1,2,3…
    # regardless of the API's return order (which can be 3,2,1 on some
    # course packs, the reported "随堂测试 items 顺序是 3,2,1 / 7,8,6").
    sorted_questions = sorted(questions, key=lambda q: q.order)
    return {
        "homework": homework_name,
        "instructions": (
            "逐题作答。单选返回单个选项字母；多选返回去重排序字母组合；"
            "判断题返回 true（正确）或 false（错误）；填空题返回文本，"
            "题干有多个空时（参考每题的 blank_count）每空一行，依序返回所有答案。"
            "题目带 images / figure_note 时，文字不足以作答：先调用 "
            "uplus_capture_question_image(task_id, course_id, order=题号) "
            "查看题目截图，再给出答案。题干提到「图 / 模型 / 流程 / 下图」"
            "但正文没有对应内容时，同样先截图确认。"
            "输出 JSON：{\"answers\": {\"<question_id>\": \"<answer>\"}}，不要解释。"
        ),
        "questions": [q.llm_prompt() for q in sorted_questions],
    }


def parse_model_answers(model_payload: str | dict | None) -> dict[str, str]:
    """Normalize a model response into ``{question_id: answer}``.

    Accepts:
      - ``{"answers": {"qid": "C"}}``
      - ``{"qid": "C"}``
      - Markdown-fenced JSON containing either shape.
    """
    if model_payload is None:
        return {}
    if isinstance(model_payload, dict):
        data = model_payload
    else:
        text = str(model_payload).strip()
        fence = re_search_json(text)
        try:
            data = json.loads(fence)
        except json.JSONDecodeError:
            return {}
    # A model may answer with a bare JSON array or a scalar; only an object
    # carries the answer map, everything else is "no answers".
    if not isinstance(data, dict):
        return {}
    answers = data.get("answers", data)
    if not isinstance(answers, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in answers.items():
        if value is None:
            continue
        result[str(key)] = str(value)
    return result


def re_search_json(text: str) -> str:
    """Extract the first JSON object from a model message, including fences."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        return fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


# A leading choice group: letter runs joined by the separators students use.
# It stops before prose ("AC because..." -> "AC"), so English explanation
# letters can never be mistaken for options.
_LEADING_CHOICES_RE = re.compile(
    r"^\s*[（(]?\s*[A-Za-z]+(?:\s*[,，、/和与及]\s*[A-Za-z]+)*"
)


def _valid_codes(question: Question) -> set[str]:
    return {
        option.code.strip().upper()
        for option in question.options
        if re.fullmatch(r"[A-Za-z]", option.code.strip())
    }


def _choice_letters(question: Question, raw: str) -> str:
    """Normalize a choice answer, keeping only real option letters.

    ``utils.normalize_letters`` extracts every Latin letter from the payload,
    so an answer like ``"AC，因为B中的..."`` or ``"AC because ..."`` would pick
    up spurious letters and silently select wrong options. The leading answer
    token is preferred; if it is not composed of real option letters, the whole
    string is scanned and intersected with the question's options instead.
    """
    valid = _valid_codes(question)
    lead = _LEADING_CHOICES_RE.match(str(raw or ""))
    if lead:
        letters = utils.normalize_letters(lead.group(0))
        if letters and (not valid or set(letters) <= valid):
            return letters
    letters = utils.normalize_letters(raw)
    if valid:
        filtered = "".join(ch for ch in letters if ch in valid)
        if filtered:
            return filtered
    return letters


#: Run of ``@`` the platform uses to join several fill-blank boxes in one
#: ``userAnswer`` string (``@GetMapping@@@@PostMapping`` for two annotation
#: blanks). Only consulted when the ordinary separators come up short.
_BLANK_JOIN_RE = re.compile(r"@{2,}")


def split_blanks(raw: object, expected: int = 0) -> list[str]:
    """Split one fill-blank answer into its per-box values.
    Model answers arrive in several shapes:

    * ``"@GetMapping\\n@PostMapping"`` — one value per line (preferred);
    * ``["@GetMapping", "@PostMapping"]`` — a JSON array string;
    * ``"@GetMapping；@PostMapping"`` — a common Chinese separator.

    ``expected`` is the number of blanks the stem has. It is 0 when the count
    is unknown: then only the unambiguous separators (newline, JSON array) are
    used, so a single answer that contains a semicolon is not torn apart.
    With a known count above one the Chinese separators are accepted too.

    The newline split is always the primary one. A value may legitimately
    contain a comma (a list of type names, an address, a date), and tearing
    it apart put half a value into one box and the other half into the next
    one — with the count still lining up, so nothing complained and a wrong
    answer was submitted. The Chinese / comma separators are therefore only a
    fallback, used when the newline split did not deliver enough values, and
    even then only when they deliver *exactly* the expected number: a split
    that yields more values than there are boxes is ambiguous, so the
    conservative newline split is kept and the count mismatch is reported
    instead of writing a wrong box.

    The platform's own ``userAnswer`` joins the boxes with a run of ``@``
    (the live record for 「@GetMapping / @PostMapping」 comes back as
    ``@GetMapping@@@@PostMapping``), so that join is accepted as a last resort
    when nothing else yields the expected number of values.

    The normalized result drops empty entries but otherwise keeps each value
    verbatim (annotations like ``@RequestMapping`` contain ``@``).
    """
    if raw is None:
        return []
    text = "" if isinstance(raw, (list, tuple)) else str(raw).strip()
    loose_allowed = False
    if isinstance(raw, (list, tuple)):
        parts = [str(item) for item in raw]
    elif not text:
        return []
    elif text.startswith("[") and text.endswith("]"):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, (list, tuple)):
            parts = [str(item) for item in decoded]
        else:
            parts = [text]
    else:
        parts = re.split(r"\r?\n", text)
        loose_allowed = True
    values = [part.strip() for part in parts if part and part.strip()]
    if loose_allowed and expected > 1 and len(values) < expected:
        # The brief asks for one value per line, but a model routinely returns
        # them comma-separated instead (``"@GetMapping,@PostMapping"``). Not
        # splitting on the comma left the whole payload as a single value, so
        # ``normalize_answer`` rejected it ("有 2 个空，只收到 1 个答案") and the
        # entire homework stalled on an answer that was actually correct.
        # The fallback only runs when the line split came up short, and only
        # its exact-match result is used: a count that lines up may still be
        # wrong (a value with a comma torn in two), and ``_answer_blank``
        # cannot detect that. Anything else keeps the line split, so the
        # count mismatch is reported instead of a wrong box being written.
        loose = [
            part.strip()
            for part in re.split(r"\r?\n|；|;|，|,|、|｜|\|", text)
            if part and part.strip()
        ]
        if len(loose) == expected:
            values = loose
    if expected > 1 and len(values) < expected and _BLANK_JOIN_RE.search(text):
        # The platform joins several boxes into one string with a run of ``@``
        # (the live record for 「@GetMapping / @PostMapping」 is
        # ``@GetMapping@@@@PostMapping``). Treat that run as the separator so
        # the answer is not misread as one filled box out of two.
        joined = [part.strip() for part in _BLANK_JOIN_RE.split(text) if part.strip()]
        if len(joined) == expected:
            return joined
    return values


def normalize_answer(question: Question, raw: str) -> str:
    """Validate and normalize one answer against its question type."""
    if question.type is QuestionType.SINGLE:
        letters = _choice_letters(question, raw)[:1]
        if not letters:
            raise ValueError(f"单选题答案缺少选项字母: {raw!r}")
        return letters
    if question.type is QuestionType.MULTIPLE:
        letters = _choice_letters(question, raw)
        if not letters:
            raise ValueError(f"多选题答案缺少选项字母: {raw!r}")
        return letters
    if question.type is QuestionType.JUDGE:
        value = utils.normalize_bool(raw)
        if value is None:
            raise ValueError(f"判断题答案非法: {raw!r}")
        return "true" if value else "false"
    if question.type is QuestionType.FILL_BLANK:
        expected = count_blanks(question.stem)
        values = split_blanks(raw, expected)
        if expected > 1 and len(values) < expected:
            raise ValueError(
                f"填空题有 {expected} 个空，只收到 {len(values)} 个答案: {raw!r}"
            )
        # Preserve the canonical joined form (newline-separated) so a single
        # blank still returns plain text and the multi-blank answer can be
        # split deterministically by the fill layer and the audit.
        return "\n".join(values)
    return raw.strip()


def rule_based_answer(question: Question) -> Optional[str]:
    """Course-specific fallback hook. Returns None so the model decides."""
    # Extend here with verified, course-specific rules only after human review.
    return None
