import asyncio
import os
import shlex
from pathlib import Path

from config import PROJECT_TEST_CMDS


def _detect_test_cmd(project_path: Path) -> list[str] | None:
    """Autodetecta comando de tests por archivos del proyecto."""
    if (project_path / "pyproject.toml").exists() or (project_path / "pytest.ini").exists():
        return ["pytest", "-q", "--no-header"]
    pkg = project_path / "package.json"
    if pkg.exists():
        try:
            import json
            data = json.loads(pkg.read_text(encoding="utf-8"))
            if "test" in (data.get("scripts") or {}):
                return ["npm", "test", "--silent"]
        except Exception:
            pass
    if (project_path / "Cargo.toml").exists():
        return ["cargo", "test", "--quiet"]
    if (project_path / "go.mod").exists():
        return ["go", "test", "./..."]
    return None


def _resolve_cmd(project: str, project_path: Path) -> list[str] | None:
    override = PROJECT_TEST_CMDS.get(project)
    if override:
        return shlex.split(override)
    return _detect_test_cmd(project_path)


async def run_tests(
    project: str, project_path: Path, timeout: int = 300
) -> tuple[bool, str, str]:
    """Corre tests del proyecto. Devuelve (success, cmd_str, output)."""
    cmd = _resolve_cmd(project, project_path)
    if not cmd:
        return False, "", (
            "No detecté comando de tests. Configura "
            f"PROJECT_{project.upper()}_TEST_CMD en .env."
        )

    env = {**os.environ, "CI": "1", "NO_COLOR": "1"}
    cmd_str = " ".join(cmd)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(project_path),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
    except FileNotFoundError as e:
        return False, cmd_str, f"Binario no encontrado: {e}"

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return False, cmd_str, f"[timeout tras {timeout}s]"

    out = stdout.decode("utf-8", errors="replace").strip()
    # Output puede ser enorme: quedarnos con primeras y últimas líneas.
    lines = out.splitlines()
    if len(lines) > 80:
        out = "\n".join(lines[:30] + ["", "...(truncado)...", ""] + lines[-30:])

    return proc.returncode == 0, cmd_str, out or "(sin output)"
