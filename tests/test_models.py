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
