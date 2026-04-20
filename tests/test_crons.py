import pytest

from workerbot.storage.crons import (
    add_cron,
    delete_cron,
    get_cron,
    list_crons,
)
from workerbot.storage.db import init_db


@pytest.fixture
def db_ready(tmp_db):
    init_db()


def test_add_cron_returns_autoincrement_id(db_ready):
    first = add_cron(chat_id=1, project="webapp", cron_expr="* * * * *", prompt="a")
    second = add_cron(chat_id=1, project="webapp", cron_expr="0 9 * * *", prompt="b")
    assert first == 1
    assert second == 2


def test_get_cron_returns_stored_row(db_ready):
    cid = add_cron(chat_id=7, project="orchestrator", cron_expr="0 9 * * *", prompt="hola")
    row = get_cron(cid)
    assert row["chat_id"] == 7
    assert row["project"] == "orchestrator"
    assert row["cron_expr"] == "0 9 * * *"
    assert row["prompt"] == "hola"
    assert row["enabled"] == 1


def test_get_cron_returns_none_when_missing(db_ready):
    assert get_cron(99999) is None


def test_list_crons_filters_by_chat_id(db_ready):
    add_cron(chat_id=1, project="webapp", cron_expr="* * * * *", prompt="p1")
    add_cron(chat_id=2, project="webapp", cron_expr="* * * * *", prompt="p2")
    add_cron(chat_id=1, project="orchestrator", cron_expr="0 9 * * *", prompt="p3")

    rows = list_crons(chat_id=1)
    assert len(rows) == 2
    assert {r["prompt"] for r in rows} == {"p1", "p3"}


def test_list_crons_empty(db_ready):
    assert list_crons(chat_id=42) == []


def test_delete_cron_removes_existing(db_ready):
    cid = add_cron(chat_id=1, project="webapp", cron_expr="* * * * *", prompt="x")
    assert delete_cron(cid) is True
    assert get_cron(cid) is None


def test_delete_cron_returns_false_when_missing(db_ready):
    assert delete_cron(99999) is False
