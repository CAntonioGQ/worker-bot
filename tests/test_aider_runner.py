from aider_runner import clean_output


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


def test_preserves_tokens_and_cost_line():
    raw = "\nEsta es la respuesta.\n\nTokens: 11k sent, 40 received. Cost: $0.0016 message.\n"
    out = clean_output(raw)
    assert "Esta es la respuesta." in out
    assert "Tokens:" in out
    assert "Cost: $0.0016" in out


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
