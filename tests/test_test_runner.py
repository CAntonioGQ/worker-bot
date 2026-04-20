import pytest

from test_runner import _detect_test_cmd, _resolve_cmd, run_tests


def test_detect_pytest_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    assert _detect_test_cmd(tmp_path)[0] == "pytest"


def test_detect_npm_from_package_json(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "jest"}}', encoding="utf-8"
    )
    cmd = _detect_test_cmd(tmp_path)
    assert cmd[0] == "npm" and "test" in cmd


def test_detect_skips_package_json_without_test_script(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {}}', encoding="utf-8")
    assert _detect_test_cmd(tmp_path) is None


def test_detect_cargo(tmp_path):
    (tmp_path / "Cargo.toml").write_text("", encoding="utf-8")
    assert _detect_test_cmd(tmp_path)[0] == "cargo"


def test_detect_go(tmp_path):
    (tmp_path / "go.mod").write_text("", encoding="utf-8")
    assert _detect_test_cmd(tmp_path)[0] == "go"


def test_detect_none_when_no_markers(tmp_path):
    assert _detect_test_cmd(tmp_path) is None


def test_override_takes_precedence(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    import test_runner
    monkeypatch.setitem(test_runner.PROJECT_TEST_CMDS, "webapp", "make test")
    cmd = _resolve_cmd("webapp", tmp_path)
    assert cmd == ["make", "test"]


@pytest.mark.asyncio
async def test_run_tests_returns_message_when_no_cmd(tmp_path):
    ok, cmd, out = await run_tests("webapp", tmp_path)
    assert ok is False
    assert cmd == ""
    assert "PROJECT_WEBAPP_TEST_CMD" in out
