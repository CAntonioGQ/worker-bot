import logging
from datetime import datetime, timezone as _tz

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
from workerbot.runners.aider import run_aider
from workerbot.runners.git import current_branch, is_dirty, run_git
from workerbot.storage.approvals import create_pending
from workerbot.storage.crons import all_enabled_crons, count_enabled
from workerbot.storage.suggestions import (
    add_suggestion,
    last_failed_run,
    last_run_time,
    record_cron_run,
)
from workerbot.storage.usage import record_usage

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


async def run_cron_job(
    bot: Bot, chat_id: int, project: str, prompt: str, cron_id: int
) -> None:
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

    await bot.send_message(
        chat_id=chat_id, text=f"🕒 Cron #{cron_id} ({project}) ejecutando…"
    )

    async with lock_for(project):
        original_branch = await current_branch(project_path)

        dirty_before, _ = await is_dirty(project_path)
        if dirty_before:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⚠️ Cron #{cron_id} abortado: '{project}' tiene cambios sin commitear "
                    f"en '{original_branch}'. Limpia antes de que el cron pueda aislar."
                ),
            )
            return

        await run_git(project_path, ["fetch", "--all", "--prune"], timeout=60)

        branch = _branch_name(cron_id)
        code, out = await run_git(project_path, ["checkout", "-b", branch])
        if code != 0:
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
            result = await run_aider(project_path, full_prompt)
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
