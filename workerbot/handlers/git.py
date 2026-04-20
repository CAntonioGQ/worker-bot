from telegram import Update
from telegram.ext import ContextTypes

from workerbot.config import PROJECTS
from workerbot.core.locks import lock_for
from workerbot.handlers.base import (
    active_project,
    authorized,
    parse_command_args,
    reply_titled,
)
from workerbot.runners.git import current_branch, is_dirty, run_git


async def git_status_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    name = active_project(update.effective_chat.id)
    path = PROJECTS[name]
    async with lock_for(name):
        branch = await current_branch(path)
        _, status = await run_git(path, ["status", "--short", "--branch"])
    await reply_titled(update, f"📁 {name} · 🌿 {branch}", status or "(limpio)")


async def git_branches_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    name = active_project(update.effective_chat.id)
    path = PROJECTS[name]
    async with lock_for(name):
        _, out = await run_git(
            path,
            [
                "branch", "-a", "--sort=-committerdate",
                "--format=%(HEAD) %(refname:short) %(committerdate:relative)",
            ],
        )
    lines = out.splitlines()[:30]
    text = "\n".join(lines) if lines else "(sin ramas)"
    await reply_titled(update, f"🌿 Ramas en {name} (top 30 por fecha):", text)


async def git_switch_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    if not ctx.args:
        await update.message.reply_text("Uso: /git_switch <rama>")
        return
    target = ctx.args[0]
    name = active_project(update.effective_chat.id)
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
            await reply_titled(update, f"❌ No pude cambiar a '{target}'", out)
            return
        branch = await current_branch(path)
    await update.message.reply_text(f"✅ '{name}' ahora en rama: {branch}")


async def git_log_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    n = 10
    if ctx.args and ctx.args[0].isdigit():
        n = max(1, min(50, int(ctx.args[0])))
    name = active_project(update.effective_chat.id)
    path = PROJECTS[name]
    async with lock_for(name):
        _, out = await run_git(
            path,
            ["log", f"-{n}", "--oneline", "--no-decorate", "--format=%h %cr · %an · %s"],
        )
    await reply_titled(update, f"📜 Últimos {n} commits en {name}:", out)


async def git_diff_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    target = ctx.args[0] if ctx.args else None
    name = active_project(update.effective_chat.id)
    path = PROJECTS[name]
    async with lock_for(name):
        if target:
            _, out = await run_git(path, ["diff", target, "--stat"])
            title = f"📊 Diff de {name} vs {target}:"
        else:
            _, out = await run_git(path, ["diff", "--stat"])
            title = f"📊 Cambios sin commitear en {name}:"
    await reply_titled(update, title, out or "(sin diferencias)")


async def git_fetch_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    name = active_project(update.effective_chat.id)
    path = PROJECTS[name]
    await update.message.reply_text(f"Haciendo fetch en {name}…")
    async with lock_for(name):
        code, out = await run_git(path, ["fetch", "--all", "--prune"], timeout=60)
    await reply_titled(update, f"{'✅' if code == 0 else '❌'} fetch de {name}:", out)


async def git_commit_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    msg = parse_command_args(update.message.text or "").strip()
    if not msg:
        await update.message.reply_text(
            "Uso: /git_commit <mensaje>\n"
            "Ejemplo: /git_commit fix: corrige validación de login\n\n"
            "Stageará todos los cambios (git add -A) y hará commit."
        )
        return

    name = active_project(update.effective_chat.id)
    path = PROJECTS[name]

    async with lock_for(name):
        dirty, _ = await is_dirty(path)
        if not dirty:
            await update.message.reply_text(f"Nada que commitear en '{name}'.")
            return

        branch = await current_branch(path)
        code, out = await run_git(path, ["add", "-A"])
        if code != 0:
            await reply_titled(update, f"❌ git add falló en {name}:", out)
            return

        code, out = await run_git(path, ["commit", "-m", msg])

    if code != 0:
        await reply_titled(update, f"❌ commit falló en {name}:", out)
        return
    await reply_titled(update, f"✅ Commit creado en {name} ({branch}):", out)


async def git_push_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    name = active_project(update.effective_chat.id)
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

    await reply_titled(
        update, f"{'✅' if code == 0 else '❌'} push de {name}:", out
    )
