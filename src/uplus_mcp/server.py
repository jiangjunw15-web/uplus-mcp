"""FastMCP server exposing U+ patrol tools **and** generic browser-driving tools.

Two tool families share one attached browser session:

U+ tools (``uplus_*``)
    Patrol courses, read homework/experiment state, fill objective answers, submit,
    upload an attachment. Read paths use the platform REST API; write paths drive
    the real answer page.

Browser tools (``browse_*``)
    Let the model operate any page directly: open URL, snapshot, click, fill, type,
    press, scroll, screenshot, run JS, switch tabs. This is the fallback when the
    platform contract changes or a step has no API.

Threading model
---------------
FastMCP calls sync tools on the asyncio loop thread, where Playwright's sync API
refuses to run. Every browser operation therefore runs on one dedicated worker
thread (``worker.BrowserThread``); tools are async and await that thread.

Run locally:
    python -m uplus_mcp.server
Or use the installed console script ``uplus-mcp``.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import CallToolResult, TextContent

from .browse import BrowseTools
from .browser import BrowserSession
from .config import Config, ConfigError, load_config
from .engine import PatrolEngine
from .launcher import cdp_alive, launch_browser_with_cdp, first_live_port
from .operator import HomeworkOperator
from .platform import PlatformClient
from .worker import BrowserThread

mcp = FastMCP("uplus-mcp")

# Lazy singletons. Everything Playwright touches is created on the worker thread.
_state: dict[str, Any] = {}
_thread = BrowserThread()


def _config() -> Config:
    """The single config instance every tool shares.

    Loading it twice would let the file tools and the engine disagree about
    the allowed IO roots (a path refused by one tool and accepted by the
    other), so the parsed config is cached here and reset by ``uplus_close``.
    """
    cfg = _state.get("config")
    if cfg is None:
        cfg = load_config()
        _state["config"] = cfg
    return cfg


def _ensure_engine() -> PatrolEngine:
    """Create the engine on the *worker* thread (Playwright thread affinity)."""
    if not _state.get("ready"):
        cfg = _config()
        session = BrowserSession(cfg)
        _state["session"] = session
        _state["client"] = PlatformClient(session, cfg)
        _state["operator"] = HomeworkOperator(session, _state["client"], cfg)
        _state["browse"] = BrowseTools(session)
        _state["engine"] = PatrolEngine(
            cfg, session, _state["client"], _state["operator"]
        )
        _state["ready"] = True
    return _state["engine"]


def _dispatch(name: str, *args: Any, **kwargs: Any):
    """Return a zero-arg callable that runs one operation on the worker thread.

    The engine is created inside the callable so it is constructed on the worker
    thread (Playwright objects must be created and used on the same thread).
    """
    def work() -> Any:
        _ensure_engine()
        if name.startswith("browse."):
            func = getattr(_state["browse"], name.split(".", 1)[1])
        else:
            func = getattr(_state["engine"], name)
        return func(*args, **kwargs)

    return work


# --------------------------------------------------------------------------- #
# U+ tools
# --------------------------------------------------------------------------- #
@mcp.tool()
async def uplus_check_login() -> dict:
    """Check whether the attached browser session is signed into U+.

    Waits for a just-finished login to settle, validates a protected read, and
    reports the attached ``account``. When the personal config sets
    ``account`` / ``UPLUS_ACCOUNT``, ``account_match`` says whether the session
    belongs to it; a mismatch makes every account-bound tool refuse to act.
    """
    return await _thread.run_async(_dispatch("check_login"))


@mcp.tool()
async def uplus_login(
    owner: str = "",
    method: str = "",
    username: str = "",
    password: str = "",
    school: str = "",
    wait_timeout_ms: int = 120000,
) -> dict:
    """Sign in to U+ by asking the user three questions, in this order. Never
    assume a login method and never pass credentials the user has not given.

    Call with no arguments first: the result carries ``stage: "owner"`` and two
    options (自己登录 / 代登录). Show that question to the user, then call again
    with ``owner``.

    * ``stage: "method"`` — three options: 学号/工号登录 (``student_id``),
      短信验证码登录 (``sms``), 手机号/账号密码登录 (``password``). Show the
      options and let the user pick; nothing is defaulted.
    * ``stage: "credentials"`` — the fields that method needs. ``student_id``
      asks 学校 + 学号/工号 + 密码; ``password`` asks 手机号或账号 + 密码.
      Show the fields as a form, then call again with those values.
    * ``stage: "manual_sms"`` — the browser is parked on the 短信登录 tab and
      nothing is filled: the user types the phone number and the verification
      code in the browser themselves. Verify later with ``uplus_check_login``.

    Only ``student_id`` and ``password`` are auto-filled and submitted.
    Credentials are used for this call only: never persisted to config, reports
    or logs, and never echoed back in the result.
    """
    return await _thread.run_async(
        _dispatch("login", owner=owner, method=method, username=username,
                  password=password, school=school,
                  wait_timeout_ms=wait_timeout_ms)
    )


@mcp.tool()
async def uplus_logout(confirm: bool = False) -> dict:
    """Sign out the attached browser profile after explicit confirmation.

    Call with ``confirm=True`` only when the user has asked to end the current
    U+ session. Uses the platform's own sign-out action and verifies the result;
    it does not silently discard a local token or close other browser profiles.
    Use independent profiles and CDP ports for separate accounts.
    """
    return await _thread.run_async(_dispatch("logout", confirm=confirm))


@mcp.tool()
async def uplus_list_courses() -> list[dict]:
    """List every course attached to the signed-in account."""
    return await _thread.run_async(_dispatch("list_courses"))


@mcp.tool()
async def uplus_list_homeworks(course_id: str = "", refresh_page_state: bool = False) -> list[dict]:
    """List homework status in a stable course-major order.

    Omit ``course_id`` to read every active course in a fixed order (configured
    courses keep their config order; a live feed is sorted by course name + id),
    and each course's tasks are sorted by due date and title. For a complete
    per-course read (homeworks then experiments) use ``uplus_scan_course``
    instead of mixing this tool with the experiment tools.

    ``refresh_page_state=True`` opens the answer page for API-unsubmitted,
    non-expired homework (the rows the interface under-reports and the answer
    flow may act on). Expired rows and API-submitted rows with a known
    ``subStatus`` keep the API status and carry ``status_source="api"``;
    ``uplus_audit_homework`` remains the authoritative per-homework page read.
    Page reads are cached briefly, so a patrol followed by an audit does not
    reload the same page. ``progress=None`` means the counter is unavailable,
    never "unanswered".
"""
    return await _thread.run_async(
        _dispatch("list_homeworks", course_id, refresh_page_state=refresh_page_state)
    )


@mcp.tool()
async def uplus_list_experiments(course_id: str, verify: bool = True) -> list[dict]:
    """List one course's experiments in 实验一/二/… order.

    ``verify=True`` opens each experiment detail page to confirm 已提交/未提交
    (the list badge has been wrong on the live platform). ``verify=False`` reads
    the list page only — much faster, one page load for the whole course.
    """
    return await _thread.run_async(
        _dispatch("list_experiments", course_id, verify=verify)
    )


@mcp.tool()
async def uplus_scan_course(
    course_id: str,
    refresh_page_state: bool = True,
    verify_experiments: bool = False,
) -> dict:
    """Read one course end-to-end: all homeworks first, then experiments.

    This is the efficient course-by-course path. Call it for one course, finish
    reading (and answering) that course, then call it for the next. The browser
    stays on the current course and does not jump between courses; homeworks are
    sorted by due date/title and experiments by 实验一/二/… order.

    ``refresh_page_state`` (default True) reads every homework's answer button
    and reconciles it with the API, including API-submitted and expired rows.
    ``verify_experiments``(default False) keeps the fast list badge for experiments; set it True to
    open every experiment detail page and confirm its submitted state.
    """
    return await _thread.run_async(
        _dispatch("scan_course", course_id,
                  refresh_page_state=refresh_page_state,
                  verify_experiments=verify_experiments)
    )


@mcp.tool()
async def uplus_read_experiment_detail(course_id: str, exp_id: str, stu_exp_id: str) -> dict:
    """Read experiment detail: requirements, material, deadline, submitted files.

    Read-only: this tool opens the experiment page and reports what it says.
    It never fills, uploads or submits anything.
    """
    from .experiment import ExperimentOperator
    def work() -> dict:
        _ensure_engine()
        op = ExperimentOperator(_state["engine"].config, _state["engine"].session)
        detail = op.read_detail(course_id, exp_id, stu_exp_id)
        return {
            "name": detail.name,
            "status": detail.status,
            "environment": detail.environment,
            "material": detail.material,
            "deadline": detail.deadline,
            "submitted_files": detail.submitted_files,
            "attachments": detail.attachments,
            "manual_text": detail.manual_text[:2000],
            # Tabs that could not be read. An empty attachments list is
            # otherwise ambiguous: none on the page, or unreachable. When this
            # map is non-empty, use the browse_* tools to look at the page.
            "tab_errors": dict(detail.tab_errors),
        }
    return await _thread.run_async(work)


@mcp.tool()
async def uplus_download_experiment_materials(
    course_id: str, exp_id: str, stu_exp_id: str
) -> dict:
    """Download an experiment's materials from **all** sources.

    Downloads every attachment from the 实验附件 tab, every file link from
    the 实验手册 tab, and every matching file from the course 资料 section
    (recursing into folders). The best template across all sources is
    identified and reported in ``template`` / ``template_source``.

    Read-only against the platform — nothing is filled, uploaded or submitted.
    Use it after ``uplus_list_experiments`` (which returns ``exp_id`` /
    ``stu_exp_id``) when you want the requirement files and the template on
    disk. Files go to ``reports/experiment_materials/``.
    """
    return await _thread.run_async(
        _dispatch("download_experiment_materials", course_id, exp_id, stu_exp_id)
    )


@mcp.tool()
async def uplus_inspect_homework(task_id: str, course_id: str) -> dict:
    """Return homework status plus a structured brief of its questions.

    The model reads the brief and returns an ``answers`` map to
    ``uplus_answer_homework``.
    """
    return await _thread.run_async(_dispatch("inspect_homework", task_id, course_id))


@mcp.tool()
async def uplus_answer_homework(
    task_id: str,
    course_id: str,
    answers: dict[str, str] | None = None,
    submit: bool = True,
    force_submit: bool = False,
    reuse: bool = True,
) -> dict:
    """Fill objective questions and optionally submit.

    ``answers`` maps ``question_id`` (or question order as a string) to a
    normalized answer: one letter, a sorted letter group, ``true``/``false``, or
    blank text. A fill-blank question with several blanks (``blank_count`` in the
    inspect brief) takes one value per blank, newline-separated in stem order.

    ``reuse=True`` (default) fills objective questions you did not answer from
    answers recorded for the same ``question_id`` by any account — the patrol
    result's ``reuse_answers`` block lists exactly what is available. Your own
    answers always win, and every reused question is named in
    ``reused_questions``. Reuse is a convenience, not a correctness proof.

    Submission is refused while any question is unanswered or failed unless
    ``force_submit=True``. If the personal config pins ``account`` /
    ``UPLUS_ACCOUNT`` and the attached browser session belongs to another
    account, the tool refuses to fill or submit anything.
    """
    return await _thread.run_async(
        _dispatch("answer_homework", task_id, course_id, answers or {},
                  submit=submit, force_submit=force_submit, reuse=reuse)
    )


@mcp.tool()
async def uplus_repair_homework(
    task_id: str,
    course_id: str,
    answers: dict[str, str] | None = None,
    verify_unjudged: bool = False,
    submit: bool = True,
    reuse: bool = True,
) -> dict:
    """Withdraw a submitted homework, fill its gaps, and resubmit it.

    Use after ``uplus_patrol`` (audit is on by default) reports a homework as
    ``missing`` (漏答) or ``partial`` (多选漏选). ``answers`` maps
    ``question_id`` (or question order as a string) to the corrected answer;
    the patrol result's ``repair_briefs`` lists exactly which questions need
    one. Answers already stored in the local journal (because this tool
    submitted the homework earlier) are used automatically, so a
    journal-covered partial can be repaired without new model answers.
    ``reuse=True`` additionally falls back to answers recorded for the same
    ``question_id`` on another account and reports them in
    ``reused_questions``.

    The tool refuses to withdraw until every known gap has an answer. A graded
    homework cannot be withdrawn and is reported without changing anything.
    Existing answers are preserved and re-filled, so a withdrawal that clears
    the draft still leaves enough answers to resubmit. Submission uses the same
    gate as ``uplus_answer_homework``; subjective items never block the repair.
    ``verify_unjudged=True`` additionally re-answers multi-selects whose
    completeness could not be checked while the homework was pending grading.
    """
    return await _thread.run_async(
        _dispatch("repair_homework", task_id, course_id, answers or {},
                  verify_unjudged=verify_unjudged, submit=submit, reuse=reuse)
    )


@mcp.tool()
async def uplus_capture_question_image(
    task_id: str,
    course_id: str,
    order: int = 0,
    question_id: str = "",
    output_path: str = "",
    full_page: bool = False,
) -> CallToolResult:
    """Screenshot one question block and return it as an image.

    Use for *figure questions* — a stem whose diagram/picture carries the part
    that makes the statement true or false (for example "以下模型是…" with a
    crypto diagram). The question text alone cannot be answered reliably, and
    the model cannot see an ``<img>`` that lives in the platform's rich text.

    The tool opens the answer page, navigates to the question by its stem
    (same rule as filling), and captures that question's container — not just
    the viewport — so the whole figure is readable. It fills and submits
    nothing. Identify the question with ``order`` (the number shown on the
    page) or ``question_id``. The result is the saved PNG path plus the image
    itself; answer the question after looking at it and pass the answer to
    ``uplus_answer_homework`` keyed by ``question_id``.

    ``output_path`` is optional and confined to the configured IO roots; by
    default the PNG goes under the report folder's ``question_images/``.
    Set ``full_page=True`` when the figure sits outside the question block
    (for example a shared table above the questions).
    """
    def work() -> dict:
        _ensure_engine()
        return _state["engine"].capture_question_image(
            task_id, course_id, order=order, question_id=question_id,
            output_path=output_path, full_page=full_page,
        )

    info = await _thread.run_async(work)
    return CallToolResult(content=[
        TextContent(type="text", text=json.dumps(info, ensure_ascii=False, indent=2)),
        Image(path=info["path"]).to_image_content(),
    ])


@mcp.tool()
async def uplus_audit_homework(task_id: str, course_id: str,
                               refresh_page_state: bool = True) -> dict:
    """Read-only completeness audit for one homework (no withdraw, no submit).

    Every question is read from the student-side detail, so an already
    submitted homework can be audited safely. A question counts as answered
    only when its ``userAnswer`` is non-empty; the platform creates an answer
    row (``hwAnswerId`` / ``isAnswer``) when a question is merely opened, so
    those fields alone prove nothing. The result lists which objective
    questions carry no answer (``missing``) and, when the platform has graded
    the homework and exposed its standard answer (``answer``), which
    multi-selects were submitted with only part of the correct options
    (``partial``, with the omitted letters). A multi-select still awaiting
    grading has no reference answer and is listed in ``unjudged`` instead of
    being silently treated as complete. Subjective / programming / attachment
    items are counted separately and never make the homework "incomplete".
    ``complete`` means no missing and no partial multi-select; ``clean`` also
    requires checked answers to be correct. Fix a gap with
    ``uplus_repair_homework`` (it withdraws only after every gap has an
    answer, then completes and resubmits).

    The API status understates (a handed-in homework may read as not submitted),
    so ``withdrawable`` comes from the page's real button state; set
    ``refresh_page_state=False`` to skip that page read and trust the API.

    The page button and the interface can disagree — the platform's answer rows
    sometimes lag behind what the student filled on the page. The result reports
    both sides (``api_says_submitted`` / ``page_says_submitted``) and, when they
    disagree, lists the reason in ``state_conflicts`` (plus a ``note``). Treat
    the page as authoritative for "is it handed in", and read the answer page
    itself before acting on a ``missing`` verdict: it may be stale. When
    ``state_conflicts`` is non-empty, do not report the gap to the user as fact
    without saying it comes from the interface.
    """
    return await _thread.run_async(
        _dispatch("audit_homework", task_id, course_id,
                  refresh_page_state=refresh_page_state)
    )


@mcp.tool()
async def uplus_upload_attachment(task_id: str, course_id: str, file_path: str) -> dict:
    """Attach a local file (PPT/PDF/ZIP) to a homework that requires an upload."""
    return await _thread.run_async(
        _dispatch("upload_attachment", task_id, course_id, file_path)
    )


@mcp.tool()
async def uplus_patrol(auto_answer: bool = False, submit: bool = False,
                       audit: bool = True) -> dict:
    """Patrol every configured course and write a Markdown report.

    The traversal is deterministic and course-major: all of one course's
    homeworks are read first (sorted by due date/title), then that course's
    experiments (实验一/二/…), then the next course. Use ``uplus_scan_course``
    when you want just one course at a time.

    Lists homework and experiments, separates submitted / pending / outstanding /
    takeover items, and collects answer briefs for outstanding objective
    questions. ``submit=True`` only submits homework that received valid answers;
    subjective, programming and file items are never auto-submitted. Page reads
    are limited to rows whose verdict needs them (API-unsubmitted, non-expired
    homework, plus rows whose API ``subStatus`` cannot classify them) and are
    cached for the session; each row says which side its status came from in
    ``status_source``.

    ``audit`` is on by default: every already-submitted homework is re-checked
    for unanswered objective questions (漏答) and for multi-selects submitted
    with only part of the correct options (漏选). The platform only reveals its
    standard answer after grading and hides it while a submission is pending;
    a local journal of answers this tool submitted fills that gap. Multi-selects
    with neither source are reported as "unjudged", never silently as complete.
    The audit itself is read-only. Results also include ``repair_briefs``: when
    a gap is found, answer the listed questions and call
    ``uplus_repair_homework`` to withdraw, complete and resubmit the homework.
    Set ``audit=False`` only to skip this extra read pass.
    """
    return await _thread.run_async(
        _dispatch("patrol", auto_answer=auto_answer, submit=submit, audit=audit)
    )


@mcp.tool()
async def uplus_finish_then_review(
    answers: dict[str, dict[str, str]] | None = None,
    submit: bool = False,
    audit: bool = True,
    screenshot: bool = False,
    reuse: bool = True,
) -> dict:
    """Two-phase homework workflow: finish every outstanding homework first,
    then review the whole account once to judge whether anything is still
    incomplete.

    This is the "先做作业，全部写完后再总体检索" flow. Unlike ``uplus_patrol``
    (which interleaves per-task read/audit/answer), it deliberately separates
    the two concerns:

    Phase 1 — *finish*. Walk every active course in the fixed patrol order and
    answer each not-submitted, non-expired homework in turn. ``answers`` maps
    ``task_id`` → ``{question_id: answer}``; supply the answers you prepared
    (from ``uplus_inspect_homework``). Any objective question left without an
    answer is collected, never silently skipped. ``submit=True`` submits each
    homework whose objective questions are all answered; keep ``submit=False``
    to fill without submitting, then review before submitting.
    ``reuse=True`` (default) fills blanks you left out with answers recorded for
    the same ``question_id`` on any account; the ``finish`` log reports how many
    answers were reused per homework.

    Phase 2 — *review*. Only after the last homework has been attempted does
    this re-read every homework's real page state and run the same completeness
    audit ``uplus_patrol`` uses. The verdict ("还有没有未完成的作业") therefore
    reflects the post-fill state of the whole account, not a per-task snapshot
    taken mid-fill. ``screenshot=True`` additionally captures the answer page of
    each still-incomplete homework so you can see the unfilled boxes before
    deciding what to do; a path is returned per incomplete item under
    ``review[].screenshot``.

    Returns ``summary`` (finish_attempted / finish_submitted / review_total /
    review_incomplete / takeover_count), the per-homework ``finish`` log, the
    per-homework ``review`` rows (with ``complete`` flags), a ``takeover`` list
    tagged by ``phase`` ("finish" / "review"), and a ``report_path``. Use
    ``uplus_repair_homework`` to withdraw and complete any homework that the
    review still reports as incomplete.

    The page button and the interface can disagree (the platform's answer rows
    sometimes lag behind the page). Each ``review`` row therefore carries
    ``api_says_submitted`` / ``page_says_submitted`` and, when they disagree,
    ``state_conflicts`` naming why; the same conflicts are repeated in the
    matching ``takeover`` entry and in the written report under
    「页面状态与接口状态差异」. When ``state_conflicts`` is non-empty, do not
    report the gap to the user as fact — read the answer page (``screenshot``)
    first.
    """
    return await _thread.run_async(
        _dispatch("finish_then_review", answers, submit=submit, audit=audit,
                  screenshot=screenshot, reuse=reuse)
    )


@mcp.tool()
async def uplus_close() -> dict:
    """Detach from the browser and release the worker session."""
    def work() -> dict:
        session = _state.get("session")
        try:
            if session is not None:
                session.close()
        finally:
            # Even a failed detach must release the cached engine/session;
            # leaving ``ready`` set would hand the next tool a closed browser.
            _state.clear()
        return {"closed": True}

    return await _thread.run_async(work)


# --------------------------------------------------------------------------- #
# Browser tools: let the model drive the page directly
# --------------------------------------------------------------------------- #
@mcp.tool()
async def browse_launch_edge(port: int = 0, refresh_profile: bool = False) -> dict:
    """Launch an installed browser (Edge / Chrome) with a CDP debug port on a
    dedicated profile copy, then verify the port listens.

    The tool name keeps the original ``browse_launch_edge`` spelling for
    compatibility; it launches whichever engine the config ``browser`` field
    selects (``auto`` picks the first installed Edge or Chrome). ``port`` of
    ``0`` means "use the configured CDP port". A port given explicitly (or a
    ``cdp_port`` from the config / ``UPLUS_CDP_PORT``) is pinned: the tool
    reports whether *that* port is listening and never answers with a
    different live port, which in a multi-account setup could belong to
    another account. Only the implicit default keeps the 9222 fallback.
    Needed because Chromium 136+ ignores ``--remote-debugging-port`` on the
    default profile directory. Login cookies survive the copy, so U+ stays
    signed in.
    """
    def work() -> dict:
        cfg = _config()
        target_port = port if port > 0 else cfg.cdp_port
        pinned = port > 0 or bool(getattr(cfg, "cdp_port_explicit", False))
        if pinned:
            alive = cdp_alive(target_port)
            if alive:
                return {"already_running": True, "port": target_port,
                        "profile_dir": cfg.profile_dir or "",
                        "pinned": True}
            info = launch_browser_with_cdp(
                port=target_port,
                refresh_profile=refresh_profile,
                kind=cfg.browser,
                profile_root=cfg.profile_dir or None,
            )
            info["pinned"] = True
            return info
        live = first_live_port(target_port)
        if live:
            return {"already_running": True, "port": live, "pinned": False}
        info = launch_browser_with_cdp(
            port=target_port,
            refresh_profile=refresh_profile,
            kind=cfg.browser,
            profile_root=cfg.profile_dir or None,
        )
        info["pinned"] = False
        return info

    return await _thread.run_async(work)


@mcp.tool()
async def browse_open(url: str) -> dict:
    """Open a URL in the attached browser tab and return its title/status."""
    return await _thread.run_async(_dispatch("browse.open", url))


@mcp.tool()
async def browse_snapshot(max_nodes: int = 220) -> dict:
    """Return a compact snapshot: interactive elements (selector/role/text) + visible text."""
    return await _thread.run_async(_dispatch("browse.snapshot", max_nodes))


@mcp.tool()
async def browse_click(selector: str = "", text: str = "", nth: int = 0) -> dict:
    """Click an element by CSS selector or by visible text."""
    return await _thread.run_async(_dispatch("browse.click", selector, text, nth))


@mcp.tool()
async def browse_fill(selector: str, value: str, press_enter: bool = False) -> dict:
    """Replace an input's value, optionally pressing Enter afterwards."""
    return await _thread.run_async(_dispatch("browse.fill", selector, value, press_enter))


@mcp.tool()
async def browse_type(text: str, selector: str = "") -> dict:
    """Type text with a human-like delay into a focused/selected element."""
    return await _thread.run_async(_dispatch("browse.type_text", text, selector))


@mcp.tool()
async def browse_press(key: str) -> dict:
    """Press a key or combination, e.g. ``Enter`` or ``Control+A``."""
    return await _thread.run_async(_dispatch("browse.press", key))


@mcp.tool()
async def browse_scroll(amount: int = 800, selector: str = "") -> dict:
    """Scroll the page by pixels, or scroll a specific element into view."""
    return await _thread.run_async(_dispatch("browse.scroll", amount, selector))


@mcp.tool()
async def browse_get_text(selector: str = "body") -> dict:
    """Read the text content of an element (defaults to the whole body)."""
    return await _thread.run_async(_dispatch("browse.get_text", selector))


@mcp.tool()
async def browse_eval_js(expression: str) -> Any:
    """Evaluate a JavaScript expression in the page and return its JSON value."""
    return await _thread.run_async(_dispatch("browse.eval_js", expression))


@mcp.tool()
async def browse_screenshot(path: str = "", full_page: bool = False) -> dict:
    """Screenshot the page: returns a path, or base64 when no path is given."""
    return await _thread.run_async(_dispatch("browse.screenshot", path, full_page))


@mcp.tool()
async def browse_wait_for(selector: str = "", text: str = "", timeout_ms: int = 15000) -> dict:
    """Wait until a selector exists or text appears."""
    return await _thread.run_async(
        _dispatch("browse.wait_for", selector, text, timeout_ms)
    )


@mcp.tool()
async def browse_tabs() -> list[dict]:
    """List open tabs with url/title and which one is active."""
    return await _thread.run_async(_dispatch("browse.tabs"))


@mcp.tool()
async def browse_switch_tab(index: int) -> dict:
    """Switch the active tab by index from ``browse_tabs``."""
    return await _thread.run_async(_dispatch("browse.switch_tab", index))


@mcp.tool()
async def browse_back() -> dict:
    """Navigate back one history entry."""
    return await _thread.run_async(_dispatch("browse.back"))


@mcp.tool()
async def browse_upload(selector: str, file_path: str) -> dict:
    """Set files on an ``input[type=file]`` element."""
    return await _thread.run_async(_dispatch("browse.upload", selector, file_path))


@mcp.tool()
async def browse_select_option(selector: str, value: str) -> dict:
    """Choose an option in a ``<select>`` by value."""
    return await _thread.run_async(_dispatch("browse.select_option", selector, value))


@mcp.tool()
async def browse_act_batch(steps: list[dict], observe: str = "summary") -> dict:
    """Run one short batch of page actions; stop on failure or navigation.

    Each step supports: ``click`` (``selector``/``text``/``nth``), ``fill``
    (``selector``, ``value``, optional ``press_enter``), ``press`` (``key``),
    ``hover``, ``scroll`` (``amount``/``selector``), ``wait``
    (``selector``/``text``/``timeout_ms``), ``extract`` (``selector``,
    ``fields``: text/value/href, ``maxRows``), ``navigate`` (``url``), ``back``.

    Returns completed steps, remaining count, extract rows and a failure or
    navigation receipt. ``observe="summary"`` skips the follow-up snapshot;
    ``observe="snapshot"`` reads the body once afterwards. Sensitive or
    destructive actions need explicit user authorization; do not repeat a step
    whose receipt reports failure or navigation.
    """
    return await _thread.run_async(_dispatch("browse.act_batch", steps, observe=observe))


def main() -> None:
    # Fail fast on a broken config file: without this the server started
    # happily and only blew up on the first tool call, with a raw JSON error
    # that named neither the file nor the line.
    try:
        load_config()
    except ConfigError as exc:
        print(f"⚠️ {exc}", file=sys.stderr)
        raise SystemExit(2)
    mcp.run()


if __name__ == "__main__":
    main()
