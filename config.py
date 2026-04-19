import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_ALLOWED_USER_ID = int(os.environ["TELEGRAM_ALLOWED_USER_ID"])

AIDER_MODEL = os.environ.get("AIDER_MODEL", "openrouter/deepseek/deepseek-chat")
AIDER_WEAK_MODEL = os.environ.get("AIDER_WEAK_MODEL", AIDER_MODEL)

PROJECTS: dict[str, Path] = {
    "webapp": Path(os.environ["PROJECT_AVI_WEBAPP"]),
    "orchestrator": Path(os.environ["PROJECT_AVI_ORCHESTRATOR"]),
}

DEFAULT_PROJECT = "webapp"
