import os

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "000:test")
os.environ.setdefault("TELEGRAM_ALLOWED_USER_ID", "0")
os.environ.setdefault("PROJECT_AVI_WEBAPP", "/tmp/webapp")
os.environ.setdefault("PROJECT_AVI_ORCHESTRATOR", "/tmp/orch")

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_sessions.db")
    return tmp_path / "test_sessions.db"
