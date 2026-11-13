"""Generate experiment report .docx files from requirements.

Two modes are supported:

* **Fill a downloaded template** — the platform usually ships a
  「实验报告模板」 in the course 资料 section. :func:`fill_template` opens that
  real .docx and writes content into its empty slots, keeping the school's own
  formatting. :func:`describe_template` first reports the structure so the
  caller knows what to write.
* **Generate from scratch** — when no template exists, :func:`generate_report`
  builds a standard document with the familiar section headings.

Both share the same section vocabulary (:data:`TEMPLATE_SECTIONS`) and the
same Chinese font pinning.
"""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

#: Chinese face used for the report body.
CN_FONT = "宋体"

#: Standard experiment report sections and the Chinese keywords that identify
#: them in a manual (experiment handbook) or in a template heading. The first
#: alias is the canonical heading text used when writing a new document.
TEMPLATE_SECTIONS: dict[str, list[str]] = {
    "实验目的": ["实验目的", "目的"],
    "实验原理": ["实验原理", "原理"],
    "实验内容": ["实验内容", "内容"],
    "实验步骤": ["实验步骤", "步骤"],
    "实验实现": ["实验实现", "实现", "实验代码", "代码实现", "算法伪代码"],
    "实验结果": ["实验结果", "实验结果分析", "结果分析", "结果", "测试结果"],
    "实验总结": ["实验总结", "总结", "小结", "实验心得", "心得"],
    "实验要求": ["实验要求", "实验报告要求", "报告要求", "要求"],
}

#: Sections the server can derive from the handbook text itself (it copies
#: the original text and never invents content). Everything else must come
#: from the client model.
MANUAL_DERIVABLE_SECTIONS = (
    "实验目的", "实验原理", "实验内容", "实验步骤", "实验要求")

#: Sections only the model can supply (思辨 / 实现 / 分析 work).
MODEL_REQUIRED_SECTIONS = ("实验实现", "实验结果", "实验总结")

#: A whole line is a placeholder when it matches one of these. ``fullmatch``
#: semantics: ordinary prose that merely contains a colon never matches.
PLACEHOLDER_PATTERNS = [
    r"[（(]\s*(?:此处|在这里|请|待)[^）)]{0,20}(?:填写|填入|补充|作答)[^）)]{0,20}[）)]",
    r"[（(]\s*(?:待填写|请填写|待补充|待写)\s*[）)]",
    r"TODO|FIXME",
    r"[Xx]{3,}",
    r"[_＿\-—=·.]{3,}",
    r"待填写|待补充|未填写|请填写|待写",
]
_PLACEHOLDER_RE = re.compile("|".join(f"(?:{p})" for p in PLACEHOLDER_PATTERNS))

_CN_NUM = "一二三四五六七八九十"


#: Map/ASCII-art rows that are legitimate report content, not blank slots. A
#: 10×10 grid drawn with dots or hashes is one long line of the same symbol, so
#: the generic underscore/dash rule must not swallow it.
_ART_CHARS = ".#*·#@+"


def is_placeholder_text(text: str) -> bool:
    """True when the whole line is only a fill-me-in placeholder.

    An empty string is a placeholder too (an empty paragraph is a slot).
    Ordinary prose such as ``实验目的：掌握进程同步`` never matches, because
    the patterns must consume the entire line.

    A row made purely of drawing symbols (``.........`` or ``########``) is
    treated as content: reports that render a grid, a path map or an ASCII
    diagram would otherwise be refused as "unfilled placeholders".
    """
    t = (text or "").strip()
    if not t:
        return True
    if len(t) >= 3 and all(ch in _ART_CHARS for ch in t):
        return False
    return bool(_PLACEHOLDER_RE.fullmatch(t))


def _clean_heading(text: str) -> str:
    """Strip markdown/decoration and leading numbering from a heading line."""
    t = (text or "").strip()
    t = re.sub(r"^[#*\-\s]+", "", t)
    t = re.sub(rf"^[（(]?[{_CN_NUM}0-9]+[）).、．:：\s]+", "", t)
    t = t.strip().rstrip("：:．.、 ")
    return t


#: Characters that may follow a heading alias on the same line. A body
#: sentence never follows the alias directly with a letter, so
#: ``要求掌握进程同步`` is *not* mistaken for the 实验要求 heading.
_HEADING_SEPARATORS = "：:、．.，,（("


def canonical_section(text: str) -> str | None:
    """Resolve a heading/label line to its canonical section name.

    Matches only when the cleaned line equals a section alias, or the alias
    is followed by a separator (``：``/``、``/a bracket). This keeps
    ``一、实验目的`` and ``实验目的：掌握…`` working while a body sentence
    such as ``要求掌握进程同步`` stays body text.
    """
    cleaned = _clean_heading(text)
    if not cleaned:
        return None
    for canonical, aliases in TEMPLATE_SECTIONS.items():
        for alias in sorted(aliases, key=len, reverse=True):
            if cleaned == alias:
                return canonical
            if cleaned.startswith(alias):
                rest = cleaned[len(alias):]
                if rest[:1] in _HEADING_SEPARATORS:
                    return canonical
    return None


def _inline_after_heading(line: str, canonical: str) -> str:
    """Text that follows the heading alias on the same line, if any."""
    cleaned = _clean_heading(line)
    for alias in sorted(TEMPLATE_SECTIONS[canonical], key=len, reverse=True):
        if cleaned == alias:
            return ""
        if cleaned.startswith(alias):
            rest = cleaned[len(alias):]
            if rest[:1] in _HEADING_SEPARATORS:
                return rest.lstrip(_HEADING_SEPARATORS + " ")
    return ""


def derive_sections(manual_text: str) -> dict[str, str]:
    """Extract sections from an experiment handbook text.

    The handbook is a free-form document whose sections are marked by
    ``实验目的：`` / ``实验内容：`` / ``实验步骤：`` / ``实验要求：`` lines (or
    the aliases in :data:`TEMPLATE_SECTIONS`). The section body runs from its
    heading to the next heading or the end of the document; inline text after
    the colon is accepted too.

    **Only the original text is copied — nothing is rewritten, paraphrased or
    invented.**

    Args:
        manual_text: The full text of the experiment handbook.

    Returns:
        ``{canonical_section: body_text}`` for every recognised section.
    """
    lines = (manual_text or "").splitlines()
    starts: list[tuple[int, str, str]] = []
    for index, raw in enumerate(lines):
        key = canonical_section(raw)
        if key:
            starts.append((index, key, raw))
    if not starts:
        return {}
    sections: dict[str, str] = {}
    for pos, (index, key, raw) in enumerate(starts):
        end = starts[pos + 1][0] if pos + 1 < len(starts) else len(lines)
        inline = _inline_after_heading(raw, key)
        block = [inline] if inline else []
        block.extend(line.strip() for line in lines[index + 1:end] if line.strip())
        body = "\n".join(block).strip()
        if body and key not in sections:
            sections[key] = body
    return sections


def extract_body_text(path: Path) -> str:
    """Return every paragraph and table cell text of a .docx, newline-joined.

    Used by the submission gate: a document that opens but carries no real
    text is still "blank", and a report must never be submitted blank.
    """
    doc = Document(str(path))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    return "\n".join(parts)


def _set_paragraph_text(paragraph: Paragraph, text: str) -> None:
    """Replace a paragraph's runs with one run carrying ``text``.

    ``Run.text``'s setter maps ``\\n`` to a Word line break, so multi-line
    section bodies keep their structure.
    """
    for run in list(paragraph.runs):
        element = run._element
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)
    paragraph.add_run(text)


def _insert_paragraph_after(paragraph: Paragraph, text: str,
                            center: bool = False) -> Paragraph:
    """Insert a new paragraph directly after ``paragraph``."""
    new_p = OxmlElement("w:p")
    paragraph._p.addnext(new_p)
    new_para = Paragraph(new_p, paragraph._parent)
    new_para.add_run(text)
    if center:
        new_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    return new_para


def _canonical_body_map(sections: dict[str, str]) -> dict[str, str]:
    """Normalize the caller's section keys and drop empty bodies."""
    out: dict[str, str] = {}
    for name, text in (sections or {}).items():
        canonical = canonical_section(name) or (name or "").strip()
        if not canonical or not (text or "").strip():
            continue
        out[canonical] = text.strip()
    return out


def describe_template(template_path: Path) -> dict:
    """Inspect an existing .docx template and return its fillable structure.

    Returns:
        ``{"headings": [...], "empty_paragraphs": [...],
        "table_cells": [...], "recognised": [...]}`` where ``recognised``
        lists the canonical sections the template already has headings for.
    """
    doc = Document(str(template_path))
    headings: list[dict] = []
    empty_paragraphs: list[dict] = []
    table_cells: list[dict] = []
    recognised: list[str] = []
    for index, paragraph in enumerate(doc.paragraphs):
        text = paragraph.text.strip()
        style = paragraph.style.name if paragraph.style else ""
        if style.startswith("Heading") and text:
            level = int(style[-1]) if style[-1].isdigit() else 2
            headings.append({"level": level, "text": text})
        if not text:
            empty_paragraphs.append({"index": index, "text": ""})
            continue
        if is_placeholder_text(text):
            empty_paragraphs.append({"index": index, "text": text})
        canonical = canonical_section(text)
        if canonical and canonical not in recognised:
            recognised.append(canonical)
    for ti, table in enumerate(doc.tables):
        for ri, row in enumerate(table.rows):
            for ci, cell in enumerate(row.cells):
                text = cell.text.strip()
                if not text or is_placeholder_text(text):
                    table_cells.append(
                        {"table": ti, "row": ri, "col": ci, "text": text})
                canonical = canonical_section(text)
                if canonical and canonical not in recognised:
                    recognised.append(canonical)
    return {
        "headings": headings,
        "empty_paragraphs": empty_paragraphs,
        "table_cells": table_cells,
        "recognised": recognised,
    }


def fill_template(
    template_path: Path,
    sections: dict[str, str],
    output_path: Path,
    student_name: str = "",
    student_id: str = "",
) -> dict:
    """Fill an existing .docx template with the given sections.

    For each section heading in the template, the first following empty or
    placeholder paragraph is filled; when the template has the heading but no
    empty slot, a new paragraph is inserted right after the heading. Table
    rows whose first cell names a section get their first empty cell filled.

    Args:
        template_path: The downloaded template .docx.
        sections: ``{section_name: body_text}`` (canonical or alias keys).
        output_path: Where to save the filled document.
        student_name: Optional name stamped on the 姓名 line.
        student_id: Optional number stamped on the 学号 line.

    Returns:
        ``{"filled": [...], "still_empty": [...], "still_placeholders": [...],
        "student_stamped": bool}``. ``still_placeholders`` non-empty means the
        caller must refuse to submit.
    """
    doc = Document(str(template_path))
    body_map = _canonical_body_map(sections)
    filled: list[str] = []
    filled_set: set[str] = set()

    paragraphs = doc.paragraphs
    for index, paragraph in enumerate(paragraphs):
        canonical = canonical_section(paragraph.text)
        if not canonical or canonical not in body_map or canonical in filled_set:
            continue
        inline = _inline_after_heading(paragraph.text, canonical)
        if inline:
            # The heading already carries body text on the same line; do not
            # overwrite work that is already there.
            filled.append(canonical)
            filled_set.add(canonical)
            continue
        slot = None
        for j in range(index + 1, len(paragraphs)):
            nxt = paragraphs[j]
            text = nxt.text.strip()
            if canonical_section(text):
                break
            if is_placeholder_text(text):
                slot = nxt
                break
        if slot is not None:
            _set_paragraph_text(slot, body_map[canonical])
        else:
            _insert_paragraph_after(paragraph, body_map[canonical])
        filled.append(canonical)
        filled_set.add(canonical)

    # Table layouts: a label cell naming a section, followed by empty cells.
    for table in doc.tables:
        for row in table.rows:
            cells = row.cells
            if len(cells) < 2:
                continue
            canonical = canonical_section(cells[0].text.strip())
            if not canonical or canonical not in body_map \
                    or canonical in filled_set:
                continue
            for cell in cells[1:]:
                text = cell.text.strip()
                if is_placeholder_text(text):
                    _clear_cell(cell)
                    cell.paragraphs[0].add_run(body_map[canonical])
                    filled.append(canonical)
                    filled_set.add(canonical)
                    break

    # Student identity line.
    stamped = False
    if student_name or student_id:
        line = "    ".join(part for part in (
            f"姓名：{student_name}" if student_name else "",
            f"学号：{student_id}" if student_id else "",
        ) if part)
        for paragraph in doc.paragraphs:
            text = paragraph.text
            if ("姓名" in text or "学号" in text) and (
                    is_placeholder_text(text)
                    or re.search(r"[_＿]{2,}", text)):
                _set_paragraph_text(paragraph, line)
                stamped = True
                break
        if not stamped:
            for paragraph in doc.paragraphs:
                text = paragraph.text.strip()
                if text and ("实验" in text or (
                        paragraph.style
                        and paragraph.style.name.startswith("Heading"))):
                    _insert_paragraph_after(paragraph, line, center=True)
                    stamped = True
                    break

    still_placeholders: list[str] = []
    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if text and is_placeholder_text(text):
            still_placeholders.append(text)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                text = cell.text.strip()
                if text and is_placeholder_text(text):
                    still_placeholders.append(text)

    still_empty = [name for name in body_map if name not in filled_set]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    return {
        "filled": filled,
        "still_empty": still_empty,
        "still_placeholders": still_placeholders,
        "student_stamped": stamped,
    }


def _clear_cell(cell) -> None:
    """Remove every paragraph in a table cell but the first, and empty it."""
    for paragraph in list(cell.paragraphs)[1:]:
        element = paragraph._p
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)
    first = cell.paragraphs[0]
    for run in list(first.runs):
        element = run._element
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)


def _apply_east_asian_font(doc: Document, font_name: str = CN_FONT) -> None:
    """Pin the East Asian font of the styles the report uses.

    ``style.font.name`` only writes ``w:ascii``/``w:hAnsi``; Word picks the
    face for Chinese glyphs from ``w:eastAsia``, which otherwise stays on the
    theme default (等线 on current Office) and ignores the requested 宋体.
    """
    for style_name in ("Normal", "Heading 1", "Heading 2", "Heading 3"):
        try:
            style = doc.styles[style_name]
        except KeyError:
            continue
        rpr = style.element.get_or_add_rPr()
        rfonts = rpr.get_or_add_rFonts()
        rfonts.set(qn("w:eastAsia"), font_name)


def generate_report(
    title: str,
    purpose: str,
    content: str,
    steps: list[str],
    requirements: list[str],
    output_path: Path,
    student_name: str = "",
    student_id: str = "",
) -> Path:
    """Generate a standard experiment report .docx from scratch.

    This is the fallback when the course ships no report template. It writes
    the same section structure the template filler understands, so a report
    generated here can also be re-opened and filled later.

    Args:
        title: Experiment title, e.g. "实验一 进程同步"
        purpose: Experiment purpose text
        content: Problem description / experiment content
        steps: List of experiment steps
        requirements: List of report requirements
        output_path: Where to save the .docx
        student_name: Student name (optional)
        student_id: Student ID (optional)

    Returns:
        Path to the generated file.
    """
    doc = Document()

    # set default font
    style = doc.styles['Normal']
    style.font.name = CN_FONT
    style.font.size = Pt(12)
    _apply_east_asian_font(doc)

    # title
    heading = doc.add_heading(title, level=1)
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # student info
    if student_name or student_id:
        info = doc.add_paragraph()
        info.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if student_name:
            info.add_run(f"姓名：{student_name}    ")
        if student_id:
            info.add_run(f"学号：{student_id}")

    # purpose
    doc.add_heading("实验目的", level=2)
    doc.add_paragraph(purpose)

    # content
    doc.add_heading("实验内容", level=2)
    doc.add_paragraph(content)

    # steps
    doc.add_heading("实验步骤", level=2)
    for i, step in enumerate(steps, 1):
        doc.add_paragraph(f"{i}. {step}")

    # implementation
    doc.add_heading("实验实现", level=2)
    doc.add_paragraph("（此处填写算法伪代码与实现代码）")
    doc.add_paragraph("")
    doc.add_paragraph("算法伪代码：")
    doc.add_paragraph("")
    doc.add_paragraph("")
    doc.add_paragraph("实现代码：")
    doc.add_paragraph("")
    doc.add_paragraph("")

    # analysis
    doc.add_heading("实验结果", level=2)
    doc.add_paragraph("（此处填写时间复杂度分析、测试用例结果）")
    doc.add_paragraph("")
    doc.add_paragraph("时间复杂度分析：")
    doc.add_paragraph("")
    doc.add_paragraph("测试用例结果：")
    doc.add_paragraph("")

    # summary
    doc.add_heading("实验总结", level=2)
    doc.add_paragraph("（此处填写实验中的关键问题和难点总结）")

    # requirements (reference)
    doc.add_heading("实验报告要求", level=2)
    for req in requirements:
        doc.add_paragraph(f"• {req}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    return output_path


# Standard template for algorithm course experiments
ALGORITHM_STEPS = [
    "根据实验内容设计算法伪代码进行算法描述",
    "利用C++/C/Java等编程语言对算法伪代码进行工程化实现",
    "输入测试用例对算法进行验证",
    "分析算法时间复杂度",
]

ALGORITHM_REQUIREMENTS = [
    "结合题目要求给出格式正确、注释清晰的算法伪代码",
    "将实现正确、运行无误的实现代码以源文件形式提交",
    "给出算法时间复杂度的完整分析和推导过程",
    "总结实验中的关键问题和难点",
]


def generate_algorithm_report(
    title: str,
    purpose: str,
    content: str,
    output_path: Path,
    student_name: str = "",
    student_id: str = "",
) -> Path:
    """Convenience wrapper for algorithm course experiments."""
    return generate_report(
        title=title,
        purpose=purpose,
        content=content,
        steps=ALGORITHM_STEPS,
        requirements=ALGORITHM_REQUIREMENTS,
        output_path=output_path,
        student_name=student_name,
        student_id=student_id,
    )