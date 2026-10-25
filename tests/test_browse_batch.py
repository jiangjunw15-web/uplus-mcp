"""Unit tests for the batched browser action receipt semantics."""

from unittest.mock import Mock

from uplus_mcp.browse import BrowseTools


class FakeLocator:
    def __init__(self, *, text="", href="", value="", password=False, count=1):
        self._text = text
        self._href = href
        self._value = value
        self._password = password
        self._count = count

    def count(self):
        return self._count

    def is_visible(self):
        return self._count > 0

    def nth(self, index):
        return self

    def scroll_into_view_if_needed(self, timeout=5000):
        return None

    def click(self, timeout=8000):
        return None

    def fill(self, value, timeout=8000):
        return None

    def press(self, key):
        return None

    def hover(self, timeout=5000):
        return None

    def inner_text(self):
        return self._text

    def get_attribute(self, name):
        return self._href if name == "href" else None

    def input_value(self):
        return "" if self._password else self._value

    def evaluate(self, expression):
        return self._password


class FakePage:
    def __init__(self, locator: FakeLocator, url="https://example.com"):
        self.url = url
        self._locator = locator
        self.keyboard = Mock()
        self.mouse = Mock()
        self.navigated = False

    def locator(self, selector):
        return self._locator

    def get_by_text(self, text, exact=False):
        return self._locator

    def inner_text(self, selector="body"):
        return "页面正文"

    def goto(self, url, wait_until="domcontentloaded"):
        self.url = url
        self.navigated = True

    def go_back(self, wait_until="domcontentloaded"):
        self.url = "https://example.com/previous"


def _tools(page: FakePage) -> BrowseTools:
    session = Mock()
    session.page = page
    return BrowseTools(session)


def test_batch_completes_steps_and_returns_extract_rows():
    page = FakePage(FakeLocator(text="标题", href="/a", value="x"))
    result = _tools(page).act_batch(
        [
            {"op": "click", "selector": "a"},
            {"op": "extract", "selector": "a", "fields": ["text", "href"], "maxRows": 10},
        ],
        observe="summary",
    )
    assert result["completed"] == [{"index": 0, "op": "click"}, {"index": 1, "op": "extract"}]
    assert result["remaining"] == 0
    assert "failure" not in result
    assert result["results"][0]["rows"][0] == {"text": "标题", "href": "/a"}


def test_batch_stops_at_unknown_op_and_keeps_receipt_honest():
    page = FakePage(FakeLocator())
    result = _tools(page).act_batch(
        [{"op": "click", "selector": "a"}, {"op": "teleport"}],
        observe="summary",
    )
    assert result["completed"] == [{"index": 0, "op": "click"}]
    assert result["remaining"] == 1
    assert result["failure"]["index"] == 1
    assert "未知动作" in result["failure"]["error"]
    assert "不要重复已完成" in result["retry"]


def test_batch_rejects_invalid_extract_fields():
    page = FakePage(FakeLocator())
    result = _tools(page).act_batch(
        [{"op": "extract", "selector": "a", "fields": ["html"]}],
        observe="summary",
    )
    assert result["completed"] == []
    assert result["failure"]["op"] == "extract"


def test_batch_never_reads_back_password_value():
    page = FakePage(FakeLocator(value="should-not-leak", password=True))
    result = _tools(page).act_batch(
        [{"op": "extract", "selector": "input", "fields": ["value"]}],
        observe="summary",
    )
    assert result["results"][0]["rows"][0]["value"] == ""


def test_batch_stops_after_navigation_and_skips_remaining_steps():
    page = FakePage(FakeLocator())
    result = _tools(page).act_batch(
        [
            {"op": "navigate", "url": "https://example.com/next"},
            {"op": "click", "selector": "a"},
        ],
        observe="summary",
    )
    assert result["completed"] == [{"index": 0, "op": "navigate"}]
    assert result["remaining"] == 1
    assert result["navigated"] is True
    assert "重新读取" in result["stopped"]


def test_batch_snapshot_observe_reads_body_once():
    page = FakePage(FakeLocator())
    result = _tools(page).act_batch([{"op": "hover", "selector": "a"}], observe="snapshot")
    assert result["read"] == "页面正文"
