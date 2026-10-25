"""Tests for the platform client's dual-shape response unpacking and teacher fallback."""

from uplus_mcp.config import Config
from uplus_mcp.platform import PlatformClient, _unpack_list, _unpack_object


class FakeSession:
    def __init__(self, payload):
        self._payload = payload

    def api_get(self, path):
        return self._payload


def _client(payload) -> PlatformClient:
    return PlatformClient(FakeSession(payload), Config())


# --------------------------------------------------------------------------- #
# unpack helpers
# --------------------------------------------------------------------------- #
def test_unpack_list_accepts_bare_array():
    raw = [{"id": "a"}, {"id": "b"}]
    assert _unpack_list(raw) == raw


def test_unpack_list_accepts_data_wrapped_array():
    assert _unpack_list({"data": [{"id": "a"}]}) == [{"id": "a"}]


def test_unpack_list_returns_empty_for_non_list_payload():
    assert _unpack_list({"data": {"id": "a"}}) == []
    assert _unpack_list(None) == []


def test_unpack_object_accepts_bare_object():
    raw = {"id": "t1", "name": "作业"}
    assert _unpack_object(raw) == raw


def test_unpack_object_accepts_data_wrapped_object():
    assert _unpack_object({"data": {"id": "t1"}}) == {"id": "t1"}


def test_unpack_object_returns_empty_for_non_object():
    assert _unpack_object(["x"]) == {}
    assert _unpack_object(None) == {}


# --------------------------------------------------------------------------- #
# course list: shape + teacher fallback
# --------------------------------------------------------------------------- #
def test_course_list_unpacks_data_wrapped_feed():
    payload = {"data": [{"id": "c1", "name": "算法", "creator": "刘老师"}]}
    courses = _client(payload).list_my_courses()
    assert len(courses) == 1
    assert courses[0].id == "c1"
    assert courses[0].teacher == "刘老师"


def test_course_list_accepts_bare_array_feed():
    payload = [{"id": "c2", "name": "示例课程 A", "teacherName": "康老师"}]
    courses = _client(payload).list_my_courses()
    assert courses[0].teacher == "康老师"


def test_teacher_falls_back_to_creator_when_teacher_name_empty():
    payload = {"data": [{"id": "c3", "name": "示例课程 B", "teacherName": "", "creator": "段老师"}]}
    courses = _client(payload).list_my_courses()
    assert courses[0].teacher == "段老师"


def test_teacher_name_wins_over_creator_when_present():
    payload = {"data": [{"id": "c4", "name": "示例课程 C", "teacherName": "贾老师", "creator": "旧作者"}]}
    courses = _client(payload).list_my_courses()
    assert courses[0].teacher == "贾老师"
