import logging
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from workerbot.config import PROJECTS
from workerbot.core.budget import budget_summary, over_budget
from workerbot.core.locks import lock_for
from workerbot.core.prompts import extract_model_marker
from workerbot.handlers.base import (
    active_project,
    authorized,
    reply_titled,
)
from workerbot.runners.aider import run_aider
from workerbot.runners.test_runner import run_tests
from workerbot.storage.usage import record_usage

log = logging.getLogger("worker-bot.handlers.ops")

MAX_TG_LEN = 3800


async def budget_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    await update.message.reply_text(budget_summary(update.effective_chat.id))


async def tests_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    name = active_project(update.effective_chat.id)
    path = PROJECTS[name]
    await update.message.reply_text(f"🧪 Corriendo tests en {name}…")
    async with lock_for(name):
        ok, cmd, out = await run_tests(name, path)
    status = "✅" if ok else "❌"
    await reply_titled(update, f"{status} Tests ({cmd}) en {name}:", out)


async def on_message(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        log.warning(
            "usuario no autorizado %s",
            update.effective_user.id if update.effective_user else "?",
        )
        return

    text = update.message.text or ""
    if not text.strip():
        return

    chat_id = update.effective_chat.id
    if over_budget(chat_id):
        await update.message.reply_text(
            f"⚠️ Cap diario alcanzado. {budget_summary(chat_id)}\n"
            "Aumenta DAILY_BUDGET_USD en .env o espera a medianoche UTC."
        )
        return

    name = active_project(chat_id)
    project_path: Path = PROJECTS[name]

    clean_text, model_override = extract_model_marker(text)
    if not clean_text.strip():
        await update.message.reply_text(
            "Usa `@heavy <instrucción>` o `@weak <instrucción>` seguido del "
            "prompt. Solo el marker no hace nada."
        )
        return

    lock = lock_for(name)
    if lock.locked():
        await update.message.reply_text(
            f"Esperando que termine la tarea previa en '{name}'…"
        )

    tag = f" · 🔥 {model_override.split('/')[-1]}" if model_override else ""
    async with lock:
        await update.message.chat.send_action(ChatAction.TYPING)
        await update.message.reply_text(f"Mandando a Aider ({name}){tag}…")

        log.info("aider@%s model=%s: %s", name, model_override or "default", clean_text[:100])
        result = await run_aider(project_path, clean_text, model=model_override)

    record_usage(
        chat_id, result.tokens_in, result.tokens_out, result.cost_usd,
        source="chat",
    )

    output = result.output
    for chunk_start in range(0, len(output), MAX_TG_LEN):
        await update.message.reply_text(output[chunk_start : chunk_start + MAX_TG_LEN])
    if result.cost_usd > 0:
        await update.message.reply_text(f"💰 ${result.cost_usd:.4f}")
