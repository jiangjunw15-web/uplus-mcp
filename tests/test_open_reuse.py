"""``BrowserSession.open`` reuse: navigating to the page already on screen.

A homework's answer page is opened once to read its state, again to fill it and
again to audit it within one workflow. Playwright's ``goto`` does not
short-circuit a same-URL navigation, so each of those used to be a full SPA
reload plus a networkidle wait. ``open`` now detects that the tab already shows
the requested target (fragment ignored, scheme/host/path/query exact) and
returns it without reloading.

These tests pin both halves of that contract: the reuse (no ``goto``) and the
safety (a *different* target still reloads, so a second task never inherits the
first task's page).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import TimeoutError as PWTimeout

from uplus_mcp.browser import BrowserSession
from uplus_mcp.config import Config


class _Page:
    """Fake page recording ``goto`` calls and reporting its current URL."""

    def __init__(self, url: str):
        self.url = url
        self.gotos: list[tuple[str, dict]] = []
        self.load_states: list[tuple[str, object]] = []
        self._closed = False

    def is_closed(self):
        return self._closed

    def goto(self, url, wait_until=None, timeout=None):
        self.gotos.append((url, {"wait_until": wait_until, "timeout": timeout}))
        self.url = url
        return self

    def wait_for_load_state(self, state, timeout=None):
        self.load_states.append((state, timeout))
        if state == "networkidle" and timeout == "never":
            raise PWTimeout("forced")
        return None


def _session(page) -> BrowserSession:
    session = BrowserSession.__new__(BrowserSession)
    session.base_url = "https://www.eduplus.net"
    session.config = Config()
    session._started = True
    session._page = page
    return session


TASK_ROUTE = "/course/workAnswer/course-1/task-1/true?isPiYue=noPiYue"


def test_open_reuses_the_page_when_target_is_already_current():
    page = _Page("https://www.eduplus.net" + TASK_ROUTE)
    session = _session(page)

    session.open(TASK_ROUTE)

    assert page.gotos == []


def test_open_reloads_when_target_is_a_different_page():
    page = _Page("https://www.eduplus.net" + TASK_ROUTE)
    session = _session(page)

    session.open("/course/workAnswer/course-1/task-2/true?isPiYue=noPiYue")

    # A second task is a different path: the page must be reloaded, otherwise
    # the fill would run against the first task's questions.
    assert len(page.gotos) == 1
    assert page.gotos[0][0].endswith("task-2/true?isPiYue=noPiYue")


def test_open_settle_is_bounded_not_a_full_networkidle_wait():
    """A fresh page open waits for domcontentloaded and then only a short
    bounded settle — not the old up-to-8 s networkidle that never quietens on
    course packs with analytics/long-poll."""
    page = _Page("https://www.eduplus.net" + TASK_ROUTE)
    session = _session(page)

    session.open("/course/workAnswer/course-1/task-2/true?isPiYue=noPiYue")

    states = [s for s, _ in page.load_states]
    # domcontentloaded was the goto wait; networkidle may appear once with a
    # short (2 s) budget. Any networkidle wait must be bounded well under the
    # old 8 s.
    for state, timeout in page.load_states:
        if state == "networkidle":
            assert timeout == 2000


def test_open_ignores_the_fragment_when_deciding_to_reuse():
    page = _Page("https://www.eduplus.net" + TASK_ROUTE + "#step=3")
    session = _session(page)

    session.open(TASK_ROUTE)

    # The SPA adds route state as a hash; that is the same document, so no
    # reload is warranted.
    assert page.gotos == []


def test_open_reloads_when_the_query_differs():
    page = _Page("https://www.eduplus.net" + TASK_ROUTE)
    session = _session(page)

    session.open("/course/workAnswer/course-1/task-1/true?isPiYue=piYue")

    # The query is part of the answer route's identity, so a change reloads.
    assert len(page.gotos) == 1


def test_open_reloads_when_the_host_differs():
    page = _Page("https://www.eduplus.net.evil.example" + TASK_ROUTE)
    session = _session(page)

    session.open(TASK_ROUTE)

    # A look-alike host is a different origin and must never be treated as the
    # same document; the real platform page has to be loaded.
    assert len(page.gotos) == 1
    assert page.gotos[0][0].startswith("https://www.eduplus.net/")


def test_same_document_url_treats_blank_urls_as_different():
    assert BrowserSession._same_document_url("", "https://x/") is False
    assert BrowserSession._same_document_url("https://x/", "") is False
    assert BrowserSession._same_document_url("not a url", "https://x/") is False


def test_open_settle_false_skips_the_bounded_settle():
    """Callers that do their own readiness wait (the homework answer page) pass
    settle=False, which skips the networkidle settle and the trailing sleep
    entirely — the goto still happens for a different page."""
    page = _Page("https://www.eduplus.net" + TASK_ROUTE)
    session = _session(page)

    session.open("/course/workAnswer/course-1/task-2/true?isPiYue=noPiYue",
                 settle=False)

    assert len(page.gotos) == 1
    # No settle wait at all: neither networkidle nor a load-state probe.
    assert page.load_states == []


# --------------------------------------------------------------------------- #
# api_get: the AccessToken probe runs once, not on every call
# --------------------------------------------------------------------------- #
class _ApiPage(_Page):
    """Fake page whose ``evaluate`` answers the token probe and the fetch."""

    def __init__(self, url: str, token: str = "tok"):
        super().__init__(url)
        self._token = token
        self.probe_calls = 0
        self.fetch_calls = 0

    def evaluate(self, script, arg=None):
        text = str(script)
        if "localStorage.getItem" in text and "fetch(" not in text:
            self.probe_calls += 1
            return bool(self._token)
        if "fetch(" in text:
            self.fetch_calls += 1
            return {"status": 200, "body": '{"data": []}'}
        return None


def test_api_get_probes_the_token_once_not_per_call():
    """The probe is a one-time defensive check: the fetch itself re-reads the
    token on every call, and a token that goes stale is caught by 401 recovery.
    Probing every time cost one ``evaluate`` round-trip per question."""
    page = _ApiPage("https://www.eduplus.net/workbench/index/school", token="tok")
    session = _session(page)

    session.api_get("/api/a", retries=0)
    session.api_get("/api/b", retries=0)
    session.api_get("/api/c", retries=0)

    assert page.probe_calls == 1
    assert page.fetch_calls == 3


def test_api_get_rearms_the_probe_after_leaving_the_platform_origin():
    """Navigating off the platform origin leaves a tab whose AccessToken is not
    ours, so the probe must run again on the next API call."""
    page = _ApiPage("https://www.eduplus.net/workbench/index/school", token="tok")
    session = _session(page)

    session.api_get("/api/a", retries=0)
    session.open("https://oss.example.com/signed")  # off-origin navigation
    session.api_get("/api/b", retries=0)

    assert page.probe_calls == 2
