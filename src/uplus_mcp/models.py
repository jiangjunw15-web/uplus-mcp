"""Domain models and U+ platform status enums.

Field mapping was captured against ``https://www.eduplus.net`` on 2026-10-25.
See ``docs/api-mapping.md`` for the raw API shape and page routes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, Enum
from typing import Optional, Any

from . import utils


# --------------------------------------------------------------------------- #
# Question type codes returned by /api/course/homeworkQuestions/.../student/detail
# --------------------------------------------------------------------------- #
class QuestionType(IntEnum):
    SINGLE = 1          # 单选题, userAnswer = "D"
    MULTIPLE = 2        # 多选题, userAnswer = "ABCD" (no separator, sorted)
    JUDGE = 3           # 判断题, options empty, userAnswer = "true" / "false"
    FILL_BLANK = 4     # 填空题 (code observed on other course packs)
    SHORT_ANSWER = 5   # 简答题
    PROGRAM = 6        # 编程题
    ATTACHMENT = 7     # 附件提交题（页面常显示为简答题，实为文件上传）
    UNKNOWN = 99

    @property
    def label(self) -> str:
        return {
            QuestionType.SINGLE: "单选题",
            QuestionType.MULTIPLE: "多选题",
            QuestionType.JUDGE: "判断题",
            QuestionType.FILL_BLANK: "填空题",
            QuestionType.SHORT_ANSWER: "简答题",
            QuestionType.PROGRAM: "编程题",
            QuestionType.ATTACHMENT: "附件题",
            QuestionType.UNKNOWN: "未知题型",
        }[self]

    @property
    def auto_answerable(self) -> bool:
        """Whether the MCP server may fill this question itself.

        Only fixed-option objective questions are auto-filled. Open answers,
        programming work and file submissions are left to the human.
        """
        return self in (QuestionType.SINGLE, QuestionType.MULTIPLE,
                        QuestionType.JUDGE, QuestionType.FILL_BLANK)


def question_type(code: Any) -> QuestionType:
    try:
        return QuestionType(int(code))
    except (TypeError, ValueError):
        return QuestionType.UNKNOWN


def _is_answered(raw: dict) -> bool:
    """Joint answered check: ``isAnswer`` may be missing/null, so a non-empty
    ``userAnswer`` or ``hwAnswerId`` also counts as answered."""
    if raw.get("userAnswer") not in (None, ""):
        return True
    if raw.get("hwAnswerId") not in (None, ""):
        return True
    flag = raw.get("isAnswer")
    return flag in (1, "1", True, "true")


# --------------------------------------------------------------------------- #
# Homework submission status
# --------------------------------------------------------------------------- #
class HomeworkStatus(str, Enum):
    SUBMITTED_GRADED = "submitted_graded"      # 已提交且已出分/批改
    SUBMITTED_PENDING = "submitted_pending"    # 已提交，待老师批改
    NOT_SUBMITTED = "not_submitted"            # 页面按钮为「提交作业」
    NO_HOMEWORK = "no_homework"                # 老师未布置


# --------------------------------------------------------------------------- #
# Domain objects
# --------------------------------------------------------------------------- #
@dataclass
class Course:
    id: str
    name: str
    teacher: str = ""
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None


@dataclass
class Option:
    code: str
    content: str

    @classmethod
    def from_api(cls, raw: dict) -> "Option":
        return cls(code=str(raw.get("id", "")),
                   content=utils.strip_html(raw.get("optionContent")))

    def prompt_line(self) -> str:
        return f"{self.code}、{self.content}"


@dataclass
class Question:
    question_id: str
    order: int
    type: QuestionType
    score: Optional[float] = None
    stem: str = ""
    options: list[Option] = field(default_factory=list)
    knowledge: str = ""
    labels: str = ""
    my_answer: Optional[str] = None
    answered: bool = False
    hw_answer_id: Optional[str] = None

    @classmethod
    def from_meta(cls, raw: dict) -> "Question":
        """Lightweight metadata item from the question list endpoint."""
        return cls(
            question_id=str(raw.get("id", "")),
            order=int(raw.get("orderNumber", 0) or 0),
            type=question_type(raw.get("qsnType")),
            answered=_is_answered(raw),
        )

    @classmethod
    def from_detail(cls, raw: dict) -> "Question":
        detail = cls.from_meta(raw)
        detail.score = raw.get("everyScore")
        detail.stem = utils.strip_html(raw.get("titleText"))
        detail.options = [Option.from_api(o) for o in (raw.get("options") or [])]
        detail.knowledge = str(raw.get("knowledges") or "")
        detail.labels = str(raw.get("labels") or "")
        detail.my_answer = raw.get("userAnswer") or None
        detail.hw_answer_id = raw.get("hwAnswerId") or None
        return detail

    @property
    def auto_answerable(self) -> bool:
        return self.type.auto_answerable

    def llm_prompt(self) -> dict:
        """Structured payload handed to the MCP client model for answering."""
        payload: dict[str, Any] = {
            "question_id": self.question_id,
            "order": self.order,
            "type": self.type.label,
            "type_code": int(self.type),
            "score": self.score,
            "stem": self.stem,
            "knowledge": self.knowledge,
            "answered": self.answered,
        }
        if self.options:
            payload["options"] = [o.prompt_line() for o in self.options]
        if self.type is QuestionType.JUDGE:
            payload["answer_hint"] = "回答 true（正确）或 false（错误）"
        elif self.type is QuestionType.SINGLE:
            payload["answer_hint"] = "回答单个选项字母，如 C"
        elif self.type is QuestionType.MULTIPLE:
            payload["answer_hint"] = "回答选项字母组合，如 ACD"
        elif self.type is QuestionType.FILL_BLANK:
            payload["answer_hint"] = "回答填空文本"
        elif self.type is QuestionType.ATTACHMENT:
            payload["answer_hint"] = "附件提交题：不可自动作答，需本人上传文件，人工接管"
        else:
            payload["answer_hint"] = "主观/编程题，需本人完成，人工接管"
        return payload


@dataclass
class HomeworkTask:
    task_id: str
    homework_id: str
    course_id: str
    name: str
    end_ms: Optional[int] = None
    submit_task: Optional[str] = None
    review_task: Optional[str] = None
    sub_status: Optional[str] = None
    question_count: int = 0
    weight: Optional[float] = None
    show_score: bool = True
    raw: dict = field(default_factory=dict)
    # Filled in after opening the answer page and reading the real button label.
    page_status: Optional[HomeworkStatus] = None
    page_progress: Optional[str] = None  # e.g. "32/32"
    score: Optional[float] = None

    @property
    def status(self) -> HomeworkStatus:
        """Best-effort status from API; the operator overrides with page truth."""
        if self.page_status is not None:
            return self.page_status
        if str(self.submit_task) == "1":
            if str(self.sub_status) == "3":
                return HomeworkStatus.SUBMITTED_GRADED
            return HomeworkStatus.SUBMITTED_PENDING
        return HomeworkStatus.NOT_SUBMITTED

    @property
    def pending_takeover(self) -> bool:
        """True when this homework cannot be finished automatically."""
        return self.status in (HomeworkStatus.NOT_SUBMITTED,) and self.question_count == 0

    def is_expired(self, reference_ms: Optional[int] = None) -> bool:
        return utils.is_expired(self.end_ms, reference_ms)

    @classmethod
    def from_api(cls, raw: dict, course_id: str) -> "HomeworkTask":
        dto = raw.get("homeworkDTO") or {}
        if isinstance(dto, str):
            import json
            try:
                dto = json.loads(dto)
            except json.JSONDecodeError:
                dto = {}
        return cls(
            task_id=str(raw.get("id", "")),
            homework_id=str(raw.get("homeworkId", "")),
            course_id=course_id,
            name=str(dto.get("name") or "未命名作业"),
            end_ms=raw.get("endTime"),
            submit_task=str(raw.get("submitTask")) if raw.get("submitTask") is not None else None,
            review_task=str(raw.get("reviewTask")) if raw.get("reviewTask") is not None else None,
            sub_status=str(dto.get("subStatus")) if dto.get("subStatus") is not None else None,
            question_count=int(dto.get("questionCount") or 0),
            weight=raw.get("weight"),
            show_score=bool(raw.get("showScore", 1)),
            score=dto.get("userScore"),
            raw=raw,
        )


@dataclass
class Experiment:
    name: str
    submitted: Optional[bool] = None
    deadline_ms: Optional[int] = None
    material: str = ""
    raw_text: str = ""


@dataclass
class AnswerOutcome:
    task_id: str
    name: str
    course_name: str
    status: str
    answered: int = 0
    skipped: int = 0
    submitted: bool = False
    skipped_questions: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    note: str = ""
