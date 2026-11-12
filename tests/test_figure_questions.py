"""Figure (image) question support.

A stem like 「以下模型是非对称密码体制中的两种模型」 carries the decisive
information in a picture. ``strip_html`` drops the <img>, so the text-only
brief left the model guessing. These tests cover the two halves of the fix:
detecting the image in the payload, and screenshotting the rendered question
block so the model can actually look at it.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from uplus_mcp.browser import UplusError
from uplus_mcp.config import Config
from uplus_mcp.engine import PatrolEngine
from uplus_mcp.models import HomeworkTask, Option, Question, QuestionType
from uplus_mcp.operator import HomeworkOperator
from uplus_mcp.solver import build_answer_brief
from uplus_mcp import utils


# --------------------------------------------------------------------------- #
# detection
# --------------------------------------------------------------------------- #
def test_extract_images_reads_src_and_data_src():
    html = '<p>见图</p><img src="/a/b.png"><img data-src="https://x/c.jpg">'
    assert utils.extract_images(html) == ["/a/b.png", "https://x/c.jpg"]


def test_extract_images_marks_inline_data_without_copying_it():
    html = '<img src="data:image/png;base64,' + "A" * 5000 + '">'
    assert utils.extract_images(html) == ["(inline image data)"]


def test_extract_images_dedupes_and_ignores_plain_text():
    assert utils.extract_images("<p>没有图</p>") == []
    assert utils.extract_images(None) == []
    html = '<img src="x.png"><img src="x.png">'
    assert utils.extract_images(html) == ["x.png"]


def test_question_detail_collects_stem_and_option_figures():
    raw = {
        "id": "q5", "orderNumber": 5, "qsnType": 3,
        "titleText": '<p>以下模型是非对称密码体制中的两种模型</p><img src="/stem.png">',
        "options": [
            {"id": "A", "optionContent": '<p>甲</p><img src="/opt.png">'},
            {"id": "B", "optionContent": "<p>乙</p>"},
        ],
        "userAnswer": "",
    }
    q = Question.from_detail(raw)
    assert q.images == ["/stem.png", "/opt.png"]
    assert q.stem == "以下模型是非对称密码体制中的两种模型"


def test_llm_prompt_flags_a_figure_question_and_names_the_tool():
    q = Question(
        question_id="q5", order=5, type=QuestionType.JUDGE,
        stem="以下模型是非对称密码体制中的两种模型", images=["/stem.png"],
    )
    brief = q.llm_prompt()
    assert brief["images"] == ["/stem.png"]
    assert "uplus_capture_question_image" in brief["figure_note"]
    assert "order=5" in brief["figure_note"]


def test_text_only_question_has_no_figure_keys():
    q = Question(question_id="q1", order=1, type=QuestionType.SINGLE,
                 stem="纯文字题干", options=[Option(code="A", content="甲")])
    brief = q.llm_prompt()
    assert "images" not in brief
    assert "figure_note" not in brief


def test_answer_brief_tells_the_model_to_look_before_answering():
    brief = build_answer_brief("作业", [])
    assert "uplus_capture_question_image" in brief["instructions"]


# --------------------------------------------------------------------------- #
# question screenshots
# --------------------------------------------------------------------------- #
class _ShotLocator:
    def __init__(self, page, selector):
        self._page = page
        self._selector = selector

    @property
    def first(self):
        return self

    def locator(self, selector):
        return _ShotLocator(self._page, selector)

    def count(self):
        if self._selector == "[data-uplus-q='1']":
            return 1 if self._page.narrowable else 0
        return 0

    def scroll_into_view_if_needed(self, timeout=None):
        return None

    def screenshot(self, path):
        self._page.shots.append(("element", self._selector, path))
        Path(path).write_bytes(b"\x89PNG-fake")


class _ShotPage:
    """List-view page fake: the stem narrows to one question container."""

    def __init__(self, narrowable=True):
        self.narrowable = narrowable
        self.shots = []

    def evaluate(self, script, arg=None):
        return self.narrowable

    def locator(self, selector):
        return _ShotLocator(self, selector)

    def inner_text(self, selector="body"):
        return "题目正文 列表视图"  # no 答题卡 / 上一题 / 下一题 markers

    def screenshot(self, path, full_page=False):
        self.shots.append(("page", full_page, path))
        Path(path).write_bytes(b"\x89PNG-fake")


def _operator(tmp_path):
    return HomeworkOperator(Mock(), Mock(), Config(report_dir=str(tmp_path)))


def _question() -> Question:
    return Question(
        question_id="q5", order=5, type=QuestionType.JUDGE,
        stem="以下模型是非对称密码体制中的两种模型", images=["/stem.png"],
    )


def test_capture_question_screenshots_the_question_container(tmp_path):
    page = _ShotPage(narrowable=True)
    path = _operator(tmp_path).capture_question(page, _question(), total=10)
    assert path.is_file()
    assert path.parent.name == "question_images"
    assert len(page.shots) == 1
    kind, selector, _ = page.shots[0]
    assert kind == "element"
    assert selector == "[data-uplus-q='1']"


def test_capture_question_falls_back_to_the_full_page(tmp_path):
    page = _ShotPage(narrowable=False)
    path = _operator(tmp_path).capture_question(page, _question(), total=10)
    assert path.is_file()
    assert page.shots == [("page", True, str(path))]


def test_capture_question_can_capture_the_full_page_on_request(tmp_path):
    page = _ShotPage(narrowable=True)
    _operator(tmp_path).capture_question(
        page, _question(), total=10, full_page=True)
    assert page.shots[0][0] == "page"


def test_capture_question_honours_an_explicit_output_path(tmp_path):
    page = _ShotPage()
    target = tmp_path / "images" / "figure.png"
    path = _operator(tmp_path).capture_question(
        page, _question(), total=10, output_path=str(target))
    assert path == target.resolve()
    assert path.is_file()


def test_capture_question_refuses_a_path_outside_the_io_roots(tmp_path):
    # An explicit allow-list replaces the default (report folder + temp), so
    # the sibling directory is genuinely outside every allowed root.
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    operator = HomeworkOperator(
        Mock(), Mock(), Config(allowed_io_paths=[str(allowed)]))
    outside = tmp_path / "not-allowed" / "figure.png"
    with pytest.raises(PermissionError):
        operator.capture_question(
            _ShotPage(), _question(), total=10, output_path=str(outside))


# --------------------------------------------------------------------------- #
# engine wiring
# --------------------------------------------------------------------------- #
class _FakeClient:
    def __init__(self, metas):
        self._metas = metas

    def list_homeworks(self, course_id):
        return [HomeworkTask(task_id="t1", homework_id="h1", name="作业",
                             course_id=course_id)]

    def list_questions(self, homework_id):
        return self._metas

    def question_detail(self, question_id):
        meta = next(m for m in self._metas if m.question_id == question_id)
        return Question(
            question_id=meta.question_id, order=meta.order, type=meta.type,
            stem="以下模型是非对称密码体制中的两种模型", images=["/stem.png"],
        )


class _FakeOperator:
    def __init__(self, path):
        self.path = path
        self.calls = []

    def open_homework(self, task):
        return object()

    def capture_question(self, page, question, total, output_path="",
                         full_page=False):
        self.calls.append((question.question_id, question.order, total,
                           output_path, full_page))
        return self.path


def _engine(tmp_path, metas):
    path = tmp_path / "shot.png"
    path.write_bytes(b"\x89PNG-fake")
    op = _FakeOperator(path)
    return PatrolEngine(Config(report_dir=str(tmp_path)), None,
                        _FakeClient(metas), op), op


def _meta(order, qtype=QuestionType.JUDGE):
    return Question(question_id=f"q{order}", order=order, type=qtype)


def test_engine_capture_uses_the_order_to_pick_the_question(tmp_path):
    engine, op = _engine(tmp_path, [_meta(1), _meta(5), _meta(7)])
    result = engine.capture_question_image("t1", "c1", order=5)
    assert result["question_id"] == "q5"
    assert result["order"] == 5
    assert result["images"] == ["/stem.png"]
    assert Path(result["path"]).is_file()
    assert op.calls == [("q5", 5, 3, "", False)]


def test_engine_capture_passes_full_page_through(tmp_path):
    engine, op = _engine(tmp_path, [_meta(5)])
    engine.capture_question_image("t1", "c1", order=5, full_page=True)
    assert op.calls[0][4] is True


def test_engine_capture_accepts_a_question_id(tmp_path):
    engine, op = _engine(tmp_path, [_meta(1), _meta(5)])
    result = engine.capture_question_image("t1", "c1", question_id="q1")
    assert result["order"] == 1
    assert op.calls[0][0] == "q1"


def test_engine_capture_requires_a_selector(tmp_path):
    engine, _ = _engine(tmp_path, [_meta(1)])
    with pytest.raises(UplusError, match="order 或 question_id"):
        engine.capture_question_image("t1", "c1")


def test_engine_capture_rejects_an_unknown_order(tmp_path):
    engine, _ = _engine(tmp_path, [_meta(1)])
    with pytest.raises(UplusError, match="未找到指定题目"):
        engine.capture_question_image("t1", "c1", order=99)


# --------------------------------------------------------------------------- #
# MCP tool contract: text + image content
# --------------------------------------------------------------------------- #
def test_tool_returns_text_and_image_content(tmp_path, monkeypatch):
    import uplus_mcp.server as server

    png = tmp_path / "shot.png"
    png.write_bytes(b"\x89PNG-fake")
    info = {"path": str(png), "order": 5, "stem": "题干", "images": ["/stem.png"]}

    class _Engine:
        def capture_question_image(self, *args, **kwargs):
            return info

    class _Thread:
        async def run_async(self, fn, *args, **kwargs):
            return fn(*args, **kwargs)

    monkeypatch.setattr(server, "_ensure_engine", lambda: None)
    monkeypatch.setattr(server, "_thread", _Thread())
    server._state["engine"] = _Engine()

    result = asyncio.run(server.uplus_capture_question_image(
        "t1", "c1", order=5))
    kinds = [block.type for block in result.content]
    assert kinds == ["text", "image"]
    payload = json.loads(result.content[0].text)
    assert payload["path"] == str(png)
    assert result.content[1].mimeType == "image/png"
    server._state.clear()
