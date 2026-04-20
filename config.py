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

DAILY_BUDGET_USD = float(os.environ.get("DAILY_BUDGET_USD", "1.00"))

HEARTBEAT_CRON = os.environ.get("HEARTBEAT_CRON", "0 8 * * *").strip()

_hb_chat = os.environ.get("HEARTBEAT_CHAT_ID", "").strip()
HEARTBEAT_CHAT_ID: int | None = int(_hb_chat) if _hb_chat else (
    min(TELEGRAM_ALLOWED_USER_IDS) if TELEGRAM_ALLOWED_USER_IDS else None
)


def _test_cmd_for(project: str) -> str | None:
    key = f"PROJECT_{project.upper()}_TEST_CMD"
    val = os.environ.get(key, "").strip()
    return val or None


PROJECT_TEST_CMDS: dict[str, str | None] = {
    name: _test_cmd_for(name) for name in PROJECTS
}
