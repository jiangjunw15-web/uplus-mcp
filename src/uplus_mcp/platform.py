"""Read-only U+ REST API client.

Endpoints captured against ``https://www.eduplus.net`` on 2026-10-25; the full
mapping lives in ``docs/api-mapping.md``. All calls run inside the browser
session so cookies and SPA tokens are reused automatically.
"""

from __future__ import annotations

from typing import Any, Optional

from .browser import BrowserSession
from .config import Config
from .models import Course, HomeworkTask, Question
from . import utils


def _unpack_list(payload: Any) -> list[dict]:
    """Accept either a bare JSON array or ``{"data": [...]}`` from the feed.

    The U+ study/homework feeds are inconsistent across courses: some return a
    top-level array, others wrap the list under ``data``.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        raw = payload.get("data")
        return raw if isinstance(raw, list) else []
    return []


def _unpack_object(payload: Any) -> dict:
    """Accept either a bare object or ``{"data": {...}}``."""
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), dict):
            return payload["data"]
        return payload
    return {}


class PlatformClient:
    COURSES_PATH = "/api/course/athena/courses/study?types=Theory%2CTrain"
    HOMEWORKS_PATH = "/api/course/homeworks/published/student?courseId={course_id}"
    HOMEWORK_DETAIL_PATH = "/api/course/homeworks/published/{task_id}/student"
    QUESTIONS_PATH = "/api/course/homeworkQuestions/student?homeworkId={homework_id}"
    QUESTION_DETAIL_PATH = "/api/course/homeworkQuestions/{question_id}/student/detail"

    def __init__(self, session: BrowserSession, config: Config) -> None:
        self.session = session
        self.config = config
        # The course feed changes rarely but was re-requested on every call:
        # ``get_course`` → ``list_my_courses``, and a patrol resolves a course
        # name several times per homework. The feed is fetched once and reused;
        # ``invalidate_courses`` forces a fresh read when the user explicitly
        # asks what is on the account right now.
        self._courses_cache: Optional[list[Course]] = None

    # ------------------------------------------------------------------ #
    # courses
    # ------------------------------------------------------------------ #
    def invalidate_courses(self) -> None:
        """Drop the cached course feed so the next read hits the network."""
        self._courses_cache = None

    def list_my_courses(self, *, refresh: bool = False) -> list[Course]:
        """The account's courses, from the cached feed when possible.

        ``refresh=True`` re-requests the feed and replaces the cache.
        """
        if refresh:
            self._courses_cache = None
        if self._courses_cache is not None:
            return list(self._courses_cache)
        payload = self.session.api_get(self.COURSES_PATH)
        raw_courses = _unpack_list(payload)
        configured = {c.id: c for c in self.config.courses}
        courses: list[Course] = []
        for raw in raw_courses:
            cid = str(raw.get("id") or raw.get("courseId") or "")
            if not cid:
                continue
            preset = configured.get(cid)
            courses.append(
                Course(
                    id=cid,
                    name=str(raw.get("name") or (preset.name if preset else "")),
                    teacher=str(raw.get("teacherName") or raw.get("creator")
                               or (preset.teacher if preset else "")),
                    start_ms=utils.to_epoch_ms(raw.get("startTime") or raw.get("startDate")),
                    end_ms=utils.to_epoch_ms(raw.get("endTime") or raw.get("endDate")),
                )
            )
        # Keep configured courses even when the study feed omits them.
        known = {c.id for c in courses}
        for cid, preset in configured.items():
            if cid not in known:
                courses.append(preset)
        self._courses_cache = courses
        return list(courses)

    def get_course(self, course_id: str) -> Optional[Course]:
        for course in self.list_my_courses():
            if course.id == course_id:
                return course
        return next((c for c in self.config.courses if c.id == course_id), None)

    # ------------------------------------------------------------------ #
    # homeworks
    # ------------------------------------------------------------------ #
    def list_homeworks(self, course_id: str) -> list[HomeworkTask]:
        safe_cid = utils.safe_url_segment(course_id, "course_id")
        payload = self.session.api_get(self.HOMEWORKS_PATH.format(course_id=safe_cid))
        raw_list = _unpack_list(payload)
        return [HomeworkTask.from_api(raw, course_id) for raw in raw_list]

    def homework_detail(self, task_id: str) -> dict:
        safe_tid = utils.safe_url_segment(task_id, "task_id")
        payload = self.session.api_get(self.HOMEWORK_DETAIL_PATH.format(task_id=safe_tid))
        return _unpack_object(payload)

    # ------------------------------------------------------------------ #
    # questions
    # ------------------------------------------------------------------ #
    def list_questions(self, homework_id: str) -> list[Question]:
        safe_hid = utils.safe_url_segment(homework_id, "homework_id")
        payload = self.session.api_get(self.QUESTIONS_PATH.format(homework_id=safe_hid))
        raw_list = _unpack_list(payload)
        return [Question.from_meta(raw) for raw in raw_list]

    def question_detail(self, question_id: str) -> Question:
        safe_qid = utils.safe_url_segment(question_id, "question_id")
        payload = self.session.api_get(
            self.QUESTION_DETAIL_PATH.format(question_id=safe_qid)
        )
        return Question.from_detail(_unpack_object(payload))

    def question_details(self, question_ids: list[str]) -> list[Question]:
        """Full details for several questions, fetched in one round-trip.

        Reading the details one by one cost one browser round-trip per
        question, so a 30-question homework paid 30 sequential round-trips
        before the model could even see the questions. The batched read
        issues the same requests in parallel inside the page and returns the
        questions in the same order, so the data is identical — only the
        number of round-trips drops from N to 1.

        Falls back to the per-question path when the client has no batched
        read (older sessions, test doubles).
        """
        if not question_ids:
            return []
        # Look the batched read up on the *class*, not the instance: a test
        # double (``Mock``) auto-creates any attribute asked of it, so an
        # instance lookup would find a non-callable stub instead of the real
        # implementation. The call itself still goes through the instance, so
        # an override or a patched session is honoured.
        if getattr(type(self.session), "api_get_many", None) is None:
            return [self.question_detail(qid) for qid in question_ids]
        paths = [
            self.QUESTION_DETAIL_PATH.format(
                question_id=utils.safe_url_segment(qid, "question_id")
            )
            for qid in question_ids
        ]
        payloads = self.session.api_get_many(paths)
        return [
            Question.from_detail(_unpack_object(payload))
            for payload in payloads
        ]

