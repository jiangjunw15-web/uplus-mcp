"""Regression tests for the 2026-11-24 multi-account findings (v0.8.18).

1. A pinned CDP port must never fall back to another port: the fallback would
   silently attach to a different account's browser session (串号).
2. The attached session's account is compared with the configured expected
   account, and account-bound tools refuse to act on a mismatch.
3. Patrol opens answer pages only where the verdict needs page truth, and a
   short-lived page-state cache keeps a patrol → audit → repair run from
   reloading the same SPA.

No test logs in, submits or touches a real account.
"""

from __future__ import annotations

import time
from unittest.mock import Mock

import pytest

from uplus_mcp.browser import BrowserSession, UplusError, connection_ports
from uplus_mcp.config import Config, load_config
from uplus_mcp.engine import PatrolEngine
from uplus_mcp.models import Course, HomeworkStatus, HomeworkTask


# --------------------------------------------------------------------------- #
# pinned port: fail loudly, never fall back
# --------------------------------------------------------------------------- #
def test_pinned_port_never_falls_back_to_a_live_port():
    """9340 dead + 9222 alive must raise, not attach to 9222."""
    config = Config(cdp_port=9340, cdp_port_explicit=True)
    with pytest.raises(UplusError, match="不会回退"):
        connection_ports(config, alive=lambda port: port == 9222)
    # The message names the pinned port, so the user knows which browser is gone.
    with pytest.raises(UplusError, match="9340"):
        connection_ports(config, alive=lambda port: False)


def test_pinned_port_connects_only_to_itself():
    config = Config(cdp_port=9340, cdp_port_explicit=True)
    assert connection_ports(config, alive=lambda port: port == 9340) == (9340,)


def test_implicit_default_keeps_the_historic_fallback():
    config = Config()  # cdp_port_explicit defaults to False
    ports = connection_ports(config, alive=lambda port: True)
    assert ports == (9333, 9222)


def test_env_port_marks_the_port_explicit(monkeypatch):
    monkeypatch.setenv("UPLUS_CDP_PORT", "9340")
    monkeypatch.delenv("UPLUS_CONFIG", raising=False)
    cfg = load_config()
    assert cfg.cdp_port == 9340
    assert cfg.cdp_port_explicit is True


def test_config_file_port_marks_the_port_explicit(tmp_path):
    path = tmp_path / "courses.json"
    path.write_text('{"cdp_port": 9341, "courses": []}', encoding="utf-8")
    cfg = load_config(str(path))
    assert cfg.cdp_port == 9341
    assert cfg.cdp_port_explicit is True


def test_default_config_port_is_not_pinned(tmp_path):
    path = tmp_path / "courses.json"
    path.write_text('{"courses": []}', encoding="utf-8")
    assert load_config(str(path)).cdp_port_explicit is False


# --------------------------------------------------------------------------- #
# account identity
# --------------------------------------------------------------------------- #
class _IdentityPage:
    url = "https://www.eduplus.net/workbench/index/school"

    def __init__(self, values):
        self.values = values

    def is_closed(self):
        return False

    def evaluate(self, script, arg=None):
        if isinstance(arg, list):
            # Only the allow-listed keys are ever returned.
            return {k: self.values[k] for k in arg if k in self.values}
        return ""


def _browser(values) -> BrowserSession:
    session = BrowserSession.__new__(BrowserSession)
    session.config = Config()
    session.base_url = "https://www.eduplus.net"
    session._page = _IdentityPage(values)
    session._started = True
    session._token_probed = True
    return session


def test_account_matches_case_insensitive():
    session = _browser({"studentId": "S1001"})
    assert session.account_matches(" s1001 ")["matched"] is True
    assert session.account_matches("S9999")["matched"] is False


def test_account_matches_accepts_any_allow_listed_identifier():
    # A portal build may store the phone instead of the student number.
    session = _browser({"phone": "13800000000"})
    assert session.account_matches("13800000000")["matched"] is True


def test_account_identity_never_reads_or_echoes_unlisted_keys():
    class _LeakyPage(_IdentityPage):
        def evaluate(self, script, arg=None):
            assert "loginToken" not in str(arg)
            return {"studentId": "S1001"}

    session = _browser({})
    session._page = _LeakyPage({})
    identity = session.account_identity()
    assert identity["identifiers"] == {"studentId": "S1001"}
    assert "loginToken" not in identity["keys"]


def test_signed_out_tab_reports_identity_unavailable():
    session = _browser({})
    match = session.account_matches("S1001")
    assert match["available"] is False
    assert match["matched"] is False


# --------------------------------------------------------------------------- #
# engine account guard
# --------------------------------------------------------------------------- #
def _engine_with(values, expected: str = "S1001") -> PatrolEngine:
    session = _browser(values)
    engine = PatrolEngine(Config(expected_account=expected), session, Mock(), Mock())
    return engine


def test_guard_refuses_a_foreign_account():
    engine = _engine_with({"studentId": "S2002"})
    with pytest.raises(UplusError, match="拒绝对当前浏览器会话执行操作"):
        engine._guard_account()
    with pytest.raises(UplusError, match="S2002"):
        engine._guard_account()


def test_guard_passes_and_caches_a_matching_account():
    engine = _engine_with({"studentId": "S1001"})
    engine._guard_account()
    assert engine._account_ok is True
    # The second call must not hit the browser again.
    engine.session.account_identity = Mock(side_effect=AssertionError("re-read"))
    engine._guard_account()


def test_guard_is_inert_without_an_expected_account():
    session = Mock()
    engine = PatrolEngine(Config(), session, Mock(), Mock())
    engine._guard_account()
    session.account_matches.assert_not_called()


def test_login_with_a_foreign_account_reports_mismatch():
    session = Mock()
    session.login_by_password.return_value = {
        "logged_in": True, "method": "password", "note": "自动登录成功",
        "url": "https://www.eduplus.net/workbench", "token_len": 40,
    }
    session.account_matches.return_value = {
        "expected": "S1001", "actual": "S2002",
        "available": True, "matched": False,
    }
    engine = PatrolEngine(Config(expected_account="S1001"),
                          session, Mock(), Mock())
    result = engine.login(owner="self", method="password",
                          username="13800000000", password="secret")
    assert result["stage"] == "account_mismatch"
    assert result["account"] == "S2002"
    assert engine._account_ok is False


def test_check_login_reports_the_attached_account_and_mismatch():
    session = Mock()
    session.is_logged_in.return_value = True
    session.account_matches.return_value = {
        "expected": "S1001", "actual": "S2002",
        "available": True, "matched": False,
    }
    engine = PatrolEngine(Config(expected_account="S1001"),
                          session, Mock(), Mock())
    result = engine.check_login()
    assert result["logged_in"] is True
    assert result["account"] == "S2002"
    assert result["account_match"] is False
    assert "不一致" in result["message"]


# --------------------------------------------------------------------------- #
# patrol page-read policy
# --------------------------------------------------------------------------- #
FUTURE = 4102444800000


def _task(task_id: str, *, submit_task="0", sub_status=None,
          end_ms=FUTURE) -> HomeworkTask:
    return HomeworkTask(
        task_id=task_id, homework_id="h-" + task_id, course_id="c1",
        name="作业 " + task_id, end_ms=end_ms, submit_task=submit_task,
        sub_status=sub_status,
    )


def _policy_engine() -> PatrolEngine:
    return PatrolEngine(Config(), Mock(), Mock(), Mock())


@pytest.mark.parametrize("task,expected", [
    # The interface under-reports these; they may be answered → read the page.
    (_task("open"), True),
    # Expired rows cannot be acted on → trust the API, no page load.
    (_task("expired", end_ms=1), False),
    # API-submitted with a recognised subStatus → no page needed.
    (_task("pending", submit_task="1", sub_status="2"), False),
    (_task("graded", submit_task="1", sub_status="3"), False),
    # API-submitted but subStatus cannot tell pending from graded → read.
    (_task("unknown", submit_task="1", sub_status=None), True),
])
def test_should_read_page_policy(task, expected):
    engine = _policy_engine()
    engine._register(task)
    assert engine._should_read_page(task) is expected


def test_patrol_opens_only_the_rows_the_policy_selects(tmp_path):
    course = Course(id="c1", name="课程")
    tasks = [
        _task("open"),                                      # opened
        _task("graded", submit_task="1", sub_status="3"),   # not opened
        _task("expired", end_ms=1),                          # not opened
    ]
    client = Mock()
    client.list_my_courses.return_value = [course]
    client.list_homeworks.return_value = tasks
    client.list_questions.return_value = []
    operator = Mock()
    operator.open_homework.return_value = Mock()
    operator.read_page_state.return_value = (HomeworkStatus.SUBMITTED_PENDING, "2/2")
    operator.read_experiments.return_value = []
    engine = PatrolEngine(Config(report_dir=str(tmp_path), courses=[course]),
                          Mock(), client, operator)

    result = engine.patrol(audit=False)

    opened = [c.args[0].task_id for c in operator.open_homework.call_args_list]
    assert opened == ["open"]
    by_id = {row["task_id"]: row for row in
             engine.list_homeworks("c1")}  # rows for status_source assertions
    assert by_id["graded"]["status_source"] == "api"
    assert by_id["expired"]["status_source"] == "api"
    assert result["summary"]["homework_submitted"] == 2


# --------------------------------------------------------------------------- #
# page-state cache
# --------------------------------------------------------------------------- #
def test_page_state_is_read_once_then_served_from_cache(tmp_path):
    engine = PatrolEngine(Config(report_dir=str(tmp_path)), Mock(), Mock(), Mock())
    operator = Mock()
    operator.open_homework.return_value = Mock()
    operator.read_page_state.return_value = (HomeworkStatus.SUBMITTED_PENDING, "2/3")
    engine.operator = operator
    task = _task("t1")

    assert engine._read_task_page_state(task) == ""
    assert engine._read_task_page_state(task) == ""
    assert operator.open_homework.call_count == 1
    assert task.raw["_page_state_source"] == "page_cache"
    assert task.page_progress == "2/3"


def test_expired_cache_entry_is_re_read(tmp_path):
    engine = PatrolEngine(Config(report_dir=str(tmp_path)), Mock(), Mock(), Mock())
    operator = Mock()
    operator.open_homework.return_value = Mock()
    operator.read_page_state.return_value = (HomeworkStatus.NOT_SUBMITTED, None)
    engine.operator = operator
    task = _task("t1")

    engine._read_task_page_state(task)
    engine._page_state_cache[task.task_id] = (
        time.monotonic() - PatrolEngine.PAGE_STATE_TTL_S - 1,
        HomeworkStatus.NOT_SUBMITTED, None)
    engine._read_task_page_state(task)
    assert operator.open_homework.call_count == 2


def test_cache_is_dropped_after_a_write(tmp_path):
    engine = PatrolEngine(Config(report_dir=str(tmp_path)), Mock(), Mock(), Mock())
    task = _task("t1")
    engine._page_state_cache[task.task_id] = (
        time.monotonic(), HomeworkStatus.NOT_SUBMITTED, None)

    engine._invalidate_page_state(task.task_id)

    assert task.task_id not in engine._page_state_cache