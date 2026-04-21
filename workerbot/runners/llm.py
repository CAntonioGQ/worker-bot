from dataclasses import dataclass

import httpx

from workerbot.config import AIDER_MODEL, OPENROUTER_API_KEY

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


@dataclass
class LLMResult:
    output: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


def _strip_openrouter_prefix(model_id: str) -> str:
    # Aider usa "openrouter/deepseek/deepseek-v3.2"; el API de OpenRouter
    # espera "deepseek/deepseek-v3.2" (sin el prefijo de proveedor).
    return model_id.removeprefix("openrouter/")


async def complete(
    prompt: str,
    model: str | None = None,
    timeout: int = 90,
    system: str | None = None,
) -> LLMResult:
    """Call OpenRouter chat completion directly. For flows that don't need
    Aider (no repo modification, just a Q&A or summarization)."""
    model_id = _strip_openrouter_prefix(model or AIDER_MODEL)

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/CAntonioGQ/worker-bot",
                "X-Title": "worker-bot",
            },
            json={"model": model_id, "messages": messages},
        )
        r.raise_for_status()
        data = r.json()

    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage") or {}
    return LLMResult(
        output=content,
        tokens_in=usage.get("prompt_tokens", 0) or 0,
        tokens_out=usage.get("completion_tokens", 0) or 0,
        cost_usd=float(usage.get("cost", 0.0) or 0.0),
    )
