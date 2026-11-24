"""账号校验：当平台只暴露不透明 id（如 userid GUID）时的绑定兜底。

v0.8.18 的守卫假设 localStorage 里有学号；实测这套部署只存 ``userid`` 一个
GUID，于是「学号 vs GUID」永远不相等，所有账号操作被误拒。修复后：用该账号
本人的凭据登录成功后，把「学号 → GUID」写进台账，后续运行按台账校验。
"""
from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from uplus_mcp.browser import UplusError
from uplus_mcp.config import Config
from uplus_mcp.engine import PatrolEngine


def _engine(tmp_path, actual: str = "guid-abc", expected: str = "2413041236"):
    cfg = Config(expected_account=expected,
                 report_dir=str(tmp_path / "reports"))
    session = Mock()
    session.account_matches.return_value = {
        "expected": expected, "actual": actual,
        "available": True, "matched": False,
    }
    return PatrolEngine(cfg, session, Mock(), Mock()), cfg


def test_first_run_without_a_binding_is_refused(tmp_path):
    engine, _ = _engine(tmp_path)
    with pytest.raises(UplusError):
        engine._guard_account()
    assert engine._account_ok is False


def test_binding_lets_the_same_session_pass_later(tmp_path):
    engine, cfg = _engine(tmp_path)
    engine._remember_fingerprint("2413041236", "guid-abc")
    engine._guard_account()
    assert engine._account_ok is True
    ledger = json.loads(
        (cfg.report_folder / "account_fingerprints.json").read_text(encoding="utf-8"))
    assert ledger == {"2413041236": "guid-abc"}


def test_binding_of_another_session_is_refused(tmp_path):
    engine, _ = _engine(tmp_path, actual="guid-other")
    engine._remember_fingerprint("2413041236", "guid-abc")
    with pytest.raises(UplusError):
        engine._guard_account()
    assert engine._account_ok is False


def test_binding_ledger_follows_the_shared_journal(monkeypatch, tmp_path):
    # Multi-account setups point UPLUS_JOURNAL_PATH at one file: bindings must be
    # shared the same way, or each account silently loses its own fingerprint.
    journal = tmp_path / "shared" / "answer_journal.json"
    monkeypatch.setenv("UPLUS_JOURNAL_PATH", str(journal))
    cfg = Config(expected_account="2413041236",
                 report_dir=str(tmp_path / "reports"))
    engine = PatrolEngine(cfg, Mock(), Mock(), Mock())
    assert engine._fingerprint_path().parent == journal.parent
    engine._remember_fingerprint("2413041236", "guid-abc")
    assert (journal.parent / "account_fingerprints.json").exists()


def test_check_login_reflects_the_binding(tmp_path):
    # check_login must not call a bound session a mismatch while the guard lets
    # it through — that used to make the status line contradict the tools.
    engine, _ = _engine(tmp_path)
    engine.session.is_logged_in.return_value = True
    engine._remember_fingerprint("2413041236", "guid-abc")
    result = engine.check_login()
    assert result["account_match"] is True
    assert result.get("matched_by") == "fingerprint"


def test_login_binds_only_the_account_the_human_typed(tmp_path):
    engine, cfg = _engine(tmp_path, expected="2413041236")
    engine.session.login_by_password.return_value = {"logged_in": True}
    res = engine.login(owner="self", method="password",
                       username="2413041236", password="secret")
    assert res["account_match"] is True
    assert res["stage"] == "done"
    assert json.loads(
        (cfg.report_folder / "account_fingerprints.json").read_text(encoding="utf-8")
    ) == {"2413041236": "guid-abc"}


def test_login_with_a_foreign_username_never_binds(tmp_path):
    engine, cfg = _engine(tmp_path, expected="2413041236")
    engine.session.login_by_password.return_value = {"logged_in": True}
    res = engine.login(owner="self", method="password",
                       username="13800000000", password="secret")
    assert res["stage"] == "account_mismatch"
    assert not (cfg.report_folder / "account_fingerprints.json").exists()
