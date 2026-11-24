"""Tests for the v0.8.15 speed work.

Covers the three hot paths that were reworked:

1. ``BrowserSession.api_get_many`` — several platform URLs read in ONE browser
   round-trip, with the sequential path kept as the fallback for every error
   case so the retry / recovery behaviour is unchanged.
2. ``PlatformClient.question_details`` / the engine's question-detail read —
   the per-question round-trips collapse into one batched call.
3. ``journal`` — the answer journal is parsed once and reused instead of being
   re-read and re-parsed for every audited homework.
4. ``PlatformClient.list_my_courses`` — the course feed is fetched once and
   reused, and can be invalidated explicitly.
"""

from __future__ import annotations

import json

from uplus_mcp import journal
from uplus_mcp.browser import BrowserSession, UplusError
from uplus_mcp.config import Config
from uplus_mcp.platform import PlatformClient


# --------------------------------------------------------------------------- #
# api_get_many
# --------------------------------------------------------------------------- #
class _BatchPage:
    """Fake page that answers the batched fetch script."""

    def __init__(self, responses):
        self._responses = responses
        self.batched = 0
        self.single = 0

    @property
    def url(self):
        return "https://www.eduplus.net/workbench/index/school"

    def is_closed(self):
        return False

    def evaluate(self, script, arg=None):
        if isinstance(arg, list):
            self.batched += 1
            return [
                {"status": 200, "body": json.dumps(payload)}
                for payload in self._responses
            ]
        self.single += 1
        return {"status": 200, "body": json.dumps(arg)}

    def inner_text(self, selector="body"):
        return "工作台 课程"


def _session(page):
    session = BrowserSession.__new__(BrowserSession)
    session.config = Config()
    session.base_url = "https://www.eduplus.net"
    session._page = page
    session._started = True
    session._token_probed = True
    session._browser = None
    session._context = None
    session._keep_open = False
    return session


def test_api_get_many_reads_every_url_in_one_round_trip():
    page = _BatchPage([{"data": {"id": 1}}, {"data": {"id": 2}}])
    session = _session(page)
    payloads = session.api_get_many(["/api/a", "/api/b"])
    assert payloads == [{"data": {"id": 1}}, {"data": {"id": 2}}]
    assert page.batched == 1
    assert page.single == 0


def test_api_get_many_keeps_the_request_order():
    page = _BatchPage([{"n": i} for i in range(5)])
    session = _session(page)
    payloads = session.api_get_many([f"/api/{i}" for i in range(5)])
    assert [p["n"] for p in payloads] == [0, 1, 2, 3, 4]


def test_api_get_many_refuses_a_cross_origin_url():
    session = _session(_BatchPage([]))
    try:
        session.api_get_many(["https://evil.example/x"])
    except UplusError as exc:
        assert "拒绝向非平台域名" in str(exc)
    else:
        raise AssertionError("a cross-origin URL must be refused")


def test_api_get_many_falls_back_to_the_sequential_path_on_a_bad_body():
    """A non-JSON body must go through ``api_get`` (retry + bounce), not raise
    straight out of the batch — the caller sees the same behaviour as before."""
    class _BadBodyPage(_BatchPage):
        def evaluate(self, script, arg=None):
            if isinstance(arg, list):
                self.batched += 1
                return [{"status": 200, "body": "<html>nope</html>"}]
            self.single += 1
            return {"status": 200, "body": json.dumps({"ok": True})}

    page = _BadBodyPage([])
    session = _session(page)
    payloads = session.api_get_many(["/api/a"])
    assert payloads == [{"ok": True}]
    # The bad response was re-read through the sequential path.
    assert page.single == 1


def test_api_get_many_falls_back_when_the_batch_call_itself_fails():
    class _BrokenPage(_BatchPage):
        def evaluate(self, script, arg=None):
            if isinstance(arg, list):
                raise RuntimeError("detached frame")
            self.single += 1
            return {"status": 200, "body": json.dumps({"ok": True})}

    page = _BrokenPage([])
    session = _session(page)
    assert session.api_get_many(["/api/a"]) == [{"ok": True}]
    assert page.single == 1


def test_api_get_many_of_nothing_does_nothing():
    session = _session(_BatchPage([]))
    assert session.api_get_many([]) == []


# --------------------------------------------------------------------------- #
# question details
# --------------------------------------------------------------------------- #
def test_question_details_uses_one_batched_call():
    calls = {"batched": 0, "single": 0}

    class _Client(PlatformClient):
        def __init__(self):
            self.config = Config()
            self.session = _session(_BatchPage([]))

    client = _Client()

    def counting(paths):
        calls["batched"] += 1
        return [{"data": {"id": "q1"}}, {"data": {"id": "q2"}}]

    client.session.api_get_many = counting
    questions = client.question_details(["q1", "q2"])
    assert [q.question_id for q in questions] == ["q1", "q2"]
    assert calls["batched"] == 1
    assert calls["single"] == 0


def test_question_details_falls_back_when_the_session_has_no_batch_read():
    class _OldSession:
        def __init__(self):
            self.config = Config()
            self.base_url = "https://www.eduplus.net"

        def api_get(self, path, *, retries=1):
            # The detail route ends in .../student/detail; the question id is
            # the segment before it.
            parts = path.split("/")
            return {"data": {"id": parts[-3]}}

    client = PlatformClient.__new__(PlatformClient)
    client.config = Config()
    client.session = _OldSession()
    questions = client.question_details(["q1", "q2"])
    assert [q.question_id for q in questions] == ["q1", "q2"]


# --------------------------------------------------------------------------- #
# journal cache
# --------------------------------------------------------------------------- #
def test_journal_reads_the_file_once_and_reuses_the_parse(tmp_path):
    config = Config(report_dir=str(tmp_path))
    path = journal.journal_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "t1": {"homework_id": "h1", "name": "作业",
               "answers": {"q1": "A"}},
    }), encoding="utf-8")

    reads = {"n": 0}
    original = type(path).read_text

    def counting(self, *args, **kwargs):
        if self == path:
            reads["n"] += 1
        return original(self, *args, **kwargs)

    type(path).read_text = counting
    try:
        assert journal.answers_for(config, "t1") == {"q1": "A"}
        assert journal.answers_for(config, "t1") == {"q1": "A"}
        assert journal.answers_for(config, "t1") == {"q1": "A"}
    finally:
        type(path).read_text = original
    assert reads["n"] == 1


def test_journal_cache_is_invalidated_by_a_write(tmp_path):
    config = Config(report_dir=str(tmp_path))
    journal.record(config, task_id="t1", homework_id="h1",
                   name="作业", answers={"q1": "A"})
    assert journal.answers_for(config, "t1") == {"q1": "A"}
    journal.record(config, task_id="t1", homework_id="h1",
                   name="作业", answers={"q1": "B"})
    assert journal.answers_for(config, "t1") == {"q1": "B"}


def test_journal_cache_picks_up_an_external_edit(tmp_path):
    config = Config(report_dir=str(tmp_path))
    path = journal.journal_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "t1": {"homework_id": "h1", "name": "作业", "answers": {"q1": "A"}},
    }), encoding="utf-8")
    assert journal.answers_for(config, "t1") == {"q1": "A"}
    # A different size guarantees a different stamp, so the cache misses.
    path.write_text(json.dumps({
        "t1": {"homework_id": "h1", "name": "作业",
               "answers": {"q1": "ABCDE"}},
    }), encoding="utf-8")
    assert journal.answers_for(config, "t1") == {"q1": "ABCDE"}


# --------------------------------------------------------------------------- #
# course feed cache
# --------------------------------------------------------------------------- #
class _FeedSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def api_get(self, path, *, retries=1):
        self.calls += 1
        return self.payload


def _feed_client():
    session = _FeedSession({"data": [
        {"id": "c1", "name": "课程一", "teacherName": "张老师"},
        {"id": "c2", "name": "课程二"},
    ]})
    client = PlatformClient(session, Config())
    return client, session


def test_course_feed_is_fetched_once_and_reused():
    client, session = _feed_client()
    first = client.list_my_courses()
    second = client.list_my_courses()
    assert [c.id for c in first] == ["c1", "c2"]
    assert [c.id for c in second] == ["c1", "c2"]
    assert session.calls == 1


def test_invalidate_courses_forces_a_fresh_read():
    client, session = _feed_client()
    client.list_my_courses()
    client.invalidate_courses()
    client.list_my_courses()
    assert session.calls == 2


def test_refresh_argument_forces_a_fresh_read():
    client, session = _feed_client()
    client.list_my_courses()
    client.list_my_courses(refresh=True)
    assert session.calls == 2


def test_get_course_reuses_the_cached_feed():
    client, session = _feed_client()
    assert client.get_course("c1").name == "课程一"
    assert client.get_course("c2").name == "课程二"
    assert session.calls == 1
