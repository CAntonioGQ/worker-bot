from datetime import datetime, timezone

from workerbot.storage.db import _conn


def add_cron(chat_id: int, project: str, cron_expr: str, prompt: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO crons (chat_id, project, cron_expr, prompt, enabled, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (chat_id, project, cron_expr, prompt, now),
        )
        return cur.lastrowid


def list_crons(chat_id: int) -> list:
    with _conn() as c:
        return c.execute(
            "SELECT * FROM crons WHERE chat_id = ? ORDER BY id",
            (chat_id,),
        ).fetchall()


def get_cron(cron_id: int):
    with _conn() as c:
        return c.execute("SELECT * FROM crons WHERE id = ?", (cron_id,)).fetchone()


def delete_cron(cron_id: int) -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM crons WHERE id = ?", (cron_id,))
        return cur.rowcount > 0


def all_enabled_crons() -> list:
    with _conn() as c:
        return c.execute("SELECT * FROM crons WHERE enabled = 1").fetchall()


def count_enabled() -> int:
    with _conn() as c:
        return c.execute(
            "SELECT COUNT(*) AS n FROM crons WHERE enabled = 1"
        ).fetchone()["n"]
