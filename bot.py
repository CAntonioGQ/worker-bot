import logging
from pathlib import Path

from apscheduler.triggers.cron import CronTrigger
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
from crons import (
    add_cron,
    delete_cron,
    get_cron,
    init_crons_db,
    list_crons,
    load_and_schedule_all,
    run_cron_job,
    schedule_cron,
    scheduler,
    unschedule_cron,
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


def _parse_command_args(text: str) -> str:
    parts = text.split(None, 1)
    return parts[1] if len(parts) == 2 else ""


async def start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    current = _active_project(update.effective_chat.id)
    project_list = "\n".join(f"  - {n}" for n in PROJECTS.keys())
    await update.message.reply_text(
        f"Hola Antonio. Bot listo.\n\n"
        f"Proyecto activo: {current}\n"
        f"Proyectos registrados:\n{project_list}\n\n"
        f"Sesión:\n"
        f"  /projects /current /use <nombre> /reset /ping\n\n"
        f"Crons (proactividad):\n"
        f"  /cron_add <proyecto>|<cron>|<prompt>\n"
        f"  /cron_list /cron_del <id> /cron_run <id>\n\n"
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


async def cron_add_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    args_text = _parse_command_args(update.message.text or "")
    parts = [p.strip() for p in args_text.split("|", 2)]
    if len(parts) != 3 or not all(parts):
        await update.message.reply_text(
            "Uso: /cron_add <proyecto>|<cron>|<prompt>\n\n"
            "Ejemplos:\n"
            "  /cron_add webapp|0 9 * * *|Revisa TODOs y sugiere siguiente paso\n"
            "  /cron_add orchestrator|*/30 * * * *|Lista cambios recientes\n\n"
            f"Proyectos: {', '.join(PROJECTS.keys())}\n"
            "Zona horaria: America/Mexico_City\n"
            "Formato cron: min hora dia_mes mes dia_semana"
        )
        return
    project, cron_expr, prompt = parts
    if project not in PROJECTS:
        await update.message.reply_text(
            f"No existe proyecto '{project}'. Opciones: {', '.join(PROJECTS.keys())}"
        )
        return
    try:
        CronTrigger.from_crontab(cron_expr)
    except Exception as e:
        await update.message.reply_text(f"Expresión cron inválida '{cron_expr}': {e}")
        return

    cron_id = add_cron(update.effective_chat.id, project, cron_expr, prompt)
    row = get_cron(cron_id)
    schedule_cron(ctx.application.bot, row)
    await update.message.reply_text(
        f"✅ Cron #{cron_id} agregado.\n"
        f"Proyecto: {project}\n"
        f"Cron: {cron_expr}\n"
        f"Prompt: {prompt}"
    )


async def cron_list_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    rows = list_crons(update.effective_chat.id)
    if not rows:
        await update.message.reply_text("No tienes crons registrados.")
        return
    lines = [f"Crons registrados ({len(rows)}):\n"]
    for r in rows:
        job = None
        for j in scheduler.get_jobs():
            if j.id == f"cron_{r['id']}":
                job = j
                break
        next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M %Z") if job and job.next_run_time else "—"
        lines.append(
            f"#{r['id']} [{r['project']}] `{r['cron_expr']}`\n"
            f"  Próximo: {next_run}\n"
            f"  Prompt: {r['prompt'][:80]}"
        )
    await update.message.reply_text("\n".join(lines))


async def cron_del_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Uso: /cron_del <id>")
        return
    cron_id = int(ctx.args[0])
    row = get_cron(cron_id)
    if not row or row["chat_id"] != update.effective_chat.id:
        await update.message.reply_text(f"No existe cron #{cron_id}.")
        return
    unschedule_cron(cron_id)
    delete_cron(cron_id)
    await update.message.reply_text(f"🗑️ Cron #{cron_id} eliminado.")


async def cron_run_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Uso: /cron_run <id>")
        return
    cron_id = int(ctx.args[0])
    row = get_cron(cron_id)
    if not row or row["chat_id"] != update.effective_chat.id:
        await update.message.reply_text(f"No existe cron #{cron_id}.")
        return
    await update.message.reply_text(f"Disparando cron #{cron_id} manualmente…")
    ctx.application.create_task(
        run_cron_job(
            ctx.application.bot,
            row["chat_id"],
            row["project"],
            row["prompt"],
            row["id"],
        )
    )


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


async def _on_startup(app: Application) -> None:
    init_db()
    init_crons_db()
    scheduler.start()
    n = load_and_schedule_all(app.bot)
    log.info("scheduler iniciado, %d crons cargados", n)


async def _on_shutdown(_app: Application) -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
    log.info("scheduler apagado")


def build_app() -> Application:
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_on_startup)
        .post_shutdown(_on_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("projects", projects))
    app.add_handler(CommandHandler("current", current))
    app.add_handler(CommandHandler("use", use))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("cron_add", cron_add_cmd))
    app.add_handler(CommandHandler("cron_list", cron_list_cmd))
    app.add_handler(CommandHandler("cron_del", cron_del_cmd))
    app.add_handler(CommandHandler("cron_run", cron_run_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app
