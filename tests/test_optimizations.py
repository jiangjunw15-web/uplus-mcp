"""Regression tests for the 0.8.2 fixes.

Covers: placeholder false positives on ASCII-art map rows, section bodies
truncated by an alias sub-heading, navigation timeout retry, and the 密码登录
tab switch that a phone-number account needs.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import TimeoutError as PWTimeout

from uplus_mcp import report_generator as rg
from uplus_mcp.browser import BrowserSession, UplusError
from uplus_mcp.config import Config
from uplus_mcp.experiment import ExperimentOperator, _missing_sections


# --------------------------------------------------------------------------- #
# placeholder detection vs drawing rows
# --------------------------------------------------------------------------- #
def test_ascii_art_row_is_not_a_placeholder():
    # A 10×10 grid rendered with dots is content, not an empty slot.
    assert rg.is_placeholder_text("..........") is False
    assert rg.is_placeholder_text("##########") is False
    assert rg.is_placeholder_text("...........X.......") is False  # mixed art char


def test_fill_in_blank_rows_stay_placeholders():
    assert rg.is_placeholder_text("________") is True
    assert rg.is_placeholder_text("---------") is True
    assert rg.is_placeholder_text("（此处填写实验实现）") is True
    assert rg.is_placeholder_text("待填写") is True
    assert rg.is_placeholder_text("TODO") is True


def test_prose_is_never_a_placeholder():
    assert rg.is_placeholder_text("实验目的：掌握进程同步") is False


# --------------------------------------------------------------------------- #
# an alias sub-heading must not end the section body early
# --------------------------------------------------------------------------- #
def test_alias_subheading_does_not_empty_the_section():
    body = "\n".join([
        "实验实现",
        "一、算法伪代码",
        "WHILE Open 非空：取 f 最小的节点扩展，写回 Close 表。",
        "实验结果",
        "扩展 13 个节点，路径代价 13.314，耗时 0.14ms。",
    ])
    missing = _missing_sections(body, ["实验实现", "实验结果"])
    assert missing == []


def test_repeated_heading_is_absorbed_not_treated_as_boundary():
    body = "\n".join([
        "实验实现",
        "实验实现",
        "WHILE Open 非空：取 f 最小的节点扩展。",
        "实验结果",
        "扩展 13 个节点。",
    ])
    # The duplicate heading is absorbed into the first section, so its body is
    # not cut off at the repeat and the section is not reported as empty.
    assert _missing_sections(body, ["实验实现", "实验结果"]) == []


# --------------------------------------------------------------------------- #
# navigation timeout retry
# --------------------------------------------------------------------------- #
class _Page:
    def __init__(self, url: str = "https://www.eduplus.net/x"):
        self.url = url

    def wait_for_load_state(self, *args, **kwargs):
        return None


class _FlakySession:
    """Fails the first navigation, succeeds afterwards."""

    def __init__(self, fail_times: int = 1):
        self.fail_times = fail_times
        self.calls = 0

    def open(self, url, timeout_ms=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise PWTimeout("Timeout 45000ms exceeded.")
        return _Page()


def test_open_detail_retries_once_on_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr("uplus_mcp.experiment.time.sleep", lambda *_: None)
    session = _FlakySession(fail_times=1)
    op = ExperimentOperator(Config(report_dir=str(tmp_path)), session)
    page = op.open_detail("c1", "e1", "s1")
    assert isinstance(page, _Page)
    assert session.calls == 2


def test_open_detail_gives_up_after_retry(tmp_path, monkeypatch):
    monkeypatch.setattr("uplus_mcp.experiment.time.sleep", lambda *_: None)
    session = _FlakySession(fail_times=9)
    op = ExperimentOperator(Config(report_dir=str(tmp_path)), session)
    with pytest.raises(UplusError) as err:
        op.open_detail("c1", "e1", "s1")
    assert "超时" in str(err.value)


def test_open_detail_still_works_with_a_session_stub(tmp_path):
    """A session whose open() takes only the URL must keep working."""
    class _Stub:
        def open(self, url):
            return _Page()

    op = ExperimentOperator(Config(report_dir=str(tmp_path)), _Stub())
    assert isinstance(op.open_detail("c1", "e1", "s1"), _Page)


# --------------------------------------------------------------------------- #
# 密码登录 tab
# --------------------------------------------------------------------------- #
class _Item:
    def __init__(self, label: str, visible: bool = True):
        self.label = label
        self._visible = visible
        self.clicked = False

    def is_visible(self):
        return self._visible

    def click(self, timeout=None):
        self.clicked = True


class _LocatorList:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]

    @property
    def first(self):
        return self.items[0]


class _MappingPage:
    """locator(selector) returns items whose key appears in the selector."""

    def __init__(self, mapping):
        self.mapping = mapping

    def locator(self, selector):
        for key, items in self.mapping.items():
            if key in selector:
                return _LocatorList(items)
        return _LocatorList([])


def _session():
    return BrowserSession.__new__(BrowserSession)


def test_password_tab_is_clicked_when_form_starts_on_student_number():
    tab = _Item("密码登录")
    page = _MappingPage({"密码登录": [tab]})
    session = _session()
    assert session._on_password_tab(page) is False
    assert session._switch_to_password_tab(page) is True
    assert tab.clicked is True


def test_password_tab_is_not_clicked_when_already_there():
    account = _Item("请输入账号/手机号")
    page = _MappingPage({"手机": [account], "密码登录": [_Item("密码登录")]})
    session = _session()
    assert session._on_password_tab(page) is True
    assert session._switch_to_password_tab(page) is True
    assert page.mapping["密码登录"][0].clicked is False


def test_no_password_tab_falls_back_to_old_behaviour():
    page = _MappingPage({})
    session = _session()
    assert session._switch_to_password_tab(page) is False


# --------------------------------------------------------------------------- #
# the 提交 click must wait for the uploader to finish converting
# --------------------------------------------------------------------------- #
class _TextPage:
    """A page whose body text follows a scripted sequence."""

    def __init__(self, texts):
        self.texts = list(texts)
        self.reads = 0

    def inner_text(self, selector):
        index = min(self.reads, len(self.texts) - 1)
        self.reads += 1
        return self.texts[index]


def test_wait_converted_skips_the_converting_window(tmp_path, monkeypatch):
    monkeypatch.setattr("uplus_mcp.experiment.time.sleep", lambda *_: None)
    page = _TextPage(["格式转换中...", "格式转换中...", "转换成功"])
    op = ExperimentOperator(Config(report_dir=str(tmp_path)), _FlakySession(0))
    assert op._wait_converted(page, timeout=60) is True
    assert page.reads == 3


def test_wait_converted_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr("uplus_mcp.experiment.time.sleep", lambda *_: None)
    page = _TextPage(["格式转换中..."])
    op = ExperimentOperator(Config(report_dir=str(tmp_path)), _FlakySession(0))
    assert op._wait_converted(page, timeout=0.01) is False


def test_section_alias_is_recognised_for_summary():
    # The teacher's template says 「5. 实验结论及心得」; it must resolve to 实验总结.
    assert rg.canonical_section("实验结论及心得") == "实验总结"
    assert rg.canonical_section("5. 实验结论及心得") == "实验总结"


def test_experiment_name_with_course_prefix():
    from uplus_mcp.experiment import extract_experiment_name

    text = "理论\n2026-2027-1-人工智能基础\n< 返回\n人工智能基础-实验3-知识提取实践\n未提交"
    assert extract_experiment_name(text) == "人工智能基础-实验3-知识提取实践"


# --------------------------------------------------------------------------- #
# the generated report must be fillable, covered and free of empty headings
# --------------------------------------------------------------------------- #
COVER = {
    "school": "中北大学软件学院",
    "major": "软件工程",
    "class_name": "24130412",
    "student_name": "王政凯",
    "student_id": "2413041221",
    "teacher": "张晓波",
    "date": "2026年11月13日",
    "hours": "4",
}


def test_generated_skeleton_carries_the_teacher_cover(tmp_path):
    out = tmp_path / "skeleton.docx"
    rg.generate_report("实验三 知识提取", "", "", [], [], out, cover=COVER)
    body = rg.extract_body_text(out)
    for label in ("中北大学软件学院", "实验报告", "实验名称：", "专业：软件工程",
                  "班级：24130412", "学号：2413041221", "姓名：王政凯",
                  "辅导教师：张晓波", "实验时间：2026年11月13日", "学时数：4"):
        assert label in body


def test_blank_cover_field_still_renders_its_label(tmp_path):
    out = tmp_path / "skeleton.docx"
    rg.generate_report("实验三", "", "", [], [], out,
                       cover={**COVER, "hours": "", "teacher": ""})
    body = rg.extract_body_text(out)
    # The label must exist (a missing field blocks); the blank value only warns.
    assert "学时数：" in body and "辅导教师：" in body


def test_skeleton_has_no_empty_scaffold_headings(tmp_path):
    out = tmp_path / "skeleton.docx"
    rg.generate_report("实验三", "", "", [], [], out, cover=COVER)
    body = rg.extract_body_text(out)
    # These were carried over from the old skeleton: they reproduced the
    # handbook's wording and stayed empty in every generated report.
    for stale in ("算法伪代码：", "实现代码：", "时间复杂度分析：",
                  "测试用例结果：", "（此处填写"):
        assert stale not in body


def test_every_section_body_lands_and_no_heading_is_left_empty(tmp_path):
    skeleton = tmp_path / "skeleton.docx"
    rg.generate_report("实验三", "", "", [], [], skeleton, cover=COVER)
    filled = rg.fill_template(
        skeleton,
        {"实验目的": "掌握实体识别", "实验内容": "12 句语料", "实验步骤": "1) 建语料",
         "实验实现": "jieba.posseg 取 nr/nt/ns", "实验结果": "41 实体、27 三元组",
         "实验总结": "规则法可解释但泛化差"},
        tmp_path / "report.docx", "王政凯", "2413041221")
    assert filled["still_empty"] == []
    assert filled["still_placeholders"] == []
    body = rg.extract_body_text(tmp_path / "report.docx")
    assert "jieba.posseg 取 nr/nt/ns" in body
    # 实验要求 was never supplied, so its empty heading must be pruned away.
    assert "实验要求" in filled["pruned_sections"]
    assert "实验要求" not in body


def test_empty_section_heading_is_pruned(tmp_path):
    skeleton = tmp_path / "skeleton.docx"
    rg.generate_report("实验三", "", "", [], [], skeleton, cover=COVER)
    filled = rg.fill_template(
        skeleton, {"实验目的": "目的正文"}, tmp_path / "r.docx", "", "")
    body = rg.extract_body_text(tmp_path / "r.docx")
    # Only 实验目的 got content; every other skeleton heading is gone.
    for name in ("实验内容", "实验步骤", "实验实现", "实验结果",
                 "实验总结", "实验要求"):
        assert name not in body
    assert "实验目的" in body


def test_long_body_is_split_at_sentence_boundaries():
    text = "。".join(["这是一句用来测试长段落拆分行为的描述"] * 12) + "。"
    chunks = rg._split_long_text(text, limit=180)
    assert len(chunks) > 1
    assert all(len(c) <= 180 for c in chunks)
    # Nothing is lost in the split.
    assert sum(len(c) for c in chunks) >= len(text) - 2


def test_tiny_body_is_kept_whole():
    assert rg._split_long_text("短句。", limit=180) == ["短句。"]


# --------------------------------------------------------------------------- #
# compliance: unreadable handbook must not read as "all requirements covered"
# --------------------------------------------------------------------------- #
def _minimal_report(tmp_path, cover=True):
    from docx import Document

    doc = Document()
    if cover:
        for line in rg._cover_lines(COVER, "实验三 知识提取"):
            doc.add_paragraph(line)
    doc.add_heading("实验三 知识提取", level=1)
    body = (
        "本次用 jieba.posseg 对 12 句语料做实体识别，脚本路径为 "
        "scripts/ner.py，运行命令 python scripts/ner.py，随机种子 seed=2026，"
        "参数 window=1。共识别 41 个实体、抽取 27 条三元组，实体准确率 0.89，"
        "关系抽取准确率 0.82，仍在同一批语料上做人工核对。"
    )
    for name in ("实验目的", "实验内容", "实验步骤", "实验实现",
                 "实验结果", "实验总结"):
        doc.add_heading(name, level=2)
        doc.add_paragraph(body)
    path = tmp_path / "实验三-知识提取实践.docx"
    doc.save(str(path))
    return path


def test_unreadable_handbook_warns_instead_of_silently_passing(tmp_path):
    from uplus_mcp.compliance import check_report

    path = _minimal_report(tmp_path)
    report = check_report(path, manual_text="", requirements="")
    ids = [i.id for i in report.issues]
    assert "5" in ids
    issue = next(i for i in report.issues if i.id == "5")
    assert issue.level == "warn"
    assert "未核对" in issue.title


def test_experiment_content_section_is_not_a_false_alarm(tmp_path):
    from uplus_mcp.compliance import check_report

    path = _minimal_report(tmp_path)
    report = check_report(path, manual_text="", requirements="")
    titles = [i.title for i in report.issues]
    assert "多出「实验内容」章节" not in titles
    assert "出现模板外章节" not in titles

