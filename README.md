# uplus-mcp

MCP server for the QingSoft U+ learning platform **with a built-in generic browser toolset**.

See [README.zh.md](README.zh.md) for the full Chinese guide (step-by-step install, run, and push-back-to-GitHub instructions).

## What's new in 0.4.0

- **Merged browse toolset**: 17 new `browse_*` tools let the model drive any web
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

- Login state persisted through Playwright `storage_state`; no passwords or cookies are stored in the repository.
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
playwright install chromium
```

### 2. Configure personal courses (gitignored)

```powershell
Copy-Item config\courses.example.json config\courses.json
```

Edit `config/courses.json` with your actual `courseId` values and local paths.

### 3. First login

```powershell
python scripts/login.py
```

A visible browser opens the U+ workbench; sign in yourself. The script detects the workbench, saves `storage_state.json` and lists recognized courses.

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

| Tool | Description |
| --- | --- |
| `uplus_check_login` | Verify the persisted login session |
| `uplus_list_courses` | List courses under the signed-in account |
| `uplus_list_homeworks` | List homework status; open answer pages to verify button truth on demand |
| `uplus_list_experiments` | List experiments and submission state |
| `uplus_inspect_homework` | Return status and a structured question brief for the client model |
| `uplus_answer_homework` | Fill objective questions and optionally submit |
| `uplus_upload_attachment` | Attach a local file to a homework that requires upload |
| `uplus_patrol` | Full patrol, Markdown report, answer briefs and takeover list |
| `uplus_close` | Release the browser session held by this process |

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
| `headless` | `true` | Run Chromium without a window |
| `nav_timeout_ms` | `30000` | Page navigation timeout |
| `action_timeout_ms` | `15000` | UI action timeout |
| `deadline_warning_hours` | `72` | Highlight deadlines inside this window |
| `storage_state_path` | `storage_state.json` | Persisted login state file |
| `report_dir` | `reports` | Markdown report output folder |
| `courses` | bundled fallback | `id` / `name` / `teacher` per course |

## Development

```powershell
pip install -e ".[dev]"
pytest
```

- Pure logic (models, config, answer normalization) is covered by unit tests.
- Browser and platform interactions are exercised against a real session in read-only patrol mode; write actions require the authorized account and an active course deadline.

## Repository layout

```
src/uplus_mcp/
  browser.py      Playwright session + in-page fetch
  platform.py     read-only REST client
  operator.py     page UI operations
  engine.py       shared orchestration
  models.py       domain objects and enums
  config.py       configuration loader
  solver.py       brief generation + answer normalization
  report.py       patrol report aggregation and Markdown render
  utils.py        HTML cleaning, time and answer normalization helpers
  server.py       FastMCP tools
  daily_patrol.py unattended CLI
scripts/login.py  first-run login helper
docs/             architecture, API mapping, client config, adapter guide
config/           personal config example (real file gitignored)
reports/          generated patrol reports (gitignored)
tests/            unit tests
```

## Safety and privacy

- Keep `storage_state.json`, `config/courses.json` and `reports/` local; they are listed in `.gitignore`.
- Do not commit student IDs, names, phone numbers, email addresses, cookies or tokens.
- The server never asks for your password and does not store credentials.

## License

MIT, see [`LICENSE`](LICENSE).
