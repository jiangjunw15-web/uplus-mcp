"""Fill-blank DOM regression against the real page layout.

The live answer page renders each blank as a textarea labelled 「答案一：」 /
「答案二：」 with the placeholder 「请输入填空项答案」, and the stem may live in
a plain ``div`` (no ``.ck-content``) split across inline spans. These tests run
the actual Playwright locator/JS path on a synthetic page; they are skipped when
no Playwright browser is installed (the normal CI runner has no browser).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from uplus_mcp.config import Config
from uplus_mcp.models import Question, QuestionType
from uplus_mcp.operator import HomeworkOperator


LIST_HTML = """
<html><body>
<div id="header"><input id="search" placeholder="搜索课程"></div>
<div class="q" id="q12">
  <div class="stem"><span>用于</span><span>表示</span><span>请求</span>
    <span>映射的</span><span>通用</span><span>注解是</span>
    <span class="blank"></span><span>。</span></div>
  <div class="ans"><span>答案一：</span>
    <textarea class="answer" placeholder="请输入填空项答案"></textarea></div>
</div>
<div class="q" id="q19">
  <div class="stem"><span>处理</span> <span>GET</span> <span>请求用</span>
    <span class="blank"></span><span>注解，处理</span> <span>POST</span>
    <span>请求用</span><span class="blank"></span><span>注解。</span></div>
  <div class="ans"><span>答案一：</span>
    <textarea class="answer" placeholder="请输入填空项答案"></textarea></div>
  <div class="ans"><span>答案二：</span>
    <textarea class="answer" placeholder="请输入填空项答案"></textarea></div>
</div>
</body></html>
"""

SINGLE_HTML = """
<html><body>
<div id="header"><input id="search" placeholder="搜索课程"></div>
<div class="q" id="q12">
  <div class="stem">用于表示请求映射的通用注解是
    <span class="blank"></span>。</div>
  <div class="ans"><span>答案一：</span>
    <textarea class="answer" placeholder="请输入填空项答案"></textarea></div>
</div>
<button>下一题</button>
</body></html>
"""


@pytest.fixture(scope="module")
def page():
    playwright = None
    browser = None
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
    except Exception as exc:  # noqa: BLE001 - no browser in CI
        if browser is not None:
            browser.close()
        if playwright is not None:
            playwright.stop()
        pytest.skip(f"Playwright browser unavailable: {exc}")
    new_page = browser.new_page()
    yield new_page
    browser.close()
    playwright.stop()


def _blank(qid: str, order: int, stem: str) -> Question:
    return Question(question_id=qid, order=order, type=QuestionType.FILL_BLANK,
                    stem=stem, options=[])


def test_live_fill_blank_layout_is_scoped_and_filled(page):
    """Both screenshot layouts (one blank, two blanks) fill the right boxes in
    a list-style page whose stem has no ``.ck-content`` and whose blank spans
    carry no underscore text. The header search box must stay untouched."""
    operator = HomeworkOperator(None, None, Config())
    page.set_content(LIST_HTML)
    assert operator._single_question_view(page) is False

    operator.fill_question(
        page, _blank("q12", 12, "用于表示请求映射的通用注解是________。"),
        "@RequestMapping", single_view=False)
    operator.fill_question(
        page,
        _blank("q19", 19,
               "处理 GET 请求用______注解，处理 POST 请求用______注解。"),
        "@GetMapping\n@PostMapping", single_view=False)

    assert page.locator("#q12 textarea").evaluate_all(
        "els => els.map(e => e.value)") == ["@RequestMapping"]
    assert page.locator("#q19 textarea").evaluate_all(
        "els => els.map(e => e.value)") == ["@GetMapping", "@PostMapping"]
    assert page.locator("#search").input_value() == ""


def test_single_question_page_does_not_fill_the_header_search(page):
    operator = HomeworkOperator(None, None, Config())
    page.set_content(SINGLE_HTML)
    assert operator._single_question_view(page) is True

    operator.fill_question(
        page, _blank("q12", 12, "用于表示请求映射的通用注解是________。"),
        "@RequestMapping", single_view=True)
    assert page.locator(".answer").input_value() == "@RequestMapping"
    assert page.locator("#search").input_value() == ""