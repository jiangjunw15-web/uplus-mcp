# uplus-mcp

MCP server for the QingSoft U+ learning platform **with a built-in generic browser toolset**.

See [README.zh.md](README.zh.md) for the full Chinese guide (step-by-step install, run, and push-back-to-GitHub instructions).

## What's new in 0.4.2

- **Two-mode sign-in** (`uplus_login`): the model first asks the user whether to
  sign in manually (open the portal and wait for the user to finish in the Edge
  window) or automatically (ask for account, password and school name, fill the
  school selector, account and password, submit and verify the session). Auto-mode
  credentials live only for that call and are never persisted to config, reports
  or logs, and never echoed back; missing fields, an unmatched school selector,
  captchas, SMS codes or wrong-password prompts fall back to manual takeover.
- **Batched page actions** (`browse_act_batch`): run a short serial batch of
  clicks, fills, presses, scrolls, waits, extracts and navigation; the batch
  stops on the first failure or navigation and returns a
  completed / remaining / failure / navigated receipt. Password-field values
  are never extracted back. The original single-step `browse_*` tools are
  unchanged.
- **Attachment question type** (`qsnType=7`) and a three-state experiment
  status (submitted / outstanding / unknown) that hands unknown cards back for
  manual review; expired courses are skipped during patrol by default.

### Earlier: 0.4.0

- **Merged browse toolset**: 17 single-step `browse_*` tools let the model drive any web
  page directly (open, snapshot, click, fill, type, press, scroll, screenshot,
  eval JS, tabs) on the same attached Edge session — the fallback when the
  platform's REST contract changes or a step has no API.
- **Fixed the asyncio crash**: FastMCP calls sync tools on the event-loop
  thread, where Playwright's sync API refuses to run. All browser work now runs
  on one dedicated worker thread (`worker.py`); tools are async and await it.
- **Fixed 401 on every read API**: U+ requires an `x-access-token` header
  (from `localStorage.AccessToken`) on each API call, not just cookies.
- **Fixed the Edge debug port that never listens**: Edge/Chromium 136+ ignores
  `--remote-debugging-port` on the default user-data directory. `launcher.py`
  copies the profile to a dedicated directory once (login survives) and starts
  Edge there with the port; `browse_launch_edge` does it in one call.

An MCP server that patrols the [青软 U+](https://www.eduplus.net) learning platform: it lists courses, homework and experiments, reads objective questions into a structured brief, lets the connected MCP client model answer, fills the answer page, verifies selections and submits. Subjective questions, programming work, experiment reports and file attachments are explicitly handed back to the human.

> **Academic integrity notice** — This tool is a personal study and reminder helper. Use it to review questions, track deadlines and organize your own work. Experiment reports, group deliverables, programming assignments and open-ended answers must be completed by yourself. Follow your school's academic honesty rules and course instructor instructions. Do not use this tool in exams, closed-book tests or any assessment that prohibits assistance.

## Features

- The session attaches to the user's own Edge through CDP; the server stores no
  passwords, cookies or login snapshots in the repository.
- Read-only REST calls reuse the in-page session; page writes follow the real human path (option click, confirmation dialog, post-submit button verification).
- Objective questions (single choice, multiple choice, true/false, fill-in) can be filled and submitted after the client model returns a normalized answer map.
- Patrol report in Markdown: submitted / pending / outstanding homework, experiments, and a takeover list for manual work.
- Works with any MCP-compatible client: Claude Desktop, Cursor, VS Code MCP, Doubao agent, and others.
- Unattended CLI for nightly scheduled patrol; it never answers or submits on its own.

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

See [`docs/architecture.md`](docs/architecture.md) for module responsibilities, [`docs/api-mapping.md`](docs/api-mapping.md) for the captured U+ endpoints and status rules, and [`docs/adapter-guide.md`](docs/adapter-guide.md) for adding another platform.

## Quick start

### 1. Install

```powershell
cd D:\u+小助手
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

The server attaches to your own Edge over CDP; no bundled Chromium download is
required.

### 2. Configure personal courses (gitignored)

```powershell
Copy-Item config\courses.example.json config\courses.json
```

Edit `config/courses.json` with your actual `courseId` values and local paths.

### 3. Sign in

Ask the MCP client to run `uplus_login`:

- `mode="manual"`: the portal opens in the attached Edge window; finish signing
  in yourself, and the tool waits until the session token appears.
- `mode="auto"`: the model first asks for your account, password and school
  name, fills the school selector, account and password fields, submits and
  verifies the session. Credentials are used only for that call and are never
  saved or echoed back. If the form cannot be found, the school selector cannot
  be matched, or a captcha / SMS code / wrong-password prompt appears, fall back
  to manual mode.

If the debug port is not listening yet, call `browse_launch_edge` once; it
copies the Edge profile to a dedicated directory and starts Edge with the port.

### 4. Register the server in your MCP client

See [`docs/mcp-clients.md`](docs/mcp-clients.md) for Claude Desktop, Cursor and VS Code examples. Minimal stdio registration:

```json
{
  "mcpServers": {
    "uplus": {
      "command": "uv",
      "args": ["--directory", "C:\\Users\\you\\projects\\uplus-mcp", "run", "python", "-m", "uplus_mcp.server"],
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
| `uplus_check_login` | Verify whether the attached Edge session is signed in |
| `uplus_list_courses` | List courses under the signed-in account |
| `uplus_list_homeworks` | List homework status; open answer pages to verify button truth on demand |
| `uplus_list_experiments` | List experiments and submission state (submitted / outstanding / unknown) |
| `uplus_inspect_homework` | Return status and a structured question brief for the client model |
| `uplus_answer_homework` | Fill objective questions and optionally submit |
| `uplus_upload_attachment` | Attach a local file to a homework that requires upload |
| `uplus_patrol` | Full patrol, Markdown report, answer briefs and takeover list |
| `uplus_close` | Release the browser session held by this process |

### Generic browser (`browse_*`)

Single-step tools: `browse_launch_edge`, `browse_open`, `browse_snapshot`,
`browse_get_text`, `browse_click`, `browse_fill`, `browse_type`, `browse_press`,
`browse_scroll`, `browse_select_option`, `browse_upload`, `browse_screenshot`,
`browse_eval_js`, `browse_wait_for`, `browse_tabs`, `browse_switch_tab`,
`browse_back`.

`browse_act_batch` runs a short serial batch of those actions: click, fill,
press, hover, scroll, wait, extract, navigate, back. The batch stops on the
first failure or navigation and returns a completed / remaining / failure /
navigated receipt; `observe="summary"` skips the follow-up snapshot. Password
fields are never extracted back.

## Nightly patrol (unattended)

```powershell
.venv\Scripts\python.exe -m uplus_mcp.daily_patrol
```

The CLI writes `reports/patrol_YYYYMMDD_HHMM.md` and a `.brief.json` for outstanding objective questions. Exit code `2` means the login session expired; exit code `1` means a runtime failure. Register this command in Windows Task Scheduler (e.g. daily 21:30) or your system cron.

> The CLI never submits homework. Answer and submission happen through an MCP session after the model reviews the brief.

## Configuration reference

`config/courses.json`:

| Field | Default | Meaning |
| --- | --- | --- |
| `base_url` | `https://www.eduplus.net` | Platform root |
| `nav_timeout_ms` | `30000` | Page navigation timeout |
| `action_timeout_ms` | `15000` | UI action timeout |
| `deadline_warning_hours` | `72` | Highlight deadlines inside this window |
| `report_dir` | `reports` | Markdown report output folder |
| `auto_launch_edge` | `true` | Copy the Edge profile and launch Edge with the CDP port when no port listens |
| `profile_dir` | system profile copy | Dedicated Edge profile directory used for the CDP launch |
| `include_expired_courses` | `false` | Skip expired courses during patrol; set `true` to keep an archived view |
| `courses` | empty | `id` / `name` / `teacher` per course; empty falls back to the live API list |

## Development

```powershell
pip install -e ".[dev]"
pytest
```

- 62 unit tests cover model parsing, config loading, answer normalization, page
  state rules, experiment classification, the two-mode login flow and the batch
  action receipt.
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
  launcher.py     locate / launch Edge with the debug port
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
- Do not commit student IDs, names, phone numbers, email addresses, cookies or tokens.
- The server never persists your password; `uplus_login` auto-mode uses
  credentials only for the current call and never writes them to config, reports
  or logs or echoes them back in tool results.

## License

MIT, see [`LICENSE`](LICENSE).
