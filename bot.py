import logging
import traceback
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from apscheduler.triggers.cron import CronTrigger
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from aider_runner import run_aider
from approvals import get_pending, list_pending, set_status as set_pending_status
from budget import budget_summary, over_budget, record_usage
from config import (
    DEFAULT_PROJECT,
    PROJECTS,
    TELEGRAM_ALLOWED_USER_IDS,
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
    schedule_heartbeat,
    scheduler,
    unschedule_cron,
)
from db import get_project, init_db, set_project
from git_runner import current_branch, is_dirty, run_git
from locks import lock_for
from suggestions import (
    get_suggestion,
    list_suggestions,
    set_suggestion_status,
    summarize,
)
from test_runner import run_tests

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def _configure_logging() -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if any(isinstance(h, TimedRotatingFileHandler) for h in root.handlers):
        return

    file_handler = TimedRotatingFileHandler(
        LOG_DIR / "bot.log",
        when="midnight",
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, TimedRotatingFileHandler) for h in root.handlers):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(fmt)
        root.addHandler(stream_handler)


_configure_logging()
log = logging.getLogger("worker-bot")

MAX_TG_LEN = 3800

AIDER_HISTORY_FILES = (
    ".aider.chat.history.md",
    ".aider.input.history",
    ".aider.llm.history",
)


def _authorized(update: Update) -> bool:
    return bool(
        update.effective_user
        and update.effective_user.id in TELEGRAM_ALLOWED_USER_IDS
    )


def _active_project(chat_id: int) -> str:
    return get_project(chat_id, DEFAULT_PROJECT)


def _parse_command_args(text: str) -> str:
    parts = text.split(None, 1)
    return parts[1] if len(parts) == 2 else ""


async def _send_chunked(update_or_bot, chat_id: int | None, text: str) -> None:
    for i in range(0, len(text), MAX_TG_LEN):
        chunk = text[i : i + MAX_TG_LEN]
        if hasattr(update_or_bot, "message") and update_or_bot.message is not None:
            await update_or_bot.message.reply_text(chunk)
        else:
            await update_or_bot.send_message(chat_id=chat_id, text=chunk)


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
        f"  /projects /current /use <nombre> /reset /ping /whoami\n\n"
        f"Crons (proactividad):\n"
        f"  /cron_add <proyecto>|<cron>|<prompt>\n"
        f"  /cron_list /cron_del <id> /cron_run <id>\n\n"
        f"Tareas y aprobaciones:\n"
        f"  /tasks /do <id> /pending /budget\n\n"
        f"Git:\n"
        f"  /git_status /git_branches /git_switch <rama>\n"
        f"  /git_log [n] /git_diff [target] /git_fetch\n"
        f"  /git_commit <msg> /git_push\n\n"
        f"Cualquier otro mensaje lo mando a Aider."
    )


async def ping(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
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


async def budget_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await update.message.reply_text(budget_summary(update.effective_chat.id))


async def tasks_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
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
    await _send_chunked(update, None, "\n".join(lines))


async def do_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
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

    await update.message.reply_text(
        f"Ejecutando #{task_id} sobre {project}…"
    )
    async with lock_for(project):
        result = await run_aider(path, row["text"])
    record_usage(
        update.effective_chat.id, result.tokens_in, result.tokens_out,
        result.cost_usd, source=f"do:{task_id}",
    )
    set_suggestion_status(task_id, "done")
    await _send_chunked(update, None, result.output)


async def skip_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
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
    if not _authorized(update):
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
    await _send_chunked(update, None, "\n".join(lines))


async def pending_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    if not _authorized(update):
        await query.answer("no autorizado", show_alert=True)
        return

    try:
        _, raw_id, action = query.data.split(":", 2)
        pending_id = int(raw_id)
    except (ValueError, AttributeError):
        await query.answer("callback inválido", show_alert=True)
        return

    row = get_pending(pending_id)
    if not row or row["chat_id"] != update.effective_chat.id:
        await query.answer(f"pending #{pending_id} no existe", show_alert=True)
        return

    project = row["project"]
    path = PROJECTS.get(project)
    if not path:
        await query.answer(f"proyecto '{project}' ya no existe", show_alert=True)
        return

    await query.answer()

    if action == "diff":
        async with lock_for(project):
            _, out = await run_git(path, ["log", "-p", "-1", row["branch"]])
        await _send_chunked(
            ctx.bot, row["chat_id"],
            f"📋 Diff de #{pending_id} (rama {row['branch']}):\n\n{out[:6000] or '(sin diff)'}",
        )

    elif action == "tests":
        await ctx.bot.send_message(
            chat_id=row["chat_id"], text=f"🧪 Corriendo tests en rama {row['branch']}…"
        )
        async with lock_for(project):
            original = await current_branch(path)
            dirty, _ = await is_dirty(path)
            if dirty:
                await ctx.bot.send_message(
                    chat_id=row["chat_id"],
                    text=f"❌ No puedo cambiar a {row['branch']}: hay cambios sin commitear en {original}.",
                )
                return
            code, out = await run_git(path, ["checkout", row["branch"]])
            if code != 0:
                await ctx.bot.send_message(
                    chat_id=row["chat_id"],
                    text=f"❌ No pude cambiar a {row['branch']}:\n{out}",
                )
                return
            try:
                ok, cmd, output = await run_tests(project, path)
            finally:
                await run_git(path, ["checkout", original])
        status = "✅" if ok else "❌"
        await _send_chunked(
            ctx.bot, row["chat_id"],
            f"{status} Tests ({cmd}) en {row['branch']}:\n\n{output}",
        )

    elif action == "push":
        async with lock_for(project):
            original = await current_branch(path)
            code, out = await run_git(path, ["checkout", row["branch"]])
            if code != 0:
                await ctx.bot.send_message(
                    chat_id=row["chat_id"],
                    text=f"❌ No pude cambiar a {row['branch']}:\n{out}",
                )
                return
            try:
                code, out = await run_git(
                    path, ["push", "-u", "origin", row["branch"]], timeout=60,
                )
            finally:
                await run_git(path, ["checkout", original])
        if code == 0:
            set_pending_status(pending_id, "pushed")
            await ctx.bot.send_message(
                chat_id=row["chat_id"],
                text=f"✅ Push OK · {row['branch']}\n\n{out}\n\nAbre PR a {row['base_branch'] or 'main'}.",
            )
        else:
            await _send_chunked(
                ctx.bot, row["chat_id"],
                f"❌ Push falló en {row['branch']}:\n{out}",
            )

    elif action == "reject":
        async with lock_for(project):
            original = await current_branch(path)
            if original == row["branch"]:
                await run_git(path, ["checkout", row["base_branch"] or "main"])
            await run_git(path, ["branch", "-D", row["branch"]])
        set_pending_status(pending_id, "rejected")
        await ctx.bot.send_message(
            chat_id=row["chat_id"],
            text=f"🗑️ Descarté #{pending_id}, rama {row['branch']} eliminada.",
        )

    else:
        await ctx.bot.send_message(
            chat_id=row["chat_id"], text=f"Acción desconocida: {action}"
        )


async def _send_git_output(update: Update, title: str, output: str) -> None:
    body = output or "(sin output)"
    full = f"{title}\n\n{body}"
    for i in range(0, len(full), MAX_TG_LEN):
        await update.message.reply_text(full[i : i + MAX_TG_LEN])


async def git_status_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    name = _active_project(update.effective_chat.id)
    path = PROJECTS[name]
    async with lock_for(name):
        branch = await current_branch(path)
        _, status = await run_git(path, ["status", "--short", "--branch"])
    await _send_git_output(update, f"📁 {name} · 🌿 {branch}", status or "(limpio)")


async def git_branches_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    name = _active_project(update.effective_chat.id)
    path = PROJECTS[name]
    async with lock_for(name):
        _, out = await run_git(
            path,
            ["branch", "-a", "--sort=-committerdate", "--format=%(HEAD) %(refname:short) %(committerdate:relative)"],
        )
    lines = out.splitlines()[:30]
    text = "\n".join(lines) if lines else "(sin ramas)"
    await _send_git_output(update, f"🌿 Ramas en {name} (top 30 por fecha):", text)


async def git_switch_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    if not ctx.args:
        await update.message.reply_text("Uso: /git_switch <rama>")
        return
    target = ctx.args[0]
    name = _active_project(update.effective_chat.id)
    path = PROJECTS[name]

    async with lock_for(name):
        dirty, status = await is_dirty(path)
        if dirty:
            await update.message.reply_text(
                f"⚠️ Hay cambios sin commitear en '{name}':\n\n{status}\n\n"
                f"Haz commit, stash o descarta antes de cambiar de rama."
            )
            return
        code, out = await run_git(path, ["checkout", target])
        if code != 0:
            await _send_git_output(update, f"❌ No pude cambiar a '{target}'", out)
            return
        branch = await current_branch(path)
    await update.message.reply_text(f"✅ '{name}' ahora en rama: {branch}")


async def git_log_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    n = 10
    if ctx.args and ctx.args[0].isdigit():
        n = max(1, min(50, int(ctx.args[0])))
    name = _active_project(update.effective_chat.id)
    path = PROJECTS[name]
    async with lock_for(name):
        _, out = await run_git(
            path,
            ["log", f"-{n}", "--oneline", "--no-decorate", "--format=%h %cr · %an · %s"],
        )
    await _send_git_output(update, f"📜 Últimos {n} commits en {name}:", out)


async def git_diff_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    target = ctx.args[0] if ctx.args else None
    name = _active_project(update.effective_chat.id)
    path = PROJECTS[name]
    async with lock_for(name):
        if target:
            _, out = await run_git(path, ["diff", target, "--stat"])
            title = f"📊 Diff de {name} vs {target}:"
        else:
            _, out = await run_git(path, ["diff", "--stat"])
            title = f"📊 Cambios sin commitear en {name}:"
    await _send_git_output(update, title, out or "(sin diferencias)")


async def git_fetch_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    name = _active_project(update.effective_chat.id)
    path = PROJECTS[name]
    await update.message.reply_text(f"Haciendo fetch en {name}…")
    async with lock_for(name):
        code, out = await run_git(path, ["fetch", "--all", "--prune"], timeout=60)
    await _send_git_output(update, f"{'✅' if code == 0 else '❌'} fetch de {name}:", out)


async def git_commit_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    msg = _parse_command_args(update.message.text or "").strip()
    if not msg:
        await update.message.reply_text(
            "Uso: /git_commit <mensaje>\n"
            "Ejemplo: /git_commit fix: corrige validación de login\n\n"
            "Stageará todos los cambios (git add -A) y hará commit."
        )
        return

    name = _active_project(update.effective_chat.id)
    path = PROJECTS[name]

    async with lock_for(name):
        dirty, _ = await is_dirty(path)
        if not dirty:
            await update.message.reply_text(f"Nada que commitear en '{name}'.")
            return

        branch = await current_branch(path)
        code, out = await run_git(path, ["add", "-A"])
        if code != 0:
            await _send_git_output(update, f"❌ git add falló en {name}:", out)
            return

        code, out = await run_git(path, ["commit", "-m", msg])

    if code != 0:
        await _send_git_output(update, f"❌ commit falló en {name}:", out)
        return
    await _send_git_output(update, f"✅ Commit creado en {name} ({branch}):", out)


async def git_push_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    name = _active_project(update.effective_chat.id)
    path = PROJECTS[name]

    async with lock_for(name):
        branch = await current_branch(path)
        if branch in ("main", "master"):
            await update.message.reply_text(
                f"⚠️ Estás en '{branch}'. Por seguridad no pusheo ramas protegidas desde el bot.\n"
                f"Si es intencional, hazlo manualmente desde la terminal."
            )
            return

        _, upstream = await run_git(
            path, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
        )
        if "no upstream" in upstream.lower() or "fatal" in upstream.lower():
            await update.message.reply_text(f"Seteando upstream y pusheando '{branch}'…")
            code, out = await run_git(
                path, ["push", "-u", "origin", branch], timeout=60
            )
        else:
            await update.message.reply_text(f"Pusheando '{branch}' a {upstream.strip()}…")
            code, out = await run_git(path, ["push"], timeout=60)

    await _send_git_output(
        update, f"{'✅' if code == 0 else '❌'} push de {name}:", out
    )


async def tests_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    name = _active_project(update.effective_chat.id)
    path = PROJECTS[name]
    await update.message.reply_text(f"🧪 Corriendo tests en {name}…")
    async with lock_for(name):
        ok, cmd, out = await run_tests(name, path)
    status = "✅" if ok else "❌"
    await _send_git_output(update, f"{status} Tests ({cmd}) en {name}:", out)


async def on_message(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        log.warning("usuario no autorizado %s", update.effective_user.id if update.effective_user else "?")
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

    name = _active_project(chat_id)
    project_path: Path = PROJECTS[name]

    lock = lock_for(name)
    if lock.locked():
        await update.message.reply_text(f"Esperando que termine la tarea previa en '{name}'…")

    async with lock:
        await update.message.chat.send_action(ChatAction.TYPING)
        await update.message.reply_text(f"Mandando a Aider ({name})…")

        log.info("aider@%s: %s", name, text[:100])
        result = await run_aider(project_path, text)

    record_usage(
        chat_id, result.tokens_in, result.tokens_out, result.cost_usd,
        source="chat",
    )

    output = result.output
    for chunk_start in range(0, len(output), MAX_TG_LEN):
        await update.message.reply_text(output[chunk_start : chunk_start + MAX_TG_LEN])
    if result.cost_usd > 0:
        await update.message.reply_text(f"💰 ${result.cost_usd:.4f}")


async def _error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tb = "".join(traceback.format_exception(None, ctx.error, ctx.error.__traceback__))
    log.error("excepción no manejada: %s\n%s", ctx.error, tb)

    chat_id = None
    if isinstance(update, Update) and update.effective_chat:
        if update.effective_user and update.effective_user.id in TELEGRAM_ALLOWED_USER_IDS:
            chat_id = update.effective_chat.id

    if chat_id is None:
        return

    msg = f"⚠️ Error: {type(ctx.error).__name__}: {ctx.error}"
    if len(msg) > 3800:
        msg = msg[:3800]
    try:
        await ctx.bot.send_message(chat_id=chat_id, text=msg)
    except Exception:
        pass


async def _on_startup(app: Application) -> None:
    init_db()
    init_crons_db()
    scheduler.start()
    n = load_and_schedule_all(app.bot)
    hb = schedule_heartbeat(app.bot)
    log.info("scheduler iniciado, %d crons cargados, heartbeat=%s", n, hb)
    log.info("usuarios autorizados: %s", sorted(TELEGRAM_ALLOWED_USER_IDS))


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
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CommandHandler("projects", projects))
    app.add_handler(CommandHandler("current", current))
    app.add_handler(CommandHandler("use", use))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("cron_add", cron_add_cmd))
    app.add_handler(CommandHandler("cron_list", cron_list_cmd))
    app.add_handler(CommandHandler("cron_del", cron_del_cmd))
    app.add_handler(CommandHandler("cron_run", cron_run_cmd))
    app.add_handler(CommandHandler("budget", budget_cmd))
    app.add_handler(CommandHandler("tasks", tasks_cmd))
    app.add_handler(CommandHandler("do", do_cmd))
    app.add_handler(CommandHandler("skip", skip_cmd))
    app.add_handler(CommandHandler("pending", pending_cmd))
    app.add_handler(CommandHandler("tests", tests_cmd))
    app.add_handler(CommandHandler("git_status", git_status_cmd))
    app.add_handler(CommandHandler("git_branches", git_branches_cmd))
    app.add_handler(CommandHandler("git_switch", git_switch_cmd))
    app.add_handler(CommandHandler("git_log", git_log_cmd))
    app.add_handler(CommandHandler("git_diff", git_diff_cmd))
    app.add_handler(CommandHandler("git_fetch", git_fetch_cmd))
    app.add_handler(CommandHandler("git_commit", git_commit_cmd))
    app.add_handler(CommandHandler("git_push", git_push_cmd))
    app.add_handler(CallbackQueryHandler(pending_callback, pattern=r"^pc:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_error_handler(_error_handler)
    return app
