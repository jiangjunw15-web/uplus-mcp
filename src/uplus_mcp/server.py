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

from typing import Any

from mcp.server.fastmcp import FastMCP

from .browse import BrowseTools
from .browser import BrowserSession
from .config import load_config
from .engine import PatrolEngine
from .launcher import launch_browser_with_cdp, first_live_port
from .operator import HomeworkOperator
from .platform import PlatformClient
from .worker import BrowserThread

mcp = FastMCP("uplus-mcp")

# Lazy singletons. Everything Playwright touches is created on the worker thread.
_state: dict[str, Any] = {}
_thread = BrowserThread()


def _ensure_engine() -> PatrolEngine:
    """Create the engine on the *worker* thread (Playwright thread affinity)."""
    if not _state.get("ready"):
        cfg = load_config()
        session = BrowserSession(cfg)
        _state["config"] = cfg
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
    """Check whether the attached browser session is signed into U+."""
    return await _thread.run_async(_dispatch("check_login"))


@mcp.tool()
async def uplus_login(
    mode: str = "manual",
    username: str = "",
    password: str = "",
    school: str = "",
    wait_timeout_ms: int = 120000,
) -> dict:
    """Sign in to U+ through the attached browser session. Ask the user which
    flow they prefer BEFORE calling:

    - ``mode="manual"``: open the portal and wait for the user to sign in by
      themselves in the visible browser window; no account or password is needed.
    - ``mode="auto"``: the model first asks the user for their account,
      password and school name, then fills the sign-in form, submits and
      verifies the session. Credentials are used only for this call: never
      persisted to config, reports or logs, and never echoed back. The wait is
      capped at 30 s so a failed auto fill hands over to ``manual`` quickly.
      If auto fill cannot find the fields, the school selector cannot be
      matched, or a captcha / SMS / wrong-password prompt appears, fall back to
      ``manual`` and hand the window to the user.

    Returns ``logged_in``, the chosen mode, current URL and token length.
    """
    return await _thread.run_async(
        _dispatch("login", mode, username=username, password=password,
                  school=school, wait_timeout_ms=wait_timeout_ms)
    )


@mcp.tool()
async def uplus_list_courses() -> list[dict]:
    """List every course attached to the signed-in account."""
    return await _thread.run_async(_dispatch("list_courses"))


@mcp.tool()
async def uplus_list_homeworks(course_id: str = "", refresh_page_state: bool = False) -> list[dict]:
    """List homework status for one course, or all courses when omitted.

    ``refresh_page_state=True`` opens each answer page to read the real submit
    button, because the REST status can lag behind the page.
    """
    return await _thread.run_async(
        _dispatch("list_homeworks", course_id, refresh_page_state=refresh_page_state)
    )


@mcp.tool()
async def uplus_list_experiments(course_id: str) -> list[dict]:
    """List experiments and their submission state for one course."""
    return await _thread.run_async(_dispatch("list_experiments", course_id))


@mcp.tool()
async def uplus_read_experiment_detail(course_id: str, exp_id: str, stu_exp_id: str) -> dict:
    """Read experiment detail: requirements, material, deadline, submitted files."""
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
        }
    return await _thread.run_async(work)


@mcp.tool()
async def uplus_generate_experiment_report(
    title: str,
    purpose: str,
    content: str,
    output_path: str,
    student_name: str = "",
    student_id: str = "",
) -> dict:
    """Generate a standard experiment report .docx file.

    ``output_path`` must stay inside the configured allowed directories
    (the report folder and the system temp dir by default; extend via
    ``allowed_io_paths`` in config or ``UPLUS_ALLOWED_PATHS``).
    """
    from pathlib import Path
    from . import utils
    from .report_generator import generate_algorithm_report
    safe_path = utils.resolve_io_path(
        output_path, load_config().io_roots)
    path = generate_algorithm_report(
        title=title,
        purpose=purpose,
        content=content,
        output_path=safe_path,
        student_name=student_name,
        student_id=student_id,
    )
    return {"generated": True, "path": str(path), "size": path.stat().st_size}


@mcp.tool()
async def uplus_submit_experiment_report(
    course_id: str,
    exp_id: str,
    stu_exp_id: str,
    file_path: str,
) -> dict:
    """Upload and submit an experiment report file.

    ``file_path`` must stay inside the configured allowed directories (see
    ``uplus_generate_experiment_report``).
    """
    from pathlib import Path
    from . import utils
    from .experiment import ExperimentOperator
    def work() -> dict:
        _ensure_engine()
        safe_path = utils.resolve_io_path(
            file_path, _state["engine"].config.io_roots, must_exist=True)
        op = ExperimentOperator(_state["engine"].config, _state["engine"].session)
        return op.upload_report(course_id, exp_id, stu_exp_id, safe_path)
    return await _thread.run_async(work)


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
    answers: dict[str, str],
    submit: bool = True,
    force_submit: bool = False,
) -> dict:
    """Fill objective questions and optionally submit.

    ``answers`` maps ``question_id`` (or question order as a string) to a
    normalized answer: one letter, a sorted letter group, ``true``/``false``, or
    blank text. Submission is refused while any question is unanswered or failed
    unless ``force_submit=True``.
    """
    return await _thread.run_async(
        _dispatch("answer_homework", task_id, course_id, answers,
                  submit=submit, force_submit=force_submit)
    )


@mcp.tool()
async def uplus_upload_attachment(task_id: str, course_id: str, file_path: str) -> dict:
    """Attach a local file (PPT/PDF/ZIP) to a homework that requires an upload."""
    return await _thread.run_async(
        _dispatch("upload_attachment", task_id, course_id, file_path)
    )


@mcp.tool()
async def uplus_patrol(auto_answer: bool = False, submit: bool = False) -> dict:
    """Patrol every configured course and write a Markdown report.

    Lists homework and experiments, separates submitted / pending / outstanding /
    takeover items, and collects answer briefs for outstanding objective
    questions. ``submit=True`` only submits homework that received valid answers;
    subjective, programming and file items are never auto-submitted.
    """
    return await _thread.run_async(_dispatch("patrol", auto_answer=auto_answer, submit=submit))


@mcp.tool()
async def uplus_close() -> dict:
    """Detach from the browser and release the worker session."""
    def work() -> dict:
        session = _state.get("session")
        if session is not None:
            session.close()
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
    ``0`` means "use the configured CDP port" (default 9333, fallback 9222).
    Needed because Chromium 136+ ignores ``--remote-debugging-port`` on the
    default profile directory. Login cookies survive the copy, so U+ stays
    signed in.
    """
    cfg = load_config()
    target_port = port if port > 0 else cfg.cdp_port
    live = first_live_port(target_port)
    if live:
        return {"already_running": True, "port": live}
    return await _thread.run_async(
        lambda: launch_browser_with_cdp(
            port=target_port,
            refresh_profile=refresh_profile,
            kind=cfg.browser,
            profile_root=cfg.profile_dir or None,
        )
    )


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
    mcp.run()


if __name__ == "__main__":
    main()
