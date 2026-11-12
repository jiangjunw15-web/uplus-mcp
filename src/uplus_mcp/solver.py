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

from .models import Question, QuestionType
from . import utils


def build_answer_brief(homework_name: str, questions: list[Question]) -> dict:
    """Structured payload an MCP client model should use to answer."""
    return {
        "homework": homework_name,
        "instructions": (
            "逐题作答。单选返回单个选项字母；多选返回去重排序字母组合；"
            "判断题返回 true（正确）或 false（错误）；填空题返回文本。"
            "题目带 images / figure_note 时，文字不足以作答：先调用 "
            "uplus_capture_question_image(task_id, course_id, order=题号) "
            "查看题目截图，再给出答案。"
            "输出 JSON：{\"answers\": {\"<question_id>\": \"<answer>\"}}，不要解释。"
        ),
        "questions": [q.llm_prompt() for q in questions],
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
    return raw.strip()


def rule_based_answer(question: Question) -> Optional[str]:
    """Course-specific fallback hook. Returns None so the model decides."""
    # Extend here with verified, course-specific rules only after human review.
    return None
