from workerbot.config import AIDER_HEAVY_MODEL, AIDER_WEAK_MODEL
from workerbot.core.prompts import extract_model_marker


def test_plain_prompt_unchanged():
    text, model = extract_model_marker("Revisa TODOs y sugiere tareas")
    assert text == "Revisa TODOs y sugiere tareas"
    assert model is None


def test_heavy_prefix_strips_marker_and_returns_heavy_model():
    text, model = extract_model_marker("@heavy Refactoriza auth a JWT")
    assert text == "Refactoriza auth a JWT"
    assert model == AIDER_HEAVY_MODEL


def test_weak_prefix_strips_marker_and_returns_weak_model():
    text, model = extract_model_marker("@weak corrige el typo en README")
    assert text == "corrige el typo en README"
    assert model == AIDER_WEAK_MODEL


def test_marker_tolerates_leading_whitespace():
    text, model = extract_model_marker("   @heavy  hazlo bien")
    assert text == "hazlo bien"
    assert model == AIDER_HEAVY_MODEL


def test_marker_alone_leaves_empty_body():
    text, model = extract_model_marker("@heavy")
    assert text == ""
    assert model == AIDER_HEAVY_MODEL


def test_marker_not_at_start_is_ignored():
    text, model = extract_model_marker("cambia X @heavy Y")
    assert text == "cambia X @heavy Y"
    assert model is None


def test_heavy_requires_exact_token_not_prefix():
    text, model = extract_model_marker("@heavier trabajo")
    assert model is None
    assert text == "@heavier trabajo"


def test_weak_requires_exact_token_not_prefix():
    text, model = extract_model_marker("@weakly dudoso")
    assert model is None
    assert text == "@weakly dudoso"


def test_heavy_wins_over_weak_when_first():
    text, model = extract_model_marker("@heavy @weak raro")
    # solo se consume el primer marker reconocido en orden (@heavy)
    assert model == AIDER_HEAVY_MODEL
    assert text == "@weak raro"
