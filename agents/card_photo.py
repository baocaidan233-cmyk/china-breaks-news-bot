"""Gets the article's own photo for the split card, or None.

Measured on real candidates, 2026-09-21 — this is what decides which card shape
agents/headline_card.py draws:

  SCMP        20/20 usable, always 1200x630
  ZeroHedge    6/6  usable
  Google News  7/8 once resolved. Its own og:image is a 300x300 Google
               placeholder, so the redirect has to be followed first — see
               _resolve_via_render() below. Two earlier measurements said 0/20
               and 0/6; both were taken after a burst of eight back-to-back
               requests had put this VM's IP into a Google 429. Re-measured at
               the pace production actually runs at (one publish per ~20 min),
               it resolved 7 of 8 with no 429 at all: Baird Maritime, Focus
               Taiwan, Yahoo, NK News, China Daily HK, Semafor, KED Global.

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

# Domains that republish other outlets' copy under their own shell. Their
# og:image is frequently a house stock photo rather than the story's, and a
# wrong photo is worse than no photo: the first end-to-end Google News render
# put an empty boardroom next to "Beijing has stepped up naval activity off
# Alaska", resolved through Yahoo. A real publisher's own og:image is the
# story's photo — the FT article resolved in the same pass returned rare earth
# ore at a Chinese port for a rare earth story. So this excludes the
# republishers, not the feature.
_SYNDICATORS = ("yahoo.com", "yahoo.co.jp", "msn.com", "news.google.com",
                "flipboard.com", "smartnews.com", "buzzing.cc")


def _is_syndicator(url: str) -> bool:
    low = (url or "").lower()
    return any(d in low for d in _SYNDICATORS)


_OG_IMAGE = re.compile(
    r"""<meta[^>]+(?:property|name)=["']og:image["'][^>]*content=["']([^"']+)""", re.I)
_OG_IMAGE_REV = re.compile(
    r"""<meta[^>]+content=["']([^"']+)["'][^>]*(?:property|name)=["']og:image["']""", re.I)
_CANONICAL = re.compile(
    r"""<link[^>]+rel=["']canonical["'][^>]*href=["']([^"']+)""", re.I)


async def _resolve_via_render(google_url: str) -> tuple[str, str]:
    """Follows a news.google.com redirect in the shared headless browser and
    reads the destination article's og:image out of the SAME rendered page.

    Reading it from the rendered page rather than re-fetching the resolved URL
    is deliberate: a plain httpx fetch of focustaiwan.tw came back with no
    og:image at all while the rendered page had one, so a second fetch would
    throw away photos this already has. It also halves the requests.

    Returns (image_url, canonical_url), either of which may be "". Fails open
    on everything — core/render_client.py already returns None rather than
    raising, and a miss here just means the text card.
    """
    from core.render_client import render

    result = await render(google_url, mode="rendered", wait_ms=6000, timeout_ms=35000)
    if not result:
        logger.info("card_photo: render service could not resolve %s", google_url[:90])
        return "", ""
    _status, html = result
    m = _OG_IMAGE.search(html) or _OG_IMAGE_REV.search(html)
    c = _CANONICAL.search(html)
    image = m.group(1).strip() if m else ""
    canonical = c.group(1).strip() if c else ""
    if image and "google" in image.lower():
        # Still on a Google page — the redirect did not complete.
        image = ""
    if image and _is_syndicator(canonical):
        logger.info("card_photo: %s is a republisher, not using its photo", canonical[:70])
        image = ""
    logger.info("card_photo: google news %s -> %s (img %s)", google_url[:60],
                canonical[:70] or "unresolved", "yes" if image else "no")
    return image, canonical


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
    if "news.google.com" in (article_url or ""):
        # The og:image the caller has is Google's placeholder; the real one is
        # behind the redirect. The resolved URL also decides the watermark
        # crop, since a Google News item can land on SCMP.
        image_url, resolved = await _resolve_via_render(article_url)
        article_url = resolved or article_url

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
