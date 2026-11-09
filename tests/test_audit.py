"""提交完整性核查（audit）的离线测试。

覆盖两层：
1. report.audit_questions：按题目判定漏答，主观题不计入"不完整"；
2. engine.audit_homework：读接口即可给出结论，全程不撤回、不提交。
"""

from uplus_mcp.config import Config
from uplus_mcp.engine import PatrolEngine
from uplus_mcp.models import (
    HomeworkStatus,
    HomeworkTask,
    Option,
    Question,
    QuestionType,
)
from uplus_mcp.report import PatrolReport, audit_questions, render_markdown


def Q(order, qtype, answer=None, answered=False, stem="题干"):
    return Question(
        question_id=f"q{order}",
        order=order,
        type=qtype,
        stem=stem,
        options=[Option(code="A", content="甲"), Option(code="B", content="乙")],
        my_answer=answer,
        answered=answered,
    )


# --------------------------------------------------------------------------- #
# audit_questions
# --------------------------------------------------------------------------- #
def test_complete_homework_reports_zero_missing():
    questions = [
        Q(1, QuestionType.SINGLE, "A", True),
        Q(2, QuestionType.MULTIPLE, "AB", True),
        Q(3, QuestionType.JUDGE, "true", True),
    ]
    result = audit_questions(questions)
    assert result["complete"] is True
    assert result["objective"] == 3
    assert result["answered"] == 3
    assert result["missing"] == []


def test_blank_objective_question_is_reported_with_order():
    questions = [
        Q(1, QuestionType.SINGLE, "A", True),
        Q(7, QuestionType.MULTIPLE, "", False, stem="软件安全管理的目标是"),
        Q(9, QuestionType.JUDGE, None, False),
    ]
    result = audit_questions(questions)
    assert result["complete"] is False
    assert [m["order"] for m in result["missing"]] == [7, 9]
    assert result["missing"][0]["type"] == "多选题"
    assert result["answered"] == 1


def test_subjective_items_do_not_count_as_incomplete():
    questions = [
        Q(1, QuestionType.SINGLE, "B", True),
        Q(2, QuestionType.SHORT_ANSWER, None, False, stem="请简述银行家算法"),
    ]
    result = audit_questions(questions)
    assert result["complete"] is True
    assert result["subjective"] == 1
    assert result["objective"] == 1


def test_hw_answer_id_counts_as_answered():
    q = Q(1, QuestionType.SINGLE, None, False)
    q.hw_answer_id = "18872e2309a740ae95fdb131fa0faa24"
    assert audit_questions([q])["complete"] is True


def test_incomplete_section_renders_in_markdown():
    snap = PatrolReport(generated_at_ms=0)
    snap.incomplete.append({
        "course": "信息安全概论", "name": "作业-综合2",
        "missing_count": 2, "missing_orders": [7, 79], "withdrawable": True,
    })
    md = render_markdown(snap)
    assert "## 提交完整性核查" in md
    assert "| 信息安全概论 | 作业-综合2 | 2 | 7、79 | 可撤回 |" in md


# --------------------------------------------------------------------------- #
# engine.audit_homework
# --------------------------------------------------------------------------- #
class FakeClient:
    def __init__(self, details, sub_status="2"):
        self._details = details
        self._sub_status = sub_status

    def list_homeworks(self, course_id):
        return [HomeworkTask(task_id="t1", homework_id="h1", name="作业-综合2",
                             course_id=course_id, submit_task="1",
                             sub_status=self._sub_status)]

    def list_questions(self, homework_id):
        return [Q(order, q["type"], q.get("answer"), bool(q.get("answer")))
                for order, q in self._details.items()]

    def question_detail(self, question_id):
        order = int(question_id[1:])
        q = self._details[order]
        return Q(order, q["type"], q.get("answer"), bool(q.get("answer")),
                 stem=q.get("stem", "题干"))

    def get_course(self, course_id):
        return None


def _engine(details, sub_status="2"):
    cfg = Config()
    return PatrolEngine(cfg, session=None,
                        client=FakeClient(details, sub_status), operator=None)


def test_engine_audit_homework_is_read_only_and_reports_missing():
    engine = _engine({
        1: {"type": QuestionType.SINGLE, "answer": "A"},
        7: {"type": QuestionType.MULTIPLE, "answer": None, "stem": "软件安全管理的目标是"},
        79: {"type": QuestionType.SINGLE, "answer": None, "stem": "软件漏洞主要由因素引起"},
    })
    result = engine.audit_homework("t1", "c1")
    assert result["name"] == "作业-综合2"
    assert result["status"] == "submitted_pending"
    assert result["withdrawable"] is True
    assert result["audit"]["complete"] is False
    assert [m["order"] for m in result["audit"]["missing"]] == [7, 79]
    assert result["audit"]["answered"] == 1


def test_engine_audit_homework_graded_is_not_withdrawable():
    engine = _engine({1: {"type": QuestionType.SINGLE, "answer": "A"}}, sub_status="3")
    result = engine.audit_homework("t1", "c1")
    assert result["withdrawable"] is False
    assert result["audit"]["complete"] is True
