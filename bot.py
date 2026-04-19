import logging
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from aider_runner import run_aider
from config import (
    DEFAULT_PROJECT,
    PROJECTS,
    TELEGRAM_ALLOWED_USER_ID,
    TELEGRAM_BOT_TOKEN,
)
from db import get_project, init_db, set_project
from locks import lock_for

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("worker-bot")

MAX_TG_LEN = 3800

AIDER_HISTORY_FILES = (
    ".aider.chat.history.md",
    ".aider.input.history",
    ".aider.llm.history",
)


def _authorized(update: Update) -> bool:
    return update.effective_user and update.effective_user.id == TELEGRAM_ALLOWED_USER_ID


def _active_project(chat_id: int) -> str:
    return get_project(chat_id, DEFAULT_PROJECT)


async def start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    current = _active_project(update.effective_chat.id)
    project_list = "\n".join(f"  - {n}" for n in PROJECTS.keys())
    await update.message.reply_text(
        f"Hola Antonio. Bot listo.\n\n"
        f"Proyecto activo: {current}\n"
        f"Proyectos registrados:\n{project_list}\n\n"
        f"Comandos:\n"
        f"  /projects — lista proyectos\n"
        f"  /use <nombre> — cambia proyecto activo\n"
        f"  /current — muestra proyecto activo\n"
        f"  /reset — limpia historial del proyecto activo\n"
        f"  /ping — test de vida\n\n"
        f"Cualquier otro mensaje lo mando a Aider."
    )


async def ping(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await update.message.reply_text("pong")


async def projects(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    current = _active_project(update.effective_chat.id)
    lines = [f"Activo: {current}", "", "Registrados:"]
    for name, path in PROJECTS.items():
        marker = " ← activo" if name == current else ""
        lines.append(f"  {name}: {path}{marker}")
    await update.message.reply_text("\n".join(lines))


async def current(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    name = _active_project(update.effective_chat.id)
    await update.message.reply_text(f"Proyecto activo: {name}\nRuta: {PROJECTS[name]}")


async def use(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    if not ctx.args:
        await update.message.reply_text(
            "Uso: /use <nombre>\nOpciones: " + ", ".join(PROJECTS.keys())
        )
        return
    name = ctx.args[0].strip().lower()
    if name not in PROJECTS:
        await update.message.reply_text(
            f"No existe '{name}'. Registrados: {', '.join(PROJECTS.keys())}"
        )
        return
    set_project(update.effective_chat.id, name)
    await update.message.reply_text(f"Proyecto activo ahora: {name}\nRuta: {PROJECTS[name]}")


async def reset(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    name = _active_project(update.effective_chat.id)
    project_path = PROJECTS[name]
    removed = []
    for fname in AIDER_HISTORY_FILES:
        p = project_path / fname
        if p.exists():
            try:
                p.unlink()
                removed.append(fname)
            except OSError as e:
                log.warning("no pude borrar %s: %s", p, e)
    if removed:
        await update.message.reply_text(
            f"Historial de '{name}' limpio. Borrados: {', '.join(removed)}"
        )
    else:
        await update.message.reply_text(f"No había historial previo en '{name}'.")


async def on_message(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        log.warning("usuario no autorizado %s", update.effective_user.id if update.effective_user else "?")
        return

    text = update.message.text or ""
    if not text.strip():
        return

    chat_id = update.effective_chat.id
    name = _active_project(chat_id)
    project_path: Path = PROJECTS[name]

    lock = lock_for(name)
    if lock.locked():
        await update.message.reply_text(f"Esperando que termine la tarea previa en '{name}'…")

    async with lock:
        await update.message.chat.send_action(ChatAction.TYPING)
        await update.message.reply_text(f"Mandando a Aider ({name})…")

        log.info("aider@%s: %s", name, text[:100])
        output = await run_aider(project_path, text)

    for chunk_start in range(0, len(output), MAX_TG_LEN):
        await update.message.reply_text(output[chunk_start : chunk_start + MAX_TG_LEN])


def build_app() -> Application:
    init_db()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("projects", projects))
    app.add_handler(CommandHandler("current", current))
    app.add_handler(CommandHandler("use", use))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app
