from apscheduler.triggers.cron import CronTrigger
from telegram import Update
from telegram.ext import ContextTypes

from workerbot.config import PROJECTS
from workerbot.core.scheduler import (
    run_cron_job,
    schedule_cron,
    scheduler,
    unschedule_cron,
)
from workerbot.handlers.base import authorized, parse_command_args
from workerbot.storage.crons import (
    add_cron,
    delete_cron,
    get_cron,
    list_crons,
)


async def cron_add_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    args_text = parse_command_args(update.message.text or "")
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
    if not authorized(update):
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
        next_run = (
            job.next_run_time.strftime("%Y-%m-%d %H:%M %Z")
            if job and job.next_run_time
            else "—"
        )
        lines.append(
            f"#{r['id']} [{r['project']}] `{r['cron_expr']}`\n"
            f"  Próximo: {next_run}\n"
            f"  Prompt: {r['prompt'][:80]}"
        )
    await update.message.reply_text("\n".join(lines))


async def cron_del_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
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
    if not authorized(update):
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
