# -*- coding: utf-8 -*-
"""The 1:1 headline card posted in place of an article link.

Three of this channel's sources are published as a card rather than as a link
(see _CARD_SOURCE_PREFIXES below): Google News, SCMP and ZeroHedge. Caidan,
2026-09-21. Google News is the clearest case and by volume the biggest one --
its RSS items are news.google.com/rss/articles/CBMi... redirect URLs, which
agents/og_metadata.py cannot read a preview out of, so every one of those posts
went out as a wall of base64 with no card at all (26 of the last 100 candidates
were this source; see the 2026-09-20 publish log).

The drawing is ported from DailyNews' own headline card (VM-01,
/home/caidan/DailyNews/agents/headline_card.py, redrawn there 2026-09-20),
which is already 1080x1080 and already all-English. Two rules carry it and both
are kept verbatim:

**The type fills its box.** _fit climbs until the block fits both the width and
the height it has to live in, so a short headline is set large and a long one
small, and neither leaves a hole.

**The colour comes from the category.** Six categories, six accents, matched on
the headline's own words. The category list and its ordering are DailyNews'
(measured there over 147 real posts); it is a China/US-desk list to begin with,
so it transfers, but the distribution has not been re-measured on this
channel's own output.

Everything on the card is English -- the headline is the generated post copy,
which this channel writes in English, and the chrome follows it.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

W, H = 1080, 1080
PAPER = (250, 249, 246)
INK = (20, 20, 22)
RULE = (24, 24, 26)
HAIRLINE = (200, 196, 190)
MUTED = (110, 108, 104)
MARGIN = 88
MASTHEAD = "CHINA BREAKS"
MAX_LINES = 6

# Category -> accent. Order matters: the first list to match wins, so a story
# about a Chinese missile test is SECURITY rather than CHINA. That ordering is
# an editorial call and lives here, in one place, to be changed in one place.
# Without it CHINA would swallow nearly everything this channel publishes --
# "china" is in almost every headline it writes.
_CATEGORIES: list[tuple[tuple[str, ...], str, tuple[int, int, int]]] = [
    (("taiwan", "missile", "navy", "defense", "military", "weapon", "strike", "troops",
      "warship", "nuclear", "army", "air force", "pentagon", "frigate", "drone"),
     "SECURITY", (198, 118, 26)),
    (("chip", "semiconductor", "ai ", "artificial intelligence", "tech", "6g", "5g",
      "huawei", "quantum", "robot", "software", "satellite"),
     "TECHNOLOGY", (74, 78, 158)),
    (("econom", "trade", "tariff", "gdp", "market", "stock", "oil", "export", "import",
      "yuan", "inflation", "investment", "currency", "bank"),
     "ECONOMY", (22, 94, 90)),
    (("china", "chinese", "beijing", "ccp", "xi jinping", "communist china", "pla "),
     "CHINA", (180, 35, 46)),
    (("trump", "washington", "white house", "congress", "u.s.", "united states", "american"),
     "UNITED STATES", (27, 58, 107)),
]
_DEFAULT_CATEGORY = ("WORLD", (90, 92, 98))

_FONTS = {
    "serif_bold": [("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc", 2),
                   ("/System/Library/Fonts/Supplemental/Songti.ttc", 0)],
    "serif": [("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", 2),
              ("/System/Library/Fonts/Supplemental/Songti.ttc", 0)],
    "sans_bold": [("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 2),
                  ("/System/Library/AssetsV2/com_apple_MobileAsset_Font8/"
                   "4a418d1fa4860652a3241e8ee457806c8557fc64.asset/AssetData/Yuanti.ttc", 2)],
    "sans": [("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 2),
             ("/System/Library/AssetsV2/com_apple_MobileAsset_Font8/"
              "4a418d1fa4860652a3241e8ee457806c8557fc64.asset/AssetData/Yuanti.ttc", 2)],
}


def _resolve(kind: str) -> tuple[str, int]:
    for path, index in _FONTS[kind]:
        if Path(path).exists():
            return path, index
    raise RuntimeError(
        f"No font found for {kind!r} in the headline card. On Linux: "
        "sudo apt-get install fonts-noto-cjk fonts-noto-cjk-extra"
    )


_RESOLVED = {kind: _resolve(kind) for kind in _FONTS}


def _font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    path, index = _RESOLVED[kind]
    return ImageFont.truetype(path, size, index=index)


# The CJK faces draw U+2019 in a full-width cell, about three times an ASCII
# apostrophe, which reads as a hole in a Latin headline.
_TYPOGRAPHIC_PUNCTUATION = {"’": "'", "‘": "'", "“": '"', "”": '"'}


def _normalize_punctuation(text: str) -> str:
    for typographic, ascii_equiv in _TYPOGRAPHIC_PUNCTUATION.items():
        text = text.replace(typographic, ascii_equiv)
    return text


def category_of(title: str) -> tuple[str, tuple[int, int, int]]:
    low = " " + title.lower()
    for keys, label, colour in _CATEGORIES:
        if any(k in low for k in keys):
            return label, colour
    return _DEFAULT_CATEGORY


def _wrap(draw: ImageDraw.ImageDraw, text: str, f, max_width: int) -> list[str]:
    """Word-wrap Latin, character-wrap CJK. Character-only wrapping splits
    English mid-word ("In do-Pacific")."""
    text = _normalize_punctuation(text)
    if " " in text:
        lines, current = [], ""
        for word in text.split(" "):
            trial = f"{current} {word}".strip()
            if draw.textlength(trial, font=f) > max_width and current:
                lines.append(current)
                current = word
            else:
                current = trial
        if current:
            lines.append(current)
        return lines
    lines, current = [], ""
    for ch in text:
        if draw.textlength(current + ch, font=f) > max_width and current:
            lines.append(current)
            current = ch
        else:
            current += ch
    if current:
        lines.append(current)
    return lines


# How much of the text box the headline may actually occupy, and how large it
# may be set. Filling the box read as a wall of type on DailyNews: a six-line
# headline came out at 87px using 678 of 694 available pixels, edge to edge
# with nothing to breathe. A long headline is bounded by the height, a short
# one by the ceiling, so both numbers are needed -- one alone moves only half
# the cases.
_BOX_FILL = 0.80
_MAX_SIZE = 120


def _fit(draw: ImageDraw.ImageDraw, text: str, max_width: int, max_height: int,
         lead: float = 1.30, max_size: int = _MAX_SIZE, min_size: int = 40):
    """Largest size whose block fits both the width and the height it has.

    Sizing on width alone is what stranded a four-word headline in the middle of
    the canvas: it fit at 76px and never grew.
    """
    size = max_size
    while size >= min_size:
        f = _font("serif_bold", size)
        lines = _wrap(draw, text, f, max_width)
        if len(lines) <= MAX_LINES and round(size * lead) * len(lines) <= max_height:
            return lines, f
        size -= 3

    f = _font("serif_bold", min_size)
    lines = _wrap(draw, text, f, max_width)
    kept = lines[:MAX_LINES]
    if kept:
        last = kept[-1]
        kept[-1] = (last.rsplit(" ", 1)[0] if " " in last else last) + "…"
    logger.warning("Headline card: title too long even at %dpx, trimmed to %d lines: %r",
                   min_size, MAX_LINES, text[:80])
    return kept, f


def make_plain_card(title: str, out_path: str, attribution: str = "") -> None:
    """The card. A broadsheet front page: masthead, rule, a category kicker in
    that category's colour, the headline set in a serif, and the source under a
    hairline."""
    label, accent = category_of(title)
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, W, 10], fill=RULE)
    d.text((MARGIN, 62), MASTHEAD, font=_font("serif_bold", 40), fill=RULE)

    date_text = datetime.now(timezone.utc).strftime("%B %-d, %Y")
    date_font = _font("sans", 26)
    d.text((W - MARGIN - d.textlength(date_text, font=date_font), 74),
           date_text, font=date_font, fill=MUTED)
    d.line([MARGIN, 132, W - MARGIN, 132], fill=RULE, width=3)

    d.rectangle([MARGIN, 168, MARGIN + 10, 200], fill=accent)
    d.text((MARGIN + 26, 166), label, font=_font("sans_bold", 28), fill=accent)

    top, bottom = 214, H - 132
    lines, f = _fit(d, title, W - MARGIN * 2, round((bottom - top - 40) * _BOX_FILL))
    line_h = round(f.size * 1.30)
    y = top + (bottom - top - line_h * len(lines)) // 2
    for line in lines:
        d.text((MARGIN, y), line, font=f, fill=INK)
        y += line_h

    d.line([MARGIN, H - 132, W - MARGIN, H - 132], fill=HAIRLINE, width=2)
    if attribution:
        d.text((MARGIN, H - 106), attribution, font=_font("serif", 26), fill=MUTED)
    d.rectangle([0, H - 10, W, H], fill=accent)
    img.save(out_path)


# ---------------------------------------------------------------------------
# Which posts get a card, and what the card says under the hairline.
# ---------------------------------------------------------------------------

# Matched against a candidate's `author` -- core/notion_candidates.py writes
# the RSS source's Notion Name into that column, so these are the source
# table's own names, lowercased and matched as prefixes. "scmp" covers the
# thirteen separate SCMP section feeds ("scmp china politics", "scmp business",
# ...) without listing each one; a new SCMP section feed is picked up for free.
_CARD_SOURCE_PREFIXES = ("google news", "scmp", "zero hedge", "zerohedge")

# The same three sources by article domain, as a backstop for a renamed or
# newly-added feed row. Checked against the registrable tail of the host, so
# "www.scmp.com" and "cms.zerohedge.com" both match.
_CARD_DOMAINS = ("news.google.com", "scmp.com", "zerohedge.com")

# What goes under the hairline. Keyed by the same source-name prefix.
_SOURCE_DISPLAY_NAME = {
    "scmp": "South China Morning Post",
    "zero hedge": "ZeroHedge",
    "zerohedge": "ZeroHedge",
    "google news": "Google News",
}
_DOMAIN_DISPLAY_NAME = {
    "scmp.com": "South China Morning Post",
    "zerohedge.com": "ZeroHedge",
    "news.google.com": "Google News",
}


def _host_matches(url: str, domain: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == domain or host.endswith("." + domain)


def uses_headline_card(author: str, url: str) -> bool:
    """True when this candidate is posted as a card instead of as a link."""
    name = (author or "").strip().lower()
    if any(name.startswith(p) for p in _CARD_SOURCE_PREFIXES):
        return True
    return any(_host_matches(url or "", d) for d in _CARD_DOMAINS)


# A Google News RSS title is "Headline - Publisher", so the real outlet is
# recoverable and is what belongs on the card -- "Google News" is an aggregator,
# not a source. Guarded on both ends: the tail must be short and must not read
# like the rest of a sentence, or a headline that simply contains a dash
# ("Trump - in Beijing this week - said") would be mistaken for an attribution.
_GOOGLE_NEWS_TAIL = re.compile(r"\s+-\s+([^-]{2,40})$")


def _google_news_publisher(title: str) -> str:
    m = _GOOGLE_NEWS_TAIL.search((title or "").strip())
    if not m:
        return ""
    tail = m.group(1).strip()
    return "" if any(c in tail for c in ".!?;:") else tail


def card_attribution(author: str, url: str, title: str) -> str:
    """The "(Source: ...)" line, or "" when the source can't be named."""
    name = (author or "").strip().lower()
    display = ""
    for prefix, label in _SOURCE_DISPLAY_NAME.items():
        if name.startswith(prefix):
            display = label
            break
    if not display:
        for domain, label in _DOMAIN_DISPLAY_NAME.items():
            if _host_matches(url or "", domain):
                display = label
                break
    if display == "Google News":
        display = _google_news_publisher(title) or display
    return f"(Source: {display})" if display else ""


# A sentence end, not an abbreviation. Two guards, both needed: the next word
# must start with a capital (rules out "U.S. export"), and the character before
# the period must not itself be a capital (rules out "U.S. Congress", where the
# next word IS capitalised). Without them the card headline for a chip-export
# story came back cut at "sweeping U.S".
_CARD_SENTENCE_END = re.compile(r'(?<![A-Z]\.)(?<=[.!?])\s+(?=[A-Z"“])')


def card_headline(post_content: str, fallback_title: str = "") -> str:
    """The first sentence of the post, or the title if there isn't one.

    The post copy, not the source title: it is already English, already in this
    channel's voice, and already the thing being published. A Google News title
    still carries its " - Publisher" tail, and an SCMP one is written for
    SCMP's readers, not this channel's.
    """
    text = (post_content or "").strip()
    if not text:
        # Only reachable if the Writer ever returns empty, which run_cycle
        # already drops -- but a Google News title carries a " - Publisher"
        # tail that must not end up set as the headline.
        title = (fallback_title or "").strip()
        tail = _google_news_publisher(title)
        return title[: -(len(tail) + 3)].strip() if tail else title
    first = _CARD_SENTENCE_END.split(text, 1)[0].strip()
    # A single very long sentence reads better trimmed at a clause, and a trim
    # must never land inside a word. rsplit returns the whole string when the
    # separator is absent, so the comma branch has to check the comma is
    # actually there -- without that, a long comma-less headline came back cut
    # at "tooling t".
    #
    # A trim gets an ellipsis. DailyNews' version does not, and its cards read
    # as though the sentence simply stopped: real China Breaks copy is longer
    # than DailyNews' and five of the first eight cards rendered from live
    # posts ended on a dangling "...amid heightened" / "...a move reflecting"
    # (2026-09-21). The marker is the difference between a trimmed headline
    # and a broken one.
    if len(first) > 160:
        head = first[:160]
        at_comma = head.rsplit(",", 1)[0] if "," in head else ""
        first = (at_comma if len(at_comma) > 80 else head.rsplit(" ", 1)[0]).rstrip(" ,.;:") + "\u2026"
    if not first.endswith("\u2026"):
        first = first.rstrip(" ,.")
    return first or text[:160]
