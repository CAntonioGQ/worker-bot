import logging
from datetime import datetime, timedelta, timezone as _tz

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from workerbot.config import (
    HEARTBEAT_CHAT_ID,
    HEARTBEAT_CRON,
    PROJECTS,
    TIMEZONE,
)
from workerbot.core.budget import budget_summary, over_budget
from workerbot.core.locks import lock_for
from workerbot.core.memory import memory_block, summarize
from workerbot.core.prompts import extract_model_marker
from workerbot.runners.aider import run_aider
from workerbot.runners.git import current_branch, is_dirty, run_git
from workerbot.runners.llm import complete as llm_complete
from workerbot.runners.news import fetch_all as fetch_news, format_for_prompt
from workerbot.storage.approvals import create_pending
from workerbot.storage.crons import all_enabled_crons, count_enabled
from workerbot.storage.suggestions import (
    add_suggestion,
    last_failed_run,
    last_run_time,
    record_cron_run,
)
from workerbot.storage.usage import record_usage

MAX_TG_LEN = 3800

log = logging.getLogger("worker-bot.scheduler")

HEARTBEAT_JOB_ID = "heartbeat"

scheduler = AsyncIOScheduler(timezone=TIMEZONE)


def _branch_name(cron_id: int) -> str:
    ts = datetime.now(_tz.utc).strftime("%Y%m%d-%H%M%S")
    return f"bot/cron-{cron_id}-{ts}"


def _approval_keyboard(pending_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📋 Diff", callback_data=f"pc:{pending_id}:diff"),
                InlineKeyboardButton("🧪 Tests", callback_data=f"pc:{pending_id}:tests"),
            ],
            [
                InlineKeyboardButton("✅ Push", callback_data=f"pc:{pending_id}:push"),
                InlineKeyboardButton("❌ Descartar", callback_data=f"pc:{pending_id}:reject"),
            ],
        ]
    )


NEWS_SYSTEM_PROMPT = (
    "Eres un analista de IA que filtra ruido. Te doy titulares crudos de varios "
    "feeds. Devuélveme los TOP 5 más relevantes del período (ignora hype vacío, "
    "duplicados y blogs de marketing). Para cada uno: una línea con qué pasó, "
    "una línea de por qué importa técnicamente, y el link. Responde en español, "
    "estilo conciso."
)


def _parse_news_directive(prompt: str) -> tuple[int, str]:
    """Parsea '@news [day|week] [resto]' → (ventana_en_días, addendum_user)."""
    tail = prompt.strip()[len("@news"):].strip()
    window_days = 1
    if tail:
        first, _, rest = tail.partition(" ")
        lower = first.lower()
        if lower == "week":
            window_days = 7
            tail = rest.strip()
        elif lower == "day":
            window_days = 1
            tail = rest.strip()
    return window_days, tail


async def _run_news_job(
    bot: Bot, chat_id: int, cron_id: int, project: str, prompt: str
) -> None:
    if over_budget(chat_id):
        await bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ Cron #{cron_id} (news) saltado: {budget_summary(chat_id)}",
        )
        return

    window_days, extra = _parse_news_directive(prompt)
    await bot.send_message(
        chat_id=chat_id,
        text=f"📰 Cron #{cron_id} — recolectando noticias IA ({window_days}d)…",
    )

    since = datetime.now(_tz.utc) - timedelta(days=window_days)
    try:
        items = await fetch_news(since=since)
    except Exception as e:
        log.exception("cron %s news fetch falló", cron_id)
        await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Cron #{cron_id}: no pude fetchar feeds: {e}",
        )
        return

    if not items:
        await bot.send_message(
            chat_id=chat_id,
            text=f"📰 Cron #{cron_id}: sin noticias nuevas en {window_days}d.",
        )
        record_cron_run(
            cron_id=cron_id, chat_id=chat_id, project=project,
            summary="(sin noticias)", output="",
            tokens_in=0, tokens_out=0, cost_usd=0,
            branch=None, had_changes=False,
        )
        return

    feed_text = format_for_prompt(items)
    user_prompt = (
        (f"Nota extra del usuario: {extra}\n\n" if extra else "")
        + f"Titulares de las últimas {window_days * 24}h ({len(items)} items):\n\n"
        + feed_text
    )

    try:
        result = await llm_complete(user_prompt, system=NEWS_SYSTEM_PROMPT)
    except Exception as e:
        log.exception("cron %s news llm falló", cron_id)
        await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Cron #{cron_id}: error llamando al LLM: {e}",
        )
        return

    record_usage(
        chat_id, result.tokens_in, result.tokens_out, result.cost_usd,
        source=f"cron:{cron_id}:news",
    )
    record_cron_run(
        cron_id=cron_id, chat_id=chat_id, project=project,
        summary=summarize(result.output), output=result.output,
        tokens_in=result.tokens_in, tokens_out=result.tokens_out,
        cost_usd=result.cost_usd,
        branch=None, had_changes=False,
    )

    header = (
        f"📰 Cron #{cron_id} — Noticias IA · {window_days}d · "
        f"{len(items)} titulares · ${result.cost_usd:.4f}\n\n"
    )
    full = header + result.output
    for i in range(0, len(full), MAX_TG_LEN):
        await bot.send_message(chat_id=chat_id, text=full[i : i + MAX_TG_LEN])


async def run_cron_job(
    bot: Bot, chat_id: int, project: str, prompt: str, cron_id: int
) -> None:
    if prompt.strip().startswith("@news"):
        await _run_news_job(bot, chat_id, cron_id, project, prompt)
        return

    prompt, model_override = extract_model_marker(prompt)

    project_path = PROJECTS.get(project)
    if not project_path:
        log.warning("cron %s: proyecto %s no existe", cron_id, project)
        await bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ Cron #{cron_id}: proyecto '{project}' no existe, se salta.",
        )
        return

    if over_budget(chat_id):
        log.info("cron %s: cap diario alcanzado, skip", cron_id)
        await bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ Cron #{cron_id} saltado: {budget_summary(chat_id)}",
        )
        return

    model_tag = (
        f" · 🔥 {model_override.split('/')[-1]}" if model_override else ""
    )
    await bot.send_message(
        chat_id=chat_id,
        text=f"🕒 Cron #{cron_id} ({project}){model_tag} ejecutando…",
    )

    async with lock_for(project):
        original_branch = await current_branch(project_path)

        stash_created = False
        dirty_before, _ = await is_dirty(project_path)
        if dirty_before:
            code, out = await run_git(
                project_path,
                ["stash", "push", "-u", "-m", f"workerbot-cron-{cron_id}"],
            )
            if code != 0:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"⚠️ Cron #{cron_id} abortado: no pude stashear tu WIP "
                        f"en '{original_branch}':\n{out}"
                    ),
                )
                return
            stash_created = True

        await run_git(project_path, ["fetch", "--all", "--prune"], timeout=60)

        branch = _branch_name(cron_id)
        code, out = await run_git(project_path, ["checkout", "-b", branch])
        if code != 0:
            if stash_created:
                await run_git(project_path, ["stash", "pop"])
            await bot.send_message(
                chat_id=chat_id,
                text=f"❌ Cron #{cron_id}: no pude crear rama '{branch}':\n{out}",
            )
            return

        full_prompt = prompt + memory_block(cron_id)
        result = None
        had_changes = False
        pending_id: int | None = None
        diff_stat = ""
        aider_error: Exception | None = None

        try:
            result = await run_aider(project_path, full_prompt, model=model_override)
            record_usage(
                chat_id, result.tokens_in, result.tokens_out, result.cost_usd,
                source=f"cron:{cron_id}",
            )

            dirty_after, _ = await is_dirty(project_path)
            if dirty_after:
                await run_git(project_path, ["add", "-A"])
                code, _ = await run_git(
                    project_path,
                    ["commit", "-m", f"cron #{cron_id}: {prompt[:60]}"],
                )
                if code == 0:
                    _, diff_stat = await run_git(
                        project_path, ["show", "--stat", "--format=", "HEAD"]
                    )
                    had_changes = True
                    pending_id = create_pending(
                        chat_id=chat_id,
                        project=project,
                        source_cron_id=cron_id,
                        branch=branch,
                        base_branch=original_branch,
                        diff_stat=diff_stat,
                        full_output=result.output,
                    )
        except Exception as e:
            aider_error = e
            log.exception("cron %s falló durante Aider: %s", cron_id, e)
        finally:
            await run_git(project_path, ["checkout", original_branch])
            if not had_changes:
                await run_git(project_path, ["branch", "-D", branch])
            if stash_created:
                pop_code, pop_out = await run_git(
                    project_path, ["stash", "pop"]
                )
                if pop_code != 0:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"⚠️ Cron #{cron_id}: tu WIP quedó en `git stash` "
                            f"(no pude popearlo automáticamente):\n{pop_out}\n\n"
                            f"Revísalo con `git stash list` y `git stash pop` a mano."
                        ),
                    )

    if aider_error is not None:
        await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Cron #{cron_id} falló: {type(aider_error).__name__}: {aider_error}",
        )
        return

    assert result is not None
    short = summarize(result.output)
    record_cron_run(
        cron_id=cron_id, chat_id=chat_id, project=project,
        summary=short, output=result.output,
        tokens_in=result.tokens_in, tokens_out=result.tokens_out,
        cost_usd=result.cost_usd,
        branch=branch if had_changes else None,
        had_changes=had_changes,
    )

    if had_changes and pending_id is not None:
        header = (
            f"🕒 Cron #{cron_id} ({project}) — propuso cambios\n"
            f"Rama: {branch}\n"
            f"Costo: ${result.cost_usd:.4f}\n\n"
            f"Resumen: {short}\n\n"
            f"{diff_stat.strip()[:800]}"
        )
        await bot.send_message(
            chat_id=chat_id,
            text=header,
            reply_markup=_approval_keyboard(pending_id),
        )
    else:
        sug_id = add_suggestion(chat_id, project, result.output, source_cron_id=cron_id)
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"🕒 Cron #{cron_id} ({project}) · ${result.cost_usd:.4f}\n\n"
                f"{short}\n\n"
                f"Guardada como sugerencia #{sug_id}. Ejecuta con /do {sug_id}"
            ),
        )


async def run_heartbeat(bot: Bot) -> None:
    if HEARTBEAT_CHAT_ID is None:
        return
    n = count_enabled()
    last = last_run_time()
    failed = last_failed_run()
    lines = [
        "💓 Heartbeat worker-bot",
        f"Crons activos: {n}",
        f"Último run: {last[:16] if last else '—'}",
        budget_summary(HEARTBEAT_CHAT_ID),
    ]
    if failed:
        lines.append(
            f"⚠️ Último posible error: cron #{failed['cron_id']} "
            f"@ {failed['ran_at'][:16]}"
        )
    await bot.send_message(chat_id=HEARTBEAT_CHAT_ID, text="\n".join(lines))


def schedule_cron(bot: Bot, row) -> None:
    trigger = CronTrigger.from_crontab(row["cron_expr"], timezone=TIMEZONE)
    scheduler.add_job(
        func=run_cron_job,
        trigger=trigger,
        args=[bot, row["chat_id"], row["project"], row["prompt"], row["id"]],
        id=f"cron_{row['id']}",
        replace_existing=True,
    )


def unschedule_cron(cron_id: int) -> None:
    try:
        scheduler.remove_job(f"cron_{cron_id}")
    except Exception as e:
        log.debug("unschedule %s: %s", cron_id, e)


def schedule_heartbeat(bot: Bot) -> bool:
    if not HEARTBEAT_CRON or HEARTBEAT_CHAT_ID is None:
        return False
    try:
        trigger = CronTrigger.from_crontab(HEARTBEAT_CRON, timezone=TIMEZONE)
    except Exception as e:
        log.error("heartbeat cron inválido '%s': %s", HEARTBEAT_CRON, e)
        return False
    scheduler.add_job(
        func=run_heartbeat,
        trigger=trigger,
        args=[bot],
        id=HEARTBEAT_JOB_ID,
        replace_existing=True,
    )
    return True


def load_and_schedule_all(bot: Bot) -> int:
    count = 0
    for row in all_enabled_crons():
        try:
            schedule_cron(bot, row)
            count += 1
        except Exception as e:
            log.error(
                "no pude programar cron %s (%s): %s",
                row["id"], row["cron_expr"], e,
            )
    return count
