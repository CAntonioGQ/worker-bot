from approvals import create_pending, get_pending, list_pending, set_status
from db import init_db


def _mk(chat_id=1, cron_id=5, branch="bot/cron-5-20260101-1200"):
    return create_pending(
        chat_id=chat_id,
        project="webapp",
        source_cron_id=cron_id,
        branch=branch,
        base_branch="main",
        diff_stat=" 1 file changed, 2 insertions(+)",
        full_output="respuesta de aider",
    )


def test_create_and_get_pending(tmp_db):
    init_db()
    pid = _mk()
    row = get_pending(pid)
    assert row["chat_id"] == 1
    assert row["project"] == "webapp"
    assert row["status"] == "pending"
    assert row["branch"].startswith("bot/cron-5-")


def test_list_pending_filters_by_chat_and_status(tmp_db):
    init_db()
    p1 = _mk(chat_id=1)
    p2 = _mk(chat_id=1, branch="bot/cron-5-b")
    _mk(chat_id=2, branch="bot/cron-5-c")
    set_status(p2, "rejected")
    rows = list_pending(chat_id=1)
    assert len(rows) == 1
    assert rows[0]["id"] == p1


def test_set_status_transitions(tmp_db):
    init_db()
    pid = _mk()
    set_status(pid, "pushed")
    assert get_pending(pid)["status"] == "pushed"
    set_status(pid, "rejected")
    assert get_pending(pid)["status"] == "rejected"
