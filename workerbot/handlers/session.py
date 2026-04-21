import logging

from telegram import Update
from telegram.ext import ContextTypes

from workerbot.config import PROJECTS, TELEGRAM_ALLOWED_USER_IDS
from workerbot.handlers.base import active_project, authorized
from workerbot.storage.chat_state import set_project

log = logging.getLogger("worker-bot.handlers.session")

AIDER_HISTORY_FILES = (
    ".aider.chat.history.md",
    ".aider.input.history",
    ".aider.llm.history",
)


async def start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    current = active_project(update.effective_chat.id)
    project_list = "\n".join(f"  - {n}" for n in PROJECTS.keys())
    await update.message.reply_text(
        f"Hola Antonio. Bot listo.\n\n"
        f"Proyecto activo: {current}\n"
        f"Proyectos registrados:\n{project_list}\n\n"
        f"Sesión:\n"
        f"  /projects /current /use <nombre> /reset /ping /whoami\n\n"
        f"Crons (proactividad):\n"
        f"  /cron_add <proyecto>|<cron>|<prompt>\n"
        f"  /cron_list /cron_del <id> /cron_run <id>\n\n"
        f"Tareas y aprobaciones:\n"
        f"  /tasks /do <id> /skip <id> /pending /budget\n\n"
        f"Git:\n"
        f"  /git_status /git_branches /git_switch <rama>\n"
        f"  /git_log [n] /git_diff [target] /git_fetch\n"
        f"  /git_commit <msg> /git_push\n\n"
        f"Cualquier otro mensaje lo mando a Aider.\n"
        f"Prefija con `@heavy <prompt>` o `@weak <prompt>` para forzar modelo."
    )


async def ping(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    await update.message.reply_text("pong")


async def whoami(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    is_auth = user.id in TELEGRAM_ALLOWED_USER_IDS
    status = "✅ autorizado" if is_auth else "❌ NO autorizado"
    await update.message.reply_text(
        f"Tu info:\n"
        f"  ID: {user.id}\n"
        f"  Nombre: {user.full_name}\n"
        f"  Username: @{user.username or '(sin username)'}\n"
        f"  Estado: {status}"
    )


async def projects_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    current = active_project(update.effective_chat.id)
    lines = [f"Activo: {current}", "", "Registrados:"]
    for name, path in PROJECTS.items():
        marker = " ← activo" if name == current else ""
        lines.append(f"  {name}: {path}{marker}")
    await update.message.reply_text("\n".join(lines))


async def current_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    name = active_project(update.effective_chat.id)
    await update.message.reply_text(
        f"Proyecto activo: {name}\nRuta: {PROJECTS[name]}"
    )


async def use_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
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
    await update.message.reply_text(
        f"Proyecto activo ahora: {name}\nRuta: {PROJECTS[name]}"
    )


async def reset_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    name = active_project(update.effective_chat.id)
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
