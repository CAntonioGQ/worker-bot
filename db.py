import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "sessions.db"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_state (
                chat_id INTEGER PRIMARY KEY,
                project TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS cron_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cron_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                project TEXT NOT NULL,
                ran_at TEXT NOT NULL,
                summary TEXT,
                output TEXT,
                tokens_in INTEGER DEFAULT 0,
                tokens_out INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0,
                branch TEXT,
                had_changes INTEGER DEFAULT 0
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS suggested_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                project TEXT NOT NULL,
                text TEXT NOT NULL,
                source_cron_id INTEGER,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                project TEXT NOT NULL,
                source_cron_id INTEGER,
                branch TEXT NOT NULL,
                base_branch TEXT,
                diff_stat TEXT,
                full_output TEXT,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                ran_at TEXT NOT NULL,
                tokens_in INTEGER DEFAULT 0,
                tokens_out INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0,
                source TEXT
            )
            """
        )


def get_project(chat_id: int, default: str) -> str:
    with _conn() as c:
        row = c.execute(
            "SELECT project FROM chat_state WHERE chat_id = ?", (chat_id,)
        ).fetchone()
    return row["project"] if row else default


def set_project(chat_id: int, project: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            """
            INSERT INTO chat_state (chat_id, project, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                project = excluded.project,
                updated_at = excluded.updated_at
            """,
            (chat_id, project, now),
        )
