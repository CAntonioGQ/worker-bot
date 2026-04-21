# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- Install deps: `uv sync`
- Run the bot: `uv run python main.py`
- Run tests: `uv run pytest`
- Run a single test file: `uv run pytest tests/test_crons.py`
- Run a single test: `uv run pytest tests/test_crons.py::test_name -v`
- Windows restart-on-crash wrapper: `powershell -ExecutionPolicy Bypass -File run.ps1`

Tests set dummy env vars in `tests/conftest.py` before importing `workerbot`, and use the `tmp_db` fixture to monkey-patch `workerbot.storage.db.DB_PATH` to a temp path — never touch the real `sessions.db`.

## Architecture

Single-process async Telegram bot that dispatches chat messages and cron-triggered prompts into [Aider](https://aider.chat) running as a subprocess against one of several local git repos. Proactive crons run on an isolated branch with an approval flow so the bot can "work while you're away" without touching your main branch without consent.

### Package layout

The code lives under the `workerbot/` package organized in four layers. Import direction flows downward — higher layers may import from lower ones, never the reverse:

```
workerbot/
├── config.py         # env vars, PROJECTS, paths — no internal deps
├── app.py            # build_app (PTB Application), logging, error handler
├── storage/          # SQLite CRUD, no async, no telegram
│   ├── db.py         # _conn + init_db (all tables in one place)
│   ├── chat_state.py
│   ├── crons.py
│   ├── suggestions.py       # cron_runs + suggested_tasks
│   ├── approvals.py         # pending_changes
│   └── usage.py             # usage_log
├── core/             # domain logic: uses storage + runners
│   ├── locks.py
│   ├── budget.py
│   ├── memory.py            # memory_block + summarize
│   └── scheduler.py         # APScheduler + run_cron_job + heartbeat
├── runners/          # subprocess / external wrappers, no DB
│   ├── aider.py             # subprocess wrapper
│   ├── git.py               # subprocess wrapper
│   ├── test_runner.py       # subprocess wrapper
│   ├── llm.py               # direct OpenRouter HTTP client (no Aider subprocess)
│   └── news.py              # RSS fetch + parse + prompt-format
└── handlers/         # telegram handlers by feature
    ├── base.py              # authorized(), reply_chunked, active_project
    ├── session.py           # start/ping/whoami/projects/current/use/reset
    ├── cron.py              # /cron_*
    ├── tasks.py             # /tasks /do /skip /pending
    ├── git.py               # /git_*
    ├── ops.py               # /tests /budget + on_message (chat)
    └── approvals.py         # CallbackQueryHandler for pc:* callbacks
```

`main.py` at the root is a 3-line entrypoint that imports `workerbot.app.build_app()` and runs polling.

### Request flow (chat)

`handlers.ops.on_message` → budget gate → resolve active project → `core.locks.lock_for(project)` → `runners.aider.run_aider()` → `storage.usage.record_usage()` → chunked reply (≤3800 chars) + cost footer.

### Cron flow (autonomous)

Each cron run in `core.scheduler.run_cron_job` follows this sequence under the project lock:
1. **Budget gate** (`core.budget.over_budget`) — if the daily USD cap is hit, skip the run.
2. **Stash WIP if dirty** — if the working tree has uncommitted changes, `git stash push -u` saves them; they're popped back at the end. The cron never aborts on a dirty tree.
3. **`git fetch --all --prune`** so Aider works against fresh upstream.
4. **Isolate on a new branch** `bot/cron-{id}-{YYYYMMDD-HHMMSS}`.
5. **Prompt with memory** — `core.memory.memory_block(cron_id)` appends the last 3 summaries for that cron so it doesn't repeat itself.
6. **Run Aider** (with `core.prompts.extract_model_marker` applied first so `@heavy`/`@weak` prefixes pick `AIDER_HEAVY_MODEL` or `AIDER_WEAK_MODEL`), record token/cost usage.
7. **If diff present** → commit on the bot branch, create a `pending_changes` row, return to original branch.
8. **If no diff** → delete the empty branch, store the text as a `suggested_tasks` row for `/do <id>` later.
9. **`finally`: restore original branch + `stash pop`** if a stash was created. If pop fails (rare conflict), the user gets a Telegram warning and the WIP stays in `git stash list`.
10. **Every run** gets a `cron_runs` record (used for memory + heartbeat).

Approval messages use inline keyboards (`📋 Diff` / `🧪 Tests` / `✅ Push` / `❌ Descartar`) routed through `handlers.approvals.pending_callback` with callback data `pc:{pending_id}:{action}`. Tests and push checkout the bot branch, do the work, then restore the original branch — all inside the lock.

### Per-project serialization

`core.locks` exposes an in-memory `defaultdict(asyncio.Lock)` keyed by project name. Aider, git, and test invocations all take this lock. Cron approval callbacks (tests, push) also take it. This lock is per-process — it does not guard against an external editor modifying the repo.

### State (SQLite at `sessions.db`)

All tables live in `workerbot/storage/db.py:init_db()` — keep schema there. CRUD is split by table group (`chat_state.py`, `crons.py`, `suggestions.py`, `approvals.py`, `usage.py`). Every module imports `_conn` from `storage.db`.

Tables:
- `chat_state` — `chat_id → active project`
- `crons` — persisted cron definitions (rehydrated on startup)
- `cron_runs` — history of every cron execution; powers memory + heartbeat
- `suggested_tasks` — text-only suggestions from crons, consumed by `/do`
- `pending_changes` — cron-generated diffs on isolated branches awaiting approval
- `usage_log` — per-call token/cost records; powers the daily cap

Aider's own chat history lives inside each project repo as `.aider.chat.history.md` (plus `.aider.input.history`, `.aider.llm.history`). `/reset` deletes these in the active project.

### Projects and authorization

- `config.PROJECTS` is a static dict of `name → Path`, populated from `PROJECT_*` env vars. Adding a project requires both a new env var **and** a new dict entry — there is no auto-discovery.
- `TELEGRAM_ALLOWED_USER_IDS` is a set of ints parsed from a comma-separated env var. The legacy singular `TELEGRAM_ALLOWED_USER_ID` is still read as a fallback.
- `handlers.base.authorized(update)` gates every handler and the callback query handler; unauthorized messages are silently logged and dropped.
- Each authorized user gets isolated state because they identify by private-chat `chat_id`.

### Aider subprocess contract

`runners.aider.run_aider` returns an `AiderResult(output, tokens_in, tokens_out, cost_usd)` dataclass. It hardcodes flags (`--yes-always`, `--no-auto-commits`, `--no-pretty`, `--no-stream`, `--no-check-update`) and env (`TERM=dumb`, `NO_COLOR=1`, `PYTHONIOENCODING=utf-8`) — these are load-bearing on Windows and for clean output parsing. `_extra_read_files()` auto-injects `CLAUDE.md`, `CONVENTIONS.md`, or `.aider.conventions.md` from the project root via `--read`. Note: Claude Code **skills** are not read — only the `CLAUDE.md` file. `clean_output` strips Aider's banner lines (see `NOISE_PREFIXES`, which also includes `Tokens:` / `Cost:` — those are parsed out by `_parse_usage` and reported via the dataclass). Timeout defaults to 300s; on expiry the process is killed and a `[timeout]` message returned.

### Budget / safety

`core.budget` composes `storage.usage`. Every Aider invocation writes to `usage_log` with a `source` tag (`chat`, `cron:{id}`, `do:{task_id}`). `over_budget()` sums today's UTC-date spend and compares to `DAILY_BUDGET_USD`. When the cap is reached, `on_message` and each cron run short-circuit. Set `DAILY_BUDGET_USD=0` to disable the cap.

### Heartbeat

`core.scheduler.schedule_heartbeat` registers a job id `heartbeat` using the `HEARTBEAT_CRON` expression (default `0 8 * * *`) to DM `HEARTBEAT_CHAT_ID` (defaults to the smallest allowed user id). Reports: active cron count, last run timestamp, today's spend, last potentially-broken run.

### Test runner

`runners.test_runner` auto-detects the test command from project markers (`pyproject.toml` → `pytest`, `package.json` with a `test` script → `npm test`, `Cargo.toml` → `cargo test`, `go.mod` → `go test ./...`). Override per project with `PROJECT_<NAME>_TEST_CMD=<cmd>` in `.env`. Invoked manually with `/tests` or from the cron approval keyboard.

### Git commands

`handlers.git` exposes `/git_*` handlers that shell out via `runners.git.run_git` (async subprocess, `GIT_TERMINAL_PROMPT=0` to avoid credential prompts). `/git_push` refuses to push from `main`/`master` as a safety rail. `/git_switch` refuses when the working tree is dirty. All git commands take the project lock.

### Special cron prompts

Prompts that start with `@<keyword>` bypass the Aider subprocess flow in `run_cron_job` and go through a dedicated handler. Currently:

- **`@news [day|week] [extra]`** — calls `runners.news.fetch_all` to pull a curated set of RSS feeds (HN AI, ArXiv cs.AI, Simon Willison, The Decoder, Latent Space), filters by time window, and sends the raw titles to the LLM via `runners.llm.complete` with a "top-5 relevant items" system prompt. No repo is touched and no isolated branch is created; the cron's `project` field is only used for bookkeeping. Usage is still recorded against the daily budget with source `cron:{id}:news`.
- **`@heavy <prompt>`** / **`@weak <prompt>`** — recognized everywhere a user-provided prompt hits Aider (`on_message` chat, cron prompts, `/do` if the suggestion starts with the marker). `core.prompts.extract_model_marker` strips the marker and returns the override model (`AIDER_HEAVY_MODEL` or `AIDER_WEAK_MODEL`); `run_aider(model=...)` uses it. Parser requires the exact token followed by whitespace or EOS (so `@heavier` is **not** a match). Order of resolution: first marker wins, so `@heavy @weak foo` runs with heavy and passes `@weak foo` as the prompt body. Chat messages that are only a marker without content are rejected with a usage hint.

When adding another special prompt, route it in `core.scheduler.run_cron_job` before the default Aider flow.

### Cron scheduling details

- Timezone is `config.TIMEZONE` (`America/Mexico_City`).
- User cron job IDs follow `cron_{id}`; the heartbeat uses id `heartbeat`.
- `schedule_cron` uses `replace_existing=True` so rescheduling is idempotent.
- Bot-created branches follow the prefix `bot/cron-` — treat this prefix as owned by the bot (it deletes empty ones automatically).

## Logging

`app._configure_logging()` runs at import time and installs a `TimedRotatingFileHandler` (midnight rotation, 7-day retention) writing to `config.LOG_DIR/bot.log`, plus a stream handler. The `_error_handler` catches unhandled exceptions, logs the traceback, and DMs a one-line summary to the originating authorized user only.

## Conventions in this codebase

- User-facing strings (Telegram messages, log lines) are in Spanish. Match that when adding handlers.
- Long Telegram replies are chunked to 3800 chars (`base.MAX_TG_LEN`) — Telegram's limit is 4096 but there's headroom for headers.
- Handlers early-return on `not authorized(update)`; callback queries use `query.answer("no autorizado", show_alert=True)` instead.
- Any new Aider invocation should `record_usage(...)` so the daily cap stays accurate.
- New storage tables go in `storage/db.py:init_db()`. Their CRUD goes in a dedicated `storage/*.py` module. Do not scatter `CREATE TABLE` across the codebase.
- Handlers only depend on `core`, `runners`, `storage`, `config`, `base`. They should not import from other handler modules.
