from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ValidationError

from core.config import AppConfig
from core.models import Candidate
from core.openai_client import create_openai_client

logger = logging.getLogger(__name__)


class ScoreOutput(BaseModel):
    llm_score: float
    llm_comment: str


class Scorer:
    """AI relevancy scoring — prompts/scoring_prompt.txt is China Breaks'
    own CCP-exposure scoring prompt (redesigned 2026-09-05 around AM1ST's
    own mature Channel-Position/Nexus-Gate/Core-Themes/score-bands
    template, after cross-checking against this project's real, live n8n
    prompts — see project_china_breaks_bot memory — not AM1ST's own
    America First scoring prompt content, which this mechanism was
    otherwise ported from).

    Runs on config.openai.chat_model (gpt-4o-mini) — this project's own
    standing "gpt-4o-mini everywhere" model-architecture rule, applied
    from day one. AM1ST's own history (see its agents/scorer.py) already
    tried moving this role onto a cheaper gpt-5-nano model and reverted
    after a live multi-cycle test found real judgment-quality regressions
    (well-formed output was verified, actual editorial judgment was not) —
    that lesson is inherited here rather than re-tested: nothing in this
    codebase should reference gpt-5-nano without a fresh,
    judgment-quality-focused live test first.

    No secondary Gemini autofix model; a single retry with the parse error
    appended does the same job a second-model fallback would."""

    def __init__(self, config: AppConfig) -> None:
        self._client = create_openai_client(config)
        self._model = config.openai.chat_model
        self._system_prompt = Path(config.openai.scoring_prompt_file).read_text(encoding="utf-8")

    async def _call(self, user_message: str) -> str:
        kwargs = dict(
            model=self._model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        if self._model.startswith("gpt-5"):
            kwargs["max_completion_tokens"] = 500
            kwargs["reasoning_effort"] = "minimal"
        else:
            kwargs["temperature"] = 0.3
            kwargs["max_tokens"] = 500
        resp = await self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    async def score(self, candidate: Candidate, trending_headlines: list[str] | None = None) -> ScoreOutput | None:
        # Corroboration signal (2026-08-06 — see core/config.py's HeatConfig
        # and project_am1st_migration memory's 2026-08-05 design note):
        # heat_score/event_first_seen_at are set by main.py's Layer 3, from
        # the cross-cycle Qdrant query, before this is called. heat_score=1.0
        # means only this one source so far; event_first_seen_at falls back
        # to this article's own published_at when nothing earlier was found.
        first_seen = candidate.event_first_seen_at or candidate.published_at
        hours_since_first_seen = round((datetime.now(timezone.utc) - first_seen).total_seconds() / 3600, 1)
        # Trending headlines (2026-09-05, ported from AM1ST's own 2026-09-04
        # addition — see project_china_breaks_bot memory) — same free
        # Google News "China CCP" feed agents/trending.py already supplies
        # to main_publish.py's priority_ranker, reused here so the Scorer
        # has an EXTERNAL signal of what's actually getting mainstream
        # attention right now, distinct from heat_score (which only
        # reflects how many of this feed's own RSS sources have
        # corroborated THIS specific candidate's underlying event).
        # Optional/best-effort — an empty list (fetch failure, or a caller
        # that doesn't pass one) just omits the section below, same
        # fail-open convention as everywhere else. The scoring_prompt.txt
        # system message references this signal by name, so main.py MUST
        # actually fetch and pass it once per cycle for the prompt's own
        # claim to be true at runtime.
        trending_block = ""
        if trending_headlines:
            headlines = "\n".join(f"- {h}" for h in trending_headlines)
            trending_block = f"\n\nCurrently trending in China/CCP news (Google News, for context only):\n{headlines}"
        user_message = (
            f"Title: {candidate.title}\n\nDescription: {candidate.description}"
            f"\n\nCorroboration: heat_score={candidate.heat_score:.1f} (1.0 = only this one source"
            f" reporting it so far; higher means more outlets, weighted, are covering the same event),"
            f" hours_since_event_first_seen={hours_since_first_seen}"
            f"{trending_block}"
        )
        raw = await self._call(user_message)
        try:
            return ScoreOutput.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning("Scorer: malformed output for %s, retrying once: %s", candidate.url, e)
            retry_message = (
                f"{user_message}\n\nYour previous response could not be parsed as "
                f'{{"llm_score": float, "llm_comment": string}}. Error: {e}. Return valid JSON only.'
            )
            raw_retry = await self._call(retry_message)
            try:
                return ScoreOutput.model_validate(json.loads(raw_retry))
            except (json.JSONDecodeError, ValidationError):
                logger.error("Scorer: gave up on %s after retry", candidate.url)
                return None
