# uplus-mcp

[![GitHub release](https://img.shields.io/github/v/release/jiangjunw15-web/uplus-mcp)](https://github.com/jiangjunw15-web/uplus-mcp/releases)
[![Release downloads](https://img.shields.io/github/downloads/jiangjunw15-web/uplus-mcp/total)](https://github.com/jiangjunw15-web/uplus-mcp/releases)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/github/license/jiangjunw15-web/uplus-mcp)](LICENSE)

> PyPI publication is pending: until `uplus-mcp` appears on PyPI, install from the
> GitHub Release wheel or clone the source (see Quick start below).

MCP server for the QingSoft U+ learning platform **with a built-in generic browser toolset**.

See [README.zh.md](README.zh.md) for the full Chinese guide, and [rename.md](rename.md)
for the step-by-step script an AI agent follows when onboarding a new user.

## What's new in 0.5.0

- **Cross-platform, machine-agnostic install**: the launcher now probes installed
  Microsoft Edge **or** Google Chrome on Windows, macOS and Linux, with a PATH
  fallback; no Windows-only executable path is hard-coded. The CDP port, browser
  engine and dedicated profile directory are configurable (`browser`, `cdp_port`,
  `profile_dir`, `auto_launch_browser`) and overridable through environment
  variables (`UPLUS_BROWSER`, `UPLUS_CDP_PORT`, `UPLUS_PROFILE_DIR`).
- **Generic browser wording**: the session attaches to whichever desktop browser
  the user has installed. The original tool name `browse_launch_edge` is kept for
  compatibility; it launches Edge or Chrome according to the config.
- **`rename.md` onboarding playbook**: every AI agent reads this before guiding a
  new user through environment check, install, client registration, two-mode sign-in,
  patrol, objective-question answering and human-takeover handoff.

### 0.4.2 — two-mode sign-in and batched page actions

- **Two-mode sign-in** (`uplus_login`): the model first asks the user whether to
  sign in manually (open the portal and wait for the user to finish in the browser
  window) or automatically (ask for account, password and school name, fill the
  school selector, account and password, submit and verify the session). Auto-mode
  credentials live only for that call and are never persisted to config, reports or
  logs, and never echoed back; missing fields, an unmatched school selector,
  captchas, SMS codes or wrong-password prompts fall back to manual takeover.
- **Batched page actions** (`browse_act_batch`): run a short serial batch of
  clicks, fills, presses, scrolls, waits, extracts and navigation; the batch
  stops on the first failure or navigation and returns a completed / remaining /
  failure / navigated receipt. Password-field values are never extracted back.
- **Attachment question type** (`qsnType=7`) and a three-state experiment status
  (submitted / outstanding / unknown) that hands unknown cards back for manual
  review; expired courses are skipped during patrol by default.

### 0.4.0 — merged browse toolset and stability fixes

- **Merged browse toolset**: 17 single-step `browse_*` tools let the model drive any
  web page directly (open, snapshot, click, fill, type, press, scroll, screenshot,
  eval JS, tabs) on the same attached browser session — the fallback when the
  platform's REST contract changes or a step has no API.
- **Fixed the asyncio crash**: FastMCP calls sync tools on the event-loop thread,
  where Playwright's sync API refuses to run. All browser work now runs on one
  dedicated worker thread (`worker.py`); tools are async and await it.
- **Fixed 401 on every read API**: U+ requires an `x-access-token` header (from
  `localStorage.AccessToken`) on each API call, not just cookies.
- **Fixed the debug port that never listens**: Edge/Chromium 136+ ignores
  `--remote-debugging-port` on the default user-data directory. `launcher.py`
  copies the profile to a dedicated directory once (login survives) and starts the
  browser there with the port; `browse_launch_edge` does it in one call.

An MCP server that patrols the [青软 U+](https://www.eduplus.net) learning
platform: it lists courses, homework and experiments, reads objective questions
into a structured brief, lets the connected MCP client model answer, fills the
answer page, verifies selections and submits. Subjective questions, programming
work, experiment reports and file attachments are explicitly handed back to the
human.

> **Academic integrity notice** — This tool is a personal study and reminder
> helper. Use it to review questions, track deadlines and organize your own work.
> Experiment reports, group deliverables, programming assignments and open-ended
> answers must be completed by yourself. Follow your school's academic honesty
> rules and course instructor instructions. Do not use this tool in exams,
> closed-book tests or any assessment that prohibits assistance.

## Features

- The session attaches to the user's own installed Edge or Chrome through CDP;
  the server stores no passwords, cookies or login snapshots in the repository.
- Read-only REST calls reuse the in-page session; page writes follow the real
  human path (option click, confirmation dialog, post-submit button verification).
- Objective questions (single choice, multiple choice, true/false, fill-in) can be
  filled and submitted after the client model returns a normalized answer map.
- Patrol report in Markdown: submitted / pending / outstanding homework,
  experiments, and a takeover list for manual work.
- Works with any MCP-compatible client: Claude Desktop, Cursor, VS Code MCP,
  Doubao agent, DSH and others.
- Unattended CLI for nightly scheduled patrol; it never answers or submits on its
  own.

## Architecture

```
MCP client  ──stdio──▶  server.py (FastMCP tools)
                              │
                          engine.py (orchestration)
              ┌───────────────┼────────────────┐
       platform.py       operator.py        report/solver
       read-only REST    page UI writes    reports + normalization
              └───────────────┼────────────────┘
                          browser.py (Playwright session)
```

See [`docs/architecture.md`](docs/architecture.md) for module responsibilities,
[`docs/api-mapping.md`](docs/api-mapping.md) for the captured U+ endpoints and
status rules, and [`docs/adapter-guide.md`](docs/adapter-guide.md) for adding
another platform.

## Quick start

### 1. Install

**From a GitHub Release wheel (recommended, no clone needed):**

1. Open the [Releases page](https://github.com/jiangjunw15-web/uplus-mcp/releases)
   and download the `uplus_mcp-<version>-py3-none-any.whl` asset attached to the
   latest release.
2. Install that downloaded wheel:

```bash
pip install <download-path>/uplus_mcp-0.5.0-py3-none-any.whl
```

This provides the `uplus-mcp` and `uplus-patrol` console scripts. Python 3.10+
is required; the server attaches to an installed Edge or Chrome over CDP, so no
bundled Chromium download is required for the normal flow.

**From source (development or latest unreleased code):**

> Replace `<install-dir>` with the absolute path of the folder containing
> `pyproject.toml` (this repository root).

**Windows PowerShell:**

```powershell
cd <install-dir>
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
```

**macOS / Linux:**

```bash
cd <install-dir>
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

### 2. Configure personal courses (gitignored, optional)

**Windows PowerShell:**

```powershell
Copy-Item config\courses.example.json config\courses.json
```

**macOS / Linux:**

```bash
cp config/courses.example.json config/courses.json
```

Edit `config/courses.json` with your actual `courseId` values and local paths.
Leaving `courses` empty makes the server fetch the live course list after sign-in.

### 3. Sign in

Ask the MCP client to run `uplus_login`:

- `mode="manual"`: the portal opens in the attached browser window; finish
  signing in yourself, and the tool waits until the session token appears.
- `mode="auto"`: the model first asks for your account, password and school
  name, fills the school selector, account and password fields, submits and
  verifies the session. Credentials are used only for that call and are never
  saved or echoed back. If the form cannot be found, the school selector cannot
  be matched, or a captcha / SMS code / wrong-password prompt appears, fall back
  to manual mode.

If the debug port is not listening yet, call `browse_launch_edge` once; it copies
the browser profile to a dedicated directory and starts Edge or Chrome with the
port.

### 4. Register the server in your MCP client

See [`docs/mcp-clients.md`](docs/mcp-clients.md) for Claude Desktop, Cursor and VS
Code examples.

**Installed from the Release wheel (or editable source): register the console script directly:**

```json
{
  "mcpServers": {
    "uplus": {
      "command": "uplus-mcp",
      "env": { "UPLUS_CONFIG": "config/courses.json" }
    }
  }
}
```

**Installed from source (Windows example; macOS/Linux use
`<install-dir>/.venv/bin/python`):**

```json
{
  "mcpServers": {
    "uplus": {
      "command": "<install-dir>\\.venv\\Scripts\\python.exe",
      "args": ["-m", "uplus_mcp.server"],
      "cwd": "<install-dir>",
      "env": { "UPLUS_CONFIG": "config/courses.json" }
    }
  }
}
```

## Tools

### U+ workflow

| Tool | Description |
| --- | --- |
| `uplus_login` | Sign in by `manual` takeover or `auto` fill (account, password, school); ask the user which mode first |
| `uplus_check_login` | Verify whether the attached browser session is signed in |
| `uplus_list_courses` | List courses under the signed-in account |
| `uplus_list_homeworks` | List homework status; open answer pages to verify button truth on demand |
| `uplus_list_experiments` | List experiments and submission state (submitted / outstanding / unknown) |
| `uplus_inspect_homework` | Return status and a structured question brief for the client model |
| `uplus_answer_homework` | Fill objective questions and optionally submit |
| `uplus_upload_attachment` | Attach a local file to a homework that requires upload |
| `uplus_patrol` | Full patrol, Markdown report, answer briefs and takeover list |
| `uplus_close` | Release the browser session held by this process |

### Generic browser (`browse_*`)

Single-step tools: `browse_launch_edge` (kept for compatibility; launches Edge or
Chrome per config), `browse_open`, `browse_snapshot`, `browse_get_text`,
`browse_click`, `browse_fill`, `browse_type`, `browse_press`, `browse_scroll`,
`browse_select_option`, `browse_upload`, `browse_screenshot`, `browse_eval_js`,
`browse_wait_for`, `browse_tabs`, `browse_switch_tab`, `browse_back`.

`browse_act_batch` runs a short serial batch of those actions: click, fill,
press, hover, scroll, wait, extract, navigate, back. The batch stops on the first
failure or navigation and returns a completed / remaining / failure / navigated
receipt; `observe="summary"` skips the follow-up snapshot. Password fields are
never extracted back.

## Nightly patrol (unattended)

**Windows:**

```powershell
.venv\Scripts\python.exe -m uplus_mcp.daily_patrol
```

**macOS / Linux:**

```bash
.venv/bin/python -m uplus_mcp.daily_patrol
```

The CLI writes `reports/patrol_YYYYMMDD_HHMM.md` and a `.brief.json` for
outstanding objective questions. Exit code `2` means the login session expired;
exit code `1` means a runtime failure. Register this command in Windows Task
Scheduler, macOS `launchd`, or Linux cron (e.g. daily 21:30).

> The CLI never submits homework. Answer and submission happen through an MCP
> session after the model reviews the brief.

## GitHub Packages

A GitHub Actions workflow publishes a metadata-only Apache Maven package to this
repository's Packages registry on every `v*` tag push (and can be run manually
from the Actions tab). The workflow uses the built-in `GITHUB_TOKEN`, so no
personal access token is required to publish.

```
maven-package/
  pom.xml              metadata-only Maven POM (com.github.jiangjunw15-web:uplus-mcp)
  artifacts/           wheel and sdist attached as supplementary files (gitignored)
.github/workflows/publish-packages.yml   build distributions and run `mvn deploy`
```

**Important:** the Maven entry only registers the package in the repository's
Packages panel. It is **not** a Python distribution and cannot be installed with
`pip`. Python users install from the Release wheel or the source checkout:

- Console script after a wheel/editable install: `uplus-mcp`
- Nightly patrol CLI: `uplus-patrol` (or `python -m uplus_mcp.daily_patrol`)

## Configuration reference

`config/courses.json`:

| Field | Default | Meaning |
| --- | --- | --- |
| `base_url` | `https://www.eduplus.net` | Platform root |
| `nav_timeout_ms` | `30000` | Page navigation timeout |
| `action_timeout_ms` | `15000` | UI action timeout |
| `deadline_warning_hours` | `72` | Highlight deadlines inside this window |
| `report_dir` | `reports` | Markdown report output folder |
| `auto_launch_browser` | `true` | Copy the profile and launch a browser with the CDP port when no port listens; legacy `auto_launch_edge` is read as an alias |
| `browser` | `auto` | `auto` / `edge` / `chrome` / `chromium`; `auto` picks the first installed Edge or Chrome |
| `cdp_port` | `9333` | Preferred CDP port; 9222 is the fallback; env `UPLUS_CDP_PORT` overrides |
| `profile_dir` | user-home dedicated directory | Dedicated browser profile directory; env `UPLUS_PROFILE_DIR` overrides |
| `include_expired_courses` | `false` | Skip expired courses during patrol; set `true` to keep an archived view |
| `courses` | empty | `id` / `name` / `teacher` per course; empty falls back to the live API list |

## Development

```bash
pip install -e ".[dev]"
pytest
```

- 73 unit tests cover model parsing, config loading, answer normalization, page
  state rules, experiment classification, the two-mode login flow, the batch
  action receipt, cross-platform browser detection and config compatibility.
- Browser and platform interactions are exercised against a real session in
  read-only patrol mode; write actions require the authorized account and an
  active course deadline.

## Repository layout

```
src/uplus_mcp/
  browser.py      CDP-attached Playwright session + in-page fetch
  browse.py       generic single-step and batched page actions
  platform.py     read-only REST client
  operator.py     page UI operations
  engine.py       shared orchestration
  models.py       domain objects and enums
  config.py       configuration loader
  solver.py       brief generation + answer normalization
  report.py       patrol report aggregation and Markdown render
  utils.py        HTML cleaning, time and answer normalization helpers
  worker.py       dedicated thread that runs all Playwright sync calls
  launcher.py     locate / launch Edge or Chrome with the debug port
  server.py       FastMCP tools
  daily_patrol.py unattended CLI
scripts/smoke_mcp.py stdio end-to-end smoke check
docs/             architecture, API mapping, client config, adapter guide
config/           personal config example (real file gitignored)
reports/          generated patrol reports (gitignored)
tests/            unit tests
```

## Safety and privacy

- Keep `config/courses.json` and `reports/` local; they are listed in `.gitignore`.
- Do not commit student IDs, names, phone numbers, email addresses, cookies or
  tokens.
- The server never persists your password; `uplus_login` auto-mode uses
  credentials only for the current call and never writes them to config, reports
  or logs or echoes them back in tool results.

## License

MIT, see [`LICENSE`](LICENSE).
