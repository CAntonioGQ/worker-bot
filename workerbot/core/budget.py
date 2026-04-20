from workerbot.config import DAILY_BUDGET_USD
from workerbot.storage.usage import spent_today


def over_budget(chat_id: int) -> bool:
    if DAILY_BUDGET_USD <= 0:
        return False
    return spent_today(chat_id) >= DAILY_BUDGET_USD


def budget_summary(chat_id: int) -> str:
    spent = spent_today(chat_id)
    pct = (spent / DAILY_BUDGET_USD * 100) if DAILY_BUDGET_USD > 0 else 0.0
    return f"💰 Gasto hoy: ${spent:.4f} / ${DAILY_BUDGET_USD:.2f} USD ({pct:.0f}%)"
