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

## What's new in 0.8.0

A complete experiment pipeline: read the experiment's requirements, search the
course 资料 section (recursing into folders) for the 实验报告模板, download the
materials, fill the template, and only submit after three inspection checks
pass. Suite is 292 passed.

- **New `materials.py`** — the 资料 section has no fixed route contract, so it
  is discovered rather than hard-coded: open the course home, find the
  「资料 / 资源 / 素材 / 文件」 entry in the sidebar, list the current folder,
  **descend into folders** (deduplicated by `(name, href)`, depth-capped), and
  match 「实验报告模板 / 报告模板 / 实验N」.
- **Binary download** (previously the tool could only upload) —
  `BrowserSession.fetch_bytes` tries an in-page `fetch` with `x-access-token`
  (same-origin), then falls back to `context.request.get` (shared cookies, not
  bound by CORS, reaches cross-origin OSS links); an entry with no href falls
  back to an `expect_download` click. Every write is confined to
  `allowed_io_paths`, under `reports/experiment_materials/`.
- **Template filling** — `report_generator.fill_template` opens the *real
  downloaded .docx template* and writes into the empty paragraph or table cell
  following each section heading, replacing「（此处填写…）」「待填写」「TODO」
  placeholders. `describe_template` reports the structure first;
  `derive_sections` extracts the handbook's own section text verbatim. With no
  template it falls back to the built-in format and runs the same filler.
- **Four new MCP tools** — `uplus_download_experiment_materials`,
  `uplus_inspect_experiment`, `uplus_answer_experiment`,
  `uplus_audit_experiment`.
- **The homework-style inspection gate** — `verify_report` refuses a report
  before upload when it is too small / has too little body text (blank), still
  carries a placeholder, or talks about a different experiment.
  `answer_experiment` also requires every model-written section; a missing one
  refuses generation and submission. After submitting, the page is re-read and
  success is only reported when the submission registered. The older
  `uplus_submit_experiment_report` now passes the same gate.
- **Patrol integration** — unsubmitted experiments produce
  `experiment_briefs` (the sections to write; nothing is downloaded or
  submitted automatically), submitted experiments get a read-only audit, and
  the Markdown report gains an "experiment completeness" table.
- **A blocking gap fixed** — `Experiment` and the experiment-list scraper did
  not carry `exp_id` / `stu_exp_id`, so `uplus_list_experiments` could not lead
  to any follow-up tool. Both ids are returned now.
- **Student name / number** — set `student_name` / `student_id` in the
  gitignored `config/courses.json`; a tool call may override them. They are
  written only into the local report .docx (gitignored), never into the repo or
  the Markdown patrol report.
- **Tests** — 42 new cases in `tests/test_lab.py`.

## What's new in 0.7.8

Patrol now checks *already submitted* homework by default, detects partial
multi-select answers, and can withdraw/complete/resubmit a homework through a
guarded repair action. Suite is 250 passed.

- **Submitted homework is audited on every patrol** — `uplus_patrol`'s
  `audit` now defaults to `true`; the CLI audits unless `--no-audit` is given.
  The completeness table gains separate "missing" and "partial multi-select"
  columns, plus a count of multi-selects that could not be judged.
- **Partial multi-select answers are detected** — the audit compares
  `userAnswer` with the platform's standard answer (`answer`) as sets. A
  proper subset (standard `ABC`, submitted `A`) is `partial` and reports the
  omitted letters (`BC`); letters outside the standard answer are `wrong`.
  Option order does not matter (`AB` equals `BA`).
- **Pending submissions get a second source of truth** — the platform only
  exposes `answer` after grading; while pending, the student detail has no
  standard answer and the page only shows "我的答案". A local answer journal
  (`reports/answer_journal.json`, gitignored) records every answer this tool
  submits, so a pending homework can still be checked. With neither source the
  question is reported as `unjudged`, never silently treated as complete.
- **New `uplus_repair_homework`** — performs "withdraw → fill gaps →
  resubmit" as one guarded action. Only a homework whose page shows
  `撤回作业` (submitted, pending grading) can be withdrawn; a graded one is
  refused. Withdrawal happens only after every known gap has an answer
  (the caller's `answers` first, the local journal second); otherwise nothing
  is withdrawn and the submission stays untouched. Existing answers are
  preserved and re-filled, so a withdrawal that clears the draft can still be
  restored. The tool re-audits after resubmitting.
- **A repair answer is validated before withdrawal** — if the new answer still
  misses letters of the known standard answer, the tool refuses to withdraw and
  says which letters are missing, so a repair cannot reintroduce 漏选.
- **Optional re-verification** — `verify_unjudged=True` re-answers every
  multi-select whose completeness could not be checked while pending, then
  resubmits. Without it, only known missing/partial questions are repaired.
- **Code-block options are readable and clickable** — options such as
  `A、<property>` / `C、<constructor-arg>` come from `<p><code>` fragments and
  are parsed as plain text; on the page they sit inside a
  `label.el-radio` whose input value is the letter, so the existing locator
  finds them uniquely. Regression tests pin both behaviours.
- **Patrol returns `repair_briefs`** — each affected homework lists the
  questions, current answer, standard/journal answer and whether the model must
  answer. The CLI writes the list next to the report as `*.repair.json`.
- **24 new tests** — partial detection, the journal, unjudged pending
  multi-selects, guarded withdraw/resubmit, rejection of incomplete repair
  answers, code-block option parsing and page locators. Total 250 passed.

## What's new in 0.7.7

A hardening pass over 0.7.6 plus figure-question support: three real defects
found while re-reading the code and one new capability, without changing any
documented behaviour. Suite is 226 passed.

- **Figure questions can finally be seen** — new tool
  `uplus_capture_question_image(task_id, course_id, order=…)`. The platform
  stores a diagram as an `<img>` inside the stem's rich text; `strip_html`
  dropped it, so a judge question like 「以下模型是非对称密码体制中的两种模型」
  reached the model as text with no picture and could only be guessed. The
  question detail now exposes `images`, the brief carries a `figure_note`
  naming the tool, and the tool navigates to the question by stem (same rule
  as filling) and returns that question's block as a real MCP **image content
  block** plus the saved PNG path. It fills and submits nothing.
- **A list page whose stem cannot be scoped no longer reads other questions'
  checkboxes** — 0.7.6 introduced page-state verification for multi-selects. On
  a list-view page the scope comes from the stem; when the stem could not be
  narrowed the code fell back to the whole page, which holds *every* question's
  boxes. Reading that mixed this question's state with its neighbours', and the
  reconciliation would then uncheck someone else's option. Such a page now
  reports "unverifiable" and the fill falls back to the API-side delta instead.
  (Single-question views are unaffected: there is nothing else on the page.)
- **`userAnswer` can be a non-string, and no longer crashes the answer path** —
  the value comes from a JSON payload, so it can arrive as a number, a list or
  a bool. `utils.normalize_letters` called `.upper()` on it directly, raising
  `AttributeError` in the middle of filling a question.
- **The experiment-name fallback no longer labels a requirement line as the
  title** — a line like `实验要求：掌握进程同步的基本方法` or
  `提交物：进程同步实验报告` used to be reported as the experiment's name. The
  fallback now requires a short line without a `：`/`:` separator, so only a
  real title line matches.
- **29 new tests** — 18 in `tests/test_figure_questions.py` cover image
  detection, the brief's figure hint, question-container screenshots, path
  confinement and the text+image tool contract; 11 in
  `tests/test_answer_fixes.py` cover the unscoped-page refusal, the non-string
  `userAnswer` path and the experiment-name fallback.

## What's new in 0.7.6

A correctness release for answering: partial multi-selects are no longer left
partial, and a question can no longer be answered on the wrong page position.
Every fix below is covered by a regression test; the suite is 195 passed.

- **Multi-select is reconciled with the page, not the API** — the old code
  clicked ``target − userAnswer``. On a page where the student had already
  ticked an option the API did not know about, that click *unchecked* it, and
  a fresh page whose boxes were already set could end up missing exactly the
  letters it meant to add. Filling now reads the real checked boxes, ticks only
  the missing letters, unticks only the extras, re-reads the page, and raises
  if the target still cannot be reached — so the submit gate refuses an
  incomplete multi-select instead of handing it in silently.
- **An already-answered multi-select is re-checked** — the loop used to skip
  every question the platform marked answered, even when the model's answer
  had more letters (the "漏选" case). It is now re-filled whenever the model
  answer differs from the recorded one; a recorded multi-select with no model
  answer is counted as "cannot verify" and blocks auto-submit instead of
  passing silently.
- **Navigation is verified before typing** — clicking an answer-card number can
  miss. The page stem is re-read after the click and compared with the expected
  question; on a mismatch the question is skipped (and reported), never filled
  into whatever question happens to be open. This also fixes a real bug where a
  question whose page position was 1 returned early without clicking, after the
  stem walk had left the page on the last question — its answer landed on the
  wrong question.
- **Duplicate stems no longer collide** — two questions with identical rich
  text used to overwrite each other in the position map, so one of them could
  never be reached. Duplicates get a positional suffix and each position is
  consumed once.
- **List-view pages are scoped per question** — when every question renders at
  once, option / judge / blank locators are now confined to the container of
  the question being filled (located by its stem) instead of always hitting the
  first question on the page. Option labels use ``starts-with(…, "A、")`` so an
  option whose *content* mentions "A、" cannot be picked by mistake.
- **"Answered" now means a real ``userAnswer``** — the platform creates an
  answer row (``hwAnswerId``) and flips ``isAnswer`` when a question is merely
  opened. Counting those as answered made the completeness audit report blank
  questions as done (the "漏题" false negative) and kept them out of answer
  briefs. The audit, the patrol brief and the fill loop now require a non-empty
  ``userAnswer``.
- **Answer parsing ignores prose** — a model answer like ``"AC because A and C
  are correct"`` used to contribute every Latin letter in the explanation as an
  option. Only the leading choice group is read, intersected with the question's
  actual options; empty choice answers are rejected instead of clicking nothing.
- **Fill-blank input is read back** — a rich text editor that ignores ``fill``
  is now reported as an error rather than looking like success.

## What's new in 0.7.5

Answering is now keyed to the question text instead of the page position, and
option clicks survive both page layouts. Two bugs surfaced on a real quiz.

- **Page order is no longer trusted** — on single-question pages (the ones with
  a `答题卡` grid and `上一题/下一题`) the page order and the API `order` differ,
  so clicking by API order answered the wrong question. The fill loop now maps
  every page position to its stem first and navigates by that, and refuses to
  answer a question whose stem cannot be found instead of risking a wrong click.
- **Options without `A、` can be clicked** — some quizzes render the letter in
  its own span (`A` + `开闭原则`) rather than as one label text. The text
  locator timed out and those questions could never be answered; it now falls
  back to `label:has(input[value="A"])` when that letter is unique on the page.
- **Nine tests** cover stem normalization, the page-order mismatch, the
  longest-prefix tie-break and the unknown-stem case. Suite is 179 (178 passed,
  1 skipped).

## What's new in 0.7.4

A completeness audit for already submitted homework: you can now tell whether a
handed-in homework left objective questions blank **without withdrawing it**,
and only a real gap leads to "withdraw → fill → resubmit". The audit itself is
read-only against the platform.

- **New tool `uplus_audit_homework(task_id, course_id)`** — every question is
  read from the student-side detail, and a question counts as answered when
  `userAnswer` or `hwAnswerId` is non-empty (the same rule as
  `models._is_answered()`). *0.7.6 corrected this rule: only a non-empty
  `userAnswer` counts — see "What's new in 0.7.6".* Nothing is withdrawn, filled or submitted, so
  pending and already-graded homework can be audited safely. Short-answer,
  programming and attachment items are counted separately and never make a
  homework "incomplete".
- **Patrol can carry the audit** — `uplus_patrol(audit=True)` and
  `uplus-patrol --audit` check every submitted homework while patrolling.
  Homes with gaps land in the report and in the takeover list, labelled
  "can withdraw and fill" or "graded, cannot withdraw", with the question
  numbers truncated after ten.
- **New report section "提交完整性核查"** — course, homework, number of blank
  questions, their question numbers, and whether the homework is still
  withdrawable, in one table.
- **The version number is finally consistent** — `__version__` still said 0.7.0
  because 0.7.1–0.7.3 only bumped `pyproject.toml`; everything is 0.7.4 now,
  `maven-package/pom.xml` included.
- **A time-bomb test is fixed** — `test_course_dates_from_the_study_feed_are_numeric`
  hard-coded a `2026-11-05` deadline, so `is_expired()` returned `True` once that
  date passed and the test could only fail. It verifies date parsing, not the
  clock, so the deadline is now built as "now + 365 days".
- **`withdrawable` follows the page, not the API** — the homework API still
  reports a handed-in homework as "not submitted", so the audit reads the answer
  page's real button state before deciding whether it can be withdrawn, and only
  falls back to the API status (with `page_state_note` explaining it) when that
  read fails. Skipping this would drop exactly the "submitted but incomplete"
  homework the audit exists for.
- **9 new tests** (`tests/test_audit.py`) cover gap detection, subjective items
  excluded, the `hwAnswerId` path, report rendering, the graded case, the page
  state overriding the API and the fallback when the page read fails.
  Total: 174.

## What's new in 0.7.3

The Maven publish is now self-diagnosing, and its credentials are written
explicitly instead of relying on a generated template.

- **The `settings.xml` is written by the workflow** — the `<server>` block
  that GitHub Packages reads the token from is now created with a known
  content, so a credential that never lands in the file is no longer
  possible.
- **The registry is probed before the deploy** — a read-only request for the
  POM reports whether the credentials were accepted (401), whether the version
  is absent (404) or already published (200), so a failed publish says which
  of the three it was.
- **The Maven error is readable without opening the raw log** — the failing
  lines are mirrored as error annotations on the run, next to the log
  artifact.

## What's new in 0.7.2

The publish pipeline now works end to end. 0.7.1 created the Release but could
not register the Maven package, because the POM attached the wheel and the
sdist to itself and the GitHub Packages Maven registry rejects artifact
extensions it does not know - the deploy failed with 400 and the Packages
panel stayed empty, which is the one thing that entry exists for.

- **The Maven package publishes only its POM** — the wheel and the sdist are
  no longer attached to it, so the deploy uploads the one file the registry
  accepts. The installable distributions remain the Release assets, which is
  where pip users get them anyway.
- **A publish failure now explains itself** — the Maven output is captured
  into a `mvn-deploy-log` artifact and the tail of it is written to the run
  summary, so the next failure says why instead of only saying that it
  happened.

## What's new in 0.7.1

A code-review release: an audit of the 0.7.0 code found deadline arithmetic
that depended on the machine's timezone, a hostname check that matched a
substring instead of an origin, and a release pipeline whose Maven publish
could never authenticate. All are fixed here; no tool contract changed.

- **Deadlines are read as Beijing time** — the experiment and homework cards
  parsed the page's wall clock with a naive `datetime`, so `.timestamp()`
  applied the *machine's* UTC offset. On a machine that is not set to UTC+8
  every deadline in the patrol report drifted by that offset, and the
  "deadline approaching" warning fired at the wrong time. Parsing is pinned
  to `Asia/Shanghai` now (`utils.parse_cn_datetime`).
- **A non-numeric timestamp no longer aborts the patrol** — the course feed
  can answer with a date string (`endDate` / `startDate`); comparing it with
  an integer raised `TypeError` and killed the whole run. Values are coerced
  (`utils.to_epoch_ms`) and degrade to "unknown".
- **The API read only runs on the platform origin** — the origin check was a
  substring test (`host not in page.url`), so a page on
  `www.eduplus.net.evil.example` passed it. The parsed host is compared now.
- **Every experiment is named** — the detail page's title was matched only for
  `实验一` / `实验二` (with an operator-precedence bug that also matched
  "实验一" anywhere in a line), so `实验三` … `实验九` came back nameless.
- **One config for all tools** — the report tools loaded their own config
  while the engine used another, so the allowed-IO allow-list could disagree
  between the two. The config is parsed once and shared, and report
  generation moved onto the worker thread instead of blocking the event loop.
- **The generated report uses 宋体** — `font.name` only writes the Latin face;
  Word reads Chinese glyphs from `w:eastAsia`, which stayed on the theme
  default.
- **The release pipeline authenticates and fails loudly** — `mvn deploy` had
  no `settings.xml`, so the Maven publish could only ever return 401, and
  `continue-on-error` hid it. The publish now gets its credentials from
  `setup-java`, the test suite runs before the build, the git tag is checked
  against the package versions, and a publish failure ends the run red with a
  named error while the GitHub Release is still created.
- **An installed wheel no longer writes into the Python library** —
  `PROJECT_ROOT` was derived from `parents[2]`, which in a wheel install is
  the directory holding `site-packages`: reports landed there (unwritable on
  a system-wide install) and the allow-list refused the user's own project.
  Relative paths now resolve against the working directory, or `UPLUS_HOME`.
- **The example config ships inside the package** —
  `uplus_mcp/courses.example.json` is packaged with the wheel, so a wheel
  install can still offer it.
- **Personal coursework is ignored** — the generated reports carry the
  student's name and student number; `*.doc`, `*.docx`, `*实验*.c` and
  `gen_exp*.py` are now ignored, so a GitHub Desktop "Commit all" cannot
  publish them.
- **42 new regression tests** (`tests/test_code_review_fixes.py`).
  Suite total: 165 tests.

## What's new in 0.7.0

A security-hardening release: an audit of the 0.6.0 code found that a page the
model had navigated to could turn a navigation step into a **local-file read**
(`file:///…`), and that several file tools accepted any path on disk. Both are
closed here. Behaviour of the documented flows is unchanged.

- **Navigation is restricted to `http`/`https`** — the batch runner
  (`browse_act_batch` with `op: "navigate"`) used to pass a URL straight to the
  browser, so a `file:///C:/…` step could open a local file and `extract` its
  contents into the model context; combined with `browse_upload` this was a
  read-and-exfiltrate path. `navigate` and `uplus`/`browse_open` now share one
  scheme allow-list (`utils.safe_navigation_url`) that refuses `file:`, `ftp:`,
  `data:`, `javascript:` and `blob:` with an explanatory error, and internal
  relative routes still resolve against the platform origin.
- **File tools are confined to allowed directories** — report output,
  screenshots, homework attachments and experiment uploads now resolve their
  path against `allowed_io_paths` (config key, or `UPLUS_ALLOWED_PATHS`) and
  refuse anything outside it. `..`, `~` and symlinks are collapsed before the
  check, so `reports/../../.ssh/id_rsa` and `~/.ssh/id_rsa` are both rejected.
  The default roots are the report folder and the system temp directory, which
  keeps "generate a report, then submit it" working with no configuration.
- **`python-docx` is now a declared dependency** — `uplus_generate_experiment_report`
  imported it lazily, so the headline feature of 0.6.0 failed with
  `ModuleNotFoundError` on any environment that did not already have it.
- **32 new regression tests** (`tests/test_security_hardening.py`) cover the
  scheme allow-list, path traversal / symlink / home-expansion escapes, the
  allowed-path config plumbing, report generation and secret redaction.
  Suite total: 123 tests.
- **CI actions pinned to commit SHAs** — `checkout`, `setup-python`,
  `setup-java` and `action-gh-release` now pin the exact commit they were
  reviewed at instead of a movable major-version tag.
- Smaller fixes: API error bodies are passed through `redact_secrets` before
  they reach the model context; the school selector no longer falls back to
  clicking the first dropdown item when nothing matched (it now fails over to
  manual sign-in rather than trying the wrong school); the `uplus_login`
  docstring documents the 30-second auto-mode wait cap.

## What's new in 0.6.0

- **Experiment automation**: three new MCP tools let the model read an experiment's requirements, generate a standard experiment report `.docx`, and upload it for submission.
  - `uplus_read_experiment_detail` — opens the experiment detail page, switches tabs, and returns name, status, environment, material, deadline, already-submitted files and the manual text.
  - `uplus_generate_experiment_report` — builds a structured `.docx` (title, purpose, content, steps, implementation, analysis, summary, requirements) from a few parameters; includes the algorithm-course template.
  - `uplus_submit_experiment_report` — uploads the generated file through the platform's file input and confirms the submission state.
- **Two new modules**: `experiment.py` (list / read detail / upload) and `report_generator.py` (python-docx based report builder).
- **18 regression tests** guard the new modules and keep the total suite green at 91 tests.

## What's new in 0.5.2

- **Five audited defects closed** (3 high, 2 medium):
  - Profile directory copy now refuses when source and destination overlap, or when the target lacks the `Local State` marker — prevents accidental recursive deletion of user data.
  - `fill()`, `snapshot`, and batch `extract` all mask password fields as `••••` — no more password echo in single-step fill or page snapshots.
  - Submit gate now counts previously-answered objectives in the total, and refuses to submit when the question list is empty (no more `0 == 0` pass).
  - `patrol(auto_answer=True, submit=True)` is now wired through the rule hook with a full-coverage gate — actually submits when all objective questions have rule answers.
  - Unknown or expired course ID now raises an error instead of silently widening the query to all courses.
- **18 regression tests** in `tests/test_fixes.py` guard all five fixes.
- **CI publish fix**: `pom.xml` artifact filenames now use `${project.version}` instead of hard-coded `0.5.0`, and the workflow clears `maven-package/artifacts` before copy to prevent stale files from previous releases.

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
- Completeness audit: `uplus_audit_homework` reports, read-only, which objective
  questions of an already submitted homework are still blank **and** which
  multi-selects were submitted with only part of the correct options. Patrol
  runs this audit by default; `audit=False` skips it. Known gaps are repaired
  through `uplus_repair_homework` (withdraw → complete → resubmit), which never
  withdraws before every gap has an answer.
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

The example config ships inside the package (`uplus_mcp/courses.example.json`).
Copy it to `config/courses.json` in your project folder, then fill in your
actual course ids.

**Windows PowerShell (source checkout):**

```powershell
Copy-Item src\uplus_mcp\courses.example.json config\courses.json
```

**macOS / Linux (source checkout):**

```bash
cp src/uplus_mcp/courses.example.json config/courses.json
```

Installed from a wheel? The example sits next to the installed package:

```bash
python -c "import pathlib,uplus_mcp;print(pathlib.Path(uplus_mcp.__file__).parent/'courses.example.json')"
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
| `uplus_list_experiments` | List experiments with `exp_id` / `stu_exp_id` and submission state (submitted / outstanding / unknown) |
| `uplus_download_experiment_materials` | Download the experiment's attachments plus the 资料-section report template |
| `uplus_inspect_experiment` | Return the experiment brief: requirements, template, `auto_sections` (from the handbook) and `needed_sections` (model-written) |
| `uplus_answer_experiment` | Fill the template, run the inspection gate, upload and submit; re-reads the page to confirm |
| `uplus_audit_experiment` | Read-only audit of a submitted experiment: blank report, leftover placeholders, wrong experiment |
| `uplus_generate_experiment_report` | Build a standard report .docx from scratch (fallback when no template exists) |
| `uplus_submit_experiment_report` | Upload and submit a report; `.docx` reports pass the same inspection gate first |
| `uplus_inspect_homework` | Return status and a structured question brief for the client model |
| `uplus_capture_question_image` | Screenshot one question block (diagram included) and return the image to the model |
| `uplus_answer_homework` | Fill objective questions and optionally submit |
| `uplus_repair_homework` | Withdraw a submitted homework, complete missing/partial answers, resubmit and re-audit |
| `uplus_upload_attachment` | Attach a local file to a homework that requires upload |
| `uplus_patrol` | Full patrol (submitted-homework audit on by default), Markdown report, answer/repair briefs and takeover list |
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
from the Actions tab with an explicit tag). The workflow uses the built-in
`GITHUB_TOKEN`, so no personal access token is required to publish.

The workflow runs the test suite before it builds, checks that the git tag
matches the version in `pyproject.toml` and `maven-package/pom.xml`, creates
the GitHub Release with the wheel and sdist attached, and only then publishes
the Maven package. A Maven failure therefore never costs the release, but it
does end the run red with a named error instead of passing silently.

Release notes come from `.github/release-notes/<tag>.md` when that file exists
(for example `v0.8.0.md`); otherwise GitHub generates them from the commits, so
a missing notes file never fails the run.

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

- 165 unit tests cover model parsing, config loading, answer normalization, page
  state rules, experiment classification, the two-mode login flow, the batch
  action receipt, cross-platform browser detection, config compatibility and
  the security hardening of 0.7.0 / 0.7.1.
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
