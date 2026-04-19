import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

_raw_ids = os.environ.get("TELEGRAM_ALLOWED_USER_IDS") or os.environ.get("TELEGRAM_ALLOWED_USER_ID", "")
TELEGRAM_ALLOWED_USER_IDS: set[int] = {
    int(x) for x in _raw_ids.split(",") if x.strip()
}
if not TELEGRAM_ALLOWED_USER_IDS:
    raise RuntimeError(
        "TELEGRAM_ALLOWED_USER_IDS no definido. Pon uno o varios IDs separados por coma en .env"
    )

AIDER_MODEL = os.environ.get("AIDER_MODEL", "openrouter/deepseek/deepseek-chat")
AIDER_WEAK_MODEL = os.environ.get("AIDER_WEAK_MODEL", AIDER_MODEL)

PROJECTS: dict[str, Path] = {
    "webapp": Path(os.environ["PROJECT_AVI_WEBAPP"]),
    "orchestrator": Path(os.environ["PROJECT_AVI_ORCHESTRATOR"]),
}

DEFAULT_PROJECT = "webapp"
