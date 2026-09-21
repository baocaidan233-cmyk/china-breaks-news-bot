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

from core.config import AppConfig
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
