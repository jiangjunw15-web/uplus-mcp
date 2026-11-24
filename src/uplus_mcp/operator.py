"""Page-level UI operations on the U+ answer pages.

The REST layer (``platform.py`` reads data; this layer drives the real answer
page to fill objective questions, verify answers, submit homework and upload
attachments. Page truth overrides API status: the button label
(``提交作业`` vs ``撤回作业``) is the authoritative submission state.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PWTimeout, Locator

from .browser import BrowserSession, UplusError, AuthExpiredError
from .config import Config
from .models import (
    Course,
    Experiment,
    HomeworkStatus,
    HomeworkTask,
    Question,
    QuestionType,
)
from .platform import PlatformClient
from . import journal, models, solver, utils


def text_key(text: str) -> str:
    """Normalize a stem for comparison: drop spaces, punctuation and brackets.

    The page renders the stem inside a rich-text block whose spacing and full
    width punctuation never match the REST payload character for character.
    """
    return re.sub(r"[\s\u00a0\u3000，。、？！：；“”‘’（）()《》〈〉\[\]【】\-—,.?!:;\"'`~]", "", text or "")


def stem_position(positions: dict[str, int], stem: str,
                   used: set[int] | None = None) -> Optional[int]:
    """Find the page position whose stem matches, preferring the longest prefix.

    Page order and API order disagree on eduplus, so answering by API ``order``
    can land on a different question. Matching by stem is exact first, then by
    the longest shared prefix (the page appends option text to the stem).

    ``used`` tracks positions already consumed so duplicate stems don't collide.
    """
    key = text_key(stem)
    if not key:
        return None
    # ``used or set()`` would replace an empty caller set with a fresh one, so
    # the first consumed position would never reach the caller; test ``None``.
    _used = used if used is not None else set()
    # Exact hit: try unsuffixed key first, then suffixed keys in order
    for candidate_key in [key] + [f"{key}::{i}" for i in range(2, 100)]:
        if candidate_key in positions and positions[candidate_key] not in _used:
            _used.add(positions[candidate_key])
            return positions[candidate_key]
    best = None
    best_len = 0
    for candidate, index in positions.items():
        if index in _used:
            continue
        # Strip the ::N suffix for prefix comparison
        bare = candidate.split("::")[0] if "::" in candidate else candidate
        shared = 0
        for a, b in zip(key, bare, strict=False):
            if a != b:
                break
            shared += 1
        if shared >= 8 and shared > best_len:
            best, best_len = index, shared
    if best is not None:
        _used.add(best)
    return best


def stem_matches(a: str, b: str) -> bool:
    """True when two stems are the same question.

    The page appends the option text to the stem, so equality is checked after
    normalization, then prefix containment (the shorter one must be at least 8
    normalized characters, so two different questions that share a boilerplate
    opening are not confused).
    """
    ka, kb = text_key(a), text_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    short, long_ = (ka, kb) if len(ka) <= len(kb) else (kb, ka)
    return len(short) >= 8 and long_.startswith(short)


# Mark the question container that owns a given stem with ``data-uplus-q`` so
# Playwright locators can be scoped to it. Matching mirrors ``stem_position``:
# exact normalized text first, then the longest shared prefix (>= 8 chars).
# ``skip`` selects the n-th exact duplicate (0-based).
_MARK_QUESTION_JS = r"""
([stem, skip]) => {
  const norm = (s) => (s || '').replace(
    /[\s 　，。、？！：；“”‘’（）()《》〈〉\[\]【】\-—,.?!:;:"'`~]/g, '');
  const key = norm(stem);
  if (!key) return false;
  // Some pages render each blank as an empty styled span, so the DOM text
  // omits the underscores the API stem carries. Match a blank-stripped key as
  // a secondary form; the primary (underscore-preserving) match still wins.
  const blankless = (s) => (s || '').replace(
    /_{2,}|＿{2,}|﹍{2,}|﹎{2,}/g, '');
  const key2 = blankless(key);
  const sharedPrefix = (a, b) => {
    let n = 0;
    const m = Math.min(a.length, b.length);
    while (n < m && a[n] === b[n]) n++;
    return n;
  };
  const visible = (el) => {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    if (!r || (!r.width && !r.height)) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none';
  };
  // A candidate must be a box the student can actually use. Hidden inputs
  // are the platform's own bookkeeping; a header search box is not an
  // answer slot either. Radio/checkbox inputs stay relevant for choice
  // questions even when the theme sizes the native input to zero; the
  // nearest-any-box fallback below then keeps the old behaviour.
  const isRelevantBox = (el) => {
    if (!el || !visible(el)) return false;
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'textarea' || el.isContentEditable
        || (el.getAttribute('role') || '') === 'textbox') return true;
    if (tag !== 'input') return false;
    const type = (el.type || 'text').toLowerCase();
    if (['hidden', 'button', 'submit', 'reset', 'file', 'image'].includes(type))
      return false;
    if (type === 'radio' || type === 'checkbox') return true;
    const hint = (el.getAttribute('placeholder')
                  || el.getAttribute('aria-label') || '').trim();
    return !/搜索|查找|关键字/.test(hint);
  };
  const boxesIn = (el) => [...el.querySelectorAll(
    'input, textarea, [contenteditable="true"], [role="textbox"]')];
  const blocks = [...document.querySelectorAll('.ck-content')];
  const exact = blocks.filter((el) => {
    const text = norm(el.innerText);
    if (text === key) return true;
    return key2 !== key && blankless(text) === key2;
  });
  let target = null;
  if (exact.length) {
    target = exact[Math.min(Math.max(skip | 0, 0), exact.length - 1)];
  } else {
    let bestLen = 0;
    for (const el of blocks) {
      const text = norm(el.innerText);
      if (!text) continue;
      const shared = Math.max(
        sharedPrefix(key, text),
        key2 !== key ? sharedPrefix(key2, blankless(text)) : 0,
      );
      if (shared >= 8 && shared > bestLen) { target = el; bestLen = shared; }
    }
  }
  if (!target) return false;
  // Walk up to the nearest ancestor that really holds an answer/option box.
  // Stopping at a hidden bookkeeping input used to mark a container that did
  // not include the fill-blank textarea, so the question reported "找不到
  // 答案输入框" even though the boxes were on screen.
  let scope = target;
  let fallback = null;
  while (scope && scope !== document.body) {
    const boxes = boxesIn(scope);
    if (boxes.length && !fallback) fallback = scope;
    if (boxes.some(isRelevantBox)) break;
    scope = scope.parentElement;
  }
  if (!scope || scope === document.body) {
    scope = fallback || target.parentElement || target;
  }
  document.querySelectorAll('[data-uplus-q]').forEach(
    (el) => el.removeAttribute('data-uplus-q'));
  scope.setAttribute('data-uplus-q', '1');
  return true;
}
"""


#: Fallback marker for answer pages whose stem is not inside ``.ck-content``
#: (some course packs render a plain ``div``). It finds the most specific
#: element carrying the stem, then the nearest ancestor that holds a usable
#: answer box. ``skip`` selects the n-th duplicate in document order.
_MARK_QUESTION_GENERIC_JS = r"""
([stem, skip]) => {
  const norm = (s) => (s || '').replace(
    /[\s 　，。、？！：；“”‘’（）()《》〈〉\[\]【】\-—,.?!:;:"'`~]/g, '');
  const key = norm(stem);
  if (!key) return false;
  // Some pages render each blank as an empty styled span, so the DOM text
  // omits the underscores the API stem carries. Match a blank-stripped key as
  // a secondary form; the primary (underscore-preserving) match still wins.
  const blankless = (s) => (s || '').replace(
    /_{2,}|＿{2,}|﹍{2,}|﹎{2,}/g, '');
  const key2 = blankless(key);
  const sharedPrefix = (a, b) => {
    let n = 0;
    const m = Math.min(a.length, b.length);
    while (n < m && a[n] === b[n]) n++;
    return n;
  };
  const visible = (el) => {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    if (!r || (!r.width && !r.height)) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none';
  };
  const isRelevantBox = (el) => {
    if (!el || !visible(el)) return false;
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'textarea' || el.isContentEditable
        || (el.getAttribute('role') || '') === 'textbox') return true;
    if (tag !== 'input') return false;
    const type = (el.type || 'text').toLowerCase();
    if (['hidden', 'button', 'submit', 'reset', 'file', 'image'].includes(type))
      return false;
    if (type === 'radio' || type === 'checkbox') return true;
    const hint = (el.getAttribute('placeholder')
                  || el.getAttribute('aria-label') || '').trim();
    return !/搜索|查找|关键字/.test(hint);
  };
  const boxesIn = (el) => [...el.querySelectorAll(
    'input, textarea, [contenteditable="true"], [role="textbox"]')];
  const starts = [];
  const push = (el) => {
    if (el && el !== document.body && el !== document.documentElement) {
      starts.push(el);
    }
  };
  const matches = (raw) => {
    const text = norm(raw);
    if (!text) return false;
    if (text === key || text.startsWith(key)) return true;
    if (key.startsWith(text) && text.length >= 8) return true;
    if (key2 !== key) {
      const stripped = blankless(text);
      if (stripped === key2) return true;
      if (stripped.length >= 8
          && (stripped.startsWith(key2) || key2.startsWith(stripped)))
        return true;
    }
    return false;
  };

  // 1) Known rich-text / question-title containers.
  document.querySelectorAll(
    '.ck-content, .question-title, .q-title, .stem, .question-stem, ' +
    '.q-content, .question-content, .subject-title, .title-text'
  ).forEach((el) => {
    if (matches(el.innerText)) push(el);
  });

  // 2) Plain text node carrying the whole stem.
  if (!starts.length) {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    let seen = 0;
    while ((node = walker.nextNode()) && seen < 4000) {
      seen++;
      const parent = node.parentElement;
      if (!parent || parent.closest('script,style,noscript')) continue;
      const text = norm(node.textContent);
      if (text.length < 8) continue;
      if (matches(node.textContent)) {
        push(parent);
        if (starts.length >= 12) break;
      }
    }
  }

  // 3) Rich text split across inline tags: inspect element text, bounded.
  if (!starts.length) {
    let seen = 0;
    for (const el of document.querySelectorAll('div,p,section,li,td')) {
      if (++seen > 4000) break;
      const raw = norm(el.textContent);
      if (!raw || raw.length > key.length + 800) continue;
      const stripped = blankless(raw);
      if (raw === key || raw.startsWith(key)
          || (key2 !== key && (stripped === key2 || stripped.startsWith(key2))))
        push(el);
    }
  }
  if (!starts.length) return false;

  const unique = [...new Set(starts)];
  const scopes = [];
  for (const start of unique) {
    let scope = start;
    let fallback = null;
    while (scope && scope !== document.body) {
      const boxes = boxesIn(scope);
      if (boxes.length && !fallback) fallback = scope;
      if (boxes.some(isRelevantBox)) break;
      scope = scope.parentElement;
    }
    const found = (scope && scope !== document.body)
      ? scope : (fallback || start.parentElement);
    if (found) scopes.push(found);
  }
  if (!scopes.length) return false;
  const final = [...new Set(scopes)].sort((a, b) => (
    a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1
  ));
  const target = final[Math.min(Math.max(skip | 0, 0), final.length - 1)];
  if (!target) return false;
  document.querySelectorAll('[data-uplus-q]').forEach(
    (el) => el.removeAttribute('data-uplus-q'));
  target.setAttribute('data-uplus-q', '1');
  return true;
}
"""


def classify_page_state(text: str) -> Optional[HomeworkStatus]:
    """Map answer-page body text to a submission state.

    Evidence captured against eduplus.net:

    * ``撤回作业`` button — submitted, awaiting grading (no reference answer).
    * ``提交作业`` button — not submitted.
    * graded page shows neither button; it exposes ``参考答案``, ``总分：`` and
      per-question ``得分：`` instead.
    * ``None`` means the page state could not be recognised; callers must keep
      the REST-derived status rather than assume "not submitted".
    """
    if "撤回作业" in text:
        return HomeworkStatus.SUBMITTED_PENDING
    if "提交作业" in text:
        return HomeworkStatus.NOT_SUBMITTED
    if "参考答案" in text or "总分：" in text or "得分：" in text:
        return HomeworkStatus.SUBMITTED_GRADED
    return None


def classify_experiment_flag(flag_text: Optional[str], flag_class: str) -> Optional[bool]:
    """Map an experiment card badge to a submission state.

    Text wins over class: a card just inserted by the SPA may still carry the
    default ``gray`` class before the API state paints, so a bare class match
    must not be trusted when the visible label has not rendered yet.

    * ``已提交`` / ``gray`` — submitted.
    * ``未提交`` / ``待提交`` / ``未开始`` / ``red`` — outstanding.
    * ``None`` — badge missing or unrecognised; the caller reports "unknown".
    """
    text = (flag_text or "").strip()
    cls = flag_class or ""
    if "已提交" in text:
        return True
    if "未提交" in text or "待提交" in text or "未开始" in text:
        return False
    if "gray" in cls:
        return True
    if "red" in cls:
        return False
    return None


class HomeworkOperator:
    ANSWER_ROUTE = "/course/workAnswer/{course_id}/{task_id}/true?isPiYue=noPiYue"
    EXPERIMENT_ROUTE = "/course/course/{course_id}/ExperimentPage?userRole="

    def __init__(self, session: BrowserSession, client: PlatformClient, config: Config) -> None:
        self.session = session
        self.client = client
        self.config = config

    # ------------------------------------------------------------------ #
    # navigation
    # ------------------------------------------------------------------ #
    def open_homework(self, task: HomeworkTask, *, settle: bool = False) -> Page:
        """Open the homework's answer page.

        ``settle=False`` (the default) skips the generic page settle in
        ``BrowserSession.open``: every caller of this method either reads the
        page state right away (which waits for ``domcontentloaded`` and polls
        for the submit button) or drives the page itself. Callers that need the
        page rendered before they act on it — the screenshot paths — pass
        ``settle=True``.
        """
        url = self.session.url_for(
            self.ANSWER_ROUTE.format(
                course_id=utils.safe_url_segment(task.course_id, "course_id"),
                task_id=utils.safe_url_segment(task.task_id, "task_id"),
            )
        )
        try:
            page = self.session.open(url, settle=settle)
        except TypeError:  # a session stub that takes only the URL
            page = self.session.open(url)
        if "login" in page.url.lower():
            raise AuthExpiredError("U+ 登录态已过期，请重新登录。")
        return page

    # ------------------------------------------------------------------ #
    # page truth: button label + progress
    # ------------------------------------------------------------------ #
    def _await_page_state(self, page: Page) -> str:
        """Wait until the SPA paints a recognizable status keyword or the
        deadline elapses; return the body text either way.

        Polling is short and bounded: the answer SPA usually paints the submit
        button within a few hundred milliseconds of ``domcontentloaded``, so a
        fast first probe (120 ms) plus a tight retry loop catches the ready
        state earlier than the old fixed 300 ms cadence. The total budget is
        capped so a stuck page never blocks the patrol indefinitely.
        """
        try:
            page.wait_for_load_state("domcontentloaded", timeout=4000)
        except PWTimeout:
            pass
        # The submit/withdraw button is the signal we actually need; once it is
        # there the body text is readable, even if ``networkidle`` never fires
        # (analytics/long-poll keep the network busy on some course packs).
        for _ in range(20):
            text = page.inner_text("body")
            if classify_page_state(text) is not None:
                return text
            time.sleep(0.15)
        return page.inner_text("body")

    def read_page_state(self, page: Page) -> tuple[Optional[HomeworkStatus], Optional[str]]:
        text = self._await_page_state(page)
        progress = None
        m = re.search(r"答题卡\s*\((\d+\s*/\s*\d+)\)", text)
        if m:
            progress = m.group(1).replace(" ", "")
            # A 0/0 progress means the answer card did not load the question
            # count — the progress is not usable. Report None instead of a
            # misleading "0/0" that the audit would treat as "nothing filled".
            num, _, den = progress.partition("/")
            if num == "0" and den == "0":
                progress = None
        return classify_page_state(text), progress

    def read_page_score(self, page: Page) -> Optional[float]:
        """Read the total score from a graded answer page.

        A graded page shows ``总分：XX`` (or ``总分: XX``). Returns ``None``
        when the page is not graded or the score text is absent. Called only
        after ``read_page_state`` so the page has already settled.
        """
        try:
            text = page.inner_text("body")
        except Exception:  # noqa: BLE001 - detached / fake page
            return None
        m = re.search(r"总分[：:]\s*([0-9]+(?:\.[0-9]+)?)", text)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
        return None

    def _probe_submit_button(self, page: Page) -> Optional[HomeworkStatus]:
        """Cheap status read: which submit/withdraw button is on the page.

        The submit and withdraw confirmation loops used to call
        ``read_page_state`` on every round, which serialises the whole body
        text — an expensive operation on a 30-question page, and one that only
        needed to answer "has the button flipped yet?". This reads the two
        button labels through one small DOM query instead, so a round costs a
        fraction of a full body read.

        It returns the state implied by the buttons, or ``None`` when neither is
        present (page still transitioning). Callers that need the progress text
        or a definitive verdict follow up with ``read_page_state`` once.
        """
        script = r"""
        () => {
            const labels = [...document.querySelectorAll('button')]
                .map(b => (b.innerText || '').trim());
            const has = (name) => labels.some(t => t === name || t.includes(name));
            if (has('撤回作业')) return 'withdrawn';
            if (has('提交作业')) return 'submit';
            return null;
        }
        """
        try:
            probe = page.evaluate(script)
        except Exception:  # noqa: BLE001 - mid-navigation / detached frame
            return None
        if probe == "withdrawn":
            return HomeworkStatus.SUBMITTED_PENDING
        if probe == "submit":
            return HomeworkStatus.NOT_SUBMITTED
        return None

    def withdraw_homework(self, page: Page, task: HomeworkTask) -> str:
        """Withdraw a submitted-but-ungraded homework so it can be corrected.

        Only the page's real "撤回作业" button is trusted. A graded homework
        cannot be withdrawn; a page that never returns to the editable state
        raises instead of pretending the withdrawal happened.
        """
        state, progress = self.read_page_state(page)
        if state is HomeworkStatus.SUBMITTED_GRADED:
            raise UplusError("作业已出分，平台不允许撤回，无法补交")
        if state is HomeworkStatus.NOT_SUBMITTED:
            task.page_status = state
            task.page_progress = progress
            return "not_submitted"
        if state is not HomeworkStatus.SUBMITTED_PENDING:
            raise UplusError("页面状态无法确认（未找到「撤回作业」按钮），为避免误操作已停止")

        button = page.get_by_role("button", name="撤回作业")
        if button.count() == 0:
            button = page.locator("text=撤回作业")
        if button.count() == 0:
            raise UplusError("未找到「撤回作业」按钮，页面状态可能已变化")
        button.first.click(timeout=6000)
        time.sleep(0.8)

        # Element Plus confirmation dialog: primary button first, then a
        # visible button whose label is the action verb.
        try:
            confirm = page.locator(".el-message-box__btns .el-button--primary")
            if confirm.count():
                confirm.first.click(timeout=4000)
            else:
                for name in ("确定", "确认", "撤回"):
                    candidate = page.get_by_role("button", name=name)
                    if candidate.count():
                        candidate.first.click(timeout=4000)
                        break
        except PWTimeout as exc:
            raise UplusError(f"撤回确认弹窗未处理：{exc}") from exc

        for _ in range(12):
            time.sleep(0.6)
            # Fast path first, full read only when the probe is inconclusive
            # (``None``), so the verdict is identical to reading every round.
            probe = self._probe_submit_button(page)
            if probe is None:
                state, progress = self.read_page_state(page)
                if state is HomeworkStatus.NOT_SUBMITTED:
                    task.page_status = state
                    task.page_progress = progress
                    return "withdrawn"
            elif probe is HomeworkStatus.NOT_SUBMITTED:
                state, progress = self.read_page_state(page)
                task.page_status = state or probe
                task.page_progress = progress
                return "withdrawn"
        state, _ = self.read_page_state(page)
        raise UplusError(f"撤回后页面未回到可作答状态（当前 {state}），请人工确认后再操作")

    # ------------------------------------------------------------------ #
    # question filling
    # ------------------------------------------------------------------ #
    def _narrowed_scope(self, page: Page, stem_text: str = "",
                        occurrence: int = 0):
        """Return the question-scoped locator, or ``None`` when none applied.

        ``None`` means "the whole page is the only scope available", which is
        correct for a single-question view but must never be treated as
        "this page is one question" on a list view (see
        :meth:`_checked_letters`).
        """
        if not stem_text:
            return None
        try:
            marked = page.evaluate(_MARK_QUESTION_JS, [stem_text, occurrence])
        except Exception:  # noqa: BLE001 - unknown page shape / fake page
            marked = False
        if not marked:
            # Answer pages from some course packs do not wrap the stem in
            # ``.ck-content``. The generic locator scans plain text nodes and
            # marks the nearest question card that actually owns an answer box.
            try:
                marked = page.evaluate(
                    _MARK_QUESTION_GENERIC_JS, [stem_text, occurrence])
            except Exception:  # noqa: BLE001 - unknown page shape / fake page
                return None
        if not marked:
            return None
        try:
            scope = page.locator("[data-uplus-q='1']")
            if scope.count():
                return scope.first
        except Exception:  # noqa: BLE001
            pass
        return None

    def _question_scope(self, page: Page, stem_text: str = "",
                        occurrence: int = 0):
        """Return a locator scoped to one question container when possible.

        On list-view pages every question renders at once, so an unscoped
        ``label.el-checkbox:has-text("A、")`` would always hit the first
        question's option A. The stem block is located in the DOM (same
        normalized matching as :func:`stem_position`), its nearest ancestor
        that contains an option input is tagged with ``data-uplus-q`` and that
        container becomes the scope. When no stem is given (single-question
        view) the whole page is the scope.

        ``occurrence`` selects the n-th identical stem for duplicate questions.
        Falls back to the whole page whenever the page cannot be evaluated or
        the stem is not found, so an unknown layout degrades to the previous
        behaviour instead of raising.
        """
        scope = self._narrowed_scope(page, stem_text, occurrence)
        return page if scope is None else scope

    def _option_locator(self, page: Page, code: str,
                        stem_text: str = "", occurrence: int = 0) -> Locator:
        """Locate the clickable option row for a letter like ``A``.

        Two page layouts exist on eduplus: one renders the option as a single
        label text ("A、开闭原则"), the other puts the letter in its own span
        ("A" + "开闭原则", no 、). The text form is tried first because it is
        unambiguous on pages that show every question at once; the letter-only
        form is matched through the radio/checkbox input's ``value``.

        ``xpath=...starts-with(normalize-space(.), 'A、')`` is used instead of
        ``:has-text`` because the latter matches the letter anywhere in the
        label, so an option whose *content* mentions "A、" could be picked by
        mistake.
        """
        scope = self._question_scope(page, stem_text, occurrence)

        for kind in ("el-radio", "el-checkbox"):
            label = scope.locator(
                f"xpath=.//label[contains(@class,'{kind}') and "
                f"starts-with(normalize-space(.), '{code}、')]"
            )
            if label.count():
                return label.first

        radio_input = scope.locator(f"label:has(input[type=radio][value=\"{code}\"])")
        check_input = scope.locator(f"label:has(input[type=checkbox][value=\"{code}\"])")
        if radio_input.count() == 1:
            return radio_input.first
        if check_input.count() == 1:
            return check_input.first

        # Fallback: text node "A、" and its clickable ancestor row.
        return scope.locator(f"text={code}、").first

    def _checked_letters(self, page: Page, stem_text: str = "",
                         occurrence: int = 0) -> Optional[set[str]]:
        """Read the option inputs' checked state inside the question scope.

        Returns the set of checked option letters, or ``None`` when the DOM
        cannot answer (no option inputs, or inputs without a letter ``value``).
        ``None`` must be treated as "unverifiable", never as "nothing checked".

        A list-view page (``stem_text`` given) whose stem could not be narrowed
        also returns ``None``: the whole page holds every question's boxes, so
        reading them would mix this question's state with its neighbours' and
        the caller would uncheck someone else's option. A single-question view
        (no ``stem_text``) has nothing else on the page, so it reads normally.
        """
        if stem_text:
            scope = self._narrowed_scope(page, stem_text, occurrence)
            if scope is None:
                return None
        else:
            scope = page
        try:
            inputs = scope.locator("input[type=checkbox], input[type=radio]")
            total = min(inputs.count(), 40)
        except Exception:  # noqa: BLE001 - fake page / detached frame
            return None
        if total == 0:
            return None
        checked: set[str] = set()
        valued = 0
        for index in range(total):
            try:
                box = inputs.nth(index)
                value = str(box.get_attribute("value") or "").strip().upper()
                match = re.fullmatch(r"[A-Z]", value)
                if not match:
                    continue
                valued += 1
                if box.is_checked():
                    checked.add(value)
            except Exception:  # noqa: BLE001 - input detached mid-read
                continue
        return checked if valued else None

    def _click_option(self, page: Page, code: str,
                      stem_text: str = "", occurrence: int = 0) -> None:
        locator = self._option_locator(page, code, stem_text=stem_text,
                                       occurrence=occurrence)
        locator.scroll_into_view_if_needed(timeout=4000)
        locator.click(timeout=6000)
        time.sleep(0.25)

    def _answer_judge(self, page: Page, value: bool,
                      stem_text: str = "", occurrence: int = 0) -> None:
        target = "正确" if value else "错误"
        scope = self._question_scope(page, stem_text, occurrence)
        btn = scope.get_by_text(target, exact=True)
        if btn.count() == 0:
            btn = scope.locator(f"label:has-text(\"{target}\")")
        btn.first.click(timeout=6000)
        time.sleep(0.25)

    # Fill-blank answer boxes. A question may expose several boxes (one per
    # blank in the stem), rendered as plain inputs, textareas or contenteditable
    # rich-text editors. The order on the page is the order of the blanks.
    _BLANK_INPUT_SELECTOR = (
        "input[type=text], input[type=search], input[type=number], "
        "input[type=email], input:not([type]), input[placeholder], "
        "textarea, [contenteditable=true]:not([contenteditable=false]), "
        "[role=textbox]"
    )

    #: Placeholder/aria text that marks a real fill-blank answer slot. The
    #: live page labels every box 「请输入填空项答案」 and shows 答案一/答案二.
    _ANSWER_HINT_RE = re.compile(r"填空|答案")
    #: Hints that identify a non-answer field (header search, list filter).
    #: A whole-page fallback scope can contain these, so they are excluded even
    #: when no answer-labelled box is present — otherwise the first blank would
    #: be typed into the search box and the real box left empty.
    _FIELD_HINT_RE = re.compile(r"搜索|查找|关键字|筛选|检索|search|filter", re.I)

    @staticmethod
    def _box_hint(box) -> str:
        """Placeholder-like text that identifies what a box is for."""
        for attr in ("placeholder", "data-placeholder", "aria-label",
                     "aria-placeholder"):
            try:
                value = box.get_attribute(attr)
            except Exception:  # noqa: BLE001 - contenteditable / detached
                continue
            if value and str(value).strip():
                return str(value).strip()
        return ""

    def _blank_boxes(self, scope, expected: int = 0) -> list:
        """All answer boxes of one fill-blank question, in DOM order.

        Hidden inputs (the platform ships several per question for its own
        bookkeeping) are skipped: only a box the student can actually type in
        counts as an answer slot. When any box carries an answer-specific hint
        (「请输入填空项答案」/「答案一」/「答案二」), unrelated text inputs in
        the same scope (a header search field, list filters) are dropped. If
        the hinted boxes already cover every expected blank, only those are
        returned; otherwise unlabelled boxes stay so a mixed editor layout
        still lines up.

        Search / filter fields are excluded in every branch, including when no
        answer-labelled box exists at all: the fallback scope is the whole page,
        and typing an answer into the header search box would leave the real
        blank empty while the read-back still "matched" the search value.
        """
        selector = scope.locator(self._BLANK_INPUT_SELECTOR)
        try:
            count = selector.count()
        except Exception:  # noqa: BLE001 - detached page
            return []
        collected: list[tuple[object, str]] = []
        for index in range(min(count, 30)):
            box = selector.nth(index)
            try:
                # A hidden input is the platform's own bookkeeping, not an
                # answer slot. An unknown state is kept: failing to answer
                # because visibility could not be read would be worse.
                if box.is_visible() is False:
                    continue
            except Exception:  # noqa: BLE001 - visibility not readable
                pass
            collected.append((box, self._box_hint(box)))
        # Drop fields identified as search/filter before any other rule.
        answerable = [
            (box, hint) for box, hint in collected
            if not self._FIELD_HINT_RE.search(hint)
        ]
        # If the filter removed everything, fall back to the unfiltered list so
        # an unusual page still gets an attempt rather than zero boxes.
        collected = answerable or collected
        if not collected:
            return []
        hinted = [
            box for box, hint in collected
            if self._ANSWER_HINT_RE.search(hint)
        ]
        if hinted and (expected <= 0 or len(hinted) >= expected):
            return hinted
        if hinted:
            # One editor may render without a placeholder; keep the unlabelled
            # slots, but still drop inputs identified as search/filter fields.
            return [
                box for box, hint in collected
                if not hint or self._ANSWER_HINT_RE.search(hint)
            ]
        return [box for box, _ in collected]

    @staticmethod
    def _read_box(box) -> str:
        """Read a box's value, whether it is an input or contenteditable."""
        try:
            value = box.input_value()
            if value is not None:
                return str(value).strip()
        except Exception:  # noqa: BLE001 - contenteditable has no input_value
            pass
        try:
            return str(box.inner_text() or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _write_box(box, value: str) -> None:
        """Write one answer; contenteditable editors need keyboard input."""
        try:
            box.fill(value, timeout=6000)
            return
        except Exception:  # noqa: BLE001 - contenteditable / non-input element
            pass
        box.click(timeout=6000)
        # Clear whatever the editor holds, then type the real answer. This is
        # the only reliable way with CKEditor/Quill-style widgets that refuse
        # Playwright's ``fill``.
        box.press("ControlOrMeta+A")
        box.press("Delete")
        box.type(value, timeout=6000)

    @staticmethod
    def _force_box_value(box, value: str) -> None:
        """Last-resort write for a box whose editor swallows fill/typing.

        Sets the DOM value through the native setter and dispatches ``input``
        and ``change`` so Vue/React-backed textareas pick the value up; a
        contenteditable fallback writes ``innerText``. The caller still reads
        the box back, so a write that cannot stick is reported as an error.
        """
        box.evaluate(
            """(el, value) => {
              const tag = (el.tagName || '').toLowerCase();
              if (tag === 'input' || tag === 'textarea') {
                const proto = tag === 'textarea'
                  ? HTMLTextAreaElement.prototype
                  : HTMLInputElement.prototype;
                const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                if (desc && desc.set) desc.set.call(el, value);
                else el.value = value;
              } else {
                el.innerText = value;
              }
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            value,
        )

    def _answer_blank(self, page: Page, text: str,
                      stem_text: str = "", occurrence: int = 0,
                      expected: int = 0) -> None:
        """Fill every blank of a fill-blank question and verify each one.

        ``text`` is newline-separated when the question has several blanks;
        one plain string when it has one. Each value goes into its own box
        (in DOM order), and a value that does not stick raises instead of
        letting a partially filled question pass the submit gate.
        """
        values = solver.split_blanks(text, expected)
        if not values:
            raise UplusError("填空题答案为空，无法写入")
        scope = self._question_scope(page, stem_text, occurrence)
        boxes = self._blank_boxes(scope, expected)
        if not boxes:
            raise UplusError("填空题找不到答案输入框，无法写入")
        if len(boxes) < len(values):
            raise UplusError(
                f"填空题有 {len(values)} 个答案，但页面只找到 {len(boxes)} 个输入框"
            )
        if expected > 1 and len(values) != expected:
            raise UplusError(
                f"填空题需要 {expected} 个答案，只收到 {len(values)} 个"
            )
        for index, value in enumerate(values):
            box = boxes[index]
            landed = ""
            try:
                self._write_box(box, value)
                landed = self._read_box(box)
                if landed != value:
                    self._write_box(box, value)
                    landed = self._read_box(box)
            except Exception:  # noqa: BLE001 - try the DOM fallback below
                landed = ""
            if landed != value:
                try:
                    self._force_box_value(box, value)
                    landed = self._read_box(box)
                except Exception:  # noqa: BLE001 - report the mismatch below
                    landed = ""
            if landed != value:
                raise UplusError(
                    f"第 {index + 1} 空写入后页面内容不一致"
                    f"（期望 {value[:30]!r}，实际 {landed[:30]!r}）"
                )

    def navigate_to_question(self, page: Page, position: int,
                             expected_stem: str = "") -> bool:
        """Click the answer-card number for ``position`` and verify the page.

        The early return for position 1 was wrong: the previous stem walk
        leaves the page on the *last* question, so a question whose page
        position is 1 must still be navigated back. Filling on whatever page
        happened to be open would answer the wrong question — the reported
        "多选漏选 / 漏题" symptom.

        When ``expected_stem`` is given, the stem now shown is compared with
        it; a mismatch returns ``False`` so the caller skips the question
        instead of typing an answer into the wrong one.
        """
        card = page.locator(
            f"xpath=//*[normalize-space(text())='{position}']"
        ).first
        try:
            card.click(timeout=4000)
            time.sleep(0.3)
        except PWTimeout:
            if position > 1:
                return False
        if not expected_stem:
            return True
        saw_stem = False
        for attempt in range(3):
            current = self._page_stem(page)
            if current:
                saw_stem = True
                if stem_matches(current, expected_stem):
                    return True
            if attempt < 2:
                time.sleep(0.3)
        # A page whose stem cannot be read at all keeps the legacy behaviour
        # (proceed); a readable but different stem means the click landed on
        # the wrong question and the caller must skip it.
        return not saw_stem

    # ---- page position mapping (page order != API order) --------------- #
    def _single_question_view(self, page: Page) -> bool:
        """True when the page shows one question at a time with an answer card.

        Such pages carry a ``答题卡`` grid and navigate with ``上一题/下一题``;
        list-style pages render every question at once. One navigation label is
        enough: the first/last question can render only one of the two buttons,
        and a page with a single visible question must not be treated as a
        list page (that would fill every API question into the one visible
        question card).
        """
        try:
            text = page.inner_text("body")
        except PWTimeout:
            return False
        return "答题卡" in text or "上一题" in text or "下一题" in text

    def _page_stem(self, page: Page) -> str:
        """The stem of the question currently shown (first rich-text block)."""
        try:
            return text_key(page.locator(".ck-content").first.inner_text())
        except Exception:  # page shape unknown: treat as unreadable, never crash
            return ""

    def page_stem_positions(self, page: Page, total: int) -> dict[str, int]:
        """Map every question stem on a single-question page to its position.

        Walks the page sequentially with ``下一题`` (clicking the answer-card
        number is unreliable: it can land before the SPA repaints, which silently
        drops a question from the map) and re-reads the stem when it comes back
        empty, which happens while the transition animation is still running.

        Duplicate stems (identical rich-text content) are disambiguated by
        appending a positional suffix, e.g. ``"题干文本::2"``, so every
        question gets a distinct position.
        """
        positions: dict[str, int] = {}
        stem_counts: dict[str, int] = {}  # track how many times each stem appears
        try:
            page.locator("xpath=//*[normalize-space(text())='1']").first.click(timeout=4000)
            self._await_stem_ready(page, "", timeout_ms=4000)
        except Exception:
            return positions

        for index in range(1, max(total, 1) + 1):
            stem = self._read_stem_after_transition(page)
            if stem:
                count = stem_counts.get(stem, 0) + 1
                stem_counts[stem] = count
                key = f"{stem}::{count}" if count > 1 or stem in positions else stem
                positions[key] = index
            if index >= total:
                break
            try:
                nxt = page.get_by_role("button", name="下一题")
                if nxt.count() == 0 or nxt.first.is_disabled():
                    break
                nxt.first.click(timeout=5000)
            except Exception:
                break
            self._await_stem_ready(page, stem, timeout_ms=4000)
        return positions

    def _read_stem_after_transition(self, page: Page) -> str:
        """Read the current question stem, retrying while the SPA repaints.

        The page briefly renders an empty stem while the question transition
        animation runs, so a single read can come back blank. This polls with a
        short interval and gives up after a bounded number of tries, matching
        the previous behaviour (three attempts, blank tolerated) without a
        fixed sleep on every single question.
        """
        for attempt in range(3):
            try:
                stem = self._page_stem(page)
            except Exception:
                stem = ""
            if stem:
                return stem
            if attempt < 2:
                time.sleep(0.15)
        return ""

    def _await_stem_ready(self, page: Page, previous: str, *,
                          timeout_ms: int = 4000) -> None:
        """Wait until the visible stem has settled after a navigation.

        After clicking the answer-card number or 下一题, the page keeps showing
        the *previous* stem until the SPA repaints. Waiting for the stem to
        change (or simply to become readable when ``previous`` is empty) returns
        as soon as the question is actually on screen, instead of always paying a
        fixed ``time.sleep(1.0)`` per question — a 30-question homework used to
        burn ~30 s of pure waiting here. The budget is bounded so a page that
        never repaints still falls through with the old tolerance.
        """
        deadline = time.time() + max(timeout_ms, 1) / 1000
        last = ""
        while time.time() < deadline:
            try:
                last = self._page_stem(page)
            except Exception:
                last = ""
            if last and last != previous:
                return
            time.sleep(0.12)
        # Fall through: the caller still tolerates a blank/unreadable stem.

    def _page_position(self, positions: dict[str, int], stem: str,
                       fallback: int, used: set[int] | None = None) -> Optional[int]:
        if not positions:
            return fallback
        return stem_position(positions, stem, used)

    def fill_question(self, page: Page, question: Question, answer: str,
                      *, single_view: bool = True, occurrence: int = 0) -> str:
        """Fill one question on the current page; return the normalized answer.

        ``single_view`` controls whether option/blank/judge locators are scoped
        to the question container (list-view pages) or left unscoped (single-
        question pages where only one question is visible). ``occurrence``
        selects the n-th identical stem on list-view pages.

        Every objective type is verified against the page after clicking:
        a multi-select is reconciled with the *actual* checked boxes (clicking a
        box that is already checked would uncheck it — the reported "漏选"),
        and a single choice / judge / blank is re-read once. A question that
        still does not match its target raises, so the submit gate refuses to
        hand in a wrong answer instead of silently losing options.
        """
        normalized = solver.normalize_answer(question, answer)
        qtype = question.type
        stem = "" if single_view else question.stem
        if qtype is QuestionType.SINGLE:
            self._click_option(page, normalized, stem_text=stem,
                               occurrence=occurrence)
            actual = self._checked_letters(page, stem, occurrence)
            if actual is not None and normalized not in actual:
                self._click_option(page, normalized, stem_text=stem,
                                   occurrence=occurrence)
                actual = self._checked_letters(page, stem, occurrence)
                if actual is not None and normalized not in actual:
                    shown = "".join(sorted(actual)) or "空"
                    raise UplusError(
                        f"单选题点选后页面显示 {shown}，目标 {normalized}，未确认选中"
                    )
            return normalized
        if qtype is QuestionType.MULTIPLE:
            target = set(normalized)
            actual = self._checked_letters(page, stem, occurrence)
            if actual is None:
                # Unverifiable layout: fall back to the API-side delta.
                current = set(utils.normalize_letters(question.my_answer or ""))
                for old in sorted(current - target):
                    self._click_option(page, old, stem_text=stem,
                                       occurrence=occurrence)
                for new in sorted(target - current):
                    self._click_option(page, new, stem_text=stem,
                                       occurrence=occurrence)
                return normalized
            for _ in range(2):
                for code in sorted(actual - target):
                    self._click_option(page, code, stem_text=stem,
                                       occurrence=occurrence)
                for code in sorted(target - actual):
                    self._click_option(page, code, stem_text=stem,
                                       occurrence=occurrence)
                reread = self._checked_letters(page, stem, occurrence)
                if reread is None:
                    # The DOM went away mid-verification. ``None`` is not a
                    # confirmation of success (the "None == no answer"
                    # convention elsewhere in the fill path): give it one more
                    # read before deciding, so a transient detach cannot mask
                    # an incomplete selection.
                    reread = self._checked_letters(page, stem, occurrence)
                if reread == target:
                    return normalized
                actual = reread or set()
            raise UplusError(
                f"多选题点选后页面显示 {''.join(sorted(actual)) or '空'}"
                f"，目标 {normalized}，选项未能全部选中"
            )
        if qtype is QuestionType.JUDGE:
            self._answer_judge(page, normalized == "true", stem_text=stem,
                               occurrence=occurrence)
            return normalized
        if qtype is QuestionType.FILL_BLANK:
            self._answer_blank(page, normalized, stem_text=stem,
                               occurrence=occurrence,
                               expected=models.count_blanks(question.stem))
            return normalized
        raise UplusError(f"题型 {qtype.label} 不支持自动作答")

    # ------------------------------------------------------------------ #
    # figure questions: screenshot the question block for the model
    # ------------------------------------------------------------------ #
    def _image_output_path(self, raw: str, order: int) -> Path:
        """Resolve where a question screenshot may be written.

        An explicit ``raw`` path is confined to the configured IO roots like
        every other file tool. Without one, the file goes under the report
        folder's ``question_images/``; if that folder is not an allowed root,
        the first configured root is used instead.
        """
        if raw:
            path = utils.resolve_io_path(raw, self.config.io_roots)
            path.parent.mkdir(parents=True, exist_ok=True)
            return path
        stamp = time.strftime("%Y%m%d_%H%M%S")
        name = f"question_{order or 'x'}_{stamp}.png"
        candidates = [self.config.report_folder / "question_images"]
        candidates.extend(Path(root) / "question_images" for root in self.config.io_roots)
        for folder in candidates:
            try:
                safe = utils.resolve_io_path(folder, self.config.io_roots)
            except (PermissionError, OSError):
                continue
            try:
                safe.mkdir(parents=True, exist_ok=True)
            except OSError:
                continue
            return safe / name
        raise UplusError(
            "没有可写的目录用于保存题目截图，请在配置的 allowed_io_paths / "
            "UPLUS_ALLOWED_PATHS 中加入一个可写目录"
        )

    def capture_question(self, page: Page, question: Question, total: int,
                         output_path: str = "", full_page: bool = False,
                         occurrence: int = 0) -> Path:
        """Screenshot one question block (diagram included) and return the path.

        Figure questions (``以下模型是…`` with a picture) cannot be answered
        from text: ``strip_html`` removes the ``<img>``, so the model needs to
        *see* the rendered block. The page is navigated to the question by stem
        first — the same rule the fill loop uses — and the question container
        is captured rather than the whole viewport, so the diagram is readable.

        ``occurrence`` selects the n-th exact duplicate when several questions
        share the same stem (mirrors ``fill_question``): without it, a list-view
        page with duplicate stems would always capture the first copy.
        """
        if self._single_question_view(page):
            # Fast path: the page may already show the requested question.
            current = self._page_stem(page)
            if not (current and stem_matches(current, question.stem)):
                positions = self.page_stem_positions(page, max(total, 1))
                page_pos = self._page_position(positions, question.stem, question.order)
                if page_pos is None:
                    raise UplusError(
                        f"第{question.order}题在页面上找不到对应题干，无法定位题目截图"
                    )
                if not self.navigate_to_question(page, page_pos, question.stem):
                    raise UplusError(
                        f"第{question.order}题跳转后题干校验不一致，未截图（避免截到别的题）"
                    )
        path = self._image_output_path(output_path, question.order)
        scope = self._narrowed_scope(page, question.stem, occurrence)
        try:
            if scope is not None and not full_page:
                scope.scroll_into_view_if_needed(timeout=4000)
                time.sleep(0.3)
                scope.screenshot(path=str(path))
            else:
                # No question container recognised, or the caller asked for
                # the full page: capture everything so the figure is never
                # cropped out.
                page.screenshot(path=str(path), full_page=True)
        except PWTimeout as exc:
            raise UplusError(f"题目截图失败：{exc}") from exc
        return path

    def capture_answer_page(self, task: HomeworkTask,
                            output_path: str = "") -> Path:
        """Screenshot the whole homework answer page.

        Used by the two-phase flow to "进入页面后截屏审查" an incomplete
        homework: unlike ``capture_question`` (which navigates to a single
        question by stem and needs an ``order``), this opens the answer page and
        captures everything in one full-page shot, so the reviewer can see every
        unfilled box at once. ``output_path`` is confined to the IO roots, like
        the question capture path.
        """
        page = self.open_homework(task, settle=True)
        path = self._image_output_path(output_path, order=0)
        try:
            page.screenshot(path=str(path), full_page=True)
        except PWTimeout as exc:
            raise UplusError(f"作答页整页截图失败：{exc}") from exc
        return path

    def read_current_my_answer(self, page: Page) -> Optional[str]:
        try:
            text = page.locator("text=我的答案").first.locator("xpath=following-sibling::*[1]").inner_text()
            return text.strip() or None
        except PWTimeout:
            return None

    # ------------------------------------------------------------------ #
    # full homework flow
    # ------------------------------------------------------------------ #
    def answer_homework(
        self,
        task: HomeworkTask,
        answers: dict[str, str],
        *,
        submit: bool = True,
        force_submit: bool = False,
        allow_subjective_skips: bool = False,
        course_name: str = "",
    ) -> dict:
        """Open a homework, fill provided objective answers, verify and submit.

        ``answers`` maps ``question_id`` (or question order number as string) to
        the normalized answer. Unsupported questions are collected in ``skipped``;
        already-submitted homework is left untouched. Submission only happens when
        every objective question was answered without errors, unless
        ``force_submit=True``.
        """
        outcome: dict = {
            "task_id": task.task_id,
            "name": task.name,
            "course_name": course_name,
            "submitted": False,
            "answered": 0,
            "skipped": 0,
            "objective_total": 0,
            "objective_answered": 0,
            "can_submit": False,
            "subjective_skipped": 0,
            "unlocated_skips": 0,
            "skipped_questions": [],
            "errors": [],
            "note": "",
        }
        submitted_answers: dict[str, str] = {}
        page = self.open_homework(task)
        status, progress = self.read_page_state(page)
        if status is not None:
            task.page_status = status
        if progress is not None:
            task.page_progress = progress

        if status is HomeworkStatus.SUBMITTED_PENDING or status is HomeworkStatus.SUBMITTED_GRADED:
            outcome["note"] = "作业已提交，未重复作答"
            return outcome

        questions = self.client.list_questions(task.homework_id)
        outcome["total"] = len(questions)
        if not questions:
            # An empty question list must never satisfy 0 == 0 and hit submit;
            # it means a material-only task or an unreadable feed.
            outcome["note"] = "题目列表为空（纯材料/附件作业或接口未返回题目），不自动提交，请人工确认。"
            return outcome

        # Page order and API order disagree on single-question views, so the
        # position to click is resolved by stem instead of by ``detail.order``.
        single_view = self._single_question_view(page)
        positions = (
            self.page_stem_positions(page, len(questions)) if single_view else {}
        )
        used_positions: set[int] = set()
        # List-view pages render duplicate stems (same rich text) at once;
        # the n-th occurrence selects the right container for each.
        stem_occurrence: dict[str, int] = {}

        # Fetch every question detail up front, in one browser round-trip.
        # Reading them one at a time inside the loop cost one round-trip per
        # question, so a 30-question homework paid 30 sequential round-trips
        # before the first answer was typed. The details are the same data in
        # the same order — only the round-trips are batched.
        # Look the batched read up on the *class*, not the instance: a test
        # double (``Mock``) auto-creates any attribute asked of it, so an
        # instance lookup would find a stub instead of the real
        # implementation. The call itself still goes through the instance.
        if getattr(type(self.client), "question_details", None) is None:
            details = [
                self.client.question_detail(q.question_id) for q in questions
            ]
        else:
            details = list(
                self.client.question_details([q.question_id for q in questions])
            )

        def record_skip(detail: Question, reason: str, *,
                        blocking: bool = True,
                        subjective: bool = False) -> None:
            """Park a question that cannot be answered and keep going.

            ``blocking`` separates "this question is genuinely unfinished"
            (no answer yet, subjective work) from "this question could not be
            located on the page". A page-mapping failure is the second kind:
            it is a page-shape problem, not missing work, so it is reported but
            never allowed to veto the rest of the homework. Both kinds are
            listed in ``skipped_questions`` so nothing disappears silently.
            """
            outcome["skipped"] += 1
            outcome["skipped_questions"].append({
                "question_id": detail.question_id,
                "order": detail.order,
                "type": detail.type.label,
                "stem": detail.stem[:120],
                "subjective": subjective,
                "blocking": blocking,
                "reason": reason,
            })

        for detail in details:
            occurrence = 0
            if not single_view:
                key = text_key(detail.stem)
                occurrence = stem_occurrence.get(key, 0)
                stem_occurrence[key] = occurrence + 1
            if single_view:
                # An empty map (the stem walk produced nothing) keeps the legacy
                # API-order fallback; the stem verification below is what
                # actually prevents a wrong-question write, and it runs on this
                # path too.
                page_pos = self._page_position(positions, detail.stem,
                                               detail.order, used=used_positions)
                if page_pos is None:
                    record_skip(
                        detail,
                        "在页面上找不到对应题干，已跳过（不冒险点错题）",
                        blocking=False,
                    )
                    continue
                # Verify the page really shows this stem before typing: the
                # answer-card click can silently miss, and filling whatever
                # question is open would answer the wrong one.
                if not self.navigate_to_question(page, page_pos, detail.stem):
                    record_skip(
                        detail,
                        "跳转后题干校验不一致，已跳过（不冒险写错题）",
                        blocking=False,
                    )
                    continue
            # Every objective question counts toward the total, including ones
            # the platform already records as answered; otherwise previously
            # answered questions inflate answered without inflating total and
            # the equality can never balance again.
            if detail.auto_answerable:
                outcome["objective_total"] += 1
            if detail.auto_answerable and detail.my_answer:
                # Snapshot the answer state that will be submitted; the audit
                # uses it later when the platform still hides its own answer.
                submitted_answers[detail.question_id] = detail.my_answer
            # A multiple-choice question that is already "answered" may still be
            # incomplete (e.g. the student picked A,B but the model says A,B,C,D).
            # Re-fill it if a model answer is available and differs from the
            # current selection.  Other question types (single / judge / blank)
            # that carry a real answer are genuinely complete and can be skipped.
            if detail.answered and detail.my_answer:
                need_refill = False
                unverifiable_partial = False
                if detail.type is QuestionType.MULTIPLE:
                    raw_answer = answers.get(detail.question_id) or answers.get(str(detail.order))
                    if raw_answer is not None:
                        target = set(solver.normalize_answer(detail, raw_answer))
                        current = set(utils.normalize_letters(detail.my_answer or ""))
                        if target != current:
                            need_refill = True
                    else:
                        # No model answer for this one: the recorded answer may
                        # be a partial multi-select (exactly the "漏选" failure),
                        # so it cannot be verified and must not pass silently.
                        unverifiable_partial = True
                elif detail.type is QuestionType.FILL_BLANK:
                    # A fill-blank with several blanks can be half filled: the
                    # recorded answer then carries fewer values than the stem
                    # has blanks (the reported "填空题答不上" symptom). Refill
                    # it when the model has an answer, and otherwise refuse to
                    # call it complete.
                    blanks = models.count_blanks(detail.stem)
                    if blanks > 1:
                        filled = len(solver.split_blanks(detail.my_answer, blanks))
                        if filled < blanks:
                            raw_answer = (answers.get(detail.question_id)
                                          or answers.get(str(detail.order)))
                            if raw_answer is not None:
                                need_refill = True
                            else:
                                unverifiable_partial = True
                if not need_refill:
                    if unverifiable_partial:
                        if detail.type is QuestionType.FILL_BLANK:
                            reason = (
                                f"填空题已答 {detail.my_answer}，但缺少模型答案，"
                                "无法补齐剩余空"
                            )
                        else:
                            reason = (
                                f"多选已答 {detail.my_answer}，但缺少模型答案，"
                                "无法核对是否漏选"
                            )
                        record_skip(detail, reason)
                    else:
                        outcome["answered"] += 1
                        if detail.auto_answerable:
                            outcome["objective_answered"] += 1
                    continue
            if not detail.auto_answerable:
                record_skip(detail, "主观题/编程题/附件题，需本人完成",
                            subjective=True)
                continue
            raw_answer = answers.get(detail.question_id) or answers.get(str(detail.order))
            if raw_answer is None:
                record_skip(detail, "缺少模型答案，待人工/LLM 补充")
                continue
            try:
                normalized = self.fill_question(page, detail, raw_answer,
                                               single_view=single_view,
                                               occurrence=occurrence)
                detail.my_answer = normalized
                detail.answered = True
                submitted_answers[detail.question_id] = normalized
                outcome["answered"] += 1
                outcome["objective_answered"] += 1
            except (UplusError, PWTimeout, ValueError) as exc:
                outcome["errors"].append(f"第{detail.order}题作答失败: {exc}")

        subjective_skips = sum(
            1 for item in outcome["skipped_questions"] if item.get("subjective")
        )
        # A question parked because the page could not be mapped is not missing
        # work: the answer was ready, the page refused to reveal the question.
        # It is reported in ``unlocated_skips`` and never counts against the
        # submit gate, so one unreadable page position cannot cost the whole
        # homework its submission.
        unlocated_skips = sum(
            1 for item in outcome["skipped_questions"]
            if item.get("blocking") is False
        )
        blocking_skips = outcome["skipped"] - unlocated_skips
        if allow_subjective_skips:
            blocking_skips -= subjective_skips
        outcome["subjective_skipped"] = subjective_skips
        outcome["unlocated_skips"] = unlocated_skips
        outcome["blocking_skips"] = blocking_skips
        # An unlocatable question already ``continue``d before
        # ``objective_total`` was counted, so answered and total stay
        # comparable: the gate compares the reachable questions only.
        outcome["can_submit"] = (
            not outcome["errors"]
            and blocking_skips == 0
            and outcome["objective_answered"] == outcome["objective_total"]
        )
        if submit and (outcome["can_submit"] or force_submit):
            self.submit_homework(page, task, outcome)
            if outcome["submitted"]:
                try:
                    journal.record(
                        self.config, task_id=task.task_id,
                        homework_id=task.homework_id, name=task.name,
                        answers=submitted_answers,
                    )
                except OSError as exc:
                    outcome["note"] += f"（本地答案记录写入失败：{exc}，不影响提交）"
        else:
            reasons = []
            if blocking_skips:
                reasons.append(f"{blocking_skips} 题未完成")
            if outcome["errors"]:
                reasons.append(f"{len(outcome['errors'])} 题作答出错")
            outcome["note"] = "存在未完成题目，已暂缓提交：" + "，".join(reasons) if reasons else "已填写，等待提交"
        return outcome

    def submit_homework(self, page: Page, task: HomeworkTask, outcome: dict) -> None:
        submit_btn = page.get_by_role("button", name="提交作业")
        if submit_btn.count() == 0:
            outcome["note"] = "未找到提交按钮"
            return
        submit_btn.first.click()
        time.sleep(0.8)
        # Confirmation dialog: "作业已经全部做完，是否提交?"
        confirm = page.locator(".el-message-box__btns .el-button--primary")
        try:
            if confirm.count():
                confirm.first.click(timeout=4000)
            else:
                page.get_by_role("button", name="确定").first.click(timeout=4000)
        except PWTimeout:
            outcome["errors"].append("提交确认弹窗未出现")
            return
        # Verify the button switched to 撤回作业.
        for _ in range(10):
            time.sleep(0.6)
            # Fast path: the cheap button probe answers "has it flipped?" with a
            # fraction of a full body read. ``None`` means inconclusive (page
            # still transitioning, or the probe is unavailable), so the full
            # read still runs — the loop's verdict is unchanged either way.
            probe = self._probe_submit_button(page)
            if probe is not None:
                if probe in (HomeworkStatus.SUBMITTED_PENDING,
                             HomeworkStatus.SUBMITTED_GRADED):
                    state, progress = self.read_page_state(page)
                    task.page_status = state or probe
                    task.page_progress = progress
                    outcome["submitted"] = True
                    outcome["note"] = "提交成功，待老师批改"
                    return
                continue
            state, progress = self.read_page_state(page)
            if state in (HomeworkStatus.SUBMITTED_PENDING, HomeworkStatus.SUBMITTED_GRADED):
                task.page_status = state
                task.page_progress = progress
                outcome["submitted"] = True
                outcome["note"] = "提交成功，待老师批改"
                return
        outcome["errors"].append("提交后页面状态未变为「撤回作业」，请人工复核")

    # ------------------------------------------------------------------ #
    # attachment upload
    # ------------------------------------------------------------------ #
    def upload_attachment(self, task: HomeworkTask, file_path: str) -> dict:
        path = utils.resolve_io_path(
            file_path, self.config.io_roots, must_exist=True)
        page = self.open_homework(task)
        status, _ = self.read_page_state(page)
        if status in (HomeworkStatus.SUBMITTED_PENDING, HomeworkStatus.SUBMITTED_GRADED):
            return {"task_id": task.task_id, "uploaded": False, "note": "作业已提交，如需修改请先撤回"}
        file_input = page.locator("input[type=file]").last
        file_input.set_input_files(str(path))
        time.sleep(1.5)
        return {
            "task_id": task.task_id,
            "uploaded": True,
            "file": str(path),
            "note": "附件已上传，仍需在作答页点击「提交作业」完成提交",
        }

    # ------------------------------------------------------------------ #
    # experiments
    # ------------------------------------------------------------------ #
    def _await_experiment_cards(self, page: Page) -> None:
        """Wait for the experiment card grid to paint; scroll through the page so
        lazily-rendered bottom cards mount before the snapshot."""
        try:
            page.wait_for_selector(".exp-item", timeout=self.config.action_timeout_ms)
        except PWTimeout:
            return
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except PWTimeout:
            pass
        page.evaluate("() => { window.scrollTo(0, document.body.scrollHeight); }")
        time.sleep(0.5)
        page.evaluate("() => window.scrollTo(0, 0)")
        time.sleep(0.3)

    def read_experiments(self, course: Course) -> list[Experiment]:
        safe_cid = utils.safe_url_segment(course.id, "course_id")
        page = self.session.open(self.EXPERIMENT_ROUTE.format(course_id=safe_cid))
        if "login" in page.url.lower():
            raise AuthExpiredError("U+ 登录态已过期，请重新登录。")
        self._await_experiment_cards(page)
        js = r"""
        () => {
          const rows = [...document.querySelectorAll('.exp-item')];
          const out = [];
          rows.forEach(row => {
            const flag = row.querySelector('.flag');
            const titleEl = row.querySelector('.exp-item-center-name');
            const title = titleEl ? titleEl.innerText.trim() : '';
            const text = row.innerText || '';
            let material = '', deadline = '';
            const m1 = text.match(/提交物[：:]\s*([^\n]+)/); if (m1) material = m1[1].trim();
            const m2 = text.match(/(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})/); if (m2) deadline = m2[1];
            // The detail route embeds both ids:
            //   expInfo/{exp_id}/stu_experiments/{stu_exp_id}/labsubmit
            // They are the only way from this list to the detail page, so the
            // list tool must carry them or the experiment flow dead-ends here.
            const link = row.querySelector('a[href*="expInfo"]');
            const href = link ? (link.getAttribute('href') || '') : '';
            const mm = href.match(/expInfo\/([a-zA-Z0-9_-]+)\/stu_experiments\/([a-zA-Z0-9_-]+)/);
            out.push({
              name: title,
              flag_text: flag ? (flag.innerText || '').trim() : null,
              flag_class: flag ? flag.className || '' : '',
              material, deadline,
              exp_id: mm ? mm[1] : '',
              stu_exp_id: mm ? mm[2] : '',
              raw: text.slice(0, 200)
            });
          });
          return out;
        }
        """
        raw_list = page.evaluate(js) or []
        # Poll once more only when the first read looks unfinished: bottom cards
        # finish painting their state badge after the first snapshot, so an
        # empty read or a card whose title is still blank is re-read. A first
        # read that already names every card is complete, and sleeping 0.8s to
        # re-read identical data only added a fixed cost to every course.
        def _incomplete(rows) -> bool:
            return (not rows
                    or any(not (r.get("name") or "").strip() for r in rows))

        if _incomplete(raw_list):
            time.sleep(0.8)
            later = page.evaluate(js) or []
            if len(later) >= len(raw_list):
                raw_list = later
        # Fallback: legacy text-based scan when the card layout changes. The
        # badge is only matched inside its own card subtree (no ancestor walk),
        # so a global status label cannot leak across cards.
        if not raw_list:
            raw_list = page.evaluate(r"""
            () => {
              const cards = [...document.querySelectorAll('div')].filter(d =>
                /^实验[一二三四五六七八九十0-9]/.test((d.innerText||'').trim()) &&
                d.innerText.includes('截止'));
              const out = []; const seen = new Set();
              cards.forEach(c => {
                const title = (c.innerText.split('\n')[0]||'').trim();
                if (seen.has(title)) return; seen.add(title);
                const badge = c.querySelector('.flag');
                const text = c.innerText;
                const m1 = text.match(/提交物[：:]\s*([^\n]+)/);
                const m2 = text.match(/(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})/);
                const link = c.querySelector('a[href*="expInfo"]');
                const href = link ? (link.getAttribute('href') || '') : '';
                const mm = href.match(/expInfo\/([a-zA-Z0-9_-]+)\/stu_experiments\/([a-zA-Z0-9_-]+)/);
                out.push({
                  name: title,
                  flag_text: badge ? (badge.innerText || '').trim() : null,
                  flag_class: badge ? badge.className || '' : '',
                  material: m1 ? m1[1].trim() : '',
                  deadline: m2 ? m2[1] : '',
                  exp_id: mm ? mm[1] : '',
                  stu_exp_id: mm ? mm[2] : '',
                  raw: text.slice(0,200)
                });
              });
              return out;
            }
            """) or []
        experiments: list[Experiment] = []
        for raw in raw_list:
            deadline_ms = None
            if raw.get("deadline"):
                deadline_ms = utils.parse_cn_datetime(raw["deadline"])
            experiments.append(
                Experiment(
                    name=raw["name"],
                    submitted=classify_experiment_flag(
                        raw.get("flag_text"), raw.get("flag_class") or ""
                    ),
                    deadline_ms=deadline_ms,
                    material=raw.get("material", ""),
                    raw_text=raw.get("raw", ""),
                    course_id=course.id,
                    exp_id=raw.get("exp_id", ""),
                    stu_exp_id=raw.get("stu_exp_id", ""),
                )
            )
        return experiments
