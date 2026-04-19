import asyncio
import os
from pathlib import Path


async def run_git(
    project_path: Path, args: list[str], timeout: int = 30
) -> tuple[int, str]:
    """Ejecuta `git <args...>` en project_path. Devuelve (returncode, output)."""
    cmd = ["git", *args]
    env = {**os.environ, "LC_ALL": "C.UTF-8", "GIT_TERMINAL_PROMPT": "0"}

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(project_path),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return -1, f"[timeout {timeout}s]"

    out = stdout.decode("utf-8", errors="replace").rstrip()
    return proc.returncode or 0, out


async def current_branch(project_path: Path) -> str:
    _, out = await run_git(project_path, ["branch", "--show-current"])
    return out.strip() or "(detached HEAD)"


async def is_dirty(project_path: Path) -> tuple[bool, str]:
    _, out = await run_git(project_path, ["status", "--porcelain"])
    return bool(out.strip()), out
