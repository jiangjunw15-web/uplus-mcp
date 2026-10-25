import json

from uplus_mcp.models import (
    HomeworkStatus,
    HomeworkTask,
    Question,
    QuestionType,
)


def test_question_type_flags():
    assert QuestionType.SINGLE.auto_answerable
    assert QuestionType.MULTIPLE.auto_answerable
    assert QuestionType.JUDGE.auto_answerable
    assert not QuestionType.SHORT_ANSWER.auto_answerable
    assert not QuestionType.PROGRAM.auto_answerable
    assert QuestionType.JUDGE.label == "判断题"


def test_question_from_detail_parses_options_and_prompt():
    raw = {
        "id": "q1",
        "orderNumber": 3,
        "qsnType": 1,
        "everyScore": 2,
        "titleText": "<p>下面哪个是进程调度算法？</p>",
        "options": [
            {"id": "A", "optionContent": "<p>FCFS</p>"},
            {"id": "B", "optionContent": "<p>页面置换</p>"},
        ],
        "userAnswer": "B",
        "isAnswer": "1",
        "knowledges": "操作系统",
    }
    q = Question.from_detail(raw)
    assert q.question_id == "q1"
    assert q.order == 3
    assert q.type is QuestionType.SINGLE
    assert q.stem == "下面哪个是进程调度算法？"
    assert [o.code for o in q.options] == ["A", "B"]
    assert q.my_answer == "B"
    brief = q.llm_prompt()
    assert brief["type_code"] == 1
    assert brief["options"][0].startswith("A、")


def test_homework_status_from_api_object_dto():
    raw = {
        "id": "t1",
        "homeworkId": "h1",
        "endTime": 1788192000000,
        "submitTask": "1",
        "reviewTask": "0",
        "homeworkDTO": {
            "name": "第一章 作业",
            "subStatus": "3",
            "questionCount": 10,
            "userScore": 95,
        },
    }
    task = HomeworkTask.from_api(raw, "c1")
    assert task.task_id == "t1"
    assert task.name == "第一章 作业"
    assert task.status is HomeworkStatus.SUBMITTED_GRADED
    assert task.score == 95
    assert task.question_count == 10


def test_homework_status_from_api_string_dto():
    raw = {
        "id": "t2",
        "homeworkId": "h2",
        "submitTask": "1",
        "homeworkDTO": json.dumps({"name": "第二章", "subStatus": "1", "questionCount": 8}),
    }
    task = HomeworkTask.from_api(raw, "c1")
    assert task.status is HomeworkStatus.SUBMITTED_PENDING
    assert task.question_count == 8


def test_unsubmitted_task_status_and_page_override():
    raw = {
        "id": "t3",
        "homeworkId": "h3",
        "submitTask": "0",
        "homeworkDTO": {"name": "上传PPT", "questionCount": 0},
    }
    task = HomeworkTask.from_api(raw, "c1")
    assert task.status is HomeworkStatus.NOT_SUBMITTED
    task.page_status = HomeworkStatus.SUBMITTED_PENDING
    assert task.status is HomeworkStatus.SUBMITTED_PENDING


# --------------------------------------------------------------------------- #
# attachment question type
# --------------------------------------------------------------------------- #
def test_attachment_question_type_is_not_auto_answerable():
    qt = QuestionType.ATTACHMENT
    assert qt.value == 7
    assert qt.label == "附件题"
    assert not qt.auto_answerable


def test_attachment_question_prompt_asks_for_human_takeover():
    raw = {
        "id": "q9",
        "orderNumber": 1,
        "qsnType": 7,
        "titleText": "<p>上传课程 PPT 汇报文件</p>",
        "userAnswer": "",
    }
    q = Question.from_detail(raw)
    brief = q.llm_prompt()
    assert "人工接管" in brief["answer_hint"]
    assert not q.auto_answerable


# --------------------------------------------------------------------------- #
# answered joint judgement
# --------------------------------------------------------------------------- #
def test_answered_from_user_answer_when_is_answer_missing():
    raw = {"id": "a", "orderNumber": 1, "qsnType": 1, "userAnswer": "C"}
    assert Question.from_meta(raw).answered is True


def test_answered_from_hw_answer_id_when_user_answer_blank():
    raw = {"id": "b", "orderNumber": 1, "qsnType": 3, "hwAnswerId": "hw-9"}
    assert Question.from_meta(raw).answered is True


def test_answered_flag_string_one_counts_as_answered():
    raw = {"id": "c", "orderNumber": 1, "qsnType": 1, "isAnswer": "1"}
    assert Question.from_meta(raw).answered is True


def test_unanswered_when_all_signals_are_blank():
    raw = {"id": "d", "orderNumber": 1, "qsnType": 1}
    assert Question.from_meta(raw).answered is False


# --------------------------------------------------------------------------- #
# experiment three-state default
# --------------------------------------------------------------------------- #
def test_experiment_unknown_state_defaults_to_none():
    from uplus_mcp.models import Experiment
    exp = Experiment(name="实验一")
    assert exp.submitted is None

