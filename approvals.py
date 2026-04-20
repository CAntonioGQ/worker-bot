from datetime import datetime, timezone

from db import _conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_pending(
    chat_id: int,
    project: str,
    source_cron_id: int | None,
    branch: str,
    base_branch: str | None,
    diff_stat: str,
    full_output: str,
) -> int:
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO pending_changes
                (chat_id, project, source_cron_id, branch, base_branch,
                 diff_stat, full_output, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                chat_id, project, source_cron_id, branch, base_branch,
                diff_stat, full_output, _now(),
            ),
        )
        return cur.lastrowid


def get_pending(pending_id: int):
    with _conn() as c:
        return c.execute(
            "SELECT * FROM pending_changes WHERE id = ?", (pending_id,)
        ).fetchone()


def list_pending(chat_id: int) -> list:
    with _conn() as c:
        return c.execute(
            """
            SELECT * FROM pending_changes
            WHERE chat_id = ? AND status = 'pending'
            ORDER BY id DESC
            """,
            (chat_id,),
        ).fetchall()


def set_status(pending_id: int, status: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE pending_changes SET status = ? WHERE id = ?",
            (status, pending_id),
        )
