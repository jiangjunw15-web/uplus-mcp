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

    # ------------------------------------------------------------------ #
    # courses
    # ------------------------------------------------------------------ #
    def list_my_courses(self) -> list[Course]:
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
                    start_ms=raw.get("startTime") or raw.get("startDate"),
                    end_ms=raw.get("endTime") or raw.get("endDate"),
                )
            )
        # Keep configured courses even when the study feed omits them.
        for cid, preset in configured.items():
            if not any(c.id == cid for c in courses):
                courses.append(preset)
        return courses

    def get_course(self, course_id: str) -> Optional[Course]:
        for course in self.list_my_courses():
            if course.id == course_id:
                return course
        return next((c for c in self.config.courses if c.id == course_id), None)

    # ------------------------------------------------------------------ #
    # homeworks
    # ------------------------------------------------------------------ #
    def list_homeworks(self, course_id: str) -> list[HomeworkTask]:
        payload = self.session.api_get(self.HOMEWORKS_PATH.format(course_id=course_id))
        raw_list = _unpack_list(payload)
        return [HomeworkTask.from_api(raw, course_id) for raw in raw_list]

    def homework_detail(self, task_id: str) -> dict:
        payload = self.session.api_get(self.HOMEWORK_DETAIL_PATH.format(task_id=task_id))
        return _unpack_object(payload)

    # ------------------------------------------------------------------ #
    # questions
    # ------------------------------------------------------------------ #
    def list_questions(self, homework_id: str) -> list[Question]:
        payload = self.session.api_get(self.QUESTIONS_PATH.format(homework_id=homework_id))
        raw_list = _unpack_list(payload)
        return [Question.from_meta(raw) for raw in raw_list]

    def question_detail(self, question_id: str) -> Question:
        payload = self.session.api_get(
            self.QUESTION_DETAIL_PATH.format(question_id=question_id)
        )
        return Question.from_detail(_unpack_object(payload))
