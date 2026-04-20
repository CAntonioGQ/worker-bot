from db import init_db
from suggestions import (
    add_suggestion,
    get_suggestion,
    list_suggestions,
    memory_block,
    record_cron_run,
    set_suggestion_status,
    summarize,
)


def test_add_and_list_suggestions(tmp_db):
    init_db()
    sid = add_suggestion(chat_id=1, project="webapp", text="refactor auth")
    rows = list_suggestions(chat_id=1)
    assert len(rows) == 1
    assert rows[0]["id"] == sid
    assert rows[0]["status"] == "pending"


def test_list_suggestions_filters_by_status(tmp_db):
    init_db()
    s1 = add_suggestion(chat_id=1, project="webapp", text="a")
    add_suggestion(chat_id=1, project="webapp", text="b")
    set_suggestion_status(s1, "done")
    pending = list_suggestions(chat_id=1, status="pending")
    done = list_suggestions(chat_id=1, status="done")
    assert len(pending) == 1 and pending[0]["text"] == "b"
    assert len(done) == 1 and done[0]["text"] == "a"


def test_get_suggestion_returns_row(tmp_db):
    init_db()
    sid = add_suggestion(chat_id=1, project="webapp", text="hola", source_cron_id=5)
    row = get_suggestion(sid)
    assert row["text"] == "hola"
    assert row["source_cron_id"] == 5


def test_memory_block_empty_when_no_runs(tmp_db):
    init_db()
    assert memory_block(cron_id=1) == ""


def test_memory_block_returns_recent_summaries(tmp_db):
    init_db()
    for i in range(4):
        record_cron_run(
            cron_id=1, chat_id=1, project="webapp",
            summary=f"sugerencia {i}", output="...",
            tokens_in=0, tokens_out=0, cost_usd=0,
            branch=None, had_changes=False,
        )
    block = memory_block(cron_id=1, limit=3)
    assert "Historial reciente" in block
    # incluye las 3 más recientes (i=3, 2, 1) y excluye i=0
    assert "sugerencia 3" in block
    assert "sugerencia 2" in block
    assert "sugerencia 1" in block
    assert "sugerencia 0" not in block


def test_summarize_truncates_long_output():
    long = "línea uno\n" + "x" * 500
    out = summarize(long, max_chars=100)
    assert len(out) <= 100
    assert out.endswith("…")


def test_summarize_handles_empty():
    assert summarize("") == "(sin respuesta)"
    assert summarize(None) == "(sin respuesta)"
