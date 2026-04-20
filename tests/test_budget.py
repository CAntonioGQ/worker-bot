from datetime import datetime, timedelta, timezone

from budget import over_budget, record_usage, spent_today
from db import _conn, init_db


def test_spent_today_zero_when_no_records(tmp_db):
    init_db()
    assert spent_today(chat_id=1) == 0.0


def test_record_and_sum_today(tmp_db):
    init_db()
    record_usage(chat_id=1, tokens_in=100, tokens_out=50, cost_usd=0.01, source="chat")
    record_usage(chat_id=1, tokens_in=200, tokens_out=80, cost_usd=0.02, source="cron:1")
    assert abs(spent_today(1) - 0.03) < 1e-9


def test_spent_today_isolated_per_chat(tmp_db):
    init_db()
    record_usage(chat_id=1, tokens_in=0, tokens_out=0, cost_usd=0.10, source="chat")
    record_usage(chat_id=2, tokens_in=0, tokens_out=0, cost_usd=0.20, source="chat")
    assert abs(spent_today(1) - 0.10) < 1e-9
    assert abs(spent_today(2) - 0.20) < 1e-9


def test_over_budget_false_below_cap(tmp_db, monkeypatch):
    import budget
    monkeypatch.setattr(budget, "DAILY_BUDGET_USD", 1.0)
    init_db()
    record_usage(chat_id=1, tokens_in=0, tokens_out=0, cost_usd=0.5, source="chat")
    assert over_budget(1) is False


def test_over_budget_true_at_or_above_cap(tmp_db, monkeypatch):
    import budget
    monkeypatch.setattr(budget, "DAILY_BUDGET_USD", 1.0)
    init_db()
    record_usage(chat_id=1, tokens_in=0, tokens_out=0, cost_usd=1.0, source="chat")
    assert over_budget(1) is True


def test_over_budget_disabled_when_cap_is_zero(tmp_db, monkeypatch):
    import budget
    monkeypatch.setattr(budget, "DAILY_BUDGET_USD", 0.0)
    init_db()
    record_usage(chat_id=1, tokens_in=0, tokens_out=0, cost_usd=999, source="chat")
    assert over_budget(1) is False


def test_old_usage_does_not_count_towards_today(tmp_db):
    init_db()
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with _conn() as c:
        c.execute(
            "INSERT INTO usage_log (chat_id, ran_at, cost_usd, source) VALUES (?, ?, ?, ?)",
            (1, yesterday, 5.0, "chat"),
        )
    assert spent_today(1) == 0.0
