"""Tests for the three-dialog sign-in flow and credential hygiene.

The tool must never pick a login method by itself: with no answers it returns
dialog 1 (owner), then dialog 2 (method), then dialog 3 (credentials), and only
then touches the sign-in form. The SMS method is handed to the human entirely.
"""

from unittest.mock import Mock

from uplus_mcp import login_flow
from uplus_mcp.browser import BrowserSession
from uplus_mcp.config import Config
from uplus_mcp.engine import PatrolEngine


def _engine(session: Mock) -> PatrolEngine:
    cfg = Config()
    return PatrolEngine(cfg, session, Mock(), Mock())


# --------------------------------------------------------------------------- #
# dialog 1: owner
# --------------------------------------------------------------------------- #
def test_no_arguments_returns_the_owner_dialog_and_touches_nothing():
    session = Mock()
    result = _engine(session).login()
    assert result["stage"] == "owner"
    assert result["logged_in"] is False
    values = [option["value"] for option in result["options"]]
    assert values == ["self", "proxy"]
    # No method is chosen on the user's behalf.
    session.login_by_password.assert_not_called()
    session.login_by_student_id.assert_not_called()
    session.open_sms_login.assert_not_called()


def test_owner_can_be_answered_in_chinese():
    assert login_flow.normalize_owner("我自己登录") == "self"
    assert login_flow.normalize_owner("代登录") == "proxy"
    assert login_flow.normalize_owner("随便") is None


# --------------------------------------------------------------------------- #
# dialog 2: method
# --------------------------------------------------------------------------- #
def test_owner_without_method_returns_the_method_dialog():
    session = Mock()
    result = _engine(session).login(owner="self")
    assert result["stage"] == "method"
    assert result["owner"] == "self"
    keys = [option["value"] for option in result["options"]]
    assert keys == ["student_id", "sms", "password"]
    session.login_by_password.assert_not_called()


def test_method_aliases_are_recognised():
    assert login_flow.normalize_method("学号/工号登录") == "student_id"
    assert login_flow.normalize_method("学号") == "student_id"
    assert login_flow.normalize_method("短信验证码登录") == "sms"
    assert login_flow.normalize_method("手机号/账号密码登录") == "password"
    assert login_flow.normalize_method("随便一种") is None


# --------------------------------------------------------------------------- #
# dialog 3: credentials
# --------------------------------------------------------------------------- #
def test_student_id_without_fields_returns_the_credentials_dialog():
    session = Mock()
    result = _engine(session).login(owner="proxy", method="student_id")
    assert result["stage"] == "credentials"
    assert result["owner"] == "proxy"
    assert [field["name"] for field in result["fields"]] == [
        "school", "username", "password"]
    assert result["missing"] == ["school", "username", "password"]
    session.login_by_student_id.assert_not_called()


def test_student_id_reports_only_the_fields_still_missing():
    session = Mock()
    result = _engine(session).login(owner="self", method="student_id",
                                    username="2413041221", password="secret")
    assert result["stage"] == "credentials"
    assert result["missing"] == ["school"]
    # A partially filled form must not be submitted.
    session.login_by_student_id.assert_not_called()


def test_password_method_asks_for_phone_and_password_only():
    session = Mock()
    result = _engine(session).login(owner="self", method="password")
    assert result["stage"] == "credentials"
    assert [field["name"] for field in result["fields"]] == ["username", "password"]


def test_secret_fields_are_marked_secret():
    session = Mock()
    result = _engine(session).login(owner="self", method="student_id")
    secret = {field["name"] for field in result["fields"] if field["secret"]}
    assert secret == {"password"}


# --------------------------------------------------------------------------- #
# auto-filled methods
# --------------------------------------------------------------------------- #
def test_student_id_login_forwards_school_and_hides_credentials():
    session = Mock()
    session.login_by_student_id.return_value = {
        "logged_in": True, "method": "student_id", "note": "自动登录成功",
        "url": "https://www.eduplus.net/workbench/index/school", "token_len": 48,
    }
    result = _engine(session).login(
        owner="proxy", method="student_id", username="2413041221",
        password="secret", school="中北大学",
    )
    session.login_by_student_id.assert_called_once_with(
        "2413041221", "secret", "中北大学", wait_ms=30_000)
    assert result["logged_in"] is True
    assert result["stage"] == "done"
    assert result["owner"] == "proxy"
    assert "2413041221" not in str(result)
    assert "secret" not in str(result)
    assert "中北大学" not in str(result)


def test_password_login_never_asks_for_a_school():
    session = Mock()
    session.login_by_password.return_value = {
        "logged_in": True, "method": "password", "note": "自动登录成功",
        "url": "https://www.eduplus.net/workbench/index/school", "token_len": 48,
    }
    result = _engine(session).login(owner="self", method="password",
                                    username="13800000000", password="secret")
    session.login_by_password.assert_called_once_with(
        "13800000000", "secret", wait_ms=30_000)
    assert result["stage"] == "done"
    assert "13800000000" not in str(result)


def test_failed_auto_login_is_reported_as_a_failure_stage():
    session = Mock()
    session.login_by_password.return_value = {
        "logged_in": False, "method": "password",
        "reason": "提交后未检测到登录成功，可能是账号密码错误",
        "url": "https://uc.eduplus.net/login", "body_hint": "请输入验证码",
    }
    result = _engine(session).login(owner="self", method="password",
                                    username="13800000000", password="secret")
    assert result["stage"] == "login_failed"
    assert result["logged_in"] is False
    assert "secret" not in str(result)


# --------------------------------------------------------------------------- #
# SM wizard: hand over, never fill
# --------------------------------------------------------------------------- #
def test_sms_method_parks_the_page_and_never_fills_anything():
    session = Mock()
    session.open_sms_login.return_value = {
        "logged_in": False, "method": "sms", "manual": True,
        "tab_selected": True, "url": "https://uc.eduplus.net/login?theme=athena",
        "body_hint": "学号/工号登录 短信登录 密码登录",
    }
    result = _engine(session).login(owner="self", method="sms")
    session.open_sms_login.assert_called_once_with()
    session.login_by_password.assert_not_called()
    session.login_by_student_id.assert_not_called()
    assert result["stage"] == "manual_sms"
    assert result["manual"] is True
    assert result["url"].startswith("https://uc.eduplus.net/login")
    assert "验证码" in result["question"]


def test_sms_method_reports_an_existing_session_instead_of_parking():
    session = Mock()
    session.open_sms_login.return_value = {
        "logged_in": True, "note": "已是登录态，无需重复登录",
        "url": "https://www.eduplus.net/workbench/index/school", "token_len": 48,
    }
    result = _engine(session).login(owner="self", method="sms")
    assert result["stage"] == "done"
    assert result["logged_in"] is True
    # Nothing to hand over when a session already exists.
    assert "question" not in result


# --------------------------------------------------------------------------- #
# tab verification: placeholders, not body text
# --------------------------------------------------------------------------- #
class _FakePage:
    """Only what ``_tab_form_matches`` reads: live input/button state."""

    def __init__(self, placeholders, buttons):
        self._state = {"placeholders": placeholders, "buttons": buttons}

    def evaluate(self, _script):
        return self._state


def _browser() -> BrowserSession:
    # The tab matcher only reads the page, so skip the Playwright launch in
    # BrowserSession.__init__ and bind the method to a bare instance.
    return object.__new__(BrowserSession)


def test_tab_match_reads_placeholders_instead_of_body_text():
    page = _FakePage(["请输入账号/手机号", "请输入密码"], ["忘记密码", "登录"])
    session = _browser()
    assert session._tab_form_matches(page, "password") is True
    # 账号/手机号 must not be mistaken for the SMS tab.
    assert session._tab_form_matches(page, "sms") is False
    assert session._tab_form_matches(page, "student_id") is False


def test_tab_match_recognises_the_sms_form():
    page = _FakePage(["请输入手机号", "请输入验证码"], ["获取验证码", "登录"])
    session = _browser()
    assert session._tab_form_matches(page, "sms") is True


def test_body_hint_degrades_to_empty_when_page_mid_navigation():
    """A login-failure receipt must never raise because the page is mid-
    navigation; the body hint degrades to "" instead."""
    class _DetachedPage:
        url = "https://www.eduplus.net/login"

        def inner_text(self, selector="body"):
            raise RuntimeError("frame is navigating away")

    session = _browser()
    assert session._body_hint(_DetachedPage()) == ""


def test_body_hint_redacts_and_truncates():
    class _BodyPage:
        url = "https://www.eduplus.net/login"

        def inner_text(self, selector="body"):
            return "密码错误 " + "x" * 500 + " token=abc"

    session = _browser()
    hint = session._body_hint(_BodyPage(), limit=300)
    assert len(hint) <= 300
    assert "token=abc" not in hint


def test_tab_match_recognises_the_student_id_form():
    page = _FakePage(["", "请输入学号/工号", "请输入密码"], ["忘记密码", "登录"])
    session = _browser()
    assert session._tab_form_matches(page, "student_id") is True
    assert session._tab_form_matches(page, "sms") is False
