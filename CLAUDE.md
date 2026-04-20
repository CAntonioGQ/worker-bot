# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- Install deps: `uv sync`
- Run the bot: `uv run python main.py`
- Run tests: `uv run pytest`
- Run a single test file: `uv run pytest tests/test_crons.py`
- Run a single test: `uv run pytest tests/test_crons.py::test_name -v`
- Windows restart-on-crash wrapper: `powershell -ExecutionPolicy Bypass -File run.ps1`

Tests set dummy env vars in `tests/conftest.py` before importing modules, and use the `tmp_db` fixture to redirect `db.DB_PATH` to a temp path — never touch the real `sessions.db`.

## Architecture

Single-process async Telegram bot that dispatches chat messages and cron-triggered prompts into [Aider](https://aider.chat) running as a subprocess against one of several local git repos. Proactive crons run on an isolated branch with an approval flow so the bot can "work while you're away" without touching your main branch without consent.

### Request flow (chat)

1. `main.py` → `bot.build_app()` wires `python-telegram-bot` handlers and `post_init`/`post_shutdown` hooks.
2. On startup: `init_db()`, `init_crons_db()`, `scheduler.start()`, `load_and_schedule_all()` rehydrates crons, `schedule_heartbeat()` adds the daily health-check.
3. Text messages hit `on_message` → budget check → resolves active project → `locks.lock_for(project)` → `aider_runner.run_aider()` → records usage → replies in ≤3800-char chunks with cost.

### Cron flow (autonomous)

Each cron run in `crons.run_cron_job` follows this sequence under the project lock:
1. **Budget gate** (`budget.over_budget`) — if the daily USD cap is hit, skip the run.
2. **Clean-tree precondition** — abort if the project has uncommitted changes (we refuse to shuffle your WIP).
3. **`git fetch --all --prune`** so Aider works against fresh upstream.
4. **Isolate on a new branch** `bot/cron-{id}-{YYYYMMDD-HHMMSS}`.
5. **Prompt with memory** — `suggestions.memory_block(cron_id)` appends the last 3 summaries for that cron so it doesn't repeat itself.
6. **Run Aider**, record token/cost usage.
7. **If diff present** → commit on the bot branch, create a `pending_changes` row, return to original branch.
8. **If no diff** → delete the empty branch, store the text as a `suggested_tasks` row for `/do <id>` later.
9. **Every run** gets a `cron_runs` record (used for memory + heartbeat).

Cron messages use inline keyboards (`📋 Diff` / `🧪 Tests` / `✅ Push` / `❌ Descartar`) routed through `pending_callback` with callback data `pc:{pending_id}:{action}`. Tests and push checkout the bot branch, do the work, then restore the original branch — all inside the lock.

### Per-project serialization

`locks.py` exposes an in-memory `defaultdict(asyncio.Lock)` keyed by project name. Aider, git, and test invocations all take this lock. Cron approval callbacks (tests, push) also take it. This lock is per-process — it does not guard against an external editor modifying the repo.

### State (SQLite at `sessions.db`)

Schema lives in `db.init_db()` and `crons.init_crons_db()`:

- `chat_state` — `chat_id → active project`
- `crons` — persisted cron definitions (rehydrated on startup)
- `cron_runs` — history of every cron execution; powers memory + heartbeat
- `suggested_tasks` — text-only suggestions from crons, consumed by `/do`
- `pending_changes` — cron-generated diffs on isolated branches awaiting approval
- `usage_log` — per-call token/cost records; powers the daily cap

`db._conn()` is imported by other modules (`crons`, `budget`, `suggestions`, `approvals`) — they all share the same connection helper. Put new schema in the respective `init_*_db()` functions.

Aider's own chat history lives inside each project repo as `.aider.chat.history.md` (plus `.aider.input.history`, `.aider.llm.history`). `/reset` deletes these in the active project. Aider is invoked one-shot with `--message` but continues conversations via these files.

### Projects and authorization

- `config.PROJECTS` is a static dict of `name → Path`, populated from `PROJECT_*` env vars. Adding a project requires both a new env var **and** a new dict entry — there is no auto-discovery.
- `TELEGRAM_ALLOWED_USER_IDS` is a set of ints parsed from a comma-separated env var. The legacy singular `TELEGRAM_ALLOWED_USER_ID` is still read as a fallback. `_authorized(update)` gates every handler and the callback query handler; unauthorized messages are silently logged and dropped.
- Each authorized user gets isolated state because they identify by private-chat `chat_id`.

### Aider subprocess contract

`aider_runner.run_aider` returns an `AiderResult(output, tokens_in, tokens_out, cost_usd)` dataclass. It hardcodes flags (`--yes-always`, `--no-auto-commits`, `--no-pretty`, `--no-stream`, `--no-check-update`) and env (`TERM=dumb`, `NO_COLOR=1`, `PYTHONIOENCODING=utf-8`) — these are load-bearing on Windows and for clean output parsing. `_extra_read_files()` auto-injects `CLAUDE.md`, `CONVENTIONS.md`, or `.aider.conventions.md` from the project root via `--read` so Aider gets your conventions without you carrying them in every prompt. Note: Claude Code **skills** are not read — only the `CLAUDE.md` file. `clean_output` strips Aider's banner lines (see `NOISE_PREFIXES`, which now also includes `Tokens:` / `Cost:` — those are parsed out by `_parse_usage` and reported via the dataclass). Timeout defaults to 300s; on expiry the process is killed and a `[timeout]` message returned.

### Budget / safety

`budget.py` writes every Aider invocation to `usage_log` with a `source` tag (`chat`, `cron:{id}`, `do:{task_id}`). `over_budget()` sums today's UTC-date spend and compares to `DAILY_BUDGET_USD`. When the cap is reached, `on_message` and each cron run short-circuit with a message. Set `DAILY_BUDGET_USD=0` to disable the cap.

### Heartbeat

`crons.schedule_heartbeat` registers a job id `heartbeat` using the `HEARTBEAT_CRON` expression (default `0 8 * * *`) to DM `HEARTBEAT_CHAT_ID` (defaults to the smallest allowed user id). Reports: active cron count, last run timestamp, today's spend, last potentially-broken run.

### Test runner

`test_runner.py` auto-detects the test command from project markers (`pyproject.toml` → `pytest`, `package.json` with a `test` script → `npm test`, `Cargo.toml` → `cargo test`, `go.mod` → `go test ./...`). Override per project with `PROJECT_<NAME>_TEST_CMD=<cmd>` in `.env`. Invoked manually with `/tests` or from the cron approval keyboard.

### Git commands

`bot.py` exposes `/git_*` handlers that shell out via `git_runner.run_git` (async subprocess, `GIT_TERMINAL_PROMPT=0` to avoid credential prompts). `/git_push` refuses to push from `main`/`master` as a safety rail. `/git_switch` refuses when the working tree is dirty. All git commands take the project lock.

### Cron scheduling details

- Timezone is hardcoded to `America/Mexico_City` in `crons.py` (`TIMEZONE`).
- User cron job IDs follow `cron_{id}`; the heartbeat uses id `heartbeat`.
- `schedule_cron` uses `replace_existing=True` so rescheduling is idempotent.
- Bot-created branches follow the prefix `bot/cron-` — treat this prefix as owned by the bot (it'll delete empty ones automatically).

## Logging

`bot._configure_logging()` runs at import time and installs a `TimedRotatingFileHandler` (midnight rotation, 7-day retention) writing to `logs/bot.log`, plus a stream handler. The `_error_handler` catches unhandled exceptions, logs the traceback, and DMs a one-line summary to the originating authorized user only.

## Conventions in this codebase

- User-facing strings (Telegram messages, log lines) are in Spanish. Match that when adding handlers.
- Long Telegram replies are chunked to 3800 chars (`MAX_TG_LEN`) — Telegram's limit is 4096 but there's headroom for headers.
- Handlers early-return on `not _authorized(update)` — keep that pattern; callback queries use `query.answer("no autorizado", show_alert=True)` instead.
- Any new Aider invocation should `record_usage(...)` so the daily cap stays accurate.
