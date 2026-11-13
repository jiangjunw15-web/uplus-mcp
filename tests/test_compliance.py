"""Tests for the report compliance checklist (compliance.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from docx import Document
from docx.shared import Inches

from uplus_mcp import compliance
from uplus_mcp.compliance import BLOCK, WARN, check_report

MANUAL_WITH_SHOTS = (
    "实验目的：理解 A* 原理\n"
    "实验要求：\n"
    "1. 创建虚拟环境并部署运行程序\n"
    "2. 记录 Open 表与 Close 表的变化\n"
    "3. 关键步骤运行截图记录在实验报告中\n"
)

COVER_LINES = [
    "中北大学软件学院",
    "实验报告",
    "专业：软件工程",
    "班级：24130412",
    "学号：2413041221",
    "姓名：王政凯",
    "辅导教师：张晓波",
    "实验时间：2026年11月13日 14时至18时",
    "学时数：4",
]


def _build(tmp_path: Path, *, cover: bool = True, name_field: bool = True,
           image: bool = False, extra: list[str] | None = None,
           name: str = "报告.docx") -> Path:
    doc = Document()
    if cover:
        for line in COVER_LINES:
            doc.add_paragraph(line)
    if name_field:
        doc.add_paragraph("实验名称：实验一 搜索策略实践")
    doc.add_paragraph("实验目的：理解 A* 算法的启发式搜索原理与 Open/Close 表维护。")
    doc.add_paragraph("实验要求：按给定示例完成状态空间搜索。")
    doc.add_paragraph("实验步骤：")
    doc.add_paragraph("在 .venv 虚拟环境中执行 python a_star_demo.py，种子固定为 20261113。")
    doc.add_paragraph("实验实现：")
    doc.add_paragraph("核心循环每次从 Open 表取 f 值最小的节点，扩展 8 邻域后写回 Close 表。")
    if image:
        doc.add_picture(str(_png(tmp_path)), width=Inches(1.0))
    doc.add_paragraph("实验结果：")
    doc.add_paragraph("欧氏+8邻域扩展 13 个节点，路径代价 13.314，耗时 0.14ms。")
    doc.add_paragraph("实验总结：")
    doc.add_paragraph("启发式函数越贴近真实代价，扩展节点越少，A* 的优势越明显。")
    if extra:
        for line in extra:
            doc.add_paragraph(line)
    path = tmp_path / name
    doc.save(str(path))
    return path


def _png(tmp_path: Path) -> Path:
    """A 1×1 PNG, written without Pillow (not a runtime dependency)."""
    import base64

    path = tmp_path / "_shot.png"
    path.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    ))
    return path


def test_complete_report_passes(tmp_path):
    path = _build(tmp_path, image=True)
    report = check_report(path, manual_text=MANUAL_WITH_SHOTS)
    assert report.ok
    assert report.blocking == []


def test_missing_teacher_cover_blocks(tmp_path):
    path = _build(tmp_path, cover=False)
    report = check_report(path, manual_text=MANUAL_WITH_SHOTS)
    blocks = [i.id for i in report.blocking]
    assert not report.ok
    assert "1" in blocks


def test_missing_experiment_name_field_blocks(tmp_path):
    path = _build(tmp_path, name_field=False)
    report = check_report(path, manual_text=MANUAL_WITH_SHOTS)
    assert "3" in [i.id for i in report.blocking]


def test_manual_requires_screenshots_and_report_has_none(tmp_path):
    path = _build(tmp_path, image=False)
    report = check_report(path, manual_text=MANUAL_WITH_SHOTS)
    assert "7" in [i.id for i in report.blocking]
    assert "截图" in report.as_dict()["blocking"][0]["detail"]


def test_no_screenshot_rule_without_manual_text_is_warning(tmp_path):
    path = _build(tmp_path, image=False)
    report = check_report(path, manual_text="")
    assert report.ok  # unreadable handbook must not block
    assert any(i.id == "7b" and i.level == WARN for i in report.issues)


def test_meta_voice_warns(tmp_path):
    path = _build(tmp_path, image=True,
                  extra=["综上所述，本次实验圆满完成，报告中的方向说明即由此补充。"])
    report = check_report(path, manual_text=MANUAL_WITH_SHOTS)
    assert report.ok
    assert any(i.id == "13" for i in report.issues)


def test_duplicated_sections_warn(tmp_path):
    body = (
        "在 Open 表中取 f 值最小的节点，依次扩展上下左右与四个斜方向，"
        "把已扩展的节点写入 Close 表，直到终点进入 Close 表为止；"
        "每次扩展都更新 g 值与 h 值并按 f 值重新排序 Open 表。"
        "在 Open 表中取 f 值最小的节点，依次扩展上下左右与四个斜方向，"
        "把已扩展的节点写入 Close 表，直到终点进入 Close 表为止。"
    )
    doc = Document()
    doc.add_paragraph("实验步骤：")
    doc.add_paragraph(body)
    doc.add_paragraph("实验实现：")
    doc.add_paragraph(body)
    path = tmp_path / "dup.docx"
    doc.save(str(path))
    report = check_report(path, manual_text="")
    assert any(i.id == "14" for i in report.issues)


def test_hash_filename_warns(tmp_path):
    path = _build(tmp_path, name="experiment_085b0061a25541d3a6ce276c614d9de0.docx")
    report = check_report(path, manual_text=MANUAL_WITH_SHOTS)
    assert any(i.id == "16" for i in report.issues)


def test_environment_claim_without_screenshot_warns(tmp_path):
    path = _build(tmp_path, image=False,
                  extra=["实验步骤：在 Neo4j Browser 中执行上述 Cypher 语句。"])
    report = check_report(path, manual_text="")
    assert any(i.id == "10" for i in report.issues)


def test_unchecked_items_are_reported_not_hidden(tmp_path):
    path = _build(tmp_path, image=True)
    report = check_report(path, manual_text=MANUAL_WITH_SHOTS)
    skipped = " ".join(report.skipped)
    assert "9 数据与代码" in skipped
    assert "19 配套交付物" in skipped
    assert report.checked


def test_report_levels_are_constants(tmp_path):
    path = _build(tmp_path, cover=False)
    report = check_report(path, manual_text="")
    assert report.blocking[0].level == BLOCK
