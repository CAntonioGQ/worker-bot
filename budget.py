from datetime import datetime, timezone

from config import DAILY_BUDGET_USD
from db import _conn


def _today_utc_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def record_usage(
    chat_id: int,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    source: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            """
            INSERT INTO usage_log
                (chat_id, ran_at, tokens_in, tokens_out, cost_usd, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (chat_id, now, tokens_in, tokens_out, cost_usd, source),
        )


def spent_today(chat_id: int) -> float:
    today = _today_utc_iso()
    with _conn() as c:
        row = c.execute(
            """
            SELECT COALESCE(SUM(cost_usd), 0) AS total
            FROM usage_log
            WHERE chat_id = ? AND substr(ran_at, 1, 10) = ?
            """,
            (chat_id, today),
        ).fetchone()
    return float(row["total"]) if row else 0.0


def over_budget(chat_id: int) -> bool:
    if DAILY_BUDGET_USD <= 0:
        return False
    return spent_today(chat_id) >= DAILY_BUDGET_USD


def budget_summary(chat_id: int) -> str:
    spent = spent_today(chat_id)
    pct = (spent / DAILY_BUDGET_USD * 100) if DAILY_BUDGET_USD > 0 else 0.0
    return (
        f"💰 Gasto hoy: ${spent:.4f} / ${DAILY_BUDGET_USD:.2f} USD ({pct:.0f}%)"
    )
