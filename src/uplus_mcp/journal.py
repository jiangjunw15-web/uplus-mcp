"""Local journal of the answers this tool submitted.

The platform hides the standard answer while a homework is still awaiting
grading, so a read-only audit cannot tell whether a submitted multi-select was
partial (漏选). Recording the answers this tool actually filled lets a later
audit compare the platform's ``userAnswer`` with what was intended, and lets
the repair flow restore those answers after a withdrawal.

The file lives in the report folder (gitignored) and contains only question
ids and answer strings; it never stores credentials or page data.

A journal that cannot be parsed is never overwritten silently: the broken
bytes are copied to ``<name>.corrupt-<stamp>.bak`` before the file is
rewritten, so a corrupted journal can be repaired by hand instead of
silently losing every recorded answer.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Optional

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


def _read_all(config: Config) -> tuple[dict, Optional[str]]:
    """Read the journal.

    Returns ``(data, problem)``. ``problem`` is ``None`` for a healthy or
    missing journal and a short description of what is wrong otherwise. A
    broken file is never repaired or truncated by a read: the bytes stay on
    disk until :func:`record` copies them aside.
    """
    path = journal_path(config)
    key = str(path)
    try:
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        with _lock:
            _cache.pop(key, None)
        return {}, None
    with _lock:
        hit = _cache.get(key)
        if hit is not None and (hit[0], hit[1]) == stamp:
            return hit[2], None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, f"读取失败：{exc}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"不是合法 JSON（第 {exc.lineno} 行第 {exc.colno} 列：{exc.msg}）"
    if not isinstance(data, dict):
        return {}, f"顶层不是 JSON 对象（当前为 {type(data).__name__}）"
    with _lock:
        _cache[key] = (stamp[0], stamp[1], data)
    return data, None


def _load_all(config: Config) -> dict:
    """The recorded answers, or an empty map when the journal is unusable."""
    data, _ = _read_all(config)
    return data


def _backup_corrupt(path: Path) -> Path:
    """Copy a broken journal aside so its bytes are never lost.

    The copy happens *before* the journal is rewritten. If it fails the
    error is raised: overwriting the original then would throw away the
    recorded answers, which are the basis of the cross-account reuse and of
    the repair flow.
    """
    stamp = datetime.datetime.now(tz=utils.CN_TZ).strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.corrupt-{stamp}.bak")
    counter = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.corrupt-{stamp}-{counter}.bak")
        counter += 1
    shutil.copy2(path, backup)
    return backup


def record(config: Config, *, task_id: str, homework_id: str, name: str,
           answers: dict[str, str], notes: Optional[list[str]] = None) -> Path:
    """Persist the normalized answers of a successful submission.

    A journal that cannot be parsed is not silently overwritten: the broken
    bytes are copied to ``<name>.corrupt-<stamp>.bak`` first, so the recorded
    answers can be restored by hand. The new journal then holds the current
    submission only, and the caller is told about the recovery through
    ``notes`` (or on stderr when no list is given).
    """
    data, problem = _read_all(config)
    # Work on a copy: the cached snapshot must not change when the write
    # below fails half-way.
    data = dict(data)
    path = journal_path(config)
    if problem:
        backup = _backup_corrupt(path)
        message = (
            f"答案簿损坏（{problem}），原文件已备份为 {backup.name}；"
            "本次记录只含当前提交，旧记录请从备份恢复"
        )
        if notes is None:
            print(f"⚠️ {message}", file=sys.stderr)
        else:
            notes.append(message)
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
