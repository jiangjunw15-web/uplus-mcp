"""Local journal of the answers this tool submitted.

The platform hides the standard answer while a homework is still awaiting
grading, so a read-only audit cannot tell whether a submitted multi-select was
partial (漏选). Recording the answers this tool actually filled lets a later
audit compare the platform's ``userAnswer`` with what was intended, and lets
the repair flow restore those answers after a withdrawal.

The file lives in the report folder (gitignored) and contains only question
ids and answer strings; it never stores credentials or page data.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .config import Config
from . import utils

FILENAME = "answer_journal.json"


def journal_path(config: Config) -> Path:
    return config.report_folder / FILENAME


def _load_all(config: Config) -> dict:
    path = journal_path(config)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def record(config: Config, *, task_id: str, homework_id: str, name: str,
           answers: dict[str, str]) -> Path:
    """Persist the normalized answers of a successful submission."""
    data = _load_all(config)
    data[str(task_id)] = {
        "homework_id": str(homework_id),
        "name": name,
        "recorded_at_ms": utils.now_ms(),
        "answers": {
            str(key): str(value)
            for key, value in answers.items()
            if value is not None and str(value).strip()
        },
    }
    path = journal_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def answers_for(config: Config, task_id: str, homework_id: str = "") -> dict[str, str]:
    """Answers recorded for one homework, or an empty map when none exist."""
    entry = _load_all(config).get(str(task_id)) or {}
    if not isinstance(entry, dict):
        return {}
    stored_homework = str(entry.get("homework_id") or "")
    if homework_id and stored_homework and stored_homework != str(homework_id):
        return {}
    answers = entry.get("answers") or {}
    if not isinstance(answers, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in answers.items()
        if value is not None and str(value).strip()
    }
