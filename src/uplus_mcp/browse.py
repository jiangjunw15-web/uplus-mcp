"""Generic "AI drives the web page" tools layered on the same browser session.

These mirror the capability of a browser-automation plugin: the model can open
a URL, read a compact page snapshot, click, type, scroll, take a screenshot and
run page JavaScript. They share ``BrowserSession`` with the U+ tools, so the
model can fall back to raw page driving whenever the platform's REST contract
changes or a task needs UI steps that have no API.

Every function here is plain synchronous Playwright code. The MCP server runs it
on ``BrowserThread`` (see ``worker.py``) so the asyncio loop never sees the sync
API directly.
"""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any

from playwright.sync_api import TimeoutError as PWTimeout

from .browser import BrowserSession, UplusError

# Keep snapshots small enough to be useful in a prompt.
MAX_NODES = 220
MAX_TEXT = 12_000


class BrowseTools:
    """Thin, explicit page-driving helpers around one attached page."""

    def __init__(self, session: BrowserSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ #
    # navigation
    # ------------------------------------------------------------------ #
    def open(self, url: str) -> dict:
        page = self.session.open(url)
        return {
            "url": page.url,
            "title": page.title(),
            "status": self._status(page),
        }

    def tabs(self) -> list[dict]:
        page = self.session.page
        out = []
        for p in self.session._context.pages if self.session._context else []:
            if p.is_closed():
                continue
            out.append({"url": p.url, "title": self._safe_title(p), "active": p is page})
        return out

    def switch_tab(self, index: int) -> dict:
        pages = [p for p in (self.session._context.pages if self.session._context else []) if not p.is_closed()]
        if not pages or index < 0 or index >= len(pages):
            raise UplusError(f"标签页索引越界：{index}（共 {len(pages)} 个）")
        self.session._page = pages[index]
        page = self.session.page
        return {"url": page.url, "title": self._safe_title(page)}

    def back(self) -> dict:
        page = self.session.page
        page.go_back(wait_until="domcontentloaded")
        time.sleep(0.4)
        return {"url": page.url, "title": self._safe_title(page)}

    # ------------------------------------------------------------------ #
    # reading
    # ------------------------------------------------------------------ #
    def snapshot(self, max_nodes: int = MAX_NODES) -> dict:
        """Compact interactive-element list plus visible text.

        Mirrors what a browser plugin's ``snapshot`` gives a model: stable
        selectors, roles, names, and enough text to decide the next action.
        """
        page = self.session.page
        script = """
        (maxNodes) => {
          const isVisible = (el) => {
            const r = el.getBoundingClientRect();
            if (!r || r.width === 0 || r.height === 0) return false;
            const s = getComputedStyle(el);
            return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
          };
          const selectorFor = (el) => {
            if (el.id) return '#' + CSS.escape(el.id);
            const testid = el.getAttribute('data-testid') || el.getAttribute('data-test-id');
            if (testid) return `[data-testid="${testid}"]`;
            const name = el.getAttribute('name');
            if (name && ['input','select','textarea'].includes(el.tagName.toLowerCase()))
              return `${el.tagName.toLowerCase()}[name="${name}"]`;
            // build a short structural path
            const parts = [];
            let cur = el, depth = 0;
            while (cur && cur.nodeType === 1 && depth < 4) {
              let part = cur.tagName.toLowerCase();
              const parent = cur.parentElement;
              if (parent) {
                const idx = [...parent.children].indexOf(cur) + 1;
                part += `:nth-child(${idx})`;
              }
              parts.unshift(part);
              cur = parent;
              depth++;
            }
            return parts.join(' > ');
          };
          const nodes = [];
          const sel = 'a,button,input,select,textarea,[role],[contenteditable="true"],[onclick]';
          for (const el of document.querySelectorAll(sel)) {
            if (!isVisible(el)) continue;
            const role = el.getAttribute('role') || el.tagName.toLowerCase();
            // Password fields never reveal their value, even without a label
            // or placeholder: the el.value fallback must not leak them.
            const label = (el.tagName === 'INPUT' && el.type === 'password')
              ? '••••'
              : (el.getAttribute('aria-label') || el.innerText ||
                 el.getAttribute('placeholder') || el.value || '').trim().slice(0, 60);
            nodes.push({
              selector: selectorFor(el),
              role,
              text: label,
              tag: el.tagName.toLowerCase(),
            });
            if (nodes.length >= maxNodes) break;
          }
          const bodyText = (document.body.innerText || '').replace(/\\n{3,}/g, '\\n\\n').trim();
          return {url: location.href, title: document.title, nodes, bodyText: bodyText.slice(0, 12000)};
        }
        """
        try:
            data = page.evaluate(script, max_nodes)
        except PWTimeout:
            raise UplusError("页面快照超时，页面可能仍在加载")
        data["bodyText"] = (data.get("bodyText") or "")[:MAX_TEXT]
        return data

    def get_text(self, selector: str = "body") -> dict:
        page = self.session.page
        try:
            text = page.inner_text(selector)
        except PWTimeout:
            raise UplusError(f"未找到元素：{selector}")
        return {"selector": selector, "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT}

    def eval_js(self, expression: str) -> Any:
        page = self.session.page
        result = page.evaluate(f"() => ({expression})")
        try:
            json.dumps(result)
            return result
        except TypeError:
            return str(result)[:2000]

    def screenshot(self, path: str = "", full_page: bool = False) -> dict:
        page = self.session.page
        if path:
            data = page.screenshot(path=path, full_page=full_page)
            return {"path": path, "bytes": len(data)}
        data = page.screenshot(full_page=full_page)
        return {
            "media_type": "image/png",
            "bytes": len(data),
            "data_base64": base64.b64encode(data).decode("ascii"),
        }

    # ------------------------------------------------------------------ #
    # acting
    # ------------------------------------------------------------------ #
    def click(self, selector: str = "", text: str = "", nth: int = 0) -> dict:
        page = self.session.page
        if selector:
            target = page.locator(selector).nth(nth)
        elif text:
            target = page.get_by_text(text, exact=False).nth(nth)
        else:
            raise UplusError("click 需要 selector 或 text 之一")
        target.scroll_into_view_if_needed(timeout=5000)
        target.click(timeout=8000)
        time.sleep(0.3)
        return {"url": page.url, "title": self._safe_title(page)}

    def fill(self, selector: str, value: str, press_enter: bool = False) -> dict:
        page = self.session.page
        box = page.locator(selector).first
        box.scroll_into_view_if_needed(timeout=5000)
        box.fill(value, timeout=8000)
        if press_enter:
            box.press("Enter")
            time.sleep(0.4)
        # Parity with the batch extract: a password field is never echoed back.
        if self._is_password(box):
            return {"filled": selector, "value": "••••"}
        return {"filled": selector, "value": value}

    @staticmethod
    def _is_password(box) -> bool:
        try:
            return bool(box.evaluate("el => el.type === 'password'"))
        except Exception:  # noqa: BLE001 - detached / non-input element
            return False

    def type_text(self, text: str, selector: str = "") -> dict:
        page = self.session.page
        if selector:
            page.locator(selector).first.type(text, delay=30)
        else:
            page.keyboard.type(text, delay=30)
        return {"typed": len(text)}

    def press(self, key: str) -> dict:
        page = self.session.page
        page.keyboard.press(key)
        time.sleep(0.3)
        return {"pressed": key, "url": page.url}

    def select_option(self, selector: str, value: str) -> dict:
        page = self.session.page
        page.locator(selector).first.select_option(value, timeout=8000)
        return {"selector": selector, "value": value}

    def scroll(self, amount: int = 800, selector: str = "") -> dict:
        page = self.session.page
        if selector:
            page.locator(selector).first.scroll_into_view_if_needed(timeout=5000)
        else:
            page.mouse.wheel(0, amount)
        time.sleep(0.3)
        return {"scrolled": amount if not selector else "into-view", "url": page.url}

    def wait_for(self, selector: str = "", text: str = "", timeout_ms: int = 15000) -> dict:
        page = self.session.page
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            try:
                if selector and page.locator(selector).count() > 0:
                    return {"found": "selector", "value": selector}
                if text and text in page.inner_text("body"):
                    return {"found": "text", "value": text}
            except Exception:  # noqa: BLE001 - page can be mid-navigation
                pass
            time.sleep(0.4)
        raise UplusError(f"等待超时：selector={selector!r} text={text!r}")

    def upload(self, selector: str, file_path: str) -> dict:
        page = self.session.page
        page.locator(selector).first.set_input_files(file_path, timeout=15000)
        time.sleep(0.8)
        return {"uploaded": file_path, "selector": selector}

    # ------------------------------------------------------------------ #
    # batched actions (one call, stop on failure or navigation)
    # ------------------------------------------------------------------ #
    _EXTRACT_FIELDS = ("text", "value", "href")

    def _locator(self, page, step: dict):
        selector = step.get("selector") or ""
        nth = int(step.get("nth", 0))
        if selector:
            return page.locator(selector).nth(nth)
        text = step.get("text") or ""
        if text:
            return page.get_by_text(text, exact=False).nth(nth)
        raise UplusError("动作需要 selector 或 text 之一")

    def _wait_step(self, page, step: dict) -> None:
        deadline = time.time() + int(step.get("timeout_ms", 15000)) / 1000
        selector = step.get("selector") or ""
        text = step.get("text") or ""
        while time.time() < deadline:
            try:
                if selector and page.locator(selector).count() > 0:
                    return
                if text and text in page.inner_text("body"):
                    return
            except Exception:  # noqa: BLE001 - page mid-navigation
                pass
            time.sleep(0.4)
        raise UplusError(f"等待超时：selector={selector!r} text={text!r}")

    def _extract(self, page, step: dict) -> dict:
        fields = step.get("fields") or ["text"]
        fields = [f for f in fields if f in self._EXTRACT_FIELDS]
        if not fields:
            raise UplusError("extract 的 fields 只能是 text / value / href")
        max_rows = max(1, min(20, int(step.get("maxRows", 10))))
        selector = step.get("selector") or ""
        nodes = page.locator(selector) if selector else page.locator("body")
        rows: list[dict] = []
        truncated = False
        count = nodes.count()
        for index in range(min(count, max_rows)):
            node = nodes.nth(index)
            row: dict[str, str] = {}
            for field in fields:
                if field == "text":
                    value = node.inner_text()
                elif field == "href":
                    value = node.get_attribute("href") or ""
                else:
                    # Never read back password fields (parity with a human form).
                    if node.evaluate("el => el.type === 'password'"):
                        value = ""
                    else:
                        value = node.input_value()
                text = str(value or "")
                text = re.sub(r"\s+", " ", text).strip()
                if len(text) > 200:
                    truncated = True
                    text = text[:200]
                row[field] = text
            rows.append(row)
        return {"rows": rows, "total": count,
                "omitted_rows": max(0, count - len(rows)),
                **({"truncated": True} if truncated else {})}

    def act_batch(self, steps: list[dict], observe: str = "summary") -> dict:
        """Run a short batch of page actions, stopping on failure or navigation.

        Supported ops: ``click`` (selector/text/nth), ``fill`` (selector,value,
        press_enter), ``press`` (key), ``hover`` (selector), ``scroll``
        (amount/selector), ``wait`` (selector/text/timeout_ms), ``extract``
        (selector,fields,maxRows), ``navigate`` (url), ``back``.

        Returns completed steps, the remaining count, extract rows and a
        failure/navigation receipt; ``observe="summary"`` skips the follow-up
        snapshot, ``observe="snapshot"`` reads the body text once afterwards.
        """
        page = self.session.page
        completed: list[dict] = []
        results: list[dict] = []
        failure: dict | None = None
        navigated = False
        for index, step in enumerate(steps):
            op = step.get("op") or ""
            try:
                before = page.url
                if op == "navigate":
                    page.goto(step["url"], wait_until="domcontentloaded")
                    time.sleep(0.4)
                elif op == "back":
                    page.go_back(wait_until="domcontentloaded")
                    time.sleep(0.3)
                elif op == "click":
                    target = self._locator(page, step)
                    target.scroll_into_view_if_needed(timeout=5000)
                    target.click(timeout=8000)
                    time.sleep(0.3)
                elif op == "fill":
                    target = page.locator(step["selector"]).nth(int(step.get("nth", 0)))
                    target.scroll_into_view_if_needed(timeout=5000)
                    target.fill(step.get("value", ""), timeout=8000)
                    if step.get("press_enter"):
                        target.press("Enter")
                        time.sleep(0.4)
                elif op == "press":
                    page.keyboard.press(step["key"])
                    time.sleep(0.2)
                elif op == "hover":
                    self._locator(page, step).hover(timeout=5000)
                elif op == "scroll":
                    if step.get("selector"):
                        page.locator(step["selector"]).first.scroll_into_view_if_needed(timeout=5000)
                    else:
                        page.mouse.wheel(0, int(step.get("amount", 600)))
                    time.sleep(0.2)
                elif op == "wait":
                    self._wait_step(page, step)
                elif op == "extract":
                    results.append(self._extract(page, step))
                else:
                    raise UplusError(f"未知动作: {op}")
                completed.append({"index": index, "op": op})
                if page.url != before:
                    navigated = True
                    break
            except PWTimeout:
                failure = {"index": index, "op": op, "error": "操作超时，页面可能仍在加载"}
                break
            except Exception as exc:  # noqa: BLE001 - receipt keeps the batch honest
                failure = {"index": index, "op": op, "error": str(exc)[:200]}
                break

        out: dict = {
            "completed": completed,
            "remaining": len(steps) - len(completed),
            "observe": observe,
        }
        if results:
            out["results"] = results
        if failure:
            out["failure"] = failure
            out["retry"] = "先读取当前页面状态，不要重复已完成的动作。"
        if navigated:
            out["navigated"] = True
            out["stopped"] = "页面发生导航；继续之前先重新读取新页面。"
        if observe == "snapshot":
            out["read"] = page.inner_text("body")[:1600]
        return out

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _safe_title(page) -> str:
        try:
            return page.title()
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _status(page) -> int:
        try:
            resp = page.evaluate(
                "() => (performance.getEntriesByType('navigation')[0] || {}).responseStatus || 0"
            )
            return int(resp or 0)
        except Exception:  # noqa: BLE001
            return 0
