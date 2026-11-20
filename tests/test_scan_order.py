"""Deterministic course-major retrieval and the per-course scan tool.

The platform feed has no stable order, and mixing the all-course homework list
with per-course experiment calls made the browser jump between courses. These
tests pin the fixed order (course → due date/title for homework → 实验N for
experiments) and that ``scan_course`` reads one course's homeworks, then its
experiments, without touching another course.
"""

from __future__ import annotations

from unittest.mock import Mock

from uplus_mcp.config import Config
from uplus_mcp.engine import (
    PatrolEngine,
    experiment_number,
)
from uplus_mcp.models import Course, Experiment, HomeworkTask


def _task(task_id: str, name: str, end_ms, submit_task=None) -> HomeworkTask:
    return HomeworkTask(
        task_id=task_id,
        homework_id="h-" + task_id,
        course_id="c1",
        name=name,
        end_ms=end_ms,
        submit_task=submit_task,
    )


def _experiment(name: str, exp_id: str, submitted=False) -> Experiment:
    return Experiment(
        name=name, course_id="c1", exp_id=exp_id, stu_exp_id="s-" + exp_id,
        submitted=submitted, deadline_ms=4102444800000,
    )


# --------------------------------------------------------------------------- #
# sort keys
# --------------------------------------------------------------------------- #
def test_experiment_number_reads_chinese_and_arabic_numbers():
    assert experiment_number("实验一 进程同步") == 1
    assert experiment_number("实验十 动态规划") == 10
    assert experiment_number("实验11 图算法") == 11
    assert experiment_number("人工智能基础-实验3-知识提取实践") == 3
    assert experiment_number("没有编号的标题") is None


def test_experiments_sort_as_一_二_十_not_lexicographically():
    from uplus_mcp.engine import _experiment_sort_key

    names = ["实验十", "实验二", "实验一", "绪论"]
    ordered = sorted(names, key=lambda n: _experiment_sort_key(
        _experiment(n, n)))
    assert ordered == ["实验一", "实验二", "实验十", "绪论"]


def test_list_courses_sorts_the_live_feed_by_name_then_id():
    client = Mock()
    client.list_my_courses.return_value = [
        Course(id="b", name="B 课"),
        Course(id="a", name="A 课"),
    ]
    engine = PatrolEngine(Config(), Mock(), client, Mock())
    rows = engine.list_courses()
    assert [row["course_id"] for row in rows] == ["a", "b"]


def test_list_homeworks_is_course_major_and_due_date_sorted():
    cfg = Config(courses=[Course(id="c2", name="B 课"),
                          Course(id="c1", name="A 课")])
    client = Mock()
    client.list_homeworks.side_effect = {
        "c2": [_task("late", "晚交作业", 2000),
               _task("early", "早交作业", 1000)],
        "c1": [_task("nod", "无截止作业", None),
               _task("dated", "有截止作业", 1500)],
    }.get
    engine = PatrolEngine(cfg, Mock(), client, Mock())

    rows = engine.list_homeworks("")

    assert [row["task_id"] for row in rows] == [
        "early", "late", "dated", "nod"]
    assert [row["course_id"] for row in rows] == ["c2", "c2", "c1", "c1"]
    assert [call.args[0] for call in client.list_homeworks.call_args_list] == [
        "c2", "c1"]


# --------------------------------------------------------------------------- #
# per-course scan
# --------------------------------------------------------------------------- #
def _scan_engine(tmp_path):
    course = Course(id="c1", name="课程一")
    cfg = Config(report_dir=str(tmp_path), courses=[course])
    client = Mock()
    client.list_homeworks.return_value = [
        _task("late", "晚交作业", 2000),
        _task("early", "早交作业", 1000),
        _task("submitted", "已交作业", 900, submit_task="1"),
    ]
    client.list_questions.return_value = []
    operator = Mock()
    operator.open_homework.return_value = Mock()
    operator.read_page_state.return_value = (None, None)
    operator.read_experiments.return_value = [
        _experiment("实验十 动态规划", "e10"),
        _experiment("实验二 调度", "e2"),
        _experiment("实验一 进程", "e1"),
    ]
    engine = PatrolEngine(cfg, Mock(), client, operator)
    return engine, client, operator


def test_scan_course_reads_one_course_homeworks_then_experiments(tmp_path):
    engine, client, operator = _scan_engine(tmp_path)

    result = engine.scan_course(
        "c1", refresh_page_state=True, verify_experiments=False)

    assert result["course_id"] == "c1"
    assert [row["task_id"] for row in result["homeworks"]] == [
        "submitted", "early", "late"]
    assert [row["name"] for row in result["experiments"]] == [
        "实验一 进程", "实验二 调度", "实验十 动态规划"]
    # Only the unsubmitted homework pages are opened, in sorted order.
    assert [call.args[0].task_id for call in operator.open_homework.call_args_list] == [
        "early", "late"]
    client.list_homeworks.assert_called_once_with("c1")
    operator.read_experiments.assert_called_once()
    # No experiment detail pages are opened in the fast path.
    assert result["summary"] == {
        "homework_total": 3,
        "homework_submitted": 1,
        "homework_outstanding": 2,
        "experiments_total": 3,
        "experiments_submitted": 0,
        "experiments_outstanding": 3,
        "experiments_unknown": 0,
    }


class _Detail:
    status = "已提交"
    submitted_files: list = []


class _ExpOp:
    def __init__(self):
        self.calls: list[tuple] = []

    def read_detail(self, *args):
        self.calls.append(args)
        return _Detail()


def test_scan_course_can_verify_experiment_details_in_the_same_pass(tmp_path):
    engine, _, operator = _scan_engine(tmp_path)
    exp_op = _ExpOp()
    engine._experiment_operator = lambda: exp_op

    result = engine.scan_course(
        "c1", refresh_page_state=False, verify_experiments=True)

    assert [row["submitted_source"] for row in result["experiments"]] == [
        "detail_page", "detail_page", "detail_page"]
    assert [row["submitted"] for row in result["experiments"]] == [True] * 3
    assert [call[1] for call in exp_op.calls] == ["e1", "e2", "e10"]
    operator.open_homework.assert_not_called()


def test_patrol_writes_homeworks_and_experiments_in_course_major_order(tmp_path):
    engine, client, operator = _scan_engine(tmp_path)

    result = engine.patrol(audit=False, auto_answer=False, submit=False)

    report = open(result["report_path"], encoding="utf-8").read()
    assert report.index("早交作业") < report.index("晚交作业")
    assert report.index("实验一 进程") < report.index("实验二 调度")
    assert report.index("实验二 调度") < report.index("实验十 动态规划")
    assert [call.args[0].task_id for call in operator.open_homework.call_args_list] == [
        "early", "late"]
    client.list_homeworks.assert_called_once_with("c1")