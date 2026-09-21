"""Decides whether a link post's Gettr preview card would actually render.

Four real posts the editors flagged on 2026-09-21 showed as a bare URL with no
card at all, each for a different reason — a source whitelist would never have
caught them, so this judges the preview itself:

  infobrics.org   prev_img was a 1x1 Yandex Metrica tracking pixel, and the
                  title was the site name ("BRICS portal") because the scrape
                  picked up site-level OG tags rather than the article's.
  allafrica.com   prev_img was the outlet's own 664x664 logo. Big enough to
                  clear any size gate, so only the filename gives it away.
  kommersant.ru   prev_img answers 200 with something that is not an image
                  (hotlink protection), and its title and description are
                  Russian on an English-language channel.

Measured over the last 31 real link posts: 6 broken, 19%. Those now get a card
instead, on top of the three sources that are carded by editorial rule (see
agents/headline_card.py's uses_headline_card).

The thresholds here are deliberately looser than agents/card_photo.py's. This
answers "would Gettr draw something acceptable", which a 400x400 photo passes;
that one answers "is this good enough to be the photo on our own card", which
it does not.
"""

from __future__ import annotations

import io
import logging
import re

import httpx

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Matched against the image's own filename, not the whole URL: a path segment
# like /static/images/ is normal, a file called aa-logo-...-square.png is not.
_LOGO_HINT = re.compile(
    r"(logo|placeholder|default|fallback|avatar|icon|sprite|share[-_]?image|"
    r"og[-_]?default|no[-_]?image|social[-_]?card)", re.I)

_TRACKER = ("mc.yandex.ru", "google-analytics.com", "googletagmanager.com",
            "facebook.com/tr", "/pixel", "matomo", "piwik", "scorecardresearch")

_LATIN = re.compile(r"[A-Za-z]")
# Cyrillic, CJK, Hangul, Arabic. agents/og_metadata.py's own guard covers CJK
# only, which is why four Russian-titled previews went out.
_NON_LATIN = re.compile(r"[Ѐ-ӿ一-鿿぀-ヿ가-힯؀-ۿ]")

_MIN_PREVIEW_SIDE = 200


def title_not_english(text: str) -> bool:
    """True when the preview's own title is mostly not in Latin script. This
    channel publishes in English; a Russian headline on the card is wrong even
    when the card renders perfectly."""
    latin = len(_LATIN.findall(text or ""))
    other = len(_NON_LATIN.findall(text or ""))
    return other > 0 and other >= latin


async def preview_fault(og: dict) -> str:
    """Returns a short reason the preview is unusable, or "" if it is fine.
    Any network error is treated as "fine" rather than as a fault — this must
    never turn a working link post into a card because of a blip."""
    if title_not_english(og.get("prev_ttl") or ""):
        return "title not English"

    img = (og.get("prev_img") or "").strip()
    if not img:
        return "no preview image"
    low = img.lower()
    if any(t in low for t in _TRACKER):
        return "tracking pixel"
    if _LOGO_HINT.search(low.rsplit("/", 1)[-1].split("?")[0]):
        return "source logo"

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                     headers={"User-Agent": _USER_AGENT}) as client:
            resp = await client.get(img)
            resp.raise_for_status()
            content = resp.content
    except Exception as e:
        logger.info("preview_gate: could not check %s (%s) — treating the preview as fine",
                    img[:90], type(e).__name__)
        return ""

    try:
        from PIL import Image
        with Image.open(io.BytesIO(content)) as im:
            w, h = im.size
    except Exception:
        return "preview image is not an image"

    if min(w, h) < _MIN_PREVIEW_SIDE:
        return f"preview image too small ({w}x{h})"
    return ""
