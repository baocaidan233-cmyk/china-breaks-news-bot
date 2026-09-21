# -*- coding: utf-8 -*-
"""The 1:1 card posted in place of an article link.

Three sources are published as a card rather than as a link (see
_CARD_SOURCE_PREFIXES): Google News, SCMP and ZeroHedge. Google News is the
biggest by volume -- 83% of card posts -- because its RSS items are
news.google.com/rss/articles/CBMi... redirect URLs that agents/og_metadata.py
cannot read a preview out of, so those posts used to go out as a wall of base64
with no card at all.

There are two card shapes and which one is drawn depends on one thing only:
whether agents/card_photo.py could get a real photo for this article.

  make_split_card  -- headline on a white ground, the article's own photo in a
                      band across the bottom. SCMP and ZeroHedge: both return a
                      usable og:image on every article measured (20/20 and 6/6,
                      2026-09-21).
  make_text_card   -- headline only. Google News: 0/20, its og:image is a
                      300x300 Google placeholder that the size gate rejects.

Both share the masthead band, the category kicker and the accent colour, so the
two shapes read as one channel alternating, not as two channels.

Design rules, all of them from the editors (2026-09-21):

**The type does not fill the card.** An earlier draft sized the headline to fill
whatever box it had and the result read as a wall: "不需要太大的字，全部塞满，
不好看". The headline is capped at _MAX_SIZE and may take at most _MAX_LINES
lines and _BOX_FILL of its box, so there is always deliberate space under it.

**The headline is written, not excerpted.** It used to be the caption's first
sentence, which meant the reader read the same sentence twice -- once on the
card, truncated mid-clause, and again in the post underneath. agents/
card_headline.py writes a real headline and an optional deck instead.

**The deck is conditional.** It is drawn only when the headline left room for it
("副标题看情况"), which in practice means most text cards get one and most
photo cards do not.

Everything on the card is English.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

W = H = 1080
MARGIN = 84
BAND_H = 124
PHOTO_H = 400
# The text card's footer block, where the split card has its photo. Without it
# the two shapes had different proportions and the text card trailed off into
# 300px of nothing with the source line orphaned at the very bottom
# (p43wdfo86dd, 2026-09-21: "文字在上，下面是空的，比例不和谐").
#
# Its height is not fixed: the footer starts a fixed gap under the text and
# runs to the bottom edge, so it ABSORBS whatever slack the headline left
# instead of leaving a hole above itself. A fixed-height footer was tried
# first and just moved the hole up by 268px. The clamps keep it from becoming
# either a sliver or most of the card.
FOOTER_MIN = 220
FOOTER_MAX = 430
FOOTER_GAP = 52          # between the end of the text group and the footer
FOOTER_TEXT_INSET = 46   # source line, down from the footer's top edge

PAPER = (255, 255, 255)
HEAD_INK = (22, 24, 27)
DECK_INK = (85, 89, 95)
SOURCE_INK = (140, 144, 152)
FOOTER_INK = (112, 116, 124)
BAND_INK = (255, 255, 255)


def _tint(accent: tuple[int, int, int], strength: float = 0.09) -> tuple[int, int, int]:
    """The category colour washed into white — the footer reads as part of the
    card's colour scheme without competing with the masthead band."""
    return tuple(round(255 + (c - 255) * strength) for c in accent)

MASTHEAD = "CHINA BREAKS"

# Seven categories, cut down from nine at the editors' request ("可以压到七类,
# 不需要那么多的分类"). The nine were drafted and measured on 200 real published
# posts; the two smallest (THE PARTY 3%, SOCIETY 4%) merged into INSIDE CHINA,
# and INFLUENCE (8.5%) merged into OVERSEAS, which is the same beat seen from
# outside -- what the CCP does beyond its own borders, openly or not.
#
# First match wins, so the order is the editorial call: a PLA drill aimed at
# Taiwan is TAIWAN, not MILITARY.
_CATEGORIES: list[tuple[tuple[str, ...], str, tuple[int, int, int]]] = [
    (("taiwan", "taipei", "taiwanese", "cross-strait", "cross-straits", "kuomintang", "kmt",
      "dpp", "democratic progressive", "mainland affairs council", "william lai", "lai ching"),
     "TAIWAN", (206, 44, 50)),

    (("president trump", "trump administration", "white house", "washington", "state visit",
      "state dinner", "u.s. congress", "u.s. lawmakers", "u.s. senate", "capitol hill",
      "bipartisan", "state department", "secretary of state", "treasury secretary",
      "u.s. trade representative", "ustr", "oval office", "xi-trump", "trump-xi"),
     "WASHINGTON", (72, 86, 196)),

    (("pla ", "military", "navy", "naval", "warship", "vessel", "aircraft carrier", "missile",
      "fighter jet", "stealth", "drills", "war games", "troops", "coast guard", "defense minist",
      "defence", "pentagon", "nuclear", "submarine", "incursion", "no-fly", "armed forces",
      "frigate", "combat", "arms sale", "aukus"),
     "MILITARY", (214, 112, 24)),

    (("purge", "expelled", "corruption", "graft", "discipline inspection", "ccdi", "crackdown",
      "censor", "politburo", "plenary session", "party congress", "judicial", "imprisoned",
      "human rights", "dissident", "repress", "detention", "re-education", "anti-corruption",
      "education", "school", "student", "teenager", "food safety", "hospital", "public health",
      "netizen", "social media", "residents", "villag", "birth rate", "elderly", "welfare",
      "pollution", "greenhouse", "emitter", "emissions"),
     "INSIDE CHINA", (124, 38, 96)),

    # The space cluster was missing on the first live run: "The China Academy
    # of Space Technology develops navigation system" fell all the way through
    # to OVERSEAS because only "space race" and "lunar" were listed. MILITARY
    # is matched first, so "aircraft carrier" and "fighter jet" still land
    # there rather than here.
    (("chip", "semiconductor", "lithography", "artificial intelligence", " ai ", " ai,", " ai-",
      "ai deal", "ai chips", "ai race", "ai safety", "robot", "satellite", "lunar",
      "quantum", "huawei", "byd", "electric vehicle", "drone", "fusion", "telecom", "5g", "6g",
      "algorithm", "data center", "launch",
      " space", "spacecraft", "aerospace", "orbit", "probe", "rocket", "navigation",
      "aircraft", "supersonic", "moon", "mars", "jupiter", "astronaut"),
     "TECHNOLOGY", (40, 132, 196)),

    (("econom", "trade", "tariff", "export", "import", "gdp", "yuan", "debt", "property",
      "real estate", "investment", "invest", "stock", "bank", "currency", "supply chain",
      "rare earth", "commerce", "manufactur", "steel", "sanction", "market", "subsid", "contract",
      "mining", "acquisition", "state-owned",
      "airline", "flight", "carrier", "aviation", "route", "shipping", "port"),
     "ECONOMY", (22, 138, 118)),
]
# The default is a real beat for this channel, not a leftover bucket: the CCP in
# Pakistan, Brazil, Brunei, Panama, Peru, plus espionage, united-front work and
# propaganda abroad.
_DEFAULT_CATEGORY = ("OVERSEAS", (150, 96, 40))

_FONTS = {
    "sans_bold": [("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 2),
                  ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0)],
    "sans": [("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 2),
             ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0)],
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
    """Matched on the headline alone, never headline + deck: the deck names
    secondary actors ("ahead of the Trump-Xi summit") and a rare-earth export
    story came out tagged WASHINGTON because of one."""
    low = " " + (title or "").lower() + " "
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


# Deliberate space, not a filled box -- see this module's docstring. _BOX_FILL
# is the share of its box the headline block may occupy; _MAX_SIZE and
# _MAX_LINES cap it from the other direction so a four-word headline does not
# blow up to fill the card either.
_BOX_FILL = 0.86
_MAX_SIZE = 88
_MAX_SIZE_WITH_PHOTO = 76
_MIN_SIZE = 46
_MAX_LINES = 4
_LEAD = 1.15

_DECK_SIZE = 35
_DECK_LEAD = 1.34
_DECK_MAX_LINES = 2
_DECK_GAP = 54          # between the headline's last baseline box and the deck
_DECK_TAIL = 44         # space the deck needs under itself before the rule


def _fit(draw, text, max_width, max_height, max_size):
    """Largest size whose block fits the width, the height and _MAX_LINES."""
    size = max_size
    while size >= _MIN_SIZE:
        f = _font("sans_bold", size)
        lines = _wrap(draw, text, f, max_width)
        if len(lines) <= _MAX_LINES and round(size * _LEAD) * len(lines) <= max_height:
            return lines, f
        size -= 2

    f = _font("sans_bold", _MIN_SIZE)
    lines = _wrap(draw, text, f, max_width)[:_MAX_LINES]
    if lines:
        last = lines[-1]
        lines[-1] = (last.rsplit(" ", 1)[0] if " " in last else last) + "…"
    logger.warning("card: headline too long even at %dpx, trimmed to %d lines: %r",
                   _MIN_SIZE, _MAX_LINES, text[:90])
    return lines, f


def _chrome(d: ImageDraw.ImageDraw, label: str, accent) -> None:
    """Masthead band, date and category kicker — identical on both shapes."""
    d.rectangle([0, 0, W, BAND_H], fill=accent)
    x = MARGIN
    mast = _font("sans_bold", 38)
    for ch in MASTHEAD:
        d.text((x, 40), ch, font=mast, fill=BAND_INK)
        x += d.textlength(ch, font=mast) + 5
    date_text = datetime.now(timezone.utc).strftime("%B %-d, %Y")
    date_font = _font("sans", 25)
    d.text((W - MARGIN - d.textlength(date_text, font=date_font), 48),
           date_text, font=date_font, fill=BAND_INK)

    d.rectangle([MARGIN, 176, MARGIN + 13, 201], fill=accent)
    x = MARGIN + 30
    kick = _font("sans_bold", 25)
    for ch in label:
        d.text((x, 173), ch, font=kick, fill=accent)
        x += d.textlength(ch, font=kick) + 4


def _draw_body(d: ImageDraw.ImageDraw, headline: str, deck: str, floor: int,
               accent, max_size: int) -> int:
    """Headline, then the deck if what is left under the headline can hold it
    without crowding, then the accent rule. `floor` is the y the body may not
    cross — the footer block on a text card, the photo band on a split card.
    Returns the y the drawn group ends at, which is what sizes the footer."""
    top = 244
    box = W - MARGIN * 2
    lines, f = _fit(d, headline, box, round((floor - top) * _BOX_FILL), max_size)
    line_h = round(f.size * _LEAD)
    y = top
    for line in lines:
        d.text((MARGIN, y), line, font=f, fill=HEAD_INK)
        y += line_h

    if deck:
        deck_font = _font("sans", _DECK_SIZE)
        deck_lines = _wrap(d, deck, deck_font, box)
        deck_h = round(_DECK_SIZE * _DECK_LEAD) * len(deck_lines)
        fits = (len(deck_lines) <= _DECK_MAX_LINES
                and y + _DECK_GAP + deck_h + _DECK_TAIL <= floor)
        if fits:
            y += _DECK_GAP
            for line in deck_lines:
                d.text((MARGIN, y), line, font=deck_font, fill=DECK_INK)
                y += round(_DECK_SIZE * _DECK_LEAD)
        else:
            logger.info("card: deck dropped, no room under the headline: %r", deck[:60])

    d.rectangle([MARGIN, y + 30, MARGIN + 132, y + 38], fill=accent)
    return y + 38


def make_text_card(headline: str, out_path: str, deck: str = "", attribution: str = "") -> None:
    """No photo: the type carries the card, over a tinted footer block."""
    label, accent = category_of(headline)
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    _chrome(d, label, accent)
    end = _draw_body(d, headline, deck, H - FOOTER_MIN - 24, accent, _MAX_SIZE)

    footer_top = min(H - FOOTER_MIN, max(end + FOOTER_GAP, H - FOOTER_MAX))
    d.rectangle([0, footer_top, W, H], fill=_tint(accent))
    d.rectangle([0, footer_top - 5, W, footer_top], fill=accent)
    if attribution:
        d.text((MARGIN, footer_top + FOOTER_TEXT_INSET), attribution,
               font=_font("sans", 26), fill=FOOTER_INK)
    img.save(out_path)


def make_split_card(headline: str, photo_bytes: bytes, out_path: str,
                    deck: str = "", attribution: str = "") -> None:
    """Headline on white, the article's photo in a band across the bottom.

    The band is 2.7:1, close to the 1.9:1 a news site's og:image actually is, so
    the photo is cropped lightly and never upscaled much. A full-bleed square
    treatment was tried first and rejected: cropping a 1200x630 SCMP frame to
    1:1 cut Xi Jinping out of a Xi-Trump handshake entirely.
    """
    import io

    label, accent = category_of(headline)
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    _chrome(d, label, accent)
    _draw_body(d, headline, deck, H - PHOTO_H - 56, accent, _MAX_SIZE_WITH_PHOTO)
    if attribution:
        d.text((MARGIN, H - PHOTO_H - 54), attribution, font=_font("sans", 25), fill=SOURCE_INK)

    photo = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    scale = max(W / photo.width, PHOTO_H / photo.height)
    photo = photo.resize((max(W, round(photo.width * scale)),
                          max(PHOTO_H, round(photo.height * scale))), Image.LANCZOS)
    left, top = (photo.width - W) // 2, (photo.height - PHOTO_H) // 2
    img.paste(photo.crop((left, top, left + W, top + PHOTO_H)), (0, H - PHOTO_H))
    d.rectangle([0, H - PHOTO_H - 5, W, H - PHOTO_H], fill=accent)
    img.save(out_path)


# ---------------------------------------------------------------------------
# Which posts get a card, and how the source is named on it.
# ---------------------------------------------------------------------------

# Matched against a candidate's `author` -- core/notion_candidates.py writes the
# RSS source's Notion Name into that column, so these are the source table's own
# names, lowercased and matched as prefixes. "scmp" covers the thirteen separate
# SCMP section feeds without listing each one.
_CARD_SOURCE_PREFIXES = ("google news", "scmp", "zero hedge", "zerohedge")

# The same three sources by article domain, as a backstop for a renamed or
# newly-added feed row. This also catches a news.google.com link arriving from
# some other aggregator feed (china.buzzing.cc republishes them), which is
# correct: the problem is a URL that has no readable preview, not which feed it
# came in on.
_CARD_DOMAINS = ("news.google.com", "scmp.com", "zerohedge.com")

_SOURCE_DISPLAY_NAME = {
    "scmp": "South China Morning Post",
    "zero hedge": "ZeroHedge",
    "zerohedge": "ZeroHedge",
}
_DOMAIN_DISPLAY_NAME = {
    "scmp.com": "South China Morning Post",
    "zerohedge.com": "ZeroHedge",
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
# recoverable and is what belongs on the card. Guarded on both ends: the tail
# must be short and must not read like the rest of a sentence, or a headline
# that merely contains a dash would be mistaken for an attribution.
_GOOGLE_NEWS_TAIL = re.compile(r"\s+-\s+([^-]{2,40})$")


def _google_news_publisher(title: str) -> str:
    m = _GOOGLE_NEWS_TAIL.search((title or "").strip())
    if not m:
        return ""
    tail = m.group(1).strip()
    return "" if any(c in tail for c in ".!?;:") else tail


def card_attribution(author: str, url: str, title: str) -> str:
    """The "(Source: ...)" line, or "" when the real outlet can't be named.

    Never "Google News" (editors, 2026-09-21: "域名应该是那篇报道哪家媒体的名字,
    不能全是Google news"). Google News is an aggregator, not the reporter — when
    its title carries no " - Publisher" tail to recover the real outlet from,
    the line is left off the card entirely rather than crediting the aggregator.
    """
    name = (author or "").strip().lower()
    for prefix, label in _SOURCE_DISPLAY_NAME.items():
        if name.startswith(prefix):
            return f"(Source: {label})"
    for domain, label in _DOMAIN_DISPLAY_NAME.items():
        if _host_matches(url or "", domain):
            return f"(Source: {label})"
    publisher = _google_news_publisher(title)
    return f"(Source: {publisher})" if publisher else ""
