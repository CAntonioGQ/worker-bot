import pytest

from db import get_project, init_db, set_project


def test_init_db_is_idempotent(tmp_db):
    init_db()
    init_db()  # segunda llamada no debe fallar


def test_get_project_returns_default_when_missing(tmp_db):
    init_db()
    assert get_project(chat_id=999, default="webapp") == "webapp"


def test_set_and_get_project(tmp_db):
    init_db()
    set_project(chat_id=123, project="orchestrator")
    assert get_project(chat_id=123, default="webapp") == "orchestrator"


def test_set_project_upserts_existing_chat(tmp_db):
    init_db()
    set_project(chat_id=1, project="webapp")
    set_project(chat_id=1, project="orchestrator")
    assert get_project(chat_id=1, default="X") == "orchestrator"


def test_different_chats_are_independent(tmp_db):
    init_db()
    set_project(chat_id=1, project="webapp")
    set_project(chat_id=2, project="orchestrator")
    assert get_project(chat_id=1, default="X") == "webapp"
    assert get_project(chat_id=2, default="X") == "orchestrator"
