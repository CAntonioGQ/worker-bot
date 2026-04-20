import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path

from workerbot.config import AIDER_MODEL, AIDER_WEAK_MODEL, OPENROUTER_API_KEY

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
    "Tokens:",
    "Cost:",
)

PROGRESS_OVERWRITE_RE = re.compile(r"[^\r\n]*\r(?!\n)")

_TOKEN_RE = re.compile(
    r"Tokens:\s*([\d.,]+)\s*([kKmM]?)\s*sent[^\d]*([\d.,]+)\s*([kKmM]?)\s*received",
    re.IGNORECASE,
)
_COST_RE = re.compile(r"Cost:\s*\$([\d.]+)\s*message", re.IGNORECASE)


@dataclass
class AiderResult:
    output: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    def __str__(self) -> str:
        return self.output


def _to_int(num: str, suffix: str) -> int:
    try:
        base = float(num.replace(",", ""))
    except ValueError:
        return 0
    mult = {"k": 1_000, "K": 1_000, "m": 1_000_000, "M": 1_000_000}.get(suffix, 1)
    return int(base * mult)


def _parse_usage(raw: str) -> tuple[int, int, float]:
    tokens_in = tokens_out = 0
    cost = 0.0
    for m in _TOKEN_RE.finditer(raw):
        tokens_in += _to_int(m.group(1), m.group(2))
        tokens_out += _to_int(m.group(3), m.group(4))
    for m in _COST_RE.finditer(raw):
        try:
            cost += float(m.group(1))
        except ValueError:
            pass
    return tokens_in, tokens_out, cost


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


def _extra_read_files(project_path: Path) -> list[str]:
    """Archivos de contexto que cargamos a Aider si existen en el proyecto.

    CLAUDE.md (convenciones de Claude Code) y CONVENTIONS.md (nativo de Aider)
    se inyectan con --read para que Aider los considere como contexto read-only.
    """
    candidates = ("CLAUDE.md", "CONVENTIONS.md", ".aider.conventions.md")
    found = []
    for name in candidates:
        p = project_path / name
        if p.exists() and p.is_file():
            found.append(str(p))
    return found


async def run_aider(
    project_path: Path, message: str, timeout: int = 300
) -> AiderResult:
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
    for extra in _extra_read_files(project_path):
        cmd.extend(["--read", extra])

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
        return AiderResult(output=f"[timeout después de {timeout}s]")

    raw = stdout.decode("utf-8", errors="replace")
    tokens_in, tokens_out, cost = _parse_usage(raw)
    cleaned = clean_output(raw) or "[sin respuesta]"
    return AiderResult(
        output=cleaned,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
    )
