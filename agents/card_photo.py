"""Gets the article's own photo for the split card, or None.

Measured on real candidates, 2026-09-21 — this is what decides which card shape
agents/headline_card.py draws:

  SCMP        20/20 usable, always 1200x630
  ZeroHedge    6/6  usable
  Google News  0/20 — its og:image is a 300x300 Google placeholder, rejected by
               the size gate below, so Google News always gets the text card.
               (Resolving the news.google.com redirect to the real article is
               possible in principle through the shared render service, but the
               attempt rate-limited to 429 against Google within eight requests
               and the self-hosted resolver DailyNews uses was returning 400 on
               every call. Not pursued; the text card is the answer for now.)

Two source-specific rules, both measured rather than assumed:

**SCMP burns its own logo into every og:image**, bottom-left, consistently
about the bottom fifth of the frame. Posting that would put another outlet's
branding on this channel's card, so the bottom of the frame is cropped away
before use (editors' call, 2026-09-21). Nothing else about the photo changes.

**ZeroHedge serves a downscaled derivative by default.** Its og:image URL is a
Drupal image style — .../styles/16_9_max_700/public/... at 700x394, which is too
small for a 1080-wide band. Dropping that path segment returns the original
(1200x800 and 951x557 on the two measured). The derivative is kept as a
fallback in case the original 404s.
"""

from __future__ import annotations

import io
import logging
import re

import httpx

logger = logging.getLogger(__name__)

# DailyNews' own gates, inherited: a frame smaller than this is a logo, an
# avatar or a placeholder, not a news photo. The 300x300 Google News
# placeholder (90,000 px) fails the area test.
MIN_AREA = 200_000
MIN_SHORT_SIDE = 300

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# domain -> fraction of the frame's height to cut off the bottom.
_WATERMARK_CROP = {"scmp.com": 0.22}

_ZH_DERIVATIVE = re.compile(r"/styles/[^/]+/public/")


def _upgrade(url: str) -> list[str]:
    """Candidate URLs to try, best first."""
    if "zerohedge.com" in url and _ZH_DERIVATIVE.search(url):
        return [_ZH_DERIVATIVE.sub("/", url), url]
    return [url]


def _watermark_fraction(article_url: str) -> float:
    low = (article_url or "").lower()
    for domain, frac in _WATERMARK_CROP.items():
        if domain in low:
            return frac
    return 0.0


async def fetch_card_photo(image_url: str, article_url: str) -> bytes | None:
    """Downloads the article's og:image and returns PNG-ready JPEG bytes, or
    None if there is no usable photo — fail open, same as every other
    best-effort network call here. None means the text card."""
    if not image_url or not image_url.startswith("http"):
        return None

    from PIL import Image

    crop = _watermark_fraction(article_url)
    async with httpx.AsyncClient(timeout=25, follow_redirects=True,
                                 headers={"User-Agent": _USER_AGENT}) as client:
        for candidate in _upgrade(image_url):
            try:
                resp = await client.get(candidate)
                resp.raise_for_status()
                img = Image.open(io.BytesIO(resp.content))
                img.load()
            except Exception as e:
                logger.info("card_photo: %s not usable (%s)", candidate[:90], type(e).__name__)
                continue

            if crop:
                img = img.crop((0, 0, img.width, int(img.height * (1 - crop))))

            w, h = img.size
            if w * h < MIN_AREA or min(w, h) < MIN_SHORT_SIDE:
                logger.info("card_photo: %dx%d too small after crop, no photo card for %s",
                            w, h, article_url[:90])
                continue

            out = io.BytesIO()
            img.convert("RGB").save(out, format="JPEG", quality=92)
            logger.info("card_photo: %dx%d usable%s for %s", w, h,
                        f" (bottom {crop:.0%} cropped)" if crop else "", article_url[:90])
            return out.getvalue()
    return None
