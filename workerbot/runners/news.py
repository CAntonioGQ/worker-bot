import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser
import httpx

log = logging.getLogger("worker-bot.runners.news")

FEEDS: dict[str, str] = {
    "HN AI": (
        "https://hnrss.org/newest?q=AI+OR+LLM+OR+GPT+OR+Claude+OR+Anthropic+OR+OpenAI&count=20"
    ),
    "ArXiv cs.AI": "http://export.arxiv.org/rss/cs.AI",
    "Simon Willison": "https://simonwillison.net/atom/everything/",
    "The Decoder": "https://the-decoder.com/feed/",
    "Latent Space": "https://www.latent.space/feed",
}

MAX_PER_FEED = 8
HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class NewsItem:
    source: str
    title: str
    link: str
    summary: str
    published: datetime | None


def _strip_html(text: str) -> str:
    clean = HTML_TAG_RE.sub(" ", text or "")
    return WHITESPACE_RE.sub(" ", clean).strip()


def _entry_published(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        tup = getattr(entry, attr, None)
        if tup:
            try:
                return datetime(*tup[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def parse_feed(source: str, raw: str, max_items: int = MAX_PER_FEED) -> list[NewsItem]:
    """Parsea un documento RSS/Atom en una lista de NewsItem. Pura, testeable."""
    feed = feedparser.parse(raw)
    items: list[NewsItem] = []
    for entry in feed.entries[:max_items]:
        title = _strip_html(getattr(entry, "title", "")) or "(sin título)"
        link = getattr(entry, "link", "") or ""
        summary = _strip_html(getattr(entry, "summary", "") or "")[:400]
        items.append(
            NewsItem(
                source=source,
                title=title,
                link=link,
                summary=summary,
                published=_entry_published(entry),
            )
        )
    return items


async def _fetch_one(
    client: httpx.AsyncClient, source: str, url: str
) -> list[NewsItem]:
    try:
        r = await client.get(url, timeout=15, follow_redirects=True)
        r.raise_for_status()
    except Exception as e:
        log.warning("feed %s falló: %s", source, e)
        return []
    return parse_feed(source, r.text)


async def fetch_all(since: datetime | None = None) -> list[NewsItem]:
    """Fetch en paralelo de los feeds configurados. Opcionalmente filtra por fecha."""
    async with httpx.AsyncClient(
        headers={"User-Agent": "worker-bot/1.0 (+rss-digest)"}
    ) as client:
        tasks = [_fetch_one(client, name, url) for name, url in FEEDS.items()]
        results = await asyncio.gather(*tasks)
    all_items = [item for batch in results for item in batch]
    if since is not None:
        all_items = [
            i for i in all_items
            if i.published is None or i.published >= since
        ]
    return all_items


def format_for_prompt(items: list[NewsItem], max_chars: int = 12_000) -> str:
    """Convierte la lista en texto compacto para inyectar al LLM."""
    lines: list[str] = []
    total = 0
    for it in items:
        date = it.published.strftime("%Y-%m-%d") if it.published else "?"
        chunk = (
            f"[{it.source} · {date}] {it.title}\n"
            f"  {it.link}\n"
            f"  {it.summary[:220]}"
        )
        total += len(chunk) + 2
        if total > max_chars:
            break
        lines.append(chunk)
    return "\n\n".join(lines)
