"""Thin client for the shared headless-Chromium render service — one
Chromium process on a VM shared across all news bots that need a real
browser render, instead of each bot launching its own Playwright instance
per call. Ported unchanged from AM1ST. See that service's own docstring
(deployed separately, not part of this repo — cross-project shared
infra) for the concurrency/scheduling rationale.

Depends on that shared VM-side render service actually being reachable at
http://127.0.0.1:8811 — this project (China Breaks) is NOT deployed to
any VM as part of this build, so this client has no real render service
to talk to yet; every call fails open (returns None) exactly like a real
network failure would, so the rest of the pipeline degrades gracefully
rather than breaking. Wire this bot up to the same shared render service
AM1ST already uses (or a China Breaks-specific one) when it is actually
deployed.

Both agents/extractor.py's paywall/bot-detection fallback and agents/
rss_fetcher.py's Playwright-fallback path for a handful of RSS feeds that
403 under a plain httpx fetch go through this client — neither imports
playwright directly."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_RENDER_SERVICE_URL = "http://127.0.0.1:8811"


async def render(
    url: str,
    mode: str = "rendered",
    wait_ms: int = 2500,
    timeout_ms: int = 20000,
    cookie: str | None = None,
    extra_headers: dict | None = None,
) -> tuple[int | None, str] | None:
    """Returns (http_status, content) on success, None on any failure
    (service unreachable, timeout, render error) — fail open, same
    convention as every other best-effort network call in this codebase.
    `mode="raw"` returns the actual HTTP response body (for RSS/XML feeds
    — Chromium's own rendered page.content() wraps raw XML in an HTML
    viewer shell, not what feed parsing wants); `mode="rendered"` (default)
    returns the DOM after wait_ms, for real HTML pages needing JS to
    finish (paywall/bot-detection challenges resolving client-side)."""
    payload = {
        "url": url,
        "mode": mode,
        "wait_ms": wait_ms,
        "timeout_ms": timeout_ms,
    }
    if cookie:
        payload["cookie"] = cookie
    if extra_headers:
        payload["extra_headers"] = extra_headers

    try:
        async with httpx.AsyncClient(timeout=(timeout_ms / 1000) + 5) as client:
            resp = await client.post(f"{_RENDER_SERVICE_URL}/render", json=payload)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                logger.info("render_client: render service reported an error for %s: %s", url, data["error"])
                return None
            return data.get("status"), data.get("content", "")
    except Exception as e:
        logger.info("render_client: failed to reach render service for %s (%s)", url, e)
        return None
