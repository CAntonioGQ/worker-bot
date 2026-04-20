import importlib

import pytest


def _reload_config(monkeypatch, env: dict[str, str]) -> object:
    for k in ("TELEGRAM_ALLOWED_USER_ID", "TELEGRAM_ALLOWED_USER_IDS"):
        monkeypatch.setenv(k, env.get(k, ""))
    from workerbot import config
    return importlib.reload(config)


def test_parses_single_user_id(monkeypatch):
    cfg = _reload_config(monkeypatch, {"TELEGRAM_ALLOWED_USER_IDS": "12345"})
    assert cfg.TELEGRAM_ALLOWED_USER_IDS == {12345}


def test_parses_multiple_ids_comma_separated(monkeypatch):
    cfg = _reload_config(monkeypatch, {"TELEGRAM_ALLOWED_USER_IDS": "10,20,30"})
    assert cfg.TELEGRAM_ALLOWED_USER_IDS == {10, 20, 30}


def test_tolerates_whitespace_around_ids(monkeypatch):
    cfg = _reload_config(monkeypatch, {"TELEGRAM_ALLOWED_USER_IDS": " 10 , 20 , 30 "})
    assert cfg.TELEGRAM_ALLOWED_USER_IDS == {10, 20, 30}


def test_falls_back_to_legacy_singular_name(monkeypatch):
    cfg = _reload_config(monkeypatch, {"TELEGRAM_ALLOWED_USER_ID": "7777"})
    assert cfg.TELEGRAM_ALLOWED_USER_IDS == {7777}


def test_empty_ids_raises(monkeypatch):
    with pytest.raises(RuntimeError):
        _reload_config(monkeypatch, {"TELEGRAM_ALLOWED_USER_IDS": ""})
