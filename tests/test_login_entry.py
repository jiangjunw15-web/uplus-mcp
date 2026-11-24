"""登录入口兜底：不同版式都要能找到「登录/注册」并落到登录表单。"""
from __future__ import annotations

from unittest.mock import Mock

from uplus_mcp.browser import BrowserSession


class _Entry:
    def __init__(self, visible: bool = True, on_click=None):
        self.visible = visible
        self.on_click = on_click
        self.clicked = 0

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def is_visible(self):
        return self.visible

    def click(self, timeout=None):
        self.clicked += 1
        if self.on_click:
            self.on_click()


class _Page:
    """Minimal Page: url changes when the entry is clicked."""

    def __init__(self, url: str, *, password_box: bool = False):
        self.url = url
        self.password_box = password_box
        self.entries = {}
        self.text_entries = {}

    def wait_for_url(self, pattern, timeout=None):
        class _Timeout(Exception):
            pass

        if "login" not in self.url.lower():
            raise _Timeout()

    def locator(self, selector):
        return self.entries.get(selector, _Entry(visible=False))

    def get_by_text(self, text, exact=False):
        return self.text_entries.get(text, _Entry(visible=False))


def _session(page: _Page) -> BrowserSession:
    session = BrowserSession.__new__(BrowserSession)
    session._page = page
    session._PASS_SELECTORS = ("input[type=password]",)
    session._first_visible = lambda p, selectors: (
        Mock(name="passbox") if p.password_box else None)
    return session


def test_bare_text_entry_is_clicked_and_lands_on_the_form():
    page = _Page("https://www.eduplus.net/home/index")

    def _click():
        page.url = "https://uc.eduplus.net/login?theme=athena"
        page.password_box = True

    page.text_entries["登录/注册"] = _Entry(on_click=_click)
    assert _session(page)._enter_login_form(page) is True
    assert page.text_entries["登录/注册"].clicked == 1


def test_class_entry_still_works_first():
    page = _Page("https://www.eduplus.net/home/index")

    def _click():
        page.url = "https://uc.eduplus.net/login"
        page.password_box = True

    page.entries[".login-button"] = _Entry(on_click=_click)
    assert _session(page)._enter_login_form(page) is True
    assert page.entries[".login-button"].clicked == 1


def test_no_entry_reports_false_instead_of_claiming_a_form():
    page = _Page("https://www.eduplus.net/home/index")
    assert _session(page)._enter_login_form(page) is False


def test_unreachable_form_message_points_at_the_entry():
    page = _Page("https://www.eduplus.net/home/index")
    result = _session(page)._login_form_unreachable(page, "student_id")
    assert result["logged_in"] is False
    assert "登录/注册" in result["reason"]
    assert "未识别到账号/密码输入框" not in result["reason"]


def test_already_on_login_page_is_a_no_op():
    page = _Page("https://uc.eduplus.net/login", password_box=True)
    assert _session(page)._enter_login_form(page) is True
