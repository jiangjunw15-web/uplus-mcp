"""Regression tests for the code-review fixes.

Covers: Beijing-time deadline parsing, tolerant timestamp coercion, the
platform-origin check before an in-page API read, experiment-name extraction
for 实验三 and later, the model-answer parser's non-object payload, the East
Asian font of the generated .docx, relative URLs in a batch navigate step,
the shared server config and the patrol CLI's startup failure path.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import zipfile

import pytest

from uplus_mcp import utils
from uplus_mcp.experiment import extract_experiment_name
from uplus_mcp.solver import parse_model_answers

CN_TZ = timezone(timedelta(hours=8))


def _beijing_epoch(*args) -> int:
    return int(datetime(*args, tzinfo=CN_TZ).timestamp() * 1000)


# --------------------------------------------------------------------------- #
# deadlines are read as Beijing wall clock, not as machine-local time
# --------------------------------------------------------------------------- #
def test_parse_cn_datetime_reads_the_platform_wall_clock():
    assert utils.parse_cn_datetime("2026-11-05 23:59") == _beijing_epoch(2026, 11, 5, 23, 59)


def test_parse_cn_datetime_accepts_date_only_and_seconds():
    assert utils.parse_cn_datetime("2026-11-05") == _beijing_epoch(2026, 11, 5, 0, 0)
    assert utils.parse_cn_datetime("2026-11-05 23:59:30") == \
        _beijing_epoch(2026, 11, 5, 23, 59, 30)


@pytest.mark.parametrize("bad", ["", "   ", "not-a-date", None])
def test_parse_cn_datetime_returns_none_for_garbage(bad):
    assert utils.parse_cn_datetime(bad) is None


def test_deadline_label_round_trips_a_beijing_deadline():
    """The label must show the platform's wall clock on any host timezone."""
    ms = utils.parse_cn_datetime("2026-11-05 23:59")
    assert utils.format_ts(ms) == "2026-11-05 23:59"
    assert "2026-11-05 23:59" in utils.deadline_label(ms, utils.parse_cn_datetime("2026-11-01 00:00"))


# --------------------------------------------------------------------------- #
# timestamps that are not epoch milliseconds must not crash a comparison
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value,expected", [
    (1788192000000, 1788192000000),
    ("1788192000000", 1788192000000),
    ("2026-11-05 23:59", _beijing_epoch(2026, 11, 5, 23, 59)),
    (None, None),
    ("", None),
    ("nonsense", None),
    (True, None),
])
def test_to_epoch_ms_coerces_or_degrades(value, expected):
    assert utils.to_epoch_ms(value) == expected


def test_is_expired_tolerates_a_date_string():
    """A string deadline used to raise TypeError inside the comparison."""
    assert utils.is_expired("2020-01-01 00:00") is True
    assert utils.is_expired("2999-01-01 00:00") is False


def test_deadline_label_tolerates_a_date_string():
    assert utils.deadline_label("2020-01-01 00:00") == "已截止"
    assert utils.deadline_label("not-a-date") == "未设置截止时间"


def test_homework_task_end_ms_is_always_numeric():
    from uplus_mcp.models import HomeworkTask

    task = HomeworkTask.from_api(
        {"id": "1", "homeworkId": "2", "endTime": "2026-11-05 23:59"}, "c1"
    )
    assert task.end_ms == _beijing_epoch(2026, 11, 5, 23, 59)


def test_course_dates_from_the_study_feed_are_numeric():
    import datetime

    from uplus_mcp.config import Config
    from uplus_mcp.platform import PlatformClient
    from uplus_mcp import utils

    # Use a date far in the future: this test checks parsing, not the clock.
    future = datetime.datetime.now(tz=utils.CN_TZ) + datetime.timedelta(days=365)
    stamp = future.strftime("%Y-%m-%d %H:%M")

    class _FakeSession:
        def api_get(self, path, *, retries=1):
            return {"data": [{"id": "c1", "name": "算法", "endTime": stamp}]}

    client = PlatformClient(_FakeSession(), Config())
    course = client.list_my_courses()[0]
    assert course.end_ms == _beijing_epoch(
        future.year, future.month, future.day, future.hour, future.minute
    )
    # The patrol filters on this value; it must not raise.
    assert utils.is_expired(course.end_ms) is False


# --------------------------------------------------------------------------- #
# the API read must run on the platform origin, not on a look-alike host
# --------------------------------------------------------------------------- #
class _FakePage:
    def __init__(self, url):
        self.url = url
        self.evaluated = []

    def is_closed(self):
        return False

    def evaluate(self, script, arg=None):
        self.evaluated.append(arg)
        return {"status": 200, "body": '{"ok": true}'}


def _session_with_page(page):
    from uplus_mcp.browser import BrowserSession
    from uplus_mcp.config import Config

    session = BrowserSession.__new__(BrowserSession)
    session.base_url = "https://www.eduplus.net"
    session.config = Config()
    session._started = True
    session._page = page
    session._context = None
    return session


def test_api_get_reopens_when_the_host_is_only_a_substring_of_the_page_url():
    """``www.eduplus.net.evil.example`` contains the platform host as text
    but is a different origin, so the read must bounce back."""
    page = _FakePage("https://www.eduplus.net.evil.example/home")
    session = _session_with_page(page)
    opened: list[str] = []
    session.open = lambda path: opened.append(path)

    session.api_get("/api/course/athena/courses/study")
    assert opened == ["/workbench/index/school"]


def test_api_get_does_not_reopen_when_already_on_the_platform_origin():
    page = _FakePage("https://www.eduplus.net/workbench/index/school")
    session = _session_with_page(page)
    opened: list[str] = []
    session.open = lambda path: opened.append(path)

    session.api_get("/api/course/athena/courses/study")
    assert opened == []


# --------------------------------------------------------------------------- #
# experiment heading: every 实验N is recognised, not only 实验一/实验二
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,expected", [
    ("实验一 进程同步\n提交物：报告", "实验一 进程同步"),
    ("实验二 程序调度算法\n提交物：报告", "实验二 程序调度算法"),
    ("实验三 磁盘调度算法\n提交物：报告", "实验三 磁盘调度算法"),
    ("实验九 动态规划算法\n提交物：报告", "实验九 动态规划算法"),
    ("实验10 动态规划算法\n提交物：报告", "实验10 动态规划算法"),
    ("提交物：实验报告\n实验四 图调度算法", "实验四 图调度算法"),
])
def test_extract_experiment_name_reads_any_experiment_number(text, expected):
    assert extract_experiment_name(text) == expected


def test_extract_experiment_name_returns_empty_without_a_heading():
    assert extract_experiment_name("提交物：实验报告\n截止：2026-11-05 23:59") == ""


# --------------------------------------------------------------------------- #
# the model answer parser must survive a payload that is not an object
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("payload", ["[1, 2]", '"just a string"', "42", "null", "true"])
def test_parse_model_answers_ignores_a_non_object_payload(payload):
    assert parse_model_answers(payload) == {}


def test_parse_model_answers_still_reads_the_documented_shapes():
    assert parse_model_answers('{"answers": {"q1": "C"}}') == {"q1": "C"}
    assert parse_model_answers({"answers": {"q1": "C"}}) == {"q1": "C"}
    assert parse_model_answers('```json\n{"answers": {"q1": "C"}}\n```') == {"q1": "C"}


# --------------------------------------------------------------------------- #
# the generated report must use 宋体 for Chinese glyphs
# --------------------------------------------------------------------------- #
def _has_docx() -> bool:
    try:
        import docx  # noqa: F401
        return True
    except ImportError:
        return False


needs_docx = pytest.mark.skipif(not _has_docx(), reason="python-docx 未安装")


@needs_docx
def test_generate_report_sets_the_east_asian_font(tmp_path):
    """``font.name`` only writes the latin face; Word reads Chinese glyphs
    from ``w:eastAsia``."""
    from uplus_mcp.report_generator import generate_report

    out = tmp_path / "r.docx"
    generate_report("实验一 进程同步", "目的", "内容", ["步骤"], ["要求"], out)
    with zipfile.ZipFile(out) as zf:
        styles = zf.read("word/styles.xml").decode("utf-8")
    assert 'w:eastAsia="宋体"' in styles


# --------------------------------------------------------------------------- #
# a batch navigate step accepts the same relative paths as browse_open
# --------------------------------------------------------------------------- #
class _GotoSpy:
    def __init__(self, url="https://www.eduplus.net/workbench"):
        self.url = url
        self.goto_calls: list[str] = []

    def goto(self, url, wait_until=None):
        self.goto_calls.append(url)
        return None

    def inner_text(self, selector="body"):
        return ""


class _SessionWithBaseUrl:
    base_url = "https://www.eduplus.net"

    def __init__(self, page):
        self.page = page


def test_act_batch_navigate_resolves_a_relative_path_against_the_platform():
    from uplus_mcp.browse import BrowseTools

    page = _GotoSpy()
    tools = BrowseTools(_SessionWithBaseUrl(page))
    out = tools.act_batch([{"op": "navigate", "url": "/home/index"}])
    assert page.goto_calls == ["https://www.eduplus.net/home/index"]
    assert "failure" not in out


def test_act_batch_navigate_still_refuses_a_file_url():
    from uplus_mcp.browse import BrowseTools

    page = _GotoSpy()
    tools = BrowseTools(_SessionWithBaseUrl(page))
    out = tools.act_batch([{"op": "navigate", "url": "file:///C:/secrets.txt"}])
    assert page.goto_calls == []
    assert "只允许 http/https" in out["failure"]["error"]


# --------------------------------------------------------------------------- #
# one config for every tool: the file tools and the engine must agree
# --------------------------------------------------------------------------- #
def test_server_config_is_loaded_once_and_shared(monkeypatch):
    import uplus_mcp.server as server

    server._state.clear()
    calls: list[int] = []

    def fake_load():
        calls.append(1)
        return server.Config()

    monkeypatch.setattr(server, "load_config", fake_load)
    first = server._config()
    second = server._config()
    assert first is second
    assert len(calls) == 1
    server._state.clear()


# --------------------------------------------------------------------------- #
# the patrol CLI must report a startup failure, not a raw traceback
# --------------------------------------------------------------------------- #
def test_patrol_cli_reports_a_startup_failure(monkeypatch, capsys):
    from uplus_mcp import daily_patrol

    def boom(config_path=""):
        raise RuntimeError("playwright is not installed")

    monkeypatch.setattr(daily_patrol, "build_engine", boom)
    monkeypatch.setattr(daily_patrol.sys, "argv", ["uplus-patrol", "--config", "x.json"])
    code = daily_patrol.main()
    assert code == 3
    assert "初始化失败" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# an installed wheel must not write into the Python library directory
# --------------------------------------------------------------------------- #
def test_wheel_layout_resolves_paths_against_the_working_directory(tmp_path, monkeypatch):
    """In a wheel install there is no project root above the package, so the
    data root must be the working directory - never the Python library."""
    from uplus_mcp import config as config_mod

    fake = tmp_path / "lib" / "python3.12" / "site-packages" / "uplus_mcp" / "config.py"
    fake.parent.mkdir(parents=True)
    fake.write_text("", encoding="utf-8")
    workdir = tmp_path / "work"
    workdir.mkdir()

    monkeypatch.setattr(config_mod, "__file__", str(fake))
    monkeypatch.chdir(workdir)
    assert config_mod._data_root() == Path.cwd()
    assert config_mod._data_root() != tmp_path / "lib" / "python3.12"


def test_uplus_home_overrides_the_data_root(tmp_path, monkeypatch):
    from uplus_mcp import config as config_mod

    monkeypatch.setenv("UPLUS_HOME", str(tmp_path))
    assert config_mod._data_root() == tmp_path
    cfg = config_mod.Config(report_dir="reports")
    assert cfg.report_folder == tmp_path / "reports"


def test_example_config_ships_inside_the_package():
    from uplus_mcp.config import EXAMPLE_CONFIG_PATH

    assert EXAMPLE_CONFIG_PATH.is_file()
    assert EXAMPLE_CONFIG_PATH.parent.name == "uplus_mcp"


def test_example_config_keeps_the_documented_default_io_roots(tmp_path):
    """An explicit list replaces the default, so the example must not narrow
    it below the report folder plus the system temp dir."""
    from uplus_mcp.config import EXAMPLE_CONFIG_PATH, load_config

    cfg = load_config(str(EXAMPLE_CONFIG_PATH))
    assert tmp_path not in cfg.io_roots
    assert any(Path(r).name == "reports" for r in cfg.io_roots)
    assert any(Path(r) == Path(tempfile.gettempdir()) for r in cfg.io_roots)
