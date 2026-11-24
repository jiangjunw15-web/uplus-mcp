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

## What's new in 0.8.19

Live use (account 2413041236) showed two blockers that made auto login and every
account-bound tool fail outright. Both are fixed, and the whole flow was re-run
against the real platform.

- **Login entry fallback.** The portal home page exposes its sign-in entry as a
  bare `登录/注册` text link on some builds, not `.login-button`; the old code
  clicked nothing and reported "no account/password box". `_enter_login_form`
  now tries `.login-button` → `a[href*='login']` → `text=登录/注册` →
  `text=登录`, verifies the landing page after each click, and returns a clear
  "hand the browser back for one manual click" result when none worked.
- **Account binding for opaque ids.** This deployment stores no student number
  in `localStorage` — only `userid`, a GUID — so the 0.8.18 identity guard
  rejected the correct account right after login. The account is now bound to
  the portal's session id once, at login, and checked against that binding
  later. The binding is written only when the typed `username` equals the
  configured `account`, so logging in as someone else still reports
  `account_mismatch` and writes nothing. The ledger lives next to the answer
  journal, so a shared `UPLUS_JOURNAL_PATH` shares bindings too.
- **Verified live:** login (`account_match=true`, `matched_by=fingerprint`),
  13 courses / 30 homeworks patrolled, `2026软件构造` 作业3 + 平时作业-3 submitted
  10/10 from cross-account reuse with page progress `10/10`, five withdrawable
  homeworks re-checked as complete, pinned port never fell back.
- **Tests.** 556 pass (544 + 11 new login/binding tests), 1 skipped.

## What's new in 0.8.18

Fixes the three issues surfaced by the second round of live testing: silent
port fallback that could cross accounts, slow patrol, and missing account
identity verification.

- **Pinned CDP port.** A `cdp_port` from the config file or `UPLUS_CDP_PORT`
  is now *pinned*: if it is not listening the session fails loudly instead of
  falling back to 9222 or another live port. In a multi-account setup the
  fallback could attach to a different account's browser and fill that
  account's homework. Only the implicit default port keeps the historic
  fallback. `browse_launch_edge` reports `pinned: true/false` accordingly.
- **Account identity guard.** New config `account` / `UPLUS_ACCOUNT` (defaults
  to `student_id`). Every account-bound tool verifies the attached browser
  session belongs to it before acting; a mismatch raises with both the actual
  and expected account. `uplus_check_login` returns `account` and
  `account_match`. This also protects the v0.8.17 cross-account reuse path.
- **Patrol speed.** Page reads are limited to rows whose verdict needs them
  (API-unsubmitted + non-expired, or API-submitted with unrecognised
  `subStatus`). Other rows keep the API status with `status_source="api"`.
  A 120-second page-state cache prevents patrol → audit → repair from
  reloading the same SPA. Writes invalidate the cache for that task.
- **Already-graded limits.** A graded homework with missing options (87/100,
  questions 86/88 each missing D) is platform-locked: `withdrawable=false`
  is reported, no withdrawal attempted. Pending grading without a reference
  answer stays `unjudged`.
- **Multi-account setup.** Each account: independent `cdp_port`,
  `profile_dir`, `account`, and working directory/report folder. Shared
  `UPLUS_JOURNAL_PATH` for cross-account reuse. The port pin + identity guard
  make silent cross-account operation impossible.
- **Tests.** 544 pass (520 + 24 new); Ruff F/B clean on touched files.

## What's new in 0.8.17

Adds **cross-account answer reuse**. The local answer journal used to be looked
up by `task_id` only, so a homework answered on one account was invisible to
another account even when the question bank was identical. The platform assigns
`question_id` per question, not per student, so a `question_id` index of the
journal lets one account's reviewed answers fill another account's blanks.

- **On by default, the caller always wins.** `uplus_answer_homework(reuse=True)`
  only fills objective questions the caller left unanswered;
  `uplus_repair_homework(reuse=True)` falls back to another account's answer
  only when this task's own journal entry has none; `uplus_finish_then_review`
  fills blanks the same way. `reuse=False` restores the 0.8.16 behaviour.
- **A hint, not an answer key.** `reused_questions` names the source
  (`source_task_id` / `source_homework_id` / `source_name`) for every reused
  question and `reuse_note` states how many were reused. `uplus_patrol` exposes
  `reuse_answers` / `reuse_sources` / `reuse_note` per answer brief, and
  `uplus_inspect_homework` shows the reusable answers before you decide.
- **No double filling.** An answer keyed by question order (`"3"`) blocks the
  `question_id` copy for that same question.
- **Reuse never blocks work.** A failed index/detail read only adds a
  `reuse_note`; the caller's answers are still submitted.
- **One shared journal.** With separate working directories per account, point
  `UPLUS_JOURNAL_PATH` at one shared journal file so reuse is actually visible;
  otherwise each account reads only its own `answer_journal.json`.
- **Platform limits unchanged.** Graded work is never withdrawn, pending
  multi-selects without a reference stay `unjudged`, and subjective /
  programming / attachment work stays with the human.
- **Verified:** 520 tests pass (7 new reuse tests cover newest-wins indexing,
  caller priority, `reuse=False`, order-key conflicts, read-failure fallback and
  repair fallback), plus a live patrol on real accounts (phone-password and
  student-id logins): the journal holds 414 question ids and 作业3 reuses 10/10.

## What's new in 0.8.16

Fixes the live-read cases where the API said "not submitted" while the answer
page already offered **撤回作业**, plus the login check that raced the SPA's
token write and redirect.

- **Page truth now reaches every read path.** With `refresh_page_state=True`,
  `uplus_list_homeworks` and `uplus_scan_course` reconcile every row, not just
  API-unsubmitted/non-expired ones. `uplus_patrol` does the same, and
  `uplus_finish_then_review` refreshes the button before phase 1, so a
  submitted assignment is never treated as outstanding work. Expiry still
  blocks every write; reading an expired page is safe.
- **A truthful login check.** `uplus_check_login` waits for the token + redirect
  to settle, then validates a protected read. It no longer treats a transient
  login URL or a rendered workbench shell as proof of a session. The API token
  probe remains cached for speed.
- **Progress unknown is not zero.** More answer-card layouts and explicit DOM
  counters are supported. `progress=null` is now accompanied by
  `progress_status="unavailable"`, nullable `answered_count` /
  `question_total_on_page`, and a note: use the submit/withdraw button for
  submission state, not a missing counter.
- **Explicit `uplus_logout(confirm=True)`.** Uses the platform's sign-out UI,
  verifies the protected session has ended, and clears account caches only on
  success. Without confirmation it changes nothing. Separate accounts should
  still use separate profiles, CDP ports and working directories/report files.
- **Platform limits remain platform limits.** A graded assignment is never
  withdrawn. Pending grading without a reference answer remains `unjudged`;
  a local answer journal is not promoted to an answer key. Identical
  `question_id` values across accounts are not treated as proof of correctness.
- **Validation.** 513 tests pass; focused Ruff F/B checks are clean. The
  real-account UI/logout flow is not exercised by these mocked regression tests.

## What's new in 0.8.15

Fixes the "MCP is not registered, only the local CLI works" symptom, plus a
round of speed work. **No tool signature or existing behaviour changes.**

- **The root cause was the registration step, not the server.** The server
  starts and handshakes fine over stdio (35 tools: 17 `uplus_*` + 18
  `browse_*`). It was simply absent from every client's MCP config: Claude
  Desktop's `claude_desktop_config.json`, Cursor's `~/.cursor/mcp.json` and
  Claude Code's `~/.claude/settings.json` all had no `uplus` entry. The package
  is also only installed in the project's virtualenv, so a plain
  `python -m uplus_mcp.server` fails with `ModuleNotFoundError` — the docs'
  `args` line only works when paired with the venv interpreter.
- **One command now does the registration.** `scripts/register_mcp.py` writes a
  correct `mcpServers.uplus` block into all three clients:

  ```powershell
  python scripts\register_mcp.py            # all detected clients
  python scripts\register_mcp.py --dry-run  # show what would change
  ```

  It resolves the venv Python by absolute path (walking up to the project root,
  so a git worktree finds the real venv), sets `cwd` and `UPLUS_CONFIG`,
  preserves every other key in the file, refuses to overwrite a file it cannot
  parse, and is idempotent. Restart the client afterwards. The entry was
  verified end-to-end: the registered command handshakes and lists all 35 tools.
- **Reading a homework's questions is now one round-trip.** Fetching the details
  of N questions used to cost N sequential browser round-trips (30 questions =
  30). `BrowserSession.api_get_many` issues the same requests in parallel inside
  the page, so a 30-question homework drops from 30 round-trips to 1 (96.7%
  fewer) — same URLs, same headers, same order, same parsed data.
- **Batched reads keep the old error semantics.** If the batched call fails, or
  any single response is not a plain success (401, non-JSON, HTTP ≥ 400), that
  URL falls back to the sequential `api_get` path with its original
  bounce-and-retry behaviour. The all-2xx path never takes the fallback.
- **The answer journal is parsed once.** It was re-read and re-parsed for every
  audited homework; it is now cached by `(mtime_ns, size)`, and both writes and
  external edits invalidate it.
- **The course feed is fetched once.** `list_my_courses()` was re-requested by
  every course lookup; the feed is now reused within the session, while
  `uplus_list_courses` — the tool that shows what is on the account right now —
  still forces a fresh read.
- **Version alignment.** `maven-package/pom.xml` was still on 0.8.13 while
  `pyproject.toml` said 0.8.14, which would have failed the release
  pipeline's version/tag check. Both are now **0.8.15**.
- **Tests.** 493 pass (478 existing with no regressions + 15 new).

## What's new in 0.8.14

Five issues from a full-source review of the 0.8.13 patrol output:

- **CLI repair hint no longer conflicts with graded homework.** `daily_patrol`
  counted `needs_model_answer` without checking `withdrawable`, so a graded
  (non-withdrawable) homework was told to 「撤回并补齐」 — impossible for 已出分
  作业. The count now splits into actionable (withdrawable) and informational
  (graded) with separate messages.
- **Per-item reason no longer contradicts the brief note.** `_repair_items`
  inherited 「待批改」 from `audit_questions` for unjudged items, while the brief
  note said 「作业已出分，平台不允许撤回」. A `graded` flag rewrites the unjudged
  reason for graded homeworks to 「平台已出分但未公布该题参考答案」.
- **Page/interface reconciliation reaches every layer.** `inspect_homework`
  and `list_homeworks` rows now carry `api_says_submitted` /
  `page_says_submitted` / `state_conflicts`; the patrol Markdown report gains a
  「页面状态与接口状态差异」 section. `_register` snapshots the API status before
  the page read overwrites `task.status`.
- **Graded homework score no longer shows `-`.** `from_api` checks
  `userScore` / `score` / `totalScore` / `myScore` at both the DTO and top
  level; `read_page_score` reads 总分：XX from the graded page as a fallback.
- **Minor fixes.** `repair_briefs` carry `withdrawable_source`; incomplete
  submitted homeworks are no longer duplicated in the takeover list;
  `build_answer_brief` sorts questions by order (was 3,2,1); `read_page_state`
  drops `0/0` progress as unusable.
- **Tests.** 478 pass with no regressions.

## What's new in 0.8.13

The U+ interface's answer rows lag behind the answer page: a question the student
filled can still come back with an empty `userAnswer`, and a homework handed in
on the page can read as `submitTask=0`. The 0.8.12 audit used the interface for
"which boxes have text" and the page for "is it handed in" without reconciling
them, which produced a self-contradictory verdict (「漏答 N 题，已出分无法撤回」)
in real use. This release fixes that class of misjudgement plus the other real
defects a further full-source review confirmed. No tool signature or existing
behaviour changes.

- **Both truth sources are reported.** Every audit returns
  `api_says_submitted` / `page_says_submitted`; when they disagree it lists
  `state_conflicts` and a `note` saying the page button is authoritative for
  submission and the漏答/漏选 verdict comes from the interface and may be stale.
- **An all-answered page is no longer reported as 漏答.** `finish_then_review`
  phase 2 reads the page answer card (`progress`): when it shows `N/N` and the
  audit's only finding is blank interface rows — no real partial, no wrong — the
  homework is complete. The lagging rows are kept as `api_only_missing` for
  transparency. A real multi-select partial (page filled with a subset) is still
  reported.
- **The written review report gains a 「页面状态与接口状态差异」 section**, and
  `patrol`'s incomplete note explains a stale-interface case instead of asserting
  a bare gap.
- **`withdrawable` distinguishes "unknown".** When the page was not read
  (`refresh_page_state=False` or the read failed) it is `None` with
  `withdrawable_source="unknown"`, not `False`; the CLI and report say 「需开页
  确认」 rather than 「已出分不可撤回」.
- **The journal is no longer used as a standard answer.** It records what *this
  tool* submitted, so treating it as the reference was circular:
  `userAnswer == journal` always held, so a multi-select the model answered
  incompletely (AB instead of ABCD) scored `correct` and was never reported — the
  fuller the journal, the more 漏选 slipped through. It may now stand in for a
  reference only when it proves a gap (the submission is a strict subset of the
  recorded intent); otherwise the question is `unjudged`. The platform's own
  answer still wins when it is available.
- **`repair_homework` no longer claims a withdrawal that did not happen.**
  `withdraw_homework` returns `"not_submitted"` without raising when the page
  shows the homework was never handed in; the repair now says so and reports
  `withdrawn=False` instead of 「已撤回」.
- **Comma-separated fill-blank answers no longer stall the homework.**
  `split_blanks` now splits multi-blank answers on `,` `，` `、` as well (a
  single-blank answer is never split, so a value containing a comma survives).
- **The fill-blank box picker excludes search/filter fields** even when no
  answer-labelled box exists, so a whole-page fallback can no longer type the
  first blank into the header search box.
- **Speed (verdicts unchanged).** `page_stem_positions` waits for the visible stem
  to change after a navigation instead of a fixed 1 s per question; `open`
  gained a `settle` flag (`open_homework` defaults to no generic settle because
  every caller waits for readiness itself — ~2.3 s saved per homework page,
  screenshots keep settling); the duplicated "list questions then fetch each
  detail" blocks share one helper; `audit_questions` and `_blank_boxes` stop
  computing the same thing twice.
- **Tests.** 464 pass with no regressions; `ruff --select F,B` clean.

## What's new in 0.8.12

A new **two-phase** orchestration separates "answer the homework" from "check
the homework", so the verdict on outstanding work reflects the whole account
after everything has been attempted — not a per-task snapshot taken mid-fill.
`uplus_patrol`'s signature and behaviour are unchanged.

- **`uplus_finish_then_review`** — the "先做作业，全部写完后再总体检索" flow.
  *Phase 1 (finish)* walks every active course in the fixed patrol order
  (course → due-date/title) and answers each not-submitted, non-expired
  homework in turn; answers arrive as `{task_id: {question_id: answer}}`.
  Graded / submitted / expired homeworks are never touched, and subjective,
  programming and attachment items go to `takeover` instead of being answered.
  A single homework's failure never aborts the rest of phase 1.
  *Phase 2 (review)* runs only after the last homework has been attempted: it
  re-reads every homework's real page state and runs the same completeness audit
  `uplus_patrol` uses, so "is anything still incomplete?" answers for the
  post-fill state of the whole account. `screenshot=True` additionally captures
  the answer page of each still-incomplete homework (`review[].screenshot`), so
  you can see the unfilled boxes before deciding whether to top up the answer or
  call `uplus_repair_homework`. Results carry `summary`, per-homework `finish`
  and `review` rows (with a `complete` flag), a `phase`-tagged `takeover` list,
  and a `report_path`.
  The submit gate is not relaxed: `submit=True` submits only when every
  objective question is answered with no write error.
- **No answers, no page open.** When a homework's objective questions have no
  answer yet, phase 1 records the question numbers it needs (`finish[].needs_answers`
  and `takeover`) and moves on — it does not open the answer page and fetch every
  question detail just to skip each one for "缺少模型答案".
- **Corrections to answered questions are not dropped.** Answers the caller
  supplies for questions the platform already marks answered (a partial
  multi-select that needs AB, a half-filled blank) reach `answer_homework`,
  which diffs and refills them. The finish phase used to treat such a homework
  as "无客观题待答" and silently discard the correction.
- **Phase 1 reads the API only, never opens the page.** Deciding which
  questions are blank is done through `list_questions` + `question_detail`
  (`_homework_question_details`), so the finish phase costs one page load per
  homework (the fill) instead of two.
- **The review report is real.** `report_path` points to
  `reports/review_YYYYmmdd_HHMMSS.md` rendered from the actual finish / review /
  takeover data (`render_finish_review_markdown`), not an empty snapshot, and
  carries a second-resolution name so it cannot overwrite a patrol report in
  the same minute.
- **Faster retrieval, same verdicts.** A patrol used to load the same homework's
  answer page 2–3 times (read state → fill → audit), each a full reload. `open`
  now returns the page it is already on when the resolved target matches
  (fragment ignored; scheme/host/path/query compared exactly), so one homework
  costs one reload while a different task or course still reloads. A fresh page
  open waits for `domcontentloaded` and then only a short bounded settle —
  not the old up-to-8-second `networkidle` that never quietens on course packs
  with analytics/long-poll. The submit and withdraw confirmation loops use a
  small button-label probe (`_probe_submit_button`) instead of serialising the
  whole body each round, falling back to the full read whenever the probe is
  inconclusive — the verdict is unchanged. `_await_page_state` polls every
  150 ms after `domcontentloaded`. The `api_get` AccessToken probe runs once per
  session instead of once per call, and `course_name` is cached per course so a
  live-mode patrol does not re-fetch the whole course feed for every row. The
  two phases share one account-course read. `read_experiments` re-reads only
  when the first snapshot looks unfinished, the experiment tab reads drop the
  sleeps that duplicated `_click_tab`'s own wait, and
  `download_experiment_materials` picks the template from the 资料 entries it
  already walked (`find_report_template(walk_materials=False)`) instead of
  walking and re-downloading the tree a second time.
- **Experiment tab failures are no longer silent.** When the 实验提交物 / 实验手册 /
  实验附件 tab cannot be read, the reason is recorded in
  `ExperimentDetail.tab_errors` (redacted) and returned as `tab_errors` from
  `uplus_read_experiment_detail` and `uplus_download_experiment_materials`, so an
  empty `manual_links` no longer means either "no manual" or "manual
  unreachable".
- **Other fixes.** `_prune_empty_sections` no longer deletes a heading that
  carries its body inline on the same line (实验目的：掌握…). A multi-select
  re-read that goes `None` mid-verification is retried and must confirm before
  it counts as filled. Login-failure receipts use a safe `_body_hint` read that
  degrades to "" instead of raising mid-navigation. `capture_question` /
  `capture_question_image` accept an optional `occurrence` so a duplicate-stem
  question can be screenshotted instead of always capturing the first copy.
- **Tests.** 8 two-phase cases, 9 page-reuse/token-probe cases, 5 button-probe
  cases, 3 experiment-tab/template cases and 2 login-hint cases among others;
  the full suite (443) passes with no regressions.

## What's new in 0.8.11

A full-source security audit fixed eight classes of vulnerability without
changing any tool signature or behaviour:

- **Cross-origin credential isolation.** `utils.is_same_origin` compares
  scheme + host:port strictly. `BrowserSession.api_get` refuses to send the
  `x-access-token` header or `credentials: 'include'` to a non-platform origin;
  `fetch_bytes` only runs the token-bearing in-page `fetch` for same-origin
  resources, sending a cross-origin OSS signed link through
  `context.request.get` instead, so the platform session token never leaks to a
  third-party host.
- **Route/query parameter injection.** New `utils.safe_url_segment` validates
  `course_id`, `task_id`, `homework_id`, `question_id`, `exp_id` and
  `stu_exp_id`, refusing `..`, `/`, `\`, `?`, `#`, `@`, `%`, whitespace and
  control characters while passing real IDs through unchanged. Applied in
  `platform.py`, `operator.py`, `experiment.py` and `materials.py`.
- **Launcher and config hardening.** `launch_browser_with_cdp` validates its
  `url` through `safe_navigation_url` before launching (blocking `file://` and
  Chromium `--flag` argument injection); ports are strictly `1..65535` and
  booleans are rejected in `cdp_alive`, the launcher and `config.load_config`.
  `_copy_profile` skips symlinks that point outside the source directory and
  enables `ignore_dangling_symlinks=True`.
- **Deeper secret redaction.** `redact_secrets` now also masks
  `Authorization: Bearer/Basic` credentials, standalone JWTs, whole
  `Cookie`/`Set-Cookie` lines and `session_id`/`JSESSIONID`/`PHPSESSID`, split on
  `&` so multi-parameter values are not missed. Applied to `browse.act_batch`
  failure receipts, `browser` login-failure echoes and `engine` error summaries.
- **Path and navigation boundaries.** `resolve_io_path` refuses empty paths and
  NUL bytes, and fails closed when `allowed_io_paths` is configured but none
  resolve; `safe_navigation_url` refuses control characters.
- **Same-origin materials crawl.** `materials.walk` checks same-origin at the
  entry and on every sub-folder jump, so a page link cannot trigger a
  cross-origin crawl.
- **Selector and atomic-write hardening.** `browse.snapshot` `CSS.escape`s the
  `data-testid` and `name` attributes; `journal.record` writes via
  `tempfile.mkstemp` + `os.replace` instead of a fixed `.tmp` file.
- **CI publish hardening.** `.github/workflows/publish-packages.yml` passes
  `ACTOR`/`GH_TOKEN`/`MVN_VER` through `env:`, XML-escapes the Maven
  `settings.xml` and writes it with `0600` permissions.
- **Tests.** 13 new security regressions; the full suite passes with no
  regressions in the existing 386 tests.

## What's new in 0.8.10

A full re-audit fixed the download path, error redaction and filename handling,
and stopped opening pages for expired homework:

- **Downloads use the navigation allow-list.** `BrowserSession.fetch_bytes` is
  the single entry point for experiment attachments, handbook files, course
  materials and templates; it used to hand any URL to the browser request
  context. It now validates with `utils.safe_navigation_url` first, so
  `file://`, `javascript:`, `blob:` and `data:` links are refused. Folder
  traversal in `materials.walk` goes through the same check instead of calling
  `page.goto` on a raw joined URL.
- **Error redaction is wider.** `redact_secrets` now also masks `signature`,
  `access_key(id)`, `credential` and `cookie`; API, 401, non-JSON, download
  failure and request-error messages redact both the URL and the short response
  body, so OSS signed query parameters and cookies never reach the model.
- **One filename sanitizer.** `utils.safe_filename` removes path separators,
  control characters and leading/trailing dots/spaces, prefixes Windows device
  names (`CON`, `NUL`, `COM1` …) with `_`, and caps the length at 120. It is
  used for attachments, handbook files, course materials and templates.
- **Expired homework pages are skipped (0.8.10–0.8.15).** These releases open
  answer pages only for API-unsubmitted, non-expired homework to save time.
  **From 0.8.16, refreshed page state reads every task, including expired ones,
  read-only, to correct API under-reporting; expiry still blocks all writes.**
- **Launcher audit.** The CDP probe uses a fixed loopback URL with an `int()`
  port; `subprocess.Popen` uses an argv list with `shell=False`. Bandit reports
  no MEDIUM/HIGH findings.
- **Tests.** 388 pass locally (including the two real headless-Chromium DOM
  cases; CI without a Playwright browser skips those two and runs the other
  386). `pip-audit` finds no known dependency vulnerabilities; `ruff F/B`
  passes. New regressions cover `safe_filename`, signed-URL/cookie redaction,
  `fetch_bytes` rejecting `file://`, and the expired-homework skip.

## What's new in 0.8.9

The platform does not guarantee the order of its course, homework or experiment
feeds, and mixing the all-course homework list with per-course experiment calls
made the browser jump between courses on every run. This release fixes the order
and adds a single-course scan:

- **Stable course order.** Configured courses keep the order written in
  `courses.json`; a live feed is sorted by name + id, so a changed API order no
  longer changes the result.
- **Stable homework order.** Within a course, tasks are sorted by due date,
  then title, then task id; tasks without a deadline go last.
- **Stable experiment order.** `实验N` is parsed from Chinese or Arabic numerals
  (实验一 → 实验二 → 实验十); unnumbered titles go last.
- **New `uplus_scan_course(course_id, refresh_page_state=True,
  verify_experiments=False)`.** It reads exactly one course: one homework feed
  call, one answer-page load per *unsubmitted* homework (0.8.9–0.8.15; 0.8.16
  reconciles all tasks when refresh is enabled), then one experiment list page. It returns `homeworks`, `experiments` and `summary`; no other
  course is touched. With `verify_experiments=True` it also opens each
  experiment detail page in the same pass; the default keeps the fast list
  badge.
- **Faster/clearer list tools.** `uplus_list_experiments` gains an optional
  `verify` flag (`False` reads the list badge only, one page load for the whole
  course). `uplus_list_homeworks(refresh_page_state=True)` opens only
  unsubmitted homework pages in 0.8.9–0.8.15; 0.8.16 reconciles every row. `uplus_patrol` is now explicitly course-major:
  all of one course's homeworks, then its experiments, then the next course.
- **Recommended retrieval flow.** Follow the order returned by
  `uplus_list_courses` and call `uplus_scan_course` once per course; finish that
  course before starting the next. Use `uplus_patrol` only when a full patrol
  report is needed.
- **Tests.** 384 pass locally (including the two real headless-Chromium DOM
  cases; CI without a Playwright browser skips those two and runs the other
  382). `tests/test_scan_order.py` adds 7 cases for the ordering and single-
  course scan.

## What's new in 0.8.8

The live answer page renders fill-in-the-blank questions with a plain stem and
one textarea per blank — 答案一 / 答案二, placeholder 请输入填空项答案 — sometimes
without `.ck-content` and with the blank drawn as an empty styled span rather
than `____`. The old locator could miss the question card or pick the header
search box, so those homework items could never be filled and the homework
stayed blocked at the submit gate. This release fixes the whole path; a
completed set of objective answers still submits automatically.

- **Generic stem locator fallback.** When `.ck-content` has no matching stem,
  the new marker scans known title containers, text nodes and element text,
  finds the most specific stem element, then walks up to the question card
  that really contains a usable answer box. A list page no longer falls back
  to the whole page and writes into a neighbour.
- **Answer-box filtering.** Only visible, editable, non-search inputs count:
  `input`, `textarea`, `contenteditable` and `role=textbox`. Boxes whose
  placeholder / `data-placeholder` / `aria-label` carries 填空 or 答案 are
  preferred; once they cover the stem's blank count, header search and filter
  fields are excluded.
- **Blank spans without `____`.** Some pages draw each blank as an empty span,
  so the DOM text has no underscores. Stem matching now retries with
  underscore runs stripped, covering both the single-blank and the
  答案一/答案二 layouts.
- **DOM value fallback.** When both `fill` and keyboard typing are swallowed,
  the native value setter writes the box and dispatches `input` / `change`;
  every box is still read back, and a value that cannot stick still blocks
  submission instead of passing a half-filled question.
- **Single-question detection.** A page that renders only 上一题 or only 下一题
  (common on the first/last question) is now treated as a single-question view.
- **Runtime hardening.** String booleans in config (`"false"`, `"0"`,
  `showScore: "0"`) are parsed instead of being truthy; a 401 with no retry
  budget raises instead of returning `{}`; expired configured courses no longer
  widen the homework query to unrelated live courses; an unreadable experiment
  detail status keeps the list badge; `networkidle` timeouts no longer fail the
  experiment read; relative material links resolve against the folder page;
  uppercase/dashed experiment ids are accepted; a failed `uplus_close` detach
  still clears the cached session.
- **Tests.** 377 pass, including two real headless-Chromium cases that render
  the screenshot layouts (no `.ck-content`, blank spans, single/double
  textareas); they skip automatically where no Playwright browser is installed.
  Tool signatures and existing flows are unchanged.

## What's new in 0.8.7

Signing in no longer has a default. `uplus_login` used to offer manual/auto,
with auto hard-wired to the password tab. It now asks the person in three
dialogs and only acts once it has answers:

- **Dialog 1 — who is signing in** (`stage="owner"`): yourself, or on someone
  else's behalf. On-behalf is carried back as a marker.
- **Dialog 2 — how** (`stage="method"`): student/employee ID, SMS code, or
  phone/account + password.
- **Dialog 3 — the details** (`stage="credentials"`): only the fields that
  method needs, plus which ones are still missing. Student ID requires a school;
  phone/password does not ask for one.
- Only a complete set triggers a fill-and-submit; result is
  `stage="done"` on success or `stage="login_failed"` on failure.
- **SMS sign-in parks the browser on the SMS tab** (`stage="manual_sms"`):
  the person types the phone number and code; the tool never fills, reads or
  stores them.
- **Tab switching is verified, not assumed**: it waits for the three labels to
  render, only clicks the element whose text is exactly the label, then confirms
  the form really changed by reading the visible inputs' placeholders. If it
  cannot, the result carries `tab_selected: false` so you can pick the tab
  yourself.
- Credentials stay in memory for that call, are stripped before the result is
  returned, and never reach config, reports or logs.

## What's new in 0.8.6

A real course pack mislabels a plain fill-in-the-blank sentence as a programming
question: 「用于表示请求映射的通用注解是 ______」 and 「处理 GET 请求用____注解，
处理 POST 请求用____注解」 arrive as `qsnType=6` (编程题), while the answer page
renders them as 填空题 with 答案一 / 答案二 boxes. The old code trusted the type
code, handed both items to the student, and the homework could never be
submitted. This release reclassifies by the stem and wires those blanks back into
the automatic path.

- **Stem-based reclassification.** `models.is_blank_style` (1–10 blanks, no code
  markers) plus `models.looks_like_code` feed `Question.from_detail`: a type-6
  question with no options whose stem really is a blank sentence becomes
  `FILL_BLANK(4)`. It then gets a `blank_count` in the answer brief, is filled
  one box at a time, is audited blank by blank, stops counting as
  `subjective_skipped`, and no longer blocks the submit gate.
- **Real programming prompts are untouched.** Stems carrying `{` `}` `public `,
  `class `, `#include`, `console.log` (and other code markers), more than ten
  blanks, or any options stay `PROGRAM` and stay with the student; `SHORT_ANSWER`
  is never reclassified.
- **The platform's joined blank answer is parsed.** A submitted multi-blank
  answer comes back as one string joined by a run of `@`
  (`@GetMapping@@@@PostMapping` for two blanks). `solver.split_blanks` now falls
  back to `@{2,}` as the separator when the usual ones come up short, so the
  audit and the half-filled refill check no longer report a complete answer as
  missing a box.
- **Tests.** `tests/test_program_blank.py` adds 13 cases covering the
  reclassification, the code/short-answer guards, per-box filling and the joined
  answer parsing.

## What's new in 0.8.5

Fill-blank questions (填空题) had no real answer path: the plugin saw one
textarea per question, but the platform renders **one box per blank** — the
question 「处理 GET 请求用____注解，处理 POST 请求用____注解。」 shows 答案一 and
答案二, and the single answer that used to be written only ever reached the
first box.

- **Blanks are counted and the answer is split.** `models.count_blanks` counts
  the underscore runs in the stem; `Question.llm_prompt` now carries
  `blank_count` and an answer hint that asks for one value per blank. The brief
  says the same. `solver.split_blanks` accepts a newline-separated list, a JSON
  array, or a Chinese separator, and `normalize_answer` rejects a multi-blank
  answer that is short one value instead of leaving a question half filled.
- **Each blank gets its own box, and every box is read back.** `_answer_blank`
  now enumerates the question's answer boxes (inputs, textareas *and*
  contenteditable rich-text editors), writes one value into each in DOM order,
  and verifies each one. A value that does not stick after a retry raises, so
  the submit gate refuses a half-filled question instead of passing it. A
  question with fewer boxes than answers is an error, not a silent drop. A
  contenteditable editor that ignores `fill` is typed into instead.
- **Half-filled fill-blanks are repaired, not passed off as done.** In the
  answer flow, a fill-blank whose recorded answer carries fewer values than the
  stem has blanks is re-filled when a model answer exists; without one it is
  reported as unverifiable and blocks submission. `audit_questions` compares
  fill-blank answers value by value, so a platform that joins its blanks with a
  different separator is not called wrong, while a missing blank lands in
  `partial` with `missing_blanks`.

全量 **333 个测试通过**（新增 `tests/test_fill_blank.py` 24 例）。

## What's new in 0.8.4

Two fixes requested after field use:

- **Page-mapping failures now skip, not veto.** On single-question pages the
  stem→position map can fail for an individual question. It used to be recorded
  as an error, and `can_submit` requires zero errors — so one unmappable
  question stopped the entire homework from being submitted. Such questions are
  now recorded in `skipped_questions` with `blocking=false`, reported in the
  new `unlocated_skips` counter, and excluded from the submit gate. A question
  is still never answered blind: if the page cannot be mapped or the stem does
  not match after navigation, it is skipped rather than typed into the wrong
  question. Genuine gaps (no model answer) remain blocking.
- **Experiments are retrieval-only.** The report-writing pipeline is removed:
  `uplus_answer_experiment`, `uplus_audit_experiment`,
  `uplus_generate_experiment_report` and `uplus_submit_experiment_report` are
  gone. Kept: `uplus_list_experiments`, `uplus_read_experiment_detail`,
  `uplus_download_experiment_materials` (attachments, handbook links and
  recursive 资料 template search). The patrol still lists experiments and
  flags outstanding ones, but no longer generates or audits reports. Homework
  answering is unchanged.

## What's new in 0.8.3

A full live run (login → list → generate → verify → upload → submit) exposed
two things: reports carried the handbook's own wording instead of what the
student actually did, and a submission that the page called successful was not
kept by the server. Suite is 334 passed, 1 skipped.

- **Report bodies hold real work, not handbook text.** `answer_experiment` used
  to merge the sections derived from the manual straight into the report; the
  handbook now comes back as an `auto_sections` hint and is never written into
  the document. Missing sections are reported as missing — nothing is invented.
- **A generated report carries the teacher cover.** With no template found, the
  built-in skeleton had no cover, so the compliance gate blocked it and no
  experiment could ever be submitted. `generate_report(..., cover=...)` writes
  school / 实验报告 / 实验名称 / 专业 / 班级 / 学号 / 姓名 / 辅导教师 / 实验时间 /
  学时数; a blank value keeps its label so the gate warns about a fillable field
  instead of a missing one. New config keys `student_school`, `student_major`,
  `student_class`, `student_teacher` stay in the gitignored personal config.
- **No empty headings or scaffold labels.** The skeleton used to hardcode
  「（此处填写算法伪代码与实现代码）」「算法伪代码：」「实现代码：」
  「时间复杂度分析：」「测试用例结果：」and an empty 「实验报告要求」 chapter.
  Each section is now one empty slot, and `_prune_empty_sections` removes any
  heading whose body stayed empty after filling.
- **Long bodies split at sentence boundaries**, so nothing trips the 180-character
  layout check.
- **Two compliance fixes.** Item 4 no longer hardcodes 「多出实验内容」 (it fired
  on templates that do carry that chapter); item 5 now reports
  「手册要求未能解析，本项未核对」 instead of silently passing when no requirement
  could be parsed.
- **A submission counts only after a server-side re-check.** The page showed
  已提交 + 撤回 right after the click, then came back as 未提交 on the next visit
  with the file still sitting there. `submit_report` reloads and re-checks, re-clicks
  once if it fell back, and returns `status_verified`; `engine` reports
  `submitted: true` only when that check passed.
- **Experiment status comes from the detail page.** The list badge reported
  `submitted: false` for every experiment, including ones the platform itself
  showed as 已提交. `list_experiments(verify=True)` re-checks each row against
  the detail page and reports `status`, `submitted_files` and `submitted_source`.
- **Submitted files expose the download button.** The platform renders 「下载」 as
  a `<button>`, so `submitted_links` was always empty; `submitted_download_buttons`
  now records it.

## What's new in 0.8.2

Four places where the 0.8.0 compliance work still got stuck on the real
platform. Suite is 321 passed, 1 skipped.

- **Phone-number accounts can sign in automatically.** The school login page
  opens on the 学号/工号 tab, whose account box asks for a student/staff number
  and requires picking a school — a phone number cannot sign in there.
  `auto_login` now switches to the 密码登录 tab first (it detects the tab via
  `input[placeholder*='手机']`) and skips the school step once there; login
  pages without that tab keep the old single-form path.
- **Drawing rows are no longer read as placeholders.** `is_placeholder_text`
  treated a whole row of dots or hashes (a grid map, a state-space diagram) as
  an unfilled slot and refused the report. Rows made only of `. # * · @ +` are
  now treated as content; `____`, `---`, `（此处填写…）`, `待填写` and `TODO`
  are still placeholders.
- **A repeated sub-heading no longer truncates a section.** A body line
  「一、算法伪代码」 resolves to the 实验实现 alias; a repeat used to count as a
  new section boundary, cutting the earlier section's body off and reporting it
  as empty. `_missing_sections` now takes only the first sighting of each
  section as its heading.
- **Navigation timeouts retry.** Some course `ExperimentPage` screens do not
  render within 30 seconds, so the whole course appeared to have no
  experiments. `list_experiment_ids` and `open_detail` now go through
  `_open_page` (45 s timeout, one retry) and only fail after both attempts;
  `BrowserSession.open` accepts `timeout_ms`.
- **Tests:** `tests/test_optimizations.py` adds 11 cases.

## What's new in 0.8.0

The complete experiment pipeline, plus a numbered pre-submission compliance
checklist that blocks hard failures before upload and fixes the "phantom
submission" where an upload alone was reported as submitted. Suite is 310
passed.

### Compliance checklist and the phantom-submission fix

The pipeline could tell whether a document *had content*; it could not tell
whether the teacher would accept it. This part adds a numbered 19-item
compliance checklist that blocks the hard failures before upload, and fixes
the "phantom submission" where an upload alone was reported as submitted.

- **New `compliance.py`** — findings come in three levels: `block` (refuse to
  submit), `warn` (docked marks, still allowed), `manual` (cannot be judged
  automatically, listed as `skipped` instead of silently passing). Items cover
  the teacher-template cover fields (school, major, class, student id, name,
  teacher), 实验时间/学时, the 实验名称 field, section structure vs the
  template, handbook requirement coverage, screenshots when the handbook
  demands them, environment claims without evidence, reproducibility
  (commands/seeds/paths/parameters), AI-sounding meta talk, duplicated
  sections, table and long-line layout, and hash-named files.
- **Stricter gate** — `verify_report` gains `strict_compliance` (default
  `True`): any blocking item refuses the upload with the item number and the
  missing detail. `answer_experiment` / `uplus_answer_experiment` gain
  `force` to downgrade the checklist to warnings.
- **No silent re-submission** — `answer_experiment` reads 「已提交」 and then
  only generates, telling the caller to withdraw first.
- **Phantom submission fixed** — `upload_report` clicks a real 「提交」
  `button` (the old code clicked any `div` carrying that text, which did
  nothing), accepts a confirmation dialog, then polls the page until
  「已提交」+「撤回」 appear and raises otherwise. `submit_report` now
  requires the 「已提交」 badge *and* either 「撤回」 or the file in the
  submitted list — an upload's "转换成功" line is no longer success.
- **Template search covers three sources** — 实验附件 → 实验手册 (new
  `manual_links` capture) → 资料 (recursive), preferring a genuinely
  downloadable file and reporting its `source`.
- **Tests** — new `tests/test_compliance.py` (11 cases) plus strict/non-strict
  gate cases; suite is 310 passed.

### Experiment pipeline

Read the experiment's requirements, search **all sources** (实验附件 → 实验手册
→ 资料, recursively) for the report template, download the materials, fill the
template, and only submit after the inspection and compliance checks pass.

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

Ask the MCP client to run `uplus_login`. The tool never assumes a login method;
it asks three questions in order and only then touches the form:

1. **Who is signing in** — yourself, or someone else on their behalf.
2. **How to sign in** — student/employee ID, SMS code, or phone/account + password.
3. **The details** — student ID method needs school, account and password; the
   phone/password method needs only account and password. Once all fields are
   present the tool fills them, submits and verifies the session. Credentials
   live in memory for that call only: never saved, never in reports, never
   echoed back in the tool result.

**SMS-code sign-in is handed to you**: the tool only parks the browser on the
SMS tab, and you type the phone number and code yourself. The model never fills
or sees them.

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
| `uplus_login` | Three-dialog sign-in: owner (self / on behalf) → method (student ID / SMS / phone+password) → credentials; student ID and password methods auto-fill and submit, SMS parks the page for the human |
| `uplus_check_login` | Wait for login readiness and validate a protected read |
| `uplus_logout` | Explicit `confirm=True` sign-out via the platform UI, with verification |
| `uplus_list_courses` | List courses under the signed-in account |
| `uplus_list_homeworks` | List homework status; open answer pages to verify button truth on demand |
| `uplus_list_experiments` | List experiments with `exp_id` / `stu_exp_id` and submission state (submitted / outstanding / unknown) |
| `uplus_read_experiment_detail` | Read an experiment's requirements, material, deadline and submitted files (read-only) |
| `uplus_download_experiment_materials` | Download the experiment's attachments plus the 资料-section report template (read-only) |

> Experiments are **retrieval and viewing only**: the server lists experiments,
> reads their detail pages and downloads requirements/templates. Filling in,
> uploading and submitting experiment reports is done by the student.
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

- 513 unit/regression tests cover models, configuration, answer normalization,
  page/API reconciliation, login readiness, sign-out orchestration, experiment
  retrieval and security hardening. They do not use real account credentials.
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
- The server never persists your password; `uplus_login` uses credentials only
  for the current call and never writes them to config, reports or logs, and
  never echoes them back in tool results. The SMS-code method is handled entirely
  by you in the browser, so the tool never receives a phone number or code.

## License

MIT, see [`LICENSE`](LICENSE).
