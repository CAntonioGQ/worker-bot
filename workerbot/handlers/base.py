from telegram import Update

from workerbot.config import DEFAULT_PROJECT, TELEGRAM_ALLOWED_USER_IDS
from workerbot.storage.chat_state import get_project

MAX_TG_LEN = 3800


def authorized(update: Update) -> bool:
    return bool(
        update.effective_user
        and update.effective_user.id in TELEGRAM_ALLOWED_USER_IDS
    )


def active_project(chat_id: int) -> str:
    return get_project(chat_id, DEFAULT_PROJECT)


def parse_command_args(text: str) -> str:
    parts = text.split(None, 1)
    return parts[1] if len(parts) == 2 else ""


async def reply_chunked(update: Update, text: str) -> None:
    for i in range(0, len(text), MAX_TG_LEN):
        await update.message.reply_text(text[i : i + MAX_TG_LEN])


async def send_chunked(bot, chat_id: int, text: str) -> None:
    for i in range(0, len(text), MAX_TG_LEN):
        await bot.send_message(chat_id=chat_id, text=text[i : i + MAX_TG_LEN])


async def reply_titled(update: Update, title: str, body: str) -> None:
    body = body or "(sin output)"
    await reply_chunked(update, f"{title}\n\n{body}")
