import logging
import traceback
from logging.handlers import TimedRotatingFileHandler

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from workerbot.config import (
    LOG_DIR,
    TELEGRAM_ALLOWED_USER_IDS,
    TELEGRAM_BOT_TOKEN,
)
from workerbot.core.scheduler import (
    load_and_schedule_all,
    schedule_heartbeat,
    scheduler,
)
from workerbot.handlers.approvals import pending_callback
from workerbot.handlers.cron import (
    cron_add_cmd,
    cron_del_cmd,
    cron_list_cmd,
    cron_run_cmd,
)
from workerbot.handlers.git import (
    git_branches_cmd,
    git_commit_cmd,
    git_diff_cmd,
    git_fetch_cmd,
    git_log_cmd,
    git_push_cmd,
    git_status_cmd,
    git_switch_cmd,
)
from workerbot.handlers.ops import (
    budget_cmd,
    on_message,
    tests_cmd,
)
from workerbot.handlers.session import (
    current_cmd,
    ping,
    projects_cmd,
    reset_cmd,
    start,
    use_cmd,
    whoami,
)
from workerbot.handlers.tasks import (
    do_cmd,
    pending_cmd,
    skip_cmd,
    tasks_cmd,
)
from workerbot.storage.db import init_db


def _configure_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
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

    if not any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, TimedRotatingFileHandler)
        for h in root.handlers
    ):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(fmt)
        root.addHandler(stream_handler)


_configure_logging()
log = logging.getLogger("worker-bot.app")


async def _error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tb = "".join(traceback.format_exception(None, ctx.error, ctx.error.__traceback__))
    log.error("excepción no manejada: %s\n%s", ctx.error, tb)

    chat_id = None
    if isinstance(update, Update) and update.effective_chat:
        if (
            update.effective_user
            and update.effective_user.id in TELEGRAM_ALLOWED_USER_IDS
        ):
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
    app.add_handler(CommandHandler("projects", projects_cmd))
    app.add_handler(CommandHandler("current", current_cmd))
    app.add_handler(CommandHandler("use", use_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))
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
