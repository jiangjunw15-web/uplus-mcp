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
import tempfile
import threading
from pathlib import Path

from .config import Config
from . import utils

FILENAME = "answer_journal.json"

#: Guards the in-memory cache so a concurrent reader never sees a torn
#: snapshot while ``record`` rewrites the file.
_lock = threading.Lock()
#: ``path -> (mtime_ns, size, data)``. The journal is read once per audited
#: homework, so a patrol over N homeworks re-read and re-parsed the same file
#: N times; the cache makes every read after the first free. The mtime and
#: size are part of the key, so an external edit is still picked up.
_cache: dict[str, tuple[int, int, dict]] = {}


def journal_path(config: Config) -> Path:
    """Where the answer journal lives.

    ``UPLUS_JOURNAL_PATH`` overrides the default ``<report folder>/answer_journal.json``.
    Multi-account setups are told to keep separate working directories; pointing
    every account at one shared journal file is what makes cross-account reuse
    actually visible, instead of each account seeing only its own submissions.
    """
    override = os.environ.get("UPLUS_JOURNAL_PATH")
    if override:
        return Path(override).expanduser()
    return config.report_folder / FILENAME


def _load_all(config: Config) -> dict:
    path = journal_path(config)
    key = str(path)
    try:
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        with _lock:
            _cache.pop(key, None)
        return {}
    with _lock:
        hit = _cache.get(key)
        if hit is not None and (hit[0], hit[1]) == stamp:
            return hit[2]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    with _lock:
        _cache[key] = (stamp[0], stamp[1], data)
    return data



def record(config: Config, *, task_id: str, homework_id: str, name: str,
           answers: dict[str, str]) -> Path:
    """Persist the normalized answers of a successful submission."""
    data = dict(_load_all(config))
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
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".answer_journal_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    # The file changed under the cache's feet: drop the entry so the next read
    # parses the new content instead of the pre-write snapshot.
    with _lock:
        _cache.pop(str(path), None)
    return path


def answer_index(config: Config) -> dict[str, dict]:
    """Every recorded answer, indexed by ``question_id``.

    Entries are keyed by ``task_id`` in the file, so a homework answered on one
    account is invisible to another account by exact lookup even when the
    question bank is identical (``question_id`` is assigned by the platform per
    question, not per student). This index exposes those answers keyed by the
    question itself; the newest recording wins when a question was submitted
    more than once.

    Returns ``question_id -> {answer, task_id, homework_id, name,
    recorded_at_ms}``. It is a local hint only: the platform hides the standard
    answer while a homework is pending, so a reused answer is a human-reviewed
    prior submission, never a proven correct answer.
    """
    best: dict[str, tuple[int, dict]] = {}
    for task_id, entry in _load_all(config).items():
        if not isinstance(entry, dict):
            continue
        answers = entry.get("answers") or {}
        if not isinstance(answers, dict):
            continue
        try:
            stamp = int(entry.get("recorded_at_ms") or 0)
        except (TypeError, ValueError):
            stamp = 0
        for question_id, answer in answers.items():
            if answer is None or not str(answer).strip():
                continue
            key = str(question_id)
            current = best.get(key)
            if current is not None and stamp < current[0]:
                continue
            best[key] = (stamp, {
                "answer": str(answer),
                "task_id": str(task_id),
                "homework_id": str(entry.get("homework_id") or ""),
                "name": str(entry.get("name") or ""),
                "recorded_at_ms": stamp,
            })
    return {question_id: item for question_id, (_, item) in best.items()}


def answers_by_question_id(config: Config,
                           question_ids: list[str] | None = None) -> dict[str, str]:
    """``question_id -> answer`` for the whole journal, optionally filtered."""
    index = answer_index(config)
    if question_ids is None:
        return {qid: item["answer"] for qid, item in index.items()}
    wanted = {str(q) for q in question_ids}
    return {qid: item["answer"] for qid, item in index.items() if qid in wanted}


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
