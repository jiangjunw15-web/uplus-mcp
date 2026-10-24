"""FastMCP server exposing U+ patrol, inspection, answering and upload tools.

Run locally:
    python -m uplus_mcp.server
Or install the package and use the ``uplus-mcp`` console script.

Tool list
---------
uplus_check_login
uplus_list_courses
uplus_list_homeworks
uplus_list_experiments
uplus_inspect_homework
uplus_answer_homework
uplus_upload_attachment
uplus_patrol
uplus_close
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .browser import BrowserSession
from .config import load_config
from .engine import PatrolEngine
from .operator import HomeworkOperator
from .platform import PlatformClient

mcp = FastMCP("uplus-mcp")

# Lazy singleton state: the browser only starts on the first tool call.
_state: dict = {}


def _engine() -> PatrolEngine:
    if not _state.get("ready"):
        cfg = load_config()
        session = BrowserSession(cfg)
        client = PlatformClient(session, cfg)
        operator = HomeworkOperator(session, client, cfg)
        _state.update(ready=True, config=cfg, session=session,
                      client=client, engine=PatrolEngine(cfg, session, client, operator))
    return _state["engine"]


# --------------------------------------------------------------------------- #
# tools
# --------------------------------------------------------------------------- #
@mcp.tool()
def uplus_check_login() -> dict:
    """Check whether the persisted U+ login session is still valid."""
    return _engine().check_login()


@mcp.tool()
def uplus_list_courses() -> list[dict]:
    """List the courses attached to the signed-in account."""
    return _engine().list_courses()


@mcp.tool()
def uplus_list_homeworks(course_id: str = "", refresh_page_state: bool = False) -> list[dict]:
    """List homeworks for one course, or every configured course when omitted.

    ``refresh_page_state=True`` opens each answer page to read the real button
    label; use it when API status looks ambiguous.
    """
    return _engine().list_homeworks(course_id, refresh_page_state=refresh_page_state)


@mcp.tool()
def uplus_list_experiments(course_id: str) -> list[dict]:
    """List experiments and their submission state for one course."""
    return _engine().list_experiments(course_id)


@mcp.tool()
def uplus_inspect_homework(task_id: str, course_id: str) -> dict:
    """Open a homework and return its status plus a structured question brief.

    The MCP client model uses the brief to produce the ``answers`` map passed to
    ``uplus_answer_homework``.
    """
    return _engine().inspect_homework(task_id, course_id)


@mcp.tool()
def uplus_answer_homework(
    task_id: str,
    course_id: str,
    answers: dict[str, str],
    submit: bool = True,
    force_submit: bool = False,
) -> dict:
    """Fill objective questions using the provided answers and optionally submit.

    ``answers`` maps ``question_id`` (or question order number as string) to the
    normalized answer: single letter, letter combination, true/false, or blank text.
    Submission is blocked when questions remain unanswered or errored; pass
    ``force_submit=True`` only after reviewing the outcome deliberately.
    """
    return _engine().answer_homework(
        task_id, course_id, answers, submit=submit, force_submit=force_submit
    )


@mcp.tool()
def uplus_upload_attachment(task_id: str, course_id: str, file_path: str) -> dict:
    """Attach a local file (PPT/PDF/ZIP/etc.) to a homework that requires upload."""
    return _engine().upload_attachment(task_id, course_id, file_path)


@mcp.tool()
def uplus_patrol(auto_answer: bool = False, submit: bool = False) -> dict:
    """Run a full patrol over configured courses and save a Markdown report.

    - Lists every homework and experiment; distinguishes submitted / pending /
      outstanding / takeover items.
    - Outstanding objective questions are collected into an answer brief for the
      MCP client model. With ``auto_answer=True`` built-in rules are tried first
      (none are bundled by default); unanswerable items stay in the takeover list.
    - ``submit=True`` only submits homework that received valid answers; file and
      subjective items are never auto-submitted.
    """
    return _engine().patrol(auto_answer=auto_answer, submit=submit)


@mcp.tool()
def uplus_close() -> dict:
    """Close the browser session held by this server process."""
    if _state.get("session"):
        _state["session"].close()
    _state.clear()
    return {"closed": True}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
