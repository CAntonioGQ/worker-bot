from workerbot.runners.aider import _extra_read_files, _parse_usage, clean_output


def test_empty_input_returns_empty():
    assert clean_output("") == ""


def test_crlf_line_endings_are_normalized():
    raw = "Aider v0.86.2\r\n\r\nLa respuesta del modelo.\r\nLinea dos.\r\n"
    out = clean_output(raw)
    assert "La respuesta del modelo." in out
    assert "Linea dos." in out
    assert "Aider v" not in out


def test_strips_known_noise_prefixes():
    raw = (
        "Aider v0.86.2\n"
        "Model: openrouter/deepseek/deepseek-chat\n"
        "Git repo: .git with 100 files\n"
        "Repo-map: using 4096 tokens\n"
        "\n"
        "Respuesta real aquí.\n"
    )
    out = clean_output(raw)
    assert out == "Respuesta real aquí."


def test_strips_tqdm_progress_bars_with_carriage_return():
    raw = (
        "Scanning repo:  10%|#         | 10/100\r"
        "Scanning repo:  50%|#####     | 50/100\r"
        "Scanning repo: 100%|##########| 100/100\r"
        "Respuesta final.\n"
    )
    out = clean_output(raw)
    assert "Scanning" not in out
    assert "Respuesta final." in out


def test_filters_tokens_and_cost_line():
    raw = "\nEsta es la respuesta.\n\nTokens: 11k sent, 40 received. Cost: $0.0016 message.\n"
    out = clean_output(raw)
    assert "Esta es la respuesta." in out
    assert "Tokens:" not in out
    assert "Cost:" not in out


def test_parse_usage_extracts_tokens_and_cost():
    raw = "Tokens: 11k sent, 40 received. Cost: $0.0016 message, $0.01 session."
    tin, tout, cost = _parse_usage(raw)
    assert tin == 11_000
    assert tout == 40
    assert cost == 0.0016


def test_parse_usage_sums_multiple_occurrences():
    raw = (
        "Tokens: 1.2k sent, 100 received. Cost: $0.0010 message.\n"
        "Tokens: 500 sent, 30 received. Cost: $0.0005 message.\n"
    )
    tin, tout, cost = _parse_usage(raw)
    assert tin == 1_700
    assert tout == 130
    assert abs(cost - 0.0015) < 1e-9


def test_parse_usage_no_match_returns_zeros():
    assert _parse_usage("sin usage aquí") == (0, 0, 0.0)


def test_extra_read_files_detects_claude_md(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# conv", encoding="utf-8")
    found = _extra_read_files(tmp_path)
    assert any("CLAUDE.md" in f for f in found)


def test_extra_read_files_empty_when_absent(tmp_path):
    assert _extra_read_files(tmp_path) == []


def test_collapses_triple_blank_lines():
    raw = "primera\n\n\n\n\nsegunda\n"
    assert clean_output(raw) == "primera\n\nsegunda"


def test_utf8_special_chars_preserved():
    raw = "\nSegún el CLAUDE.md, búsqueda inteligente y generación.\n"
    out = clean_output(raw)
    assert "Según" in out
    assert "búsqueda" in out
    assert "generación" in out


def test_detected_dumb_terminal_line_stripped():
    raw = "Detected dumb terminal, disabling fancy input and pretty output.\n\nRespuesta.\n"
    out = clean_output(raw)
    assert "Detected dumb terminal" not in out
    assert "Respuesta." in out
