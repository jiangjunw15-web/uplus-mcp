"""Tests for the two-mode sign-in flow and credential hygiene."""

from unittest.mock import Mock

from uplus_mcp.config import Config
from uplus_mcp.engine import PatrolEngine


def _engine(session: Mock) -> PatrolEngine:
    cfg = Config()
    return PatrolEngine(cfg, session, Mock(), Mock())


def test_login_manual_waits_for_user_takeover_and_reports_success():
    session = Mock()
    session.wait_for_session.return_value = {
        "logged_in": True, "token_len": 48,
        "url": "https://www.eduplus.net/workbench/index/school", "title": "工作台",
    }
    result = _engine(session).login(mode="manual", wait_timeout_ms=30_000)
    session.open_login_page.assert_called_once()
    session.auto_login.assert_not_called()
    assert result["logged_in"] is True
    assert result["mode"] == "manual"
    assert result["token_len"] == 48


def test_login_manual_reports_timeout_without_side_effects():
    session = Mock()
    session.wait_for_session.return_value = {
        "logged_in": False, "token_len": 0,
        "url": "https://uc.eduplus.net/login", "title": "登录",
    }
    result = _engine(session).login(mode="manual", wait_timeout_ms=2_000)
    assert result["logged_in"] is False
    assert "等待超时" in result["reason"]
    session.auto_login.assert_not_called()


def test_login_auto_refuses_when_credentials_missing():
    session = Mock()
    result = _engine(session).login(mode="auto", username="", password="")
    assert result["logged_in"] is False
    assert "账号与密码" in result["reason"]
    session.auto_login.assert_not_called()


def test_login_auto_forwards_credentials_and_reports_success():
    session = Mock()
    session.auto_login.return_value = {
        "logged_in": True, "mode": "auto", "note": "自动登录成功",
        "url": "https://www.eduplus.net/workbench/index/school", "token_len": 48,
    }
    result = _engine(session).login(mode="auto", username="student",
                                    password="secret", school="some-school")
    session.auto_login.assert_called_once_with(
        "student", "secret", "some-school", wait_ms=30_000)
    assert result["logged_in"] is True
    # Credentials must never appear in the tool result.
    assert "student" not in str(result)
    assert "secret" not in str(result)
    assert "some-school" not in str(result)


def test_login_auto_failure_is_handed_back_for_manual_takeover():
    session = Mock()
    session.auto_login.return_value = {
        "logged_in": False, "mode": "auto",
        "reason": "提交后未检测到登录成功，可能是账号密码错误、学校未选对，或需要短信/图形验证码",
        "url": "https://uc.eduplus.net/login", "body_hint": "请输入验证码",
    }
    result = _engine(session).login(mode="auto", username="student",
                                    password="secret", school="some-school")
    assert result["logged_in"] is False
    assert "验证码" in result["reason"] or "验证码" in result.get("body_hint", "")
    assert "secret" not in str(result)
    assert "some-school" not in str(result)
