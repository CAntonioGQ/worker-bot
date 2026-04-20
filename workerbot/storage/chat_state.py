from datetime import datetime, timezone

from workerbot.storage.db import _conn


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
