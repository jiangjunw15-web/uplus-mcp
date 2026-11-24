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
