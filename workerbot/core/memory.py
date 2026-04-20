from workerbot.storage.suggestions import recent_runs_for_cron


def memory_block(cron_id: int, limit: int = 3) -> str:
    """Texto para inyectar en el prompt: últimas ejecuciones de este cron."""
    rows = recent_runs_for_cron(cron_id, limit)
    if not rows:
        return ""
    lines = ["\n\n--- Historial reciente de este cron (no repetir sugerencias) ---"]
    for r in rows:
        summary = (r["summary"] or "").strip()
        if summary:
            date = r["ran_at"][:10]
            lines.append(f"[{date}] {summary[:300]}")
    return "\n".join(lines) if len(lines) > 1 else ""


def summarize(output: str | None, max_chars: int = 220) -> str:
    """Toma las primeras líneas significativas del output de Aider como resumen."""
    text = (output or "").strip()
    if not text:
        return "(sin respuesta)"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    summary = " · ".join(lines[:3])
    if len(summary) > max_chars:
        summary = summary[: max_chars - 1].rstrip() + "…"
    return summary
