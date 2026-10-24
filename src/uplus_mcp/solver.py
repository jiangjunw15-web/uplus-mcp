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


def normalize_answer(question: Question, raw: str) -> str:
    """Validate and normalize one answer against its question type."""
    if question.type is QuestionType.SINGLE:
        return utils.normalize_letters(raw)[:1]
    if question.type is QuestionType.MULTIPLE:
        return utils.normalize_letters(raw)
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
