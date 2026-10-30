"""Regression tests for the 0.6.0 experiment automation and the audited fixes.

Covers: report generation (python-docx), the experiment upload path guard,
the navigation scheme allow-list (file:// refusal) and the file-path
confinement that keeps every file-touching tool inside allowed_io_paths.
"""

import os
from pathlib import Path

import pytest

from uplus_mcp import utils
from uplus_mcp.config import Config, load_config


# --------------------------------------------------------------------------- #
# P2: python-docx dependency actually works
# --------------------------------------------------------------------------- #
def _has_docx() -> bool:
    try:
        import docx  # noqa: F401
        return True
    except ImportError:
        return False


needs_docx = pytest.mark.skipif(not _has_docx(), reason="python-docx 未安装")


@needs_docx
def test_generate_algorithm_report_writes_a_valid_docx(tmp_path):
    from uplus_mcp.report_generator import generate_algorithm_report

    out = tmp_path / "reports" / "实验一.docx"
    path = generate_algorithm_report(
        title="实验一 进程同步",
        purpose="验证信号量互斥",
        content="生产者消费者问题",
        output_path=out,
        student_name="张三",
        student_id="20240001",
    )
    assert path == out
    assert path.stat().st_size > 0
    # A .docx is a zip container; the magic bytes prove the file is real.
    with open(path, "rb") as fh:
        assert fh.read(2) == b"PK"


@needs_docx
def test_generate_report_creates_missing_parent_directories(tmp_path):
    from uplus_mcp.report_generator import generate_report

    out = tmp_path / "deep" / "nested" / "r.docx"
    path = generate_report("标题", "目的", "内容", ["步骤一"], ["要求一"], out)
    assert path.exists()
    assert path.parent.is_dir()


# --------------------------------------------------------------------------- #
# P1: navigation scheme allow-list (the file:// read/upload chain)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("blocked", [
    "file:///C:/Users/huawei/.ssh/id_rsa",
    "file:///etc/passwd",
    "ftp://example.com/x",
    "data:text/html,<script>alert(1)</script>",
    "javascript:alert(1)",
    "blob:https://x/abc",
])
def test_safe_navigation_url_refuses_non_web_schemes(blocked):
    with pytest.raises(ValueError, match="只允许 http/https"):
        utils.safe_navigation_url(blocked)


def test_safe_navigation_url_refuses_empty():
    with pytest.raises(ValueError):
        utils.safe_navigation_url("   ")


@pytest.mark.parametrize("ok", [
    "https://www.eduplus.net/home/index",
    "http://127.0.0.1:9333/json",
])
def test_safe_navigation_url_allows_web_schemes(ok):
    assert utils.safe_navigation_url(ok) == ok


def test_safe_navigation_url_resolves_relative_paths_against_base():
    base = "https://www.eduplus.net"
    assert utils.safe_navigation_url("/workbench/index/school", base_url=base) == \
        "https://www.eduplus.net/workbench/index/school"
    assert utils.safe_navigation_url("home/index", base_url=base) == \
        "https://www.eduplus.net/home/index"


def test_safe_navigation_url_needs_base_for_relative_path():
    with pytest.raises(ValueError, match="base_url"):
        utils.safe_navigation_url("/workbench/index/school")


def test_safe_navigation_url_rejects_url_without_host():
    with pytest.raises(ValueError, match="主机名"):
        utils.safe_navigation_url("https://")


def test_browser_url_for_no_longer_folds_file_url_into_base_url():
    """url_for must refuse file:// instead of silently building an eduplus URL
    that resolves back to a local file on the next navigation."""
    from uplus_mcp.browser import BrowserSession
    session = BrowserSession.__new__(BrowserSession)
    session.base_url = "https://www.eduplus.net"
    with pytest.raises(ValueError, match="只允许 http/https"):
        session.url_for("file:///C:/secrets.txt")
    # Internal routes still resolve against the platform origin.
    assert session.url_for("/home/index") == "https://www.eduplus.net/home/index"


def test_act_batch_navigate_rejects_file_scheme_before_goto():
    """The batch runner must fail the step, never call page.goto with file://."""
    from uplus_mcp.browse import BrowseTools
    from uplus_mcp.browser import UplusError

    page = _GotoSpy()
    tools = BrowseTools(_SessionWithConfig(page))
    out = tools.act_batch([{"op": "navigate", "url": "file:///C:/secrets.txt"}])
    assert page.goto_calls == []           # never navigated
    assert out["failure"]["op"] == "navigate"
    assert "只允许 http/https" in out["failure"]["error"]
    assert out["completed"] == []


class _GotoSpy:
    url = "https://www.eduplus.net/workbench"

    def goto(self, url, wait_until=None):
        self.goto_calls.append(url)
        return None

    goto_calls: list = []

    def inner_text(self, selector="body"):
        return ""


class _SessionWithConfig:
    def __init__(self, page):
        self.page = page
        self.config = Config(allowed_io_paths=["reports"])


# --------------------------------------------------------------------------- #
# P4: file-path confinement
# --------------------------------------------------------------------------- #
def _roots(tmp_path):
    root = tmp_path / "uplus-data"
    (root / "reports").mkdir(parents=True)
    return root / "reports"


def test_resolve_io_path_allows_a_path_inside_the_root(tmp_path):
    root = _roots(tmp_path)
    assert utils.resolve_io_path(str(root / "r.docx"), [root]) == \
        (root / "r.docx").resolve()


@pytest.mark.parametrize("escape", [
    "..", "../.ssh/id_rsa", "reports/../../.ssh/id_rsa",
])
def test_resolve_io_path_refuses_traversal_outside_the_root(tmp_path, escape):
    root = _roots(tmp_path)
    with pytest.raises(PermissionError, match="不在允许的目录内"):
        utils.resolve_io_path(str(root / escape), [root])


def test_resolve_io_path_refuses_absolute_path_outside_the_root(tmp_path):
    root = _roots(tmp_path)
    outside = tmp_path / "elsewhere" / "x.docx"
    outside.parent.mkdir()
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(PermissionError):
        utils.resolve_io_path(str(outside), [root])


def test_resolve_io_path_refuses_home_expansion_outside_the_root(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    (ssh / "id_rsa").write_text("k", encoding="utf-8")
    root = _roots(tmp_path)
    with pytest.raises(PermissionError):
        utils.resolve_io_path("~/.ssh/id_rsa", [root])


def test_resolve_io_path_refuses_symlink_pointing_outside_the_root(tmp_path):
    root = _roots(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("k", encoding="utf-8")
    link = root / "link.txt"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):  # Windows without developer mode
        pytest.skip("当前系统不支持创建符号链接")
    with pytest.raises(PermissionError):
        utils.resolve_io_path(str(link), [root])


def test_resolve_io_path_must_exist_raises_for_missing_file(tmp_path):
    root = _roots(tmp_path)
    with pytest.raises(FileNotFoundError):
        utils.resolve_io_path(str(root / "nope.docx"), [root], must_exist=True)


def test_resolve_io_path_must_exist_accepts_a_real_file(tmp_path):
    root = _roots(tmp_path)
    real = root / "r.docx"
    real.write_text("x", encoding="utf-8")
    assert utils.resolve_io_path(str(real), [root], must_exist=True) == real.resolve()


def test_resolve_io_path_with_no_roots_allows_anything(tmp_path):
    """An explicitly empty root list means "no confinement configured"."""
    anywhere = tmp_path / "x.txt"
    assert utils.resolve_io_path(str(anywhere), []) == anywhere.resolve()


# --------------------------------------------------------------------------- #
# config plumbing
# --------------------------------------------------------------------------- #
def test_config_default_io_roots_cover_reports_and_temp(tmp_path):
    cfg = Config(report_dir=str(tmp_path / "reports"))
    roots = cfg.io_roots
    assert (tmp_path / "reports").resolve() in roots
    assert Path(utils_resolve_tempdir()) in [Path(r) for r in roots]


def utils_resolve_tempdir() -> str:
    import tempfile
    return tempfile.gettempdir()


def test_load_config_honours_allowed_io_paths_override(tmp_path, monkeypatch):
    import json

    cfg_file = tmp_path / "courses.json"
    cfg_file.write_text(json.dumps(
        {"allowed_io_paths": [str(tmp_path / "only-here")],
         "report_dir": "reports"}), encoding="utf-8")
    cfg = load_config(str(cfg_file))
    assert cfg.io_roots == [(tmp_path / "only-here").resolve()]


def test_load_config_env_var_replaces_default_roots(tmp_path, monkeypatch):
    import json

    cfg_file = tmp_path / "courses.json"
    cfg_file.write_text(json.dumps({"report_dir": "reports"}), encoding="utf-8")
    monkeypatch.setenv("UPLUS_ALLOWED_PATHS",
                       os.pathsep.join([str(tmp_path / "a"), str(tmp_path / "b")]))
    cfg = load_config(str(cfg_file))
    assert cfg.io_roots == [(tmp_path / "a").resolve(), (tmp_path / "b").resolve()]


def test_load_config_without_file_defaults_to_reports_plus_temp():
    cfg = load_config(str(Path(__file__).with_name("does-not-exist.json")))
    assert Path("reports").resolve() in cfg.io_roots


# --------------------------------------------------------------------------- #
# P6: secret redaction
# --------------------------------------------------------------------------- #
def test_redact_secrets_masks_token_like_pairs():
    body = '{"x-access-token": "abcdef123456", "name": "作业"}'
    out = utils.redact_secrets(body)
    assert "abcdef123456" not in out
    assert "x-access-token" in out and "***" in out
    assert "作业" in out


def test_redact_secrets_leaves_ordinary_text_untouched():
    assert utils.redact_secrets("登录态已过期") == "登录态已过期"
    assert utils.redact_secrets(None) == ""
