from datetime import datetime, timezone

from workerbot.core.scheduler import _parse_news_directive
from workerbot.runners.news import NewsItem, format_for_prompt, parse_feed

_SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Sample</title>
    <item>
      <title>GPT-6 released with 10M context</title>
      <link>https://example.com/gpt6</link>
      <description>&lt;p&gt;OpenAI dropped GPT-6 today with big gains.&lt;/p&gt;</description>
      <pubDate>Mon, 20 Apr 2026 10:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Anthropic Claude 5 leaked internal memo</title>
      <link>https://example.com/c5</link>
      <description>Next model incoming.</description>
      <pubDate>Sun, 19 Apr 2026 15:30:00 GMT</pubDate>
    </item>
  </channel>
</rss>"""


def test_parse_feed_returns_items():
    items = parse_feed("TestFeed", _SAMPLE_RSS)
    assert len(items) == 2
    assert items[0].source == "TestFeed"
    assert items[0].title == "GPT-6 released with 10M context"
    assert items[0].link == "https://example.com/gpt6"
    assert items[0].published is not None
    assert items[0].published.year == 2026


def test_parse_feed_strips_html_from_summary():
    items = parse_feed("TestFeed", _SAMPLE_RSS)
    assert "<p>" not in items[0].summary
    assert "OpenAI dropped GPT-6" in items[0].summary


def test_parse_feed_respects_max_items():
    items = parse_feed("TestFeed", _SAMPLE_RSS, max_items=1)
    assert len(items) == 1


def test_parse_feed_handles_missing_fields():
    broken = "<rss><channel><item></item></channel></rss>"
    items = parse_feed("Broken", broken)
    # Uno o cero items según cómo feedparser interprete; el punto es no romper.
    for it in items:
        assert it.source == "Broken"
        assert it.title  # nunca vacío (se cae a "(sin título)")


def test_format_for_prompt_includes_source_title_and_link():
    items = [
        NewsItem(
            source="HN",
            title="Nuevo modelo X",
            link="https://h.com/1",
            summary="Descripción breve.",
            published=datetime(2026, 4, 20, tzinfo=timezone.utc),
        ),
    ]
    text = format_for_prompt(items)
    assert "HN" in text
    assert "Nuevo modelo X" in text
    assert "https://h.com/1" in text
    assert "2026-04-20" in text


def test_format_for_prompt_respects_char_budget():
    items = [
        NewsItem(
            source=f"S{i}",
            title=f"title {i}" + ("x" * 500),
            link=f"https://e/{i}",
            summary="sum" * 100,
            published=None,
        )
        for i in range(10)
    ]
    text = format_for_prompt(items, max_chars=500)
    assert len(text) <= 1200  # holgura porque cortamos después del último chunk


def test_format_for_prompt_empty():
    assert format_for_prompt([]) == ""


def test_parse_news_directive_default_is_day():
    days, extra = _parse_news_directive("@news")
    assert days == 1
    assert extra == ""


def test_parse_news_directive_week():
    days, extra = _parse_news_directive("@news week")
    assert days == 7
    assert extra == ""


def test_parse_news_directive_day_explicit():
    days, extra = _parse_news_directive("@news day")
    assert days == 1


def test_parse_news_directive_preserves_extra():
    days, extra = _parse_news_directive("@news week enfócate en modelos open-source")
    assert days == 7
    assert "open-source" in extra


def test_parse_news_directive_ignores_unknown_mode():
    days, extra = _parse_news_directive("@news foo bar")
    # no es day/week → se queda en default 1 y el tail completo va a extra
    assert days == 1
    assert extra == "foo bar"
