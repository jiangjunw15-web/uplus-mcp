"""Pre-submission compliance checks for experiment reports.

The inspection gate in :meth:`uplus_mcp.experiment.ExperimentOperator.verify_report`
只回答"文档里有没有内容"；它不回答"这份报告交上去老师会不会挑毛病"。这个模块
补上后者：一套编号固定的核查项，覆盖实验报告最常见的失效方式，在提交前把问题
拦下来。

分三级：

* :data:`BLOCK` — 硬伤，拒绝提交；
* :data:`WARN` — 会被扣分/被追问，但不拦提交；
* :data:`INFO` — 无法自动判定，需要人工确认（如实标注"未检查"，不假装通过）。

每项都带编号，提交结果里可以逐条核对。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from docx import Document

from .report_generator import TEMPLATE_SECTIONS, canonical_section

BLOCK = "block"
WARN = "warn"
INFO = "info"

#: 教师报告模板封面的必备字段及其在文档里的写法别名。
COVER_FIELDS: dict[str, tuple[str, ...]] = {
    "学校/学院": ("大学", "学院", "学校"),
    "实验报告标题": ("实验报告",),
    "专业": ("专业",),
    "班级": ("班级",),
    "学号": ("学号",),
    "姓名": ("姓名",),
    "辅导教师": ("辅导教师", "指导教师", "任课教师", "授课教师"),
    "实验时间": ("实验时间", "实验日期", "实习时间"),
    "学时数": ("学时",),
}

#: 封面必须具备的字段（"实验报告标题" 由 :func:`_check_cover` 单独判定）。
COVER_REQUIRED = (
    "学校/学院", "专业", "班级", "学号", "姓名",
    "辅导教师", "实验时间", "学时数",
)

#: 报告里出现这些，说明它不是在跟老师说话，而是在跟文档/自己说话。
META_PHRASES = (
    "本次实验的局限", "本报告的局限", "报告中的", "本文档",
    "综上所述", "综上", "总而言之", "值得注意的是",
    "通过本次实验，我", "本次实验让我", "圆满完成",
)

#: 说明"结果可被复核"的证据词，至少要出现两类。
REPRO_WORDS = (
    ("python", "pip", "命令", "cmd", "命令行"),
    ("种子", "seed", "随机种子"),
    ("路径", "脚本", "目录", "文件"),
    ("参数", "阈值", "权重"),
)

_HASH_STEM = re.compile(r"^experiment_[0-9a-f]{32}$", re.IGNORECASE)
_NUMBERED_ITEM = re.compile(r"^[\s>*\-]*([0-9]+)[.、)．]\s*(.+)$")
_BLANK_LABEL = re.compile(r"^(实验名称|实验时间|学时数?|专业|班级|学号|姓名|辅导教师)\s*[:：]?\s*$")


@dataclass
class Issue:
    """One checklist finding."""

    id: str
    level: str
    title: str
    detail: str
    hint: str = ""

    def as_dict(self) -> dict:
        out = {"id": self.id, "level": self.level,
               "title": self.title, "detail": self.detail}
        if self.hint:
            out["hint"] = self.hint
        return out


@dataclass
class ComplianceReport:
    """Aggregated checklist result."""

    issues: list[Issue]
    checked: list[str]
    skipped: list[str]
    stats: dict

    @property
    def blocking(self) -> list[Issue]:
        return [i for i in self.issues if i.level == BLOCK]

    @property
    def ok(self) -> bool:
        return not self.blocking

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "blocking": [i.as_dict() for i in self.blocking],
            "warnings": [i.as_dict() for i in self.issues if i.level == WARN],
            "manual": [i.as_dict() for i in self.issues if i.level == INFO],
            "checked": self.checked,
            "skipped": self.skipped,
            "stats": self.stats,
        }


def _block(issue_id: str, title: str, detail: str, hint: str = "") -> Issue:
    return Issue(issue_id, BLOCK, title, detail, hint)


def _warn(issue_id: str, title: str, detail: str, hint: str = "") -> Issue:
    return Issue(issue_id, WARN, title, detail, hint)


# --------------------------------------------------------------------------- #
# document helpers
# --------------------------------------------------------------------------- #
def _head_and_body(path: Path) -> tuple[list[str], list[str]]:
    """Return ``(head, body)``.

    ``head`` is the cover region — the first paragraphs plus every cell of the
    first tables; ``body`` is the non-empty paragraph text used for content
    checks.
    """
    doc = Document(str(path))
    paragraphs = [p.text.strip() for p in doc.paragraphs]
    body = [t for t in paragraphs if t]
    head = body[:25]
    for table in doc.tables[:3]:
        for row in table.rows:
            for cell in row.cells:
                text = cell.text.strip()
                if text:
                    head.append(text)
    return head, body


def _image_count(path: Path) -> int:
    """Number of embedded images (screenshots are evidence of a real run)."""
    doc = Document(str(path))
    return len(doc.inline_shapes)


def _section_bodies(text: str) -> dict[str, str]:
    """Split body text into canonical sections, like the filler does."""
    bodies: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        key = canonical_section(line) if line.strip() else None
        if key:
            current = key
            bodies.setdefault(current, [])
            continue
        if current:
            bodies[current].append(line)
    return {k: "\n".join(v).strip() for k, v in bodies.items()}


def _jaccard(a: str, b: str) -> float:
    ta = set(re.findall(r"[0-9A-Za-z\u4e00-\u9fff]+", a or ""))
    tb = set(re.findall(r"[0-9A-Za-z\u4e00-\u9fff]+", b or ""))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _manual_items(manual_text: str) -> list[str]:
    """Numbered requirement lines (``1. xxx``) from the handbook."""
    items: list[str] = []
    for line in (manual_text or "").splitlines():
        m = _NUMBERED_ITEM.match(line.strip())
        if not m:
            continue
        body = m.group(2).strip()
        if 6 <= len(body) <= 80:
            items.append(body)
    return items


# --------------------------------------------------------------------------- #
# checks — 编号与问题清单一致
# --------------------------------------------------------------------------- #
def _check_cover(head: list[str], stats: dict) -> list[Issue]:
    """1/2/3: 教师模板封面、实验时间与学时、实验名称字段."""
    issues: list[Issue] = []
    head_text = "\n".join(head)

    missing = [name for name in COVER_REQUIRED
               if not any(alias in head_text for alias in COVER_FIELDS[name])]
    stats["cover_missing"] = missing
    if len(missing) >= 3:
        issues.append(_block(
            "1", "未使用教师报告模板",
            "封面缺 " + "、".join(missing) + f"（必备 {len(COVER_REQUIRED)} 项）",
            "把教师模板（含学校/专业/班级/学号/姓名/辅导教师/时间/学时）当 template_path 传入",
        ))
    elif missing:
        issues.append(_warn("1", "封面字段不全", "缺 " + "、".join(missing),
                            "补齐封面后再提交"))

    if "实验报告" not in head_text:
        issues.append(_warn("1b", "缺「实验报告」标题", "封面未出现「实验报告」字样"))

    name_label = any(x in head_text for x in ("实验名称", "实验项目"))
    if not name_label:
        issues.append(_block(
            "3", "缺「实验名称」字段",
            "教师模板要求独立填写实验名称，报告未出现该字段",
            "填进模板的「1.实验名称」槽位，写平台上的实验全名",
        ))
    else:
        for line in head:
            if _BLANK_LABEL.match(line):
                issues.append(_warn("2c", "字段值为空", f"「{line}」后面没有内容"))
                break

    if not any(x in head_text for x in COVER_FIELDS["实验时间"]):
        issues.append(_warn("2", "缺实验时间", "未找到实验时间/日期栏"))
    if "学时" not in head_text:
        issues.append(_warn("2b", "缺学时数", "未找到「学时」栏"))
    return issues


def _check_sections(text: str, stats: dict) -> list[Issue]:
    """4: 章节与教师模板一致（顺序、无自加章节）."""
    issues: list[Issue] = []
    order = list(TEMPLATE_SECTIONS)
    found: list[str] = []
    for line in text.splitlines():
        key = canonical_section(line) if line.strip() else None
        if key and key not in found:
            found.append(key)
    stats["sections"] = found

    if "实验内容" in found:
        issues.append(_warn("4", "多出「实验内容」章节",
                            "教师模板只有 实验名称/目的/要求/步骤，没有这一章",
                            "并入「实验要求」或删除"))
    positions = [order.index(s) for s in found if s in order]
    if positions != sorted(positions):
        issues.append(_warn("4b", "章节顺序与模板不一致",
                            "出现逆序：" + "、".join(found)))
    return issues


def _check_screenshots(images: int, manual_text: str,
                       requirements: str, stats: dict) -> list[Issue]:
    """7: 关键步骤运行截图."""
    stats["images"] = images
    needs = "截图" in (manual_text or "") or "截图" in (requirements or "")
    if images > 0:
        return []
    if needs:
        return [_block("7", "缺关键步骤运行截图",
                       "手册明确要求截图，报告里却没有任何图片",
                       "把运行过程截图插入 docx 再提交")]
    return [_warn("7b", "报告无图片",
                  "未检测到图像；手册若要求截图，请在提交前补上")]


def _check_manual_coverage(text: str, manual_text: str, stats: dict) -> list[Issue]:
    """5: 手册「实验要求」条目是否逐条有对应内容."""
    items = _manual_items(manual_text)
    stats["manual_items"] = len(items)
    if not items:
        return []
    covered, missing = 0, []
    for item in items:
        fragments = [f for f in re.split(r"[，。；、（）()：:]", item) if len(f) >= 4]
        if any(f in text for f in fragments):
            covered += 1
        else:
            missing.append(item[:24])
    ratio = covered / len(items)
    stats["manual_covered"] = f"{covered}/{len(items)}"
    if ratio == 0 and len(items) >= 3 and len(manual_text) > 300:
        return [_block("5", "手册要求一条都没对上",
                       f"手册列了 {len(items)} 条要求，报告找不到任何对应内容",
                       "逐条按手册要求补写")]
    if ratio < 0.6:
        return [_warn("5", "手册要求覆盖不足",
                      f"覆盖 {covered}/{len(items)} 条；未对上：" + "、".join(missing[:3]),
                      "逐条补齐手册要求")]
    return []


def _check_reproducibility(text: str, stats: dict) -> list[Issue]:
    """11: 可复核性（脚本、命令、种子、参数）."""
    hits = [group[0] for group in REPRO_WORDS if any(w in text for w in group)]
    stats["repro_groups"] = hits
    if len(hits) < 2:
        return [_warn("11", "结果不可复核",
                      f"只出现 {len(hits)} 类可复核信息（命令/种子/路径/参数）",
                      "补上脚本路径、运行命令、随机种子与关键参数")]
    return []


def _check_meta_voice(text: str) -> list[Issue]:
    """13: AI 味 / 元叙述."""
    found: list[str] = []
    for phrase in META_PHRASES:
        idx = text.find(phrase)
        if idx >= 0:
            snippet = text[max(0, idx - 12):idx + len(phrase) + 12].replace("\n", " ")
            found.append(f"「{snippet.strip()}」")
    if not found:
        return []
    return [_warn("13", "出现元叙述/AI 味表述",
                  f"共 {len(found)} 处：" + "；".join(found[:3]),
                  "删掉对报告本身说话、自我评价的句子，只写实验事实")]


def _check_repetition(text: str, stats: dict) -> list[Issue]:
    """14: 章节重复注水."""
    bodies = _section_bodies(text)
    pairs: list[tuple[str, str, float]] = []
    keys = [k for k in ("实验步骤", "实验实现", "实验内容", "实验原理") if k in bodies]
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = bodies[keys[i]], bodies[keys[j]]
            if len(a) > 100 and len(b) > 100:
                score = _jaccard(a, b)
                if score >= 0.6:
                    pairs.append((keys[i], keys[j], round(score, 2)))
    stats["repetition"] = pairs
    if not pairs:
        return []
    return [_warn("14", "章节内容重复",
                  "、".join(f"{a}↔{b}（{s}）" for a, b, s in pairs[:2]),
                  "步骤写流程、实现写代码与决策，不要互相复述")]


def _check_layout(path: Path, text: str, stats: dict) -> list[Issue]:
    """15: 排版健康度（表格列数不一致、超长行）."""
    issues: list[Issue] = []
    doc = Document(str(path))
    ragged = sum(1 for table in doc.tables
                 if len({len(row.cells) for row in table.rows}) > 1)
    long_lines = [ln for ln in text.splitlines() if len(ln) > 180]
    stats["ragged_tables"] = ragged
    stats["long_lines"] = len(long_lines)
    if ragged:
        issues.append(_warn("15", "表格列数不一致",
                            f"{ragged} 张表各行单元格数不同，渲染会错位"))
    if long_lines:
        issues.append(_warn("15b", "超长行",
                            f"{len(long_lines)} 行超过 180 字符，会溢出页边"))
    return issues


def _check_filename(path: Path) -> list[Issue]:
    """16: 提交文件名规范."""
    if _HASH_STEM.match(path.stem):
        return [_warn("16", "提交文件名不规范",
                      f"{path.name} 是哈希命名，老师按文件名归档认不出作者",
                      "另存为「学号-姓名-实验名.docx」再提交")]
    return []


def _check_environment(text: str, images: int) -> list[Issue]:
    """10: 环境声明无证据（说了搭了 Neo4j/虚拟环境，却没有界面截图）."""
    claims = [w for w in ("Neo4j", "neo4j", "Docker", "虚拟环境") if w in text]
    if claims and images == 0:
        return [_warn("10", "环境声明无证据",
                      "文中提到 " + "、".join(claims) + "，但没有任何界面截图",
                      "补环境截图，或改成如实的等价验证说明")]
    return []


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def check_report(path: Path, *, manual_text: str = "",
                 requirements: str = "") -> ComplianceReport:
    """Run the full checklist against ``path``.

    Args:
        path: the generated ``.docx`` report.
        manual_text: the handbook text, when it could be read. Empty means the
            handbook was unreadable — requirement coverage (item 5) and the
            screenshot rule (item 7) then stay at warning level instead of
            silently passing.
        requirements: the merged requirement block from the experiment page.

    Returns:
        A :class:`ComplianceReport`; ``ok`` is False when any blocking item
        fired.
    """
    head, body = _head_and_body(path)
    text = "\n".join(body)
    images = _image_count(path)
    stats: dict = {}
    issues: list[Issue] = []
    checked: list[str] = []

    issues += _check_cover(head, stats)
    checked.append("1/2/3 模板与封面字段")
    issues += _check_sections(text, stats)
    checked.append("4 章节结构")
    issues += _check_screenshots(images, manual_text, requirements, stats)
    checked.append("7 运行截图")
    issues += _check_manual_coverage(text, manual_text, stats)
    checked.append("5 手册要求覆盖")
    issues += _check_reproducibility(text, stats)
    checked.append("11 可复核")
    issues += _check_meta_voice(text)
    checked.append("13 AI 味/元叙述")
    issues += _check_repetition(text, stats)
    checked.append("14 章节重复")
    issues += _check_layout(path, text, stats)
    checked.append("15 排版")
    issues += _check_filename(path)
    checked.append("16 文件命名")
    issues += _check_environment(text, images)
    checked.append("10 环境证据")

    # 无法自动判定的项必须如实列出，不能假装通过。
    skipped = [
        "6 是否使用手册指定数据/示例",
        "8 手册要求的图示（状态空间图/流程图）",
        "9 数据与代码是否自洽",
        "12 分析是否贴合实测数据",
        "17 提交是否真实生效（由 submit 流程回读页面校验）",
        "19 配套交付物（视频/PPT/源码）",
    ]
    return ComplianceReport(issues=issues, checked=checked,
                            skipped=skipped, stats=stats)


__all__ = ["BLOCK", "WARN", "INFO", "Issue", "ComplianceReport", "check_report"]
