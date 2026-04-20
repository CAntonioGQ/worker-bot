from datetime import datetime, timezone

from workerbot.storage.db import _conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_cron_run(
    cron_id: int,
    chat_id: int,
    project: str,
    summary: str,
    output: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    branch: str | None,
    had_changes: bool,
) -> int:
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO cron_runs
                (cron_id, chat_id, project, ran_at, summary, output,
                 tokens_in, tokens_out, cost_usd, branch, had_changes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cron_id, chat_id, project, _now(), summary, output,
                tokens_in, tokens_out, cost_usd, branch, 1 if had_changes else 0,
            ),
        )
        return cur.lastrowid


def recent_runs_for_cron(cron_id: int, limit: int = 3) -> list:
    with _conn() as c:
        return c.execute(
            """
            SELECT summary, ran_at
            FROM cron_runs
            WHERE cron_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (cron_id, limit),
        ).fetchall()


def last_run_time() -> str | None:
    with _conn() as c:
        row = c.execute(
            "SELECT ran_at FROM cron_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return row["ran_at"] if row else None


def last_failed_run():
    with _conn() as c:
        return c.execute(
            """
            SELECT ran_at, cron_id, summary FROM cron_runs
            WHERE output LIKE '%timeout%' OR output LIKE '%[sin respuesta]%'
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()


def add_suggestion(
    chat_id: int, project: str, text: str, source_cron_id: int | None = None
) -> int:
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO suggested_tasks
                (chat_id, project, text, source_cron_id, created_at, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
            """,
            (chat_id, project, text, source_cron_id, _now()),
        )
        return cur.lastrowid


def list_suggestions(chat_id: int, status: str = "pending") -> list:
    with _conn() as c:
        return c.execute(
            """
            SELECT * FROM suggested_tasks
            WHERE chat_id = ? AND status = ?
            ORDER BY id DESC
            LIMIT 50
            """,
            (chat_id, status),
        ).fetchall()


def get_suggestion(task_id: int):
    with _conn() as c:
        return c.execute(
            "SELECT * FROM suggested_tasks WHERE id = ?", (task_id,)
        ).fetchone()


def set_suggestion_status(task_id: int, status: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE suggested_tasks SET status = ? WHERE id = ?",
            (status, task_id),
        )
