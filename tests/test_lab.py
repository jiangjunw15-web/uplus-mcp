"""Tests for experiment retrieval: 资料 walk, download, template discovery
and detail-page scraping.

These are pure/offline tests: fake pages and fake sessions stand in for
Playwright, and downloaded files are written to ``tmp_path``. They mirror the
conventions used across the suite (hand-written fakes, ``Mock`` only for
collaborators, ``Config(report_dir=str(tmp_path))``, Chinese
``pytest.raises(match=...)``).
"""

from __future__ import annotations

import base64

import pytest
from docx import Document

from uplus_mcp import materials, report_generator as rg
from uplus_mcp.browser import BrowserSession, UplusError
from uplus_mcp.config import Config
from uplus_mcp.engine import PatrolEngine
from uplus_mcp.experiment import (
    ExperimentDetail,
    ExperimentOperator,
    _template_matcher,
    _template_priority,
    Attachment,
)
from uplus_mcp.models import Course, Experiment
from uplus_mcp.operator import HomeworkOperator


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
class _EmptyLocator:
    @property
    def first(self):
        return self

    def count(self):
        return 0


class _Resp:
    def __init__(self, body: bytes, ok: bool = True, status: int = 200):
        self._body = body
        self.ok = ok
        self.status = status

    def body(self):
        return self._body

    def text(self):
        return self._body.decode("utf-8", "replace")


class _Request:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        return self._resp


class _Context:
    def __init__(self, resp):
        self.request = _Request(resp)


class _FetchPage:
    """Fake page for ``BrowserSession.fetch_bytes``."""

    def __init__(self, result=None, exc=None):
        self.url = "https://www.eduplus.net/workbench/index/school"
        self._result = result
        self._exc = exc

    def is_closed(self):
        return False

    def evaluate(self, script, arg=None):
        if self._exc:
            raise self._exc
        return self._result


def _session(page, context=None):
    """Attach a fake page/context to a real BrowserSession (no browser)."""
    session = BrowserSession.__new__(BrowserSession)
    session.base_url = "https://www.eduplus.net"
    session.config = Config()
    session._started = True
    session._page = page
    session._context = context
    return session


def _docx(tmp_path, paragraphs, name="report.docx"):
    doc = Document()
    for line in paragraphs:
        doc.add_paragraph(line)
    path = tmp_path / name
    doc.save(str(path))
    return path


def _detail(**kwargs):
    base = dict(course_id="c1", exp_id="e1", stu_exp_id="s1",
                name="实验一 进程同步", material="提交实验报告",
                manual_text="实验目的：掌握进程同步\n实验步骤：1. 编写代码")
    base.update(kwargs)
    return ExperimentDetail(**base)


# --------------------------------------------------------------------------- #
# binary fetch / download
# --------------------------------------------------------------------------- #
def test_fetch_bytes_uses_in_page_fetch_first():
    raw = b"\x89PNG fake bytes"
    page = _FetchPage({"ok": True, "status": 200,
                       "b64": base64.b64encode(raw).decode("ascii")})
    context = _Context(_Resp(b"should-not-be-used"))
    session = _session(page, context)
    assert session.fetch_bytes("https://www.eduplus.net/api/file/1") == raw
    assert context.request.calls == []


def test_fetch_bytes_falls_back_to_context_request_for_cross_origin():
    page = _FetchPage({"ok": False, "status": 0, "error": "CORS"})
    context = _Context(_Resp(b"OSS-BYTES"))
    session = _session(page, context)
    assert session.fetch_bytes("https://oss.example.com/signed") == b"OSS-BYTES"
    assert context.request.calls == ["https://oss.example.com/signed"]


def test_fetch_bytes_raises_when_both_paths_fail():
    page = _FetchPage(exc=RuntimeError("detached"))
    session = _session(page, _Context(_Resp(b"nope", ok=False, status=403)))
    with pytest.raises(UplusError, match="HTTP 403"):
        session.fetch_bytes("https://www.eduplus.net/api/file/1")


def test_download_to_writes_inside_allowed_roots(tmp_path):
    raw = b"PK fake docx"
    page = _FetchPage({"ok": True, "status": 200,
                       "b64": base64.b64encode(raw).decode("ascii")})
    session = _session(page)
    session.config = Config(report_dir=str(tmp_path))
    dest = tmp_path / "sub" / "模板.docx"
    path = session.download_to("https://www.eduplus.net/f", dest)
    assert path == dest.resolve()
    assert path.read_bytes() == raw


def test_download_to_refuses_outside_allowed_roots(tmp_path):
    allowed = tmp_path / "reports"
    allowed.mkdir()
    session = _session(_FetchPage({"ok": True, "b64": "eA=="}))
    session.config = Config(allowed_io_paths=[str(allowed)])
    with pytest.raises(PermissionError, match="不在允许的目录内"):
        session.download_to("https://www.eduplus.net/f", tmp_path / "outside.bin")


# --------------------------------------------------------------------------- #
# materials: template matching and folder listing
# --------------------------------------------------------------------------- #
class _Entry:
    def __init__(self, name):
        self.name = name


def test_template_matcher_finds_template_and_experiment_files():
    matcher = _template_matcher("实验一")
    assert matcher(_Entry("实验一 进程同步.pdf"))
    assert matcher(_Entry("实验报告模板.docx"))
    assert not matcher(_Entry("实验二 线程同步.pdf"))
    assert not matcher(_Entry(""))


def test_template_priority_prefers_explicit_report_template():
    ordered = sorted(
        [_Entry("实验一 材料.zip"), _Entry("报告模板.docx"),
         _Entry("实验报告模板.docx")],
        key=_template_priority,
    )
    assert ordered[0].name == "实验报告模板.docx"
    assert ordered[-1].name == "实验一 材料.zip"


class _MaterialPage:
    """Fake page for ``materials.list_folder`` (strategy-2 fallback)."""

    def __init__(self, entries, entry_href=""):
        self.url = "https://www.eduplus.net/course/course/c1"
        self.entries = entries
        self.entry_href = entry_href

    def locator(self, selector):
        return _EmptyLocator()

    def evaluate(self, script, arg=None):
        if "const labels" in script:
            return self.entry_href
        if "seen.has" in script:
            return self.entries
        return None


def test_list_folder_returns_structured_entries():
    page = _MaterialPage([
        {"name": "实验一", "href": "", "kind": "folder", "size": ""},
        {"name": "实验报告模板.docx", "href": "http://x/t.docx",
         "kind": "file", "size": "12KB"},
    ])
    entries = materials.list_folder(page)
    assert [e.name for e in entries] == ["实验一", "实验报告模板.docx"]
    assert entries[0].kind == "folder"
    assert entries[1].href == "http://x/t.docx"


class _HrefName:
    def __init__(self, href):
        self.href = href

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def inner_text(self):
        return "实验一 指导书.docx"

    def get_attribute(self, name):
        return self.href if name == "href" else None


class _HrefRow:
    def __init__(self, href):
        self.name_el = _HrefName(href)

    def locator(self, selector):
        if selector == "a, [role='button'], .name, .title":
            return self.name_el
        return _EmptyCells()


class _EmptyCells:
    def all(self):
        return []


class _HrefRows:
    def __init__(self, row):
        self.row = row

    def count(self):
        return 1

    def nth(self, index):
        return self.row


class _HrefPage:
    """Fake table-layout 资料 page (strategy 1) with a relative file href."""

    url = "https://www.eduplus.net/course/course/c1/MaterialPage"

    def __init__(self, href):
        self.row = _HrefRow(href)

    def locator(self, selector):
        if selector == ".el-table__row":
            return _HrefRows(self.row)
        return _EmptyLocator()


def test_list_folder_resolves_relative_hrefs_against_the_folder_page():
    """A relative file href must not be fetched from the root listing later."""
    page = _HrefPage("download/exp1.docx")
    entries = materials.list_folder(page)
    assert entries[0].href == (
        "https://www.eduplus.net/course/course/c1/download/exp1.docx")


class _TreeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    def count(self):
        return 1 if "text=" in self.selector else 0

    def click(self, timeout=None):
        name = self.selector.split("text=", 1)[1]
        # Jumping into a folder shows that folder's own listing.
        self.page.current = name


class _TreePage:
    """Fake 资料 page tree used to test ``materials.walk`` recursion."""

    def __init__(self):
        self.base = "https://www.eduplus.net"
        self.home_url = self.base + "/course/course/c1"
        self.material_url = self.base + "/course/course/c1/MaterialPage"
        self.url = self.home_url
        self.current = None
        self.history: list[str] = []
        self.lists = {
            "root": [
                {"name": "实验一", "href": "", "kind": "folder", "size": ""},
                {"name": "课程说明.docx", "href": "http://x/readme.docx",
                 "kind": "file", "size": ""},
            ],
            "实验一": [
                {"name": "实验报告模板.docx", "href": "http://x/tpl.docx",
                 "kind": "file", "size": ""},
                {"name": "实验一 进程同步.pdf", "href": "http://x/e1.pdf",
                 "kind": "file", "size": ""},
            ],
        }

    def locator(self, selector):
        return _TreeLocator(self, selector)

    def evaluate(self, script, arg=None):
        if "const labels" in script:
            return self.material_url if self.url == self.home_url else ""
        if "seen.has" in script:
            return self.lists.get(self.current or "root", [])
        return None

    def goto(self, url, wait_until=None, timeout=None):
        self.history.append(self.url)
        self.url = url
        self.current = "root"

    def go_back(self, wait_until=None, timeout=None):
        self.url = self.history.pop() if self.history else self.url
        self.current = "root" if self.current == "实验一" else None

    def wait_for_load_state(self, *args, **kwargs):
        return None


class _TreeSession:
    def __init__(self, page):
        self.page = page
        self.opened: list[str] = []

    def open(self, url):
        self.opened.append(url)
        self.page.url = url if url.startswith("http") else self.page.base + url
        self.page.current = "root" if self.page.url.endswith("MaterialPage") else None
        return self.page


def test_walk_recurses_into_folders_and_matches(monkeypatch):
    page = _TreePage()
    session = _TreeSession(page)
    # ``walk`` waits between navigations; keep the test fast.
    monkeypatch.setattr(materials.time, "sleep", lambda *_: None)
    results = materials.walk(
        session, "c1", _template_matcher("实验一"), max_depth=3)
    names = {e.name for e in results}
    assert "实验报告模板.docx" in names
    assert "实验一 进程同步.pdf" in names
    assert "课程说明.docx" not in names


def test_walk_honours_max_depth(monkeypatch):
    page = _TreePage()
    session = _TreeSession(page)
    monkeypatch.setattr(materials.time, "sleep", lambda *_: None)
    # depth 0 already reads the root listing; folders need one more level.
    root_only = materials.walk(
        session, "c1", _template_matcher("实验一"), max_depth=1)
    assert {e.name for e in root_only} == {"实验一"}
    deep = materials.walk(
        session, "c1", _template_matcher("实验一"), max_depth=3)
    assert any(e.name == "实验报告模板.docx" for e in deep)


def test_download_falls_back_to_download_event():
    class _Download:
        def save_as(self, path):
            return path

    class _DlInfo:
        value = _Download()

    class _Page:
        def locator(self, selector):
            return _TreeLocator(_TreePage(), selector)

        def expect_download(self, timeout=None):
            class _Ctx:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *a):
                    return False

                @property
                def value(self_inner):
                    return _Download()

            return _Ctx()

    class _Session:
        page = _Page()
        config = Config()

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(
            materials.utils, "resolve_io_path", lambda p, roots: p)
        entry = materials.MaterialEntry(name="报告.docx", href="")
        # A fake save_as is enough; the file need not exist for this check.
        result = materials.download(_Session(), entry, "unused.docx")
        assert str(result) == "unused.docx"
    finally:
        monkeypatch.undo()


# --------------------------------------------------------------------------- #
# manual derivation and template filling
# --------------------------------------------------------------------------- #
def test_derive_sections_copies_manual_text_verbatim():
    manual = (
        "实验一 进程同步\n"
        "实验目的：掌握进程同步与互斥的实现方法\n"
        "实验内容：\n"
        "1. 用信号量实现生产者-消费者\n"
        "2. 用管程实现读者-写者\n"
        "实验步骤：\n"
        "1. 分析问题\n"
        "2. 编写代码\n"
        "实验要求：提交实验报告，包含代码与分析\n"
    )
    sections = rg.derive_sections(manual)
    assert sections["实验目的"] == "掌握进程同步与互斥的实现方法"
    assert "生产者" in sections["实验内容"]
    assert sections["实验步骤"].startswith("1. 分析问题")
    assert "提交实验报告" in sections["实验要求"]


def test_derive_sections_does_not_treat_body_sentence_as_heading():
    assert rg.derive_sections("要求掌握进程同步") == {}
    assert rg.derive_sections("结果是生产者") == {}


@pytest.mark.parametrize("line,canonical", [
    ("一、实验目的", "实验目的"),
    ("实验目的：掌握", "实验目的"),
    ("实验结果分析：", "实验结果"),
    ("实验心得", "实验总结"),
    ("实验原理", "实验原理"),
    ("实验报告要求", "实验要求"),
])
def test_canonical_section_variants(line, canonical):
    assert rg.canonical_section(line) == canonical


@pytest.mark.parametrize("line", [
    "要求掌握进程同步", "结果是生产者", "内容包括如下实验", "原理上可行",
])
def test_canonical_section_rejects_body_sentences(line):
    assert rg.canonical_section(line) is None


def test_fill_template_into_skeleton_fills_every_section(tmp_path):
    skeleton = tmp_path / "skeleton.docx"
    rg.generate_report(title="实验一 进程同步", purpose="", content="",
                       steps=[], requirements=[], output_path=skeleton)
    sections = {
        "实验目的": "掌握同步",
        "实验内容": "生产者消费者",
        "实验步骤": "1. 编写代码",
        "实验实现": "伪代码与代码",
        "实验结果": "测试通过，复杂度 O(n)",
        "实验总结": "理解了互斥",
        "实验要求": "包含代码与分析",
    }
    filled = rg.fill_template(
        skeleton, sections, tmp_path / "report.docx", "张三", "2023001")
    assert set(filled["still_empty"]) == set()
    assert filled["still_placeholders"] == []
    body = rg.extract_body_text(tmp_path / "report.docx")
    for text in ("掌握同步", "生产者消费者", "伪代码与代码",
                 "测试通过", "理解了互斥", "2023001"):
        assert text in body
    assert "（此处填写" not in body


def test_fill_template_replaces_placeholders_and_reports_leftovers(tmp_path):
    doc = Document()
    doc.add_heading("实验一 进程同步", level=1)
    doc.add_heading("实验目的", level=2)
    doc.add_paragraph("（此处填写实验目的）")
    doc.add_heading("实验实现", level=2)
    doc.add_paragraph("TODO")
    doc.add_heading("实验结果", level=2)
    doc.add_paragraph("")
    template = tmp_path / "template.docx"
    doc.save(str(template))

    description = rg.describe_template(template)
    assert set(description["recognised"]) >= {"实验目的", "实验实现", "实验结果"}

    filled = rg.fill_template(
        template,
        {"实验目的": "掌握同步", "实验实现": "代码如下", "实验结果": "测试通过"},
        tmp_path / "filled.docx",
    )
    assert filled["filled"] == ["实验目的", "实验实现", "实验结果"]
    body = rg.extract_body_text(tmp_path / "filled.docx")
    assert "掌握同步" in body and "代码如下" in body and "测试通过" in body
    assert filled["still_placeholders"] == []


def test_fill_template_reports_unfilled_placeholder(tmp_path):
    doc = Document()
    doc.add_heading("实验一 进程同步", level=1)
    doc.add_paragraph("（此处填写实验实现）")
    template = tmp_path / "template.docx"
    doc.save(str(template))
    filled = rg.fill_template(template, {"实验目的": "掌握同步"},
                              tmp_path / "out.docx")
    # The untouched placeholder is reported so the gate can refuse it.
    assert "（此处填写实验实现）" in filled["still_placeholders"]


# --------------------------------------------------------------------------- #
# list scraping carries the ids the rest of the flow needs
# --------------------------------------------------------------------------- #
class _ExpListPage:
    def __init__(self, rows):
        self.url = "https://www.eduplus.net/course/course/c1/ExperimentPage"
        self._rows = rows

    def wait_for_selector(self, selector, timeout=None):
        return None

    def wait_for_load_state(self, *args, **kwargs):
        return None

    def evaluate(self, script, arg=None):
        return self._rows

    def inner_text(self, selector="body"):
        return ""


class _ExpListSession:
    def __init__(self, page):
        self.page = page

    def open(self, path):
        return self.page


def test_read_experiments_carries_exp_and_stu_exp_ids(monkeypatch):
    rows = [{
        "name": "实验一 进程同步",
        "flag_text": "未提交", "flag_class": "flag red",
        "material": "实验报告", "deadline": "2026-12-31 23:59",
        "exp_id": "abc123", "stu_exp_id": "def456",
        "raw": "实验一 进程同步 未提交",
    }]
    session = _ExpListSession(_ExpListPage(rows))
    operator = HomeworkOperator(session, client=None,
                                config=Config())
    monkeypatch.setattr("uplus_mcp.operator.time.sleep", lambda *_: None)
    experiments = operator.read_experiments(Course(id="c1", name="课程"))
    assert len(experiments) == 1
    exp = experiments[0]
    assert isinstance(exp, Experiment)
    assert exp.exp_id == "abc123"
    assert exp.stu_exp_id == "def456"
    assert exp.course_id == "c1"
    assert exp.submitted is False

# --------------------------------------------------------------------------- #
# template search across all sources (实验附件 → 实验手册 → 资料)
# --------------------------------------------------------------------------- #
class _DownloadSession:
    """Session that records downloads instead of hitting the network."""

    def __init__(self, tmp_path):
        self.folder = tmp_path / "experiment_materials"
        self.calls: list[str] = []
        self._tmp = tmp_path

    def download_to(self, url, dest):
        from pathlib import Path
        self.calls.append(url)
        target = Path(dest)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"PK fake docx")
        return target

    def open(self, path):
        raise AssertionError("资料 walk should not run when 附件/手册 matched")


def test_find_template_prefers_downloadable_attachment(tmp_path):
    detail = _detail(attachments=[
        Attachment(name="实验一 进程同步.zip", href="http://x/e1.zip"),
        Attachment(name="实验报告模板.docx", href="http://x/tpl.docx"),
    ])
    session = _DownloadSession(tmp_path)
    op = ExperimentOperator(Config(report_dir=str(tmp_path)), session)
    hit = op.find_report_template(detail)
    assert hit is not None
    assert hit["name"] == "实验报告模板.docx"
    assert hit["source"] == "实验附件"
    assert "http://x/tpl.docx" in session.calls


def test_find_template_falls_back_to_manual_link(tmp_path):
    # The attachment carries a name that matches nothing, so the search
    # continues to the 实验手册 links.
    detail = _detail(
        attachments=[Attachment(name="课程大纲.docx", href="http://x/outline.docx")],
        manual_links=[Attachment(name="实验报告模板.docx", href="http://x/manual.docx")],
    )
    session = _DownloadSession(tmp_path)
    op = ExperimentOperator(Config(report_dir=str(tmp_path)), session)
    hit = op.find_report_template(detail)
    assert hit is not None
    assert hit["source"] == "实验手册"
    assert "http://x/manual.docx" in session.calls
    assert "http://x/outline.docx" not in session.calls

def test_find_template_skips_attachment_without_href(tmp_path):
    detail = _detail(
        attachments=[Attachment(name="实验报告模板.docx", href="")],
        manual_links=[Attachment(name="实验报告模板.docx", href="http://x/m.docx")],
    )
    session = _DownloadSession(tmp_path)
    op = ExperimentOperator(Config(report_dir=str(tmp_path)), session)
    hit = op.find_report_template(detail)
    assert hit is not None
    assert hit["source"] == "实验手册"


def test_find_template_returns_none_when_no_match_anywhere(tmp_path):
    detail = _detail(
        attachments=[Attachment(name="课程大纲.docx", href="http://x/outline.docx")],
        manual_links=[Attachment(name="课程说明.docx", href="http://x/readme.docx")],
    )

    class _NoMaterialSession(_DownloadSession):
        def open(self, path):
            raise AssertionError("资料 walk should not run without a match")

    session = _NoMaterialSession(tmp_path)
    op = ExperimentOperator(Config(report_dir=str(tmp_path)), session)
    # No 资料 walk happens because the matcher never matched; the walk is
    # skipped only when nothing at all matched.
    from uplus_mcp import materials as materials_mod

    import unittest.mock as mock
    with mock.patch.object(materials_mod, "walk", return_value=[]):
        hit = op.find_report_template(detail)
    assert hit is None


def test_find_template_ranks_attachment_by_priority(tmp_path):
    detail = _detail(attachments=[
        Attachment(name="实验一 材料.zip", href="http://x/e1.zip"),
        Attachment(name="实验报告模板.docx", href="http://x/tpl.docx"),
        Attachment(name="报告模板.docx", href="http://x/r.docx"),
    ])
    session = _DownloadSession(tmp_path)
    op = ExperimentOperator(Config(report_dir=str(tmp_path)), session)
    hit = op.find_report_template(detail)
    assert hit is not None
    assert hit["name"] == "实验报告模板.docx"
    assert "http://x/tpl.docx" in session.calls


def test_read_detail_captures_manual_links(tmp_path):
    class _TabPage:
        def __init__(self):
            self.url = "https://www.eduplus.net/course/course/c1/ExperimentPage/x/y/labsubmit"

        def wait_for_load_state(self, *a, **k):
            return None

        def inner_text(self, selector="body"):
            return "实验一 进程同步\n提交物：实验报告\n2026-12-31 23:59"

        def evaluate(self, script, arg=None):
            # The tab-switch helper and the link collectors each have their
            # own script; the link one is the last that matches 'a.href'.
            if "exp-info-tab-btn" in script:
                return True
            if "a.href" in script:
                return [{"name": "实验报告模板.docx", "href": "http://x/tpl.docx"}]
            return []

    class _Session:
        def open(self, url):
            return _TabPage()

    op = ExperimentOperator(Config(report_dir=str(tmp_path)), _Session())
    detail = op.read_detail("c1", "e1", "s1")
    assert detail.manual_links
    assert detail.manual_links[0].name == "实验报告模板.docx"
    assert detail.manual_links[0].href == "http://x/tpl.docx"
    # A fully readable page records no tab failures.
    assert detail.tab_errors == {}


def test_read_detail_records_unreadable_tabs(tmp_path):
    """A tab that cannot be clicked or read is recorded in ``tab_errors``
    instead of being swallowed, so an empty manual_links is no longer
    ambiguous between "no manual" and "manual unreachable"."""
    class _FailingTabPage:
        def __init__(self):
            self.url = "https://www.eduplus.net/course/course/c1/ExperimentPage/x/y/labsubmit"

        def wait_for_load_state(self, *a, **k):
            return None

        def inner_text(self, selector="body"):
            return "实验一 进程同步\n提交物：实验报告\n2026-12-31 23:59"

        def evaluate(self, script, arg=None):
            if "exp-info-tab-btn" in script:
                # The 实验手册 tab is missing on this page; 实验附件 is there.
                return "实验手册" not in str(arg)
            if "a.href" in script:
                raise RuntimeError("frame detached")
            return []

    class _Session:
        def open(self, url):
            return _FailingTabPage()

    op = ExperimentOperator(Config(report_dir=str(tmp_path)), _Session())
    detail = op.read_detail("c1", "e1", "s1")

    # Both tab reads failed; the detail is still returned, but the failures are
    # visible so the caller knows the manual/attachments are missing data.
    assert set(detail.tab_errors) >= {"实验手册", "实验附件"}
    # 实验手册 is absent from the page; 实验附件 exists but its read raised.
    assert "找不到标签页" in detail.tab_errors["实验手册"]
    assert "frame detached" in detail.tab_errors["实验附件"]
    # Secrets never leak through the recorded reason.
    assert "AccessToken" not in str(detail.tab_errors)


def test_download_experiment_materials_covers_all_sources(tmp_path):
    """Read-only material download still walks 附件 and 手册 after the
    fill/submit pipeline was removed."""
    from pathlib import Path
    from unittest import mock

    from uplus_mcp import materials as materials_mod
    from uplus_mcp.experiment import Attachment as Att

    detail = _detail(
        attachments=[Att(name="附件一.pdf", href="http://x/a1.pdf")],
        manual_links=[Att(name="实验手册.docx", href="http://x/manual.docx")],
    )

    class _ReadOp:
        _experiment_label = staticmethod(
            ExperimentOperator._experiment_label)

        def read_detail(self, *args):
            return detail

        def collect_requirements(self, d):
            return ""

        def find_report_template(self, d):
            return None

    class _Session:
        def download_to(self, url, dest):
            target = Path(dest)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"PK")
            return target

    cfg = Config(report_dir=str(tmp_path))
    engine = PatrolEngine(cfg, session=_Session(), client=None, operator=None)
    engine._experiment_operator = lambda: _ReadOp()
    with mock.patch.object(materials_mod, "walk", return_value=[]):
        result = engine.download_experiment_materials("c1", "e1", "s1")
    sources = {item["source"] for item in result["downloaded"]}
    assert sources == {"实验附件", "实验手册"}
    assert {item["name"] for item in result["downloaded"]} == {"附件一.pdf", "实验手册.docx"}
