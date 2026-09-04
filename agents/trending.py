from __future__ import annotations

import logging

import feedparser
import httpx

logger = logging.getLogger(__name__)

# Google News' own "what's hot right now" ranking — free, no API key, no
# registration. Used purely as read-only context for the publish cycle's
# priority re-rank (agents/priority_ranker.py): we only pull the headline
# titles, never ingest/extract/score/publish from this feed itself. This
# is deliberately a separate, much lighter-weight mechanism than the RSS
# sources in the Notion source table. Same role AM1ST's own trending.py
# plays for its own channel (that one settled on the NATION topic section
# over POLITICS, for the equivalent reason below).
#
# Google's WORLD topic section vs. a topic-search query for "China CCP" —
# checked both live 2026-09-04, same methodology AM1ST used to pick its
# own NATION-vs-POLITICS section: WORLD (~30 headlines fetched) was
# overwhelmingly NOT China-relevant — Nepal floods, a German state
# election, Philippine VP politics, Russia/Ukraine, Iran/Kuwait, Denmark
# migration, Sudan's war, Israel/Lebanon — with exactly one item (a
# Pacific-leaders-summit story mentioning a Chinese missile launch) that
# actually touched this channel's own theme. The "China CCP" search-query
# feed instead came back essentially 100% on-theme: CCP influence
# operations in US institutions, Xi Jinping profiles, a Politburo meeting
# analysis, Tibet information control, forced-labor allegations, a new
# CCP loyalty-test law for lawyers — exactly the kind of headline this
# channel's own priority-ranker needs to judge "is this candidate part of
# what's actively trending in CCP-exposure coverage right now." The
# search-query feed wins decisively; a broad topic section (WORLD, or any
# other) doesn't concentrate China/CCP coverage the way a direct query
# does, unlike AM1ST's NATION section, which already was near-exclusively
# US-domestic without needing a search query at all.
TRENDING_FEED_URL = "https://news.google.com/rss/search?q=China+CCP&hl=en-US&gl=US&ceid=US:en"


async def fetch_trending_headlines(limit: int = 15) -> list[str]:
    """Returns up to `limit` current top headline titles from Google News'
    "China CCP" search-query feed, or an empty list if the request fails —
    this is a context signal, not a required dependency, so a failure here
    should never block the publish cycle."""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(TRENDING_FEED_URL)
            resp.raise_for_status()
    except Exception:
        logger.exception("fetch_trending_headlines: request failed")
        return []

    parsed = feedparser.parse(resp.content)
    return [entry.get("title", "") for entry in parsed.entries[:limit] if entry.get("title")]
