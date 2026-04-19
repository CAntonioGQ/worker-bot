import asyncio
import os
import re
from pathlib import Path

from config import AIDER_MODEL, AIDER_WEAK_MODEL, OPENROUTER_API_KEY

NOISE_PREFIXES = (
    "Aider v",
    "Model:",
    "Weak model:",
    "Git repo:",
    "Repo-map:",
    "Added .aider",
    "Initial repo scan",
    "You can skip this",
    "Can't initialize prompt toolkit",
    "Found xterm",
    "Maybe try",
    "Or otherwise",
    "Detected dumb terminal",
    "disabling fancy input",
    "https://aider.chat",
    "Update:",
    "Use /help",
)

PROGRESS_OVERWRITE_RE = re.compile(r"[^\r\n]*\r(?!\n)")


def clean_output(raw: str) -> str:
    raw = raw.replace("\r\n", "\n")
    raw = PROGRESS_OVERWRITE_RE.sub("", raw)

    lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        if stripped.startswith(NOISE_PREFIXES):
            continue
        lines.append(line)

    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


async def run_aider(project_path: Path, message: str, timeout: int = 300) -> str:
    cmd = [
        "aider",
        "--model", AIDER_MODEL,
        "--weak-model", AIDER_WEAK_MODEL,
        "--message", message,
        "--yes-always",
        "--no-auto-commits",
        "--no-show-model-warnings",
        "--no-pretty",
        "--no-stream",
        "--no-check-update",
    ]

    env = {
        **os.environ,
        "OPENROUTER_API_KEY": OPENROUTER_API_KEY,
        "TERM": "dumb",
        "NO_COLOR": "1",
        "PYTHONIOENCODING": "utf-8",
    }

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(project_path),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
    )

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return f"[timeout después de {timeout}s]"

    raw = stdout.decode("utf-8", errors="replace")
    return clean_output(raw) or "[sin respuesta]"
