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

Single-process async Telegram bot that dispatches chat messages and cron-triggered prompts into [Aider](https://aider.chat) running as a subprocess against one of several local git repos.

### Request flow

1. `main.py` → `bot.build_app()` wires `python-telegram-bot` handlers and `post_init`/`post_shutdown` hooks.
2. On startup: `init_db()`, `init_crons_db()`, `scheduler.start()`, then `load_and_schedule_all(bot)` rehydrates crons from SQLite into APScheduler.
3. Text messages hit `on_message` → resolves the chat's active project via `db.get_project` → acquires `locks.lock_for(project)` → calls `aider_runner.run_aider(project_path, text)` → streams output back in ≤3800-char chunks.
4. Cron-triggered jobs run `crons.run_cron_job`, which takes the same per-project lock and posts results to the originating `chat_id`.

### Per-project serialization

`locks.py` exposes an in-memory `defaultdict(asyncio.Lock)` keyed by project name. Every Aider and git invocation takes this lock. Messages to the **same** project serialize; messages to **different** projects run in parallel. This lock is per-process — it does not guard against an external editor modifying the repo.

### State

- SQLite at `sessions.db` (gitignored). Two tables: `chat_state` (chat_id → active project) and `crons` (persisted cron definitions).
- `db._conn()` is imported directly by `crons.py` — both modules share the same connection helper. Keep schema migrations in the respective `init_*_db()` functions.
- Aider's own chat history lives inside each project repo as `.aider.chat.history.md` (plus `.aider.input.history`, `.aider.llm.history`). `/reset` deletes these in the active project. Aider is invoked one-shot with `--message` but continues conversations via these files.

### Projects and authorization

- `config.PROJECTS` is a static dict of `name → Path`, populated from `PROJECT_*` env vars. Adding a project requires both a new env var **and** a new dict entry — there is no auto-discovery.
- `TELEGRAM_ALLOWED_USER_IDS` is a set of ints parsed from a comma-separated env var. The legacy singular `TELEGRAM_ALLOWED_USER_ID` is still read as a fallback. `_authorized(update)` gates every handler; unauthorized messages are silently logged and dropped.
- Each authorized user gets isolated state because they identify by private-chat `chat_id`; there is no shared workspace.

### Aider subprocess contract

`aider_runner.run_aider` hardcodes flags (`--yes-always`, `--no-auto-commits`, `--no-pretty`, `--no-stream`, `--no-check-update`) and env (`TERM=dumb`, `NO_COLOR=1`, `PYTHONIOENCODING=utf-8`) — these are load-bearing on Windows and for clean output parsing. `clean_output` strips Aider's banner lines (see `NOISE_PREFIXES`) and `\r`-overwrite progress lines. Timeout defaults to 300s and on expiry the process is killed and a `[timeout]` message returned.

### Git commands

`bot.py` exposes `/git_*` handlers that shell out via `git_runner.run_git` (async subprocess, `GIT_TERMINAL_PROMPT=0` to avoid credential prompts). `/git_push` refuses to push from `main`/`master` as a safety rail. `/git_switch` refuses when the working tree is dirty. All git commands take the project lock.

### Cron scheduling

- Timezone is hardcoded to `America/Mexico_City` in `crons.py`.
- Job IDs follow the pattern `cron_{id}` — used by `/cron_list` to look up `next_run_time` and by `unschedule_cron` on deletion.
- `schedule_cron` uses `replace_existing=True` so rescheduling is idempotent.

## Logging

`bot._configure_logging()` runs at import time and installs a `TimedRotatingFileHandler` (midnight rotation, 7-day retention) writing to `logs/bot.log`, plus a stream handler. The `_error_handler` catches unhandled exceptions, logs the traceback, and DMs a one-line summary to the originating authorized user only.

## Conventions in this codebase

- User-facing strings (Telegram messages, log lines) are in Spanish. Match that when adding handlers.
- Long Telegram replies are chunked to 3800 chars (`MAX_TG_LEN`) — Telegram's limit is 4096 but there's headroom for headers.
- Handlers early-return on `not _authorized(update)` — keep that pattern.
