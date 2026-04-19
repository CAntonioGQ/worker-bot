import logging
from datetime import datetime, timezone as _tz

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot

from aider_runner import run_aider
from config import PROJECTS
from db import _conn
from locks import lock_for

log = logging.getLogger("worker-bot.crons")

TIMEZONE = "America/Mexico_City"
MAX_TG_LEN = 3800

scheduler = AsyncIOScheduler(timezone=TIMEZONE)


def init_crons_db() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS crons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                project TEXT NOT NULL,
                cron_expr TEXT NOT NULL,
                prompt TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )


def add_cron(chat_id: int, project: str, cron_expr: str, prompt: str) -> int:
    now = datetime.now(_tz.utc).isoformat()
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO crons (chat_id, project, cron_expr, prompt, enabled, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (chat_id, project, cron_expr, prompt, now),
        )
        return cur.lastrowid


def list_crons(chat_id: int) -> list:
    with _conn() as c:
        return c.execute(
            "SELECT * FROM crons WHERE chat_id = ? ORDER BY id",
            (chat_id,),
        ).fetchall()


def get_cron(cron_id: int):
    with _conn() as c:
        return c.execute("SELECT * FROM crons WHERE id = ?", (cron_id,)).fetchone()


def delete_cron(cron_id: int) -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM crons WHERE id = ?", (cron_id,))
        return cur.rowcount > 0


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

    await bot.send_message(
        chat_id=chat_id,
        text=f"🕒 Cron #{cron_id} ({project}) ejecutando…",
    )

    async with lock_for(project):
        output = await run_aider(project_path, prompt)

    header = f"🕒 Cron #{cron_id} ({project}) — resultado:\n\n"
    full = header + output
    for i in range(0, len(full), MAX_TG_LEN):
        await bot.send_message(chat_id=chat_id, text=full[i : i + MAX_TG_LEN])


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


def load_and_schedule_all(bot: Bot) -> int:
    with _conn() as c:
        rows = c.execute("SELECT * FROM crons WHERE enabled = 1").fetchall()
    count = 0
    for row in rows:
        try:
            schedule_cron(bot, row)
            count += 1
        except Exception as e:
            log.error("no pude programar cron %s (%s): %s", row["id"], row["cron_expr"], e)
    return count
