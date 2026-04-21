from telegram import Update
from telegram.ext import ContextTypes

from workerbot.config import PROJECTS
from workerbot.core.budget import budget_summary, over_budget
from workerbot.core.locks import lock_for
from workerbot.core.memory import summarize
from workerbot.core.prompts import extract_model_marker
from workerbot.handlers.base import authorized, reply_chunked
from workerbot.runners.aider import run_aider
from workerbot.storage.approvals import list_pending
from workerbot.storage.suggestions import (
    get_suggestion,
    list_suggestions,
    set_suggestion_status,
)
from workerbot.storage.usage import record_usage


async def tasks_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    rows = list_suggestions(update.effective_chat.id)
    if not rows:
        await update.message.reply_text(
            "No hay sugerencias pendientes. Los crons van guardando aquí lo que proponen."
        )
        return
    lines = [f"Sugerencias pendientes ({len(rows)}):\n"]
    for r in rows:
        snip = summarize(r["text"], max_chars=120)
        src = f" · cron #{r['source_cron_id']}" if r["source_cron_id"] else ""
        lines.append(f"#{r['id']} [{r['project']}]{src}\n  {snip}")
    lines.append("\nEjecuta una con /do <id> · Descártala con /skip <id>")
    await reply_chunked(update, "\n".join(lines))


async def do_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Uso: /do <id>")
        return
    task_id = int(ctx.args[0])
    row = get_suggestion(task_id)
    if not row or row["chat_id"] != update.effective_chat.id:
        await update.message.reply_text(f"No existe sugerencia #{task_id}.")
        return
    if row["status"] != "pending":
        await update.message.reply_text(
            f"Sugerencia #{task_id} ya está en estado '{row['status']}'."
        )
        return

    project = row["project"]
    path = PROJECTS.get(project)
    if not path:
        await update.message.reply_text(f"Proyecto '{project}' ya no existe.")
        return
    if over_budget(update.effective_chat.id):
        await update.message.reply_text(
            f"⚠️ Cap diario alcanzado. {budget_summary(update.effective_chat.id)}"
        )
        return

    clean_text, model_override = extract_model_marker(row["text"])
    tag = f" · 🔥 {model_override.split('/')[-1]}" if model_override else ""
    await update.message.reply_text(f"Ejecutando #{task_id} sobre {project}{tag}…")
    async with lock_for(project):
        result = await run_aider(path, clean_text or row["text"], model=model_override)
    record_usage(
        update.effective_chat.id, result.tokens_in, result.tokens_out,
        result.cost_usd, source=f"do:{task_id}",
    )
    set_suggestion_status(task_id, "done")
    await reply_chunked(update, result.output)


async def skip_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Uso: /skip <id>")
        return
    task_id = int(ctx.args[0])
    row = get_suggestion(task_id)
    if not row or row["chat_id"] != update.effective_chat.id:
        await update.message.reply_text(f"No existe sugerencia #{task_id}.")
        return
    set_suggestion_status(task_id, "rejected")
    await update.message.reply_text(f"🗑️ Sugerencia #{task_id} descartada.")


async def pending_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    rows = list_pending(update.effective_chat.id)
    if not rows:
        await update.message.reply_text("No hay cambios pendientes de aprobación.")
        return
    lines = [f"Cambios pendientes ({len(rows)}):\n"]
    for r in rows:
        src = f" · cron #{r['source_cron_id']}" if r["source_cron_id"] else ""
        lines.append(
            f"#{r['id']} [{r['project']}] rama `{r['branch']}`{src}\n"
            f"  {r['created_at'][:16]}"
        )
    lines.append(
        "\nUsa los botones del mensaje original o manualmente:\n"
        "  /git_switch <rama> para revisar."
    )
    await reply_chunked(update, "\n".join(lines))
