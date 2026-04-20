from telegram import Update
from telegram.ext import ContextTypes

from workerbot.config import PROJECTS
from workerbot.core.locks import lock_for
from workerbot.handlers.base import authorized, send_chunked
from workerbot.runners.git import current_branch, is_dirty, run_git
from workerbot.runners.test_runner import run_tests
from workerbot.storage.approvals import (
    get_pending,
    set_status as set_pending_status,
)


async def pending_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    if not authorized(update):
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
        await send_chunked(
            ctx.bot, row["chat_id"],
            f"📋 Diff de #{pending_id} (rama {row['branch']}):\n\n{out[:6000] or '(sin diff)'}",
        )

    elif action == "tests":
        await ctx.bot.send_message(
            chat_id=row["chat_id"],
            text=f"🧪 Corriendo tests en rama {row['branch']}…",
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
        await send_chunked(
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
                text=(
                    f"✅ Push OK · {row['branch']}\n\n{out}\n\n"
                    f"Abre PR a {row['base_branch'] or 'main'}."
                ),
            )
        else:
            await send_chunked(
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
