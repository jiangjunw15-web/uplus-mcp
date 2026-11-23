"""提交完整性核查（audit）与撤回补交的离线测试。

覆盖：
1. report.audit_questions：漏答、多选漏选、错答、无法核对；
2. engine.audit_homework：读接口即可给出结论，全程不撤回、不提交；
3. engine.patrol：默认核查已提交作业并返回 repair_briefs；
4. engine.repair_homework：有答案才撤回、补齐并重新提交，已出分拒绝。
"""

from pathlib import Path

from uplus_mcp import journal
from uplus_mcp.config import Config
from uplus_mcp.engine import PatrolEngine
from uplus_mcp.models import (
    Course,
    HomeworkStatus,
    HomeworkTask,
    Option,
    Question,
    QuestionType,
)
from uplus_mcp.report import PatrolReport, audit_questions, render_markdown


def Q(order, qtype, answer=None, answered=False, stem="题干",
      reference=None, is_correct=None, user_score=None):
    return Question(
        question_id=f"q{order}",
        order=order,
        type=qtype,
        stem=stem,
        options=[Option(code="A", content="甲"), Option(code="B", content="乙"),
                 Option(code="C", content="丙"), Option(code="D", content="丁")],
        my_answer=answer,
        answered=answered,
        reference_answer=reference,
        is_correct=is_correct,
        user_score=user_score,
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


def test_hw_answer_id_without_user_answer_is_reported_missing():
    """An answer row exists as soon as the question is opened; only a
    non-empty userAnswer proves the student actually chose something."""
    q = Q(1, QuestionType.SINGLE, None, False)
    q.hw_answer_id = "18872e2309a740ae95fdb131fa0faa24"
    result = audit_questions([q])
    assert result["complete"] is False
    assert [m["order"] for m in result["missing"]] == [1]


def test_multiselect_partial_is_detected_against_standard_answer():
    """The exact reported failure: A selected, standard answer ABC."""
    q = Q(3, QuestionType.MULTIPLE, "A", True, reference="ABC")
    result = audit_questions([q])
    assert result["complete"] is False
    assert result["partial_count"] == 1
    assert result["partial"][0]["order"] == 3
    assert result["partial"][0]["missing_letters"] == "BC"


def test_multiselect_order_does_not_matter_when_comparing():
    q = Q(4, QuestionType.MULTIPLE, "AB", True, reference="BA")
    result = audit_questions([q])
    assert result["complete"] is True
    assert result["correct"] == 1


def test_multiselect_extra_letters_are_wrong_but_not_incomplete():
    q = Q(5, QuestionType.MULTIPLE, "ABCD", True, reference="AB")
    result = audit_questions([q])
    assert result["complete"] is True
    assert result["clean"] is False
    assert result["wrong"][0]["extra_letters"] == "CD"


def test_single_choice_difference_is_reported_as_wrong():
    q = Q(6, QuestionType.SINGLE, "A", True, reference="B")
    result = audit_questions([q])
    assert result["wrong_count"] == 1
    assert result["complete"] is True


def test_journal_answer_detects_partial_before_grading():
    """The platform hides ``answer`` while pending; the local journal fills in."""
    q = Q(7, QuestionType.MULTIPLE, "A", True)
    result = audit_questions([q], expected_answers={"q7": "ABC"})
    assert result["partial_count"] == 1
    assert result["partial"][0]["missing_letters"] == "BC"
    assert result["partial"][0]["reference_source"] == "journal"


def test_journal_answer_equal_to_submission_is_not_a_reference():
    """The journal records what *this tool* submitted, so it can never judge
    that answer correct. When it equals the submission it is not a reference:
    the question stays ``unjudged`` rather than being scored correct.

    This is the circular-reasoning bug: the fuller the journal, the more 漏选
    slipped through, because ``selected == expected_letters`` always held.
    """
    q = Q(9, QuestionType.MULTIPLE, "AB", True)
    result = audit_questions([q], expected_answers={"q9": "AB"})
    assert result["correct"] == 0
    assert result["partial_count"] == 0
    assert result["unjudged_count"] == 1
    assert result["complete"] is True     # no proven blank, no proven partial
    assert result["verified"] is False    # but nothing was actually checked


def test_journal_superset_of_submission_reports_the_missing_letters():
    """The recorded intent is a strict superset of what was actually submitted —
    a real 漏选, and the journal is the only source that can prove it."""
    q = Q(10, QuestionType.MULTIPLE, "AB", True)
    result = audit_questions([q], expected_answers={"q10": "ABCD"})
    assert result["partial_count"] == 1
    assert result["partial"][0]["missing_letters"] == "CD"
    assert result["complete"] is False


def test_platform_answer_still_wins_over_the_journal():
    """A graded homework exposes the platform's own answer; it must be used, not
    the journal (which may record an older, incomplete submission)."""
    q = Q(11, QuestionType.MULTIPLE, "AB", True, reference="ABCD")
    result = audit_questions([q], expected_answers={"q11": "AB"})
    assert result["partial_count"] == 1
    assert result["partial"][0]["missing_letters"] == "CD"
    assert result["partial"][0]["reference_source"] == "platform"


def test_multiselect_without_any_reference_is_unjudged_not_complete():
    q = Q(8, QuestionType.MULTIPLE, "AB", True)
    result = audit_questions([q])
    assert result["complete"] is True       # no blank, no known partial
    assert result["verified"] is False      # but completeness was not proven
    assert result["unjudged_count"] == 1


def test_incomplete_section_renders_in_markdown():
    snap = PatrolReport(generated_at_ms=0)
    snap.incomplete.append({
        "course": "信息安全概论", "name": "作业-综合2",
        "missing_count": 2, "missing_orders": [7, 79], "withdrawable": True,
        "partial_count": 0, "partial": [],
    })
    md = render_markdown(snap)
    assert "## 提交完整性核查" in md
    assert "| 信息安全概论 | 作业-综合2 | 2 | 0 | 7、79 | 可撤回 |" in md


def test_partial_section_renders_orders_and_omitted_letters():
    snap = PatrolReport(generated_at_ms=0)
    snap.incomplete.append({
        "course": "操作系统", "name": "第三章作业",
        "missing_count": 0, "missing_orders": [],
        "partial_count": 1,
        "partial": [{"order": 4, "missing_letters": "CD"}],
        "withdrawable": False,
    })
    md = render_markdown(snap)
    assert "| 操作系统 | 第三章作业 | 0 | 1 | 漏选 4（缺CD） | 已出分不可撤回 |" in md


def test_unjudged_multiselects_render_a_note():
    snap = PatrolReport(generated_at_ms=0, audit_checked=2, audit_unjudged=3)
    md = render_markdown(snap)
    assert "本次核查的已提交作业没有漏答或漏选" in md
    assert "另有 3 道多选题因平台尚未公布参考答案" in md


# --------------------------------------------------------------------------- #
# engine.audit_homework
# --------------------------------------------------------------------------- #
class FakeClient:
    def __init__(self, details, sub_status="2", submit_task="1"):
        self._details = details
        self._sub_status = sub_status
        self._submit_task = submit_task

    def list_homeworks(self, course_id):
        return [HomeworkTask(task_id="t1", homework_id="h1", name="作业-综合2",
                             course_id=course_id, submit_task=self._submit_task,
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


def _engine(details, sub_status="2", submit_task="1", config=None):
    cfg = config or Config()
    return PatrolEngine(cfg, session=None,
                        client=FakeClient(details, sub_status, submit_task),
                        operator=None)


class RepairOperator:
    """Withdraw/edit/resubmit fake; records the answers it received."""

    def __init__(self, client, page_status):
        self.client = client
        self.page_status = page_status
        self.withdraw_calls = 0
        self.answer_calls: list[tuple[dict, dict]] = []

    def open_homework(self, task):
        return object()

    def read_page_state(self, page):
        return self.page_status, None

    def withdraw_homework(self, page, task):
        self.withdraw_calls += 1
        self.page_status = HomeworkStatus.NOT_SUBMITTED
        return "withdrawn"

    def answer_homework(self, task, answers, **kwargs):
        self.answer_calls.append((dict(answers), kwargs))
        for question_id, answer in answers.items():
            order = int(question_id[1:])
            self.client._details[order]["answer"] = answer
        self.page_status = HomeworkStatus.SUBMITTED_PENDING
        return {"task_id": task.task_id, "submitted": True,
                "note": "提交成功", "errors": []}


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


class FakeOperator:
    """Only the two calls audit_homework needs."""

    def __init__(self, page_status):
        self._page_status = page_status

    def open_homework(self, task):
        return object()

    def read_page_state(self, page):
        return self._page_status, None


def test_page_state_overrides_api_status():
    # API claims "not submitted", the page says "submitted, pending grading":
    # the audit must trust the page, otherwise a real gap would be skipped.
    engine = _engine({1: {"type": QuestionType.SINGLE, "answer": None}}, submit_task="0")
    engine.operator = FakeOperator(HomeworkStatus.SUBMITTED_PENDING)
    result = engine.audit_homework("t1", "c1")
    assert result["api_status"] == "not_submitted"
    assert result["status"] == "submitted_pending"
    assert result["withdrawable"] is True
    assert result["audit"]["complete"] is False


def test_page_state_read_failure_falls_back_to_api_status():
    class Boom:
        def open_homework(self, task):
            raise RuntimeError("页面打不开")

        def read_page_state(self, page):
            return None, None

    engine = _engine({1: {"type": QuestionType.SINGLE, "answer": "A"}})
    engine.operator = Boom()
    result = engine.audit_homework("t1", "c1")
    assert result["status"] == "submitted_pending"  # API truth (sub_status=2)
    assert "页面状态读取失败" in result["page_state_note"]


def test_api_page_submission_disagreement_is_named_as_a_conflict():
    """The page button and the API submit flag can disagree. The audit reports
    both and names the disagreement instead of silently trusting one side."""
    engine = _engine({1: {"type": QuestionType.SINGLE, "answer": None}},
                     submit_task="0")
    engine.operator = FakeOperator(HomeworkStatus.SUBMITTED_PENDING)
    result = engine.audit_homework("t1", "c1")

    assert result["api_says_submitted"] is False
    assert result["page_says_submitted"] is True
    assert result["state_conflicts"]
    assert any("接口显示未提交" in c for c in result["state_conflicts"])
    assert "note" in result and "差异" in result["note"]


def test_blank_api_answers_on_a_submitted_page_are_flagged():
    """The reported symptom: the interface says a question is blank while the
    page shows the homework as handed in. The gap is still reported, but with
    the caveat that the platform's answer row may simply be behind."""
    engine = _engine({
        1: {"type": QuestionType.SINGLE, "answer": "A"},
        7: {"type": QuestionType.MULTIPLE,
            "answer": None, "stem": "软件安全管理的目标是"},
    }, submit_task="1", sub_status="2")
    engine.operator = FakeOperator(HomeworkStatus.SUBMITTED_PENDING)
    result = engine.audit_homework("t1", "c1")

    assert result["audit"]["missing_count"] == 1
    assert any("接口答案行可能滞后" in c or "平台答案行可能滞后" in c
               for c in result["state_conflicts"])


def test_no_conflict_when_page_and_api_agree_on_not_submitted():
    engine = _engine({1: {"type": QuestionType.SINGLE, "answer": None}},
                     submit_task="0")
    engine.operator = FakeOperator(HomeworkStatus.NOT_SUBMITTED)
    result = engine.audit_homework("t1", "c1")

    assert result["api_says_submitted"] is False
    assert result["page_says_submitted"] is False
    assert result["state_conflicts"] == []
    assert "note" not in result


def test_page_read_failure_reports_no_conflict():
    """A page that cannot be read must not manufacture a disagreement: the
    audit simply falls back to the API status with no conflict claimed."""
    class Boom:
        def open_homework(self, task):
            raise RuntimeError("页面打不开")

        def read_page_state(self, page):
            return None, None

    engine = _engine({1: {"type": QuestionType.SINGLE, "answer": None}})
    engine.operator = Boom()
    result = engine.audit_homework("t1", "c1")
    assert result["state_conflicts"] == []
    assert "note" not in result


def test_withdrawable_is_unknown_when_the_page_was_not_read():
    """``withdrawable`` must be None (unknown), not False, when the page state was
    never read: the API flag alone keeps submitTask=0 for handed-in work, so
    False would assert "cannot withdraw" when the truth is "cannot tell"."""
    class Boom:
        def open_homework(self, task):
            raise RuntimeError("页面打不开")

        def read_page_state(self, page):
            return None, None

    engine = _engine({1: {"type": QuestionType.SINGLE, "answer": "A"}})
    engine.operator = Boom()
    result = engine.audit_homework("t1", "c1")
    assert result["withdrawable"] is None
    assert result["withdrawable_source"] == "unknown"


def test_withdrawable_comes_from_the_page_when_it_was_read():
    engine = _engine({1: {"type": QuestionType.SINGLE, "answer": "A"}})
    engine.operator = FakeOperator(HomeworkStatus.SUBMITTED_PENDING)
    result = engine.audit_homework("t1", "c1")
    assert result["withdrawable"] is True
    assert result["withdrawable_source"] == "page"


# --------------------------------------------------------------------------- #
# engine.repair_homework
# --------------------------------------------------------------------------- #
def test_repair_refuses_to_withdraw_without_answers():
    engine = _engine({1: {"type": QuestionType.SINGLE, "answer": None}})
    operator = RepairOperator(engine.client, HomeworkStatus.SUBMITTED_PENDING)
    engine.operator = operator
    result = engine.repair_homework("t1", "c1", {})
    assert result["withdrawn"] is False
    assert result["needs_answers"]
    assert operator.withdraw_calls == 0


def test_repair_withdraws_fills_and_resubmits_missing_question():
    engine = _engine({
        1: {"type": QuestionType.SINGLE, "answer": "A"},
        2: {"type": QuestionType.SINGLE, "answer": None},
        3: {"type": QuestionType.SHORT_ANSWER, "answer": None},
    })
    operator = RepairOperator(engine.client, HomeworkStatus.SUBMITTED_PENDING)
    engine.operator = operator
    result = engine.repair_homework("t1", "c1", {"2": "B"})
    assert result["withdrawn"] is True
    assert result["submitted"] is True
    assert operator.withdraw_calls == 1
    answers, kwargs = operator.answer_calls[0]
    assert answers == {"q1": "A", "q2": "B"}
    assert kwargs["allow_subjective_skips"] is True
    assert result["audit_after"]["complete"] is True


def test_repair_does_not_claim_a_withdrawal_that_did_not_happen():
    """When the page shows the homework was never submitted, withdraw_homework
    returns "not_submitted" without raising. The repair still fills the gaps, but
    it must report that no withdrawal happened instead of 「作业已撤回」."""
    class NoWithdrawOperator(RepairOperator):
        def withdraw_homework(self, page, task):
            self.withdraw_calls += 1
            # The page disagrees with the API: it says the homework is open.
            self.page_status = HomeworkStatus.NOT_SUBMITTED
            return "not_submitted"

    engine = _engine({
        1: {"type": QuestionType.SINGLE, "answer": "A"},
        2: {"type": QuestionType.SINGLE, "answer": None},
    })
    operator = NoWithdrawOperator(
        engine.client, HomeworkStatus.SUBMITTED_PENDING)
    engine.operator = operator

    result = engine.repair_homework("t1", "c1", {"2": "B"}, submit=True)

    assert result["withdrawal"] == "not_submitted"
    assert result["withdrawn"] is False
    # It still completed the answers...
    assert result["submitted"] is True
    # ...but the note must not claim a withdrawal that never happened. The
    # success branch says 「已补齐并提交」, not 「已撤回」.
    assert "已撤回" not in result["note"]
    assert "已补齐并提交" in result["note"]


def test_repair_uses_journal_answer_for_partial_multiselect(tmp_path):
    config = Config(report_dir=str(tmp_path))
    journal.record(config, task_id="t1", homework_id="h1",
                   name="作业-综合2", answers={"q2": "ABC"})
    engine = _engine({
        1: {"type": QuestionType.SINGLE, "answer": "A"},
        2: {"type": QuestionType.MULTIPLE, "answer": "A"},
    }, config=config)
    operator = RepairOperator(engine.client, HomeworkStatus.SUBMITTED_PENDING)
    engine.operator = operator
    result = engine.repair_homework("t1", "c1")
    assert result["audit"]["partial_count"] == 1
    assert result["audit"]["partial"][0]["missing_letters"] == "BC"
    assert result["withdrawn"] is True
    assert operator.answer_calls[0][0]["q2"] == "ABC"


def test_repair_never_withdraws_a_graded_homework():
    engine = _engine({1: {"type": QuestionType.SINGLE, "answer": "A"}},
                     sub_status="3")
    operator = RepairOperator(engine.client, HomeworkStatus.SUBMITTED_GRADED)
    engine.operator = operator
    result = engine.repair_homework("t1", "c1", {"1": "A"})
    assert result["withdrawn"] is False
    assert operator.withdraw_calls == 0
    assert "已出分" in result["note"]


def test_repair_is_a_noop_when_nothing_is_missing():
    engine = _engine({1: {"type": QuestionType.SINGLE, "answer": "A"}})
    operator = RepairOperator(engine.client, HomeworkStatus.SUBMITTED_PENDING)
    engine.operator = operator
    result = engine.repair_homework("t1", "c1")
    assert result["withdrawn"] is False
    assert operator.withdraw_calls == 0
    assert "无需撤回" in result["note"]


def test_repair_can_reverify_an_unjudged_multiselect():
    engine = _engine({1: {"type": QuestionType.MULTIPLE, "answer": "A"}})
    operator = RepairOperator(engine.client, HomeworkStatus.SUBMITTED_PENDING)
    engine.operator = operator
    result = engine.repair_homework(
        "t1", "c1", {"1": "AB"}, verify_unjudged=True)
    assert result["audit"]["unjudged_count"] == 1
    assert result["withdrawn"] is True
    assert operator.answer_calls[0][0]["q1"] == "AB"


def test_repair_rejects_an_answer_that_still_misses_options(tmp_path):
    config = Config(report_dir=str(tmp_path))
    journal.record(config, task_id="t1", homework_id="h1",
                   name="作业", answers={"q1": "ABC"})
    engine = _engine({1: {"type": QuestionType.MULTIPLE, "answer": "A"}},
                     config=config)
    operator = RepairOperator(engine.client, HomeworkStatus.SUBMITTED_PENDING)
    engine.operator = operator
    result = engine.repair_homework("t1", "c1", {"1": "A"})
    assert result["withdrawn"] is False
    assert operator.withdraw_calls == 0
    assert "仍缺 BC" in result["needs_answers"][0]["reason"]


# --------------------------------------------------------------------------- #
# engine.patrol: audit is on by default and produces repair briefs
# --------------------------------------------------------------------------- #
class PatrolClient:
    def __init__(self, task, questions):
        self.task = task
        self.questions = questions

    def list_my_courses(self):
        return [Course(id="c1", name="课程", end_ms=4102444800000)]

    def get_course(self, course_id):
        return None

    def list_homeworks(self, course_id):
        return [self.task]

    def list_questions(self, homework_id):
        return list(self.questions)

    def question_detail(self, question_id):
        return next(q for q in self.questions if q.question_id == question_id)


class PatrolOperator:
    def open_homework(self, task):
        return object()

    def read_page_state(self, page):
        return HomeworkStatus.SUBMITTED_PENDING, "1/1"

    def read_experiments(self, course):
        return []


def test_patrol_audits_submitted_homework_by_default(tmp_path):
    config = Config(report_dir=str(tmp_path))
    task = HomeworkTask(task_id="t1", homework_id="h1", course_id="c1",
                        name="作业", end_ms=4102444800000,
                        submit_task="1", sub_status="1")
    question = Q(1, QuestionType.MULTIPLE, "A", True)
    journal.record(config, task_id="t1", homework_id="h1",
                   name="作业", answers={"q1": "ABC"})
    engine = PatrolEngine(config, None,
                          PatrolClient(task, [question]), PatrolOperator())
    result = engine.patrol()
    assert result["audit_summary"]["checked"] == 1
    assert result["incomplete_homeworks"][0]["partial"][0]["missing_letters"] == "BC"
    brief = result["repair_briefs"][0]
    assert brief["withdrawable"] is True
    assert brief["items"][0]["kind"] == "partial"
    assert brief["items"][0]["journal_answer"] == "ABC"
    assert brief["items"][0]["needs_model_answer"] is False
    report = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "多选漏选" in report


def test_patrol_can_skip_the_audit(tmp_path):
    config = Config(report_dir=str(tmp_path))
    task = HomeworkTask(task_id="t1", homework_id="h1", course_id="c1",
                        name="作业", end_ms=4102444800000,
                        submit_task="1", sub_status="1")
    engine = PatrolEngine(config, None,
                          PatrolClient(task, [Q(1, QuestionType.MULTIPLE, "A", True)]),
                          PatrolOperator())
    result = engine.patrol(audit=False)
    assert result["audit_summary"]["checked"] == 0
    assert result["incomplete_homeworks"] == []
