from workerbot.config import AIDER_HEAVY_MODEL, AIDER_WEAK_MODEL

_MARKERS: tuple[tuple[str, str], ...] = (
    ("@heavy", AIDER_HEAVY_MODEL),
    ("@weak", AIDER_WEAK_MODEL),
)


def extract_model_marker(prompt: str) -> tuple[str, str | None]:
    """Si el prompt empieza con '@heavy' o '@weak' como token completo
    (seguido de whitespace o fin de string), lo strippea y devuelve
    (prompt_limpio, modelo_override). Si no, devuelve (prompt, None).

    '@heavier' NO matchea '@heavy'; '@weakly' NO matchea '@weak'.
    """
    stripped = prompt.lstrip()
    for marker, model in _MARKERS:
        if stripped == marker:
            return "", model
        if stripped.startswith(marker) and stripped[len(marker)].isspace():
            return stripped[len(marker):].lstrip(), model
    return prompt, None
