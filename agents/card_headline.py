"""Writes the headline (and an optional deck) printed on the card.

A separate single-purpose call rather than two more fields on
prompts/content_gen_prompt.txt — the same reason agents/staleness_checker.py is
its own call, and because DailyNews established on its own writer that adding
fields to an already-large prompt degrades the fields that were there first.

What this replaces: the card used to print the caption's first sentence. That
meant the reader read the same sentence twice, once on the card (truncated
mid-clause, since captions run past 160 characters) and once in the post
directly underneath it.

The one accuracy rule that had to be written explicitly: the first version
turned "Taiwan must enhance its resilience, warned scholar Christopher Walker"
into "Taiwan enhances resilience against CCP and Russia threats" — a
recommendation restated as an event. It did the same to a WHO nominee's call
for cooperation. Both are in the prompt below as a worked example.
"""

from __future__ import annotations

import json
import logging
import re

from core.caption_guard import former_president_violation, invented_biden
from core.config import AppConfig
from core.language import _non_latin_letters
from core.openai_client import create_openai_client

logger = logging.getLogger(__name__)

_SYSTEM = """You write the headline for a news poster. The poster carries the
post's own caption underneath it, so the headline must not repeat the caption's
opening sentence — the reader sees both at once.

You are given that caption. Reply with JSON only:
{"headline": "...", "deck": "..."}

HEADLINE — 5 to 11 words. Declarative, present tense, no full stop at the end,
never a question. Lead with the actor the caption leads with (the CCP, Beijing,
Xi Jinping, Trump, Taiwan, a named ministry or company). Say the single most
consequential thing that happened. Use the caption's own naming: "the CCP", not
"China's government".

DECK — one line, at most 14 words. It carries the SECOND fact: a number, a date,
a named party, a consequence. Never restate the headline in other words. Return
"" when the caption has nothing worth adding — a card with no deck is fine and
often better.

Keep the kind of act the caption reports. If someone urged, warned, called for,
predicted or recommended something, say so and name them. Never turn a
recommendation into an event:
  caption: "Taiwan must enhance its resilience, warned scholar Christopher Walker"
  WRONG:   "Taiwan enhances resilience against CCP and Russia threats"
  RIGHT:   "Scholar warns Taiwan is exposed to CCP and Russian sharp power"

Sentence case: capitalise the first word and proper nouns only, never Every Word.

Everything must be supported by the caption. Invent nothing."""

_MAX_HEADLINE_WORDS = 14
_MAX_DECK_WORDS = 18


class CardHeadlineWriter:
    def __init__(self, config: AppConfig) -> None:
        self._client = create_openai_client(config)
        self._model = config.openai.chat_model

    async def write(self, caption: str) -> tuple[str, str]:
        """Returns (headline, deck). Falls back to ("", "") on any failure —
        main_publish then falls back to the caption's first sentence, which is
        what the card printed before this existed, so a failure here costs
        quality and never a post."""
        caption = (caption or "").strip()
        if not caption:
            return "", ""
        # system + user split, static rules first: the same prompt-caching
        # shape agents/staleness_checker.py and core/event_identity.py settled
        # on — OpenAI only discounts a request's cumulative prefix, so the
        # byte-identical rules have to come before the per-call caption.
        kwargs = dict(
            model=self._model,
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": caption[:4000]}],
            response_format={"type": "json_object"},
        )
        if self._model.startswith("gpt-5"):
            kwargs["max_completion_tokens"] = 200
            kwargs["reasoning_effort"] = "minimal"
        else:
            kwargs["temperature"] = 0.3
            kwargs["max_tokens"] = 200
        try:
            resp = await self._client.chat.completions.create(**kwargs)
            data = json.loads((resp.choices[0].message.content or "").strip())
        except Exception:
            logger.exception("CardHeadlineWriter: failed, falling back to the caption")
            return "", ""

        headline = str(data.get("headline") or "").strip().rstrip(".")
        deck = str(data.get("deck") or "").strip().rstrip(".")

        # Length is capped rather than trusted: a model that ignores the word
        # count would otherwise push the card's type down to its minimum size.
        if not headline or len(headline.split()) > _MAX_HEADLINE_WORDS:
            logger.info("CardHeadlineWriter: headline rejected (%d words): %r",
                        len(headline.split()), headline[:90])
            return "", ""
        if len(deck.split()) > _MAX_DECK_WORDS:
            logger.info("CardHeadlineWriter: deck dropped (%d words): %r",
                        len(deck.split()), deck[:90])
            deck = ""
        return headline, deck


# ---- checks on the card text (2026-10-16, Linear AI-89) ---------------------
# The headline and deck are a second model's retelling of the caption, printed
# in large type, and until now nothing checked them. Pure code, against the
# caption they were written from (which passed every gate) and the article.
# A failure only costs the card its headline: main_publish falls back to the
# caption's first sentence.

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_EDGE_PUNCT = "\"'\u201c\u201d\u2018\u2019()[]{},;:!?.\u2026"
_POSSESSIVE = re.compile(r"(?:'s|\u2019s|'|\u2019)$")
# Words a sentence-case headline may still capitalise (after a colon, say)
# that are not names.
_COMMON_TITLE_WORDS = {
    "a", "an", "the", "and", "or", "but", "nor", "of", "in", "on", "at", "to", "for", "by", "with",
    "from", "as", "into", "over", "after", "before", "amid", "against", "about", "is", "are", "was",
    "were", "be", "it", "its", "this", "that", "these", "those", "no", "not", "new", "why", "how",
    "what", "who", "when", "where",
}


def _numbers(text: str) -> set[str]:
    """Every number in text, commas dropped and trailing decimal zeros cut, so
    "$1,200" / "1200" and "4.50%" / "4.5" compare equal ($ and % are outside
    the match)."""
    out = set()
    for m in _NUMBER.finditer(text):
        n = m.group(0).replace(",", "")
        if "." in n:
            n = n.rstrip("0").rstrip(".")
        out.add(n)
    return out


def _name_words(line: str) -> list[str]:
    """The capitalised words of one headline or deck line, past its first
    word: "Beijing's" -> "Beijing", "Russia-Ukraine" -> both, "U.S." -> "US"."""
    out = []
    for w in line.split()[1:]:
        for part in re.split(r"[-/\u2013\u2014]", w):
            part = _POSSESSIVE.sub("", part.strip(_EDGE_PUNCT)).replace(".", "")
            if part and part[0].isupper() and not any(ch.isdigit() for ch in part) \
                    and part.lower() not in _COMMON_TITLE_WORDS:
                out.append(part)
    return out


def card_text_problem(headline: str, deck: str, caption: str, article: str) -> str | None:
    """Why the card's headline/deck must not be printed, or None: not English,
    Trump called a former president, Biden named when the article never does,
    a number the caption doesn't have, or a name-like word the caption doesn't
    have (case-insensitive; a plain plural "s" either way is the same word)."""
    text = f"{headline}\n{deck}".strip()
    # The letter half of is_english_caption() only: its langdetect half reads
    # a short headline badly (Market Watcher, 10-16: 6 of 20 plain English
    # headlines refused). The slip to catch is a pasted foreign word.
    if _non_latin_letters(text):
        return "not English"
    rule = former_president_violation(text)
    if rule:
        return f"caption_guard {rule}"
    if invented_biden(text, article):
        return "invented-biden"
    have = _numbers(caption)
    missing = sorted(_numbers(text) - have)
    if missing:
        return f"number not in the caption: {missing[0]}"
    cap = caption.replace(".", "").lower()
    for line in (headline, deck):
        for word in _name_words(line):
            w = word.lower()
            forms = {w, w[:-1]} if w.endswith("s") and len(w) > 3 else {w, w + "s"}
            if not any(re.search(rf"(?<!\w){re.escape(f)}(?!\w)", cap) for f in forms):
                return f"name not in the caption: {word}"
    return None
