from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from core.config import AppConfig
from core.models import Candidate
from core.openai_client import create_openai_client

logger = logging.getLogger(__name__)


# 2026-09-08: real, literal phrases pulled from actual llm_comment text
# where the model admitted no real China/CCP connection existed, yet still
# returned llm_score>=5 (and, in one case, nexus_gate="PASS") anyway — see
# Scorer._enforce_gate()'s own docstring. Deliberately just a substring
# scan, not an exhaustive taxonomy: the point is to catch the model's OWN
# admission in its own words, not to guess every way it might phrase one.
_SELF_CONTRADICTION_PHRASES = (
    "does not have a direct connection",
    "lacks a direct connection",
    "lacks a concrete link",
    "lacks a concrete connection",
    "no direct connection to the ccp",
    "no direct connection to china",
)


class ScoreOutput(BaseModel):
    llm_score: float
    llm_comment: str
    # 2026-09-08: no default — a real audit found a candidate (a Hong Kong
    # listing story) where llm_comment itself said "lacks a direct
    # connection to the CCP" but llm_score was still 5, and the code-level
    # _enforce_gate() override below DIDN'T catch it. With a default of ""
    # here, a model response that simply omits nexus_gate (rather than
    # explicitly writing FAIL) silently defaults to "" — which
    # _enforce_gate()'s startswith("FAIL") check treats as "not FAIL," so
    # the override never fires. Making this a required Literal instead
    # means an omitted/malformed field is a real ValidationError, which
    # routes into Scorer.score()'s EXISTING retry path (which explicitly
    # re-prompts for the missing field) instead of silently bypassing
    # enforcement.
    nexus_gate: Literal["PASS", "FAIL"]


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
        self._scorer_model = config.openai.scorer_model or self._model
        self._scorer_effort = config.openai.scorer_reasoning_effort
        self._fallback_model = config.openai.fallback_model
        self._calls_log = Path(config.openai.scorer_calls_log_path)
        self._prompt_sha = hashlib.sha256(self._system_prompt.encode("utf-8")).hexdigest()[:12]

    def _log_call(self, candidate: Candidate, user_message: str, raw: ScoreOutput | None,
                  final: ScoreOutput | None, retried: bool, model: str) -> None:
        """One JSON line per scoring call (2026-10-27): the exact message, the
        model's own answer and the score after _enforce_gate(). Best-effort:
        a logging failure never fails the score."""
        try:
            record = {"logged_at": int(time.time()), "url": candidate.url, "title": candidate.title,
                      "model": model, "prompt_sha": self._prompt_sha, "user_message": user_message,
                      "nexus_gate": raw.nexus_gate if raw else None,
                      "raw_score": raw.llm_score if raw else None,
                      "llm_score": final.llm_score if final else None,
                      "llm_comment": raw.llm_comment if raw else None, "retried": retried}
            self._calls_log.parent.mkdir(parents=True, exist_ok=True)
            with self._calls_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            logger.exception("Scorer: failed to write %s -- continuing", self._calls_log)

    async def _call(self, user_message: str, model: str) -> str:
        kwargs = dict(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        if model.startswith("gpt-6"):
            # 2026-10-28: a reasoning model. Its reasoning counts against the
            # cap (about 100-200 tokens at low on top of the ~80-token answer,
            # measured on 300 test calls), and it takes no temperature.
            kwargs["max_completion_tokens"] = 4000
            kwargs["reasoning_effort"] = self._scorer_effort
        elif model.startswith("gpt-5"):
            kwargs["max_completion_tokens"] = 500
            kwargs["reasoning_effort"] = "minimal"
        else:
            # 2026-09-07: was 0.3 — real production evidence found two
            # articles about the literal same event (an EU "Buy European"
            # procurement story, one via Google News/FT, one via a Chinese
            # Sputnik translation) scoring 7 and 6 respectively, with
            # near-identical llm_comment reasoning. Sampling temperature was
            # part of that inconsistency; every other consequential judgment
            # call in this codebase (EventVerifier's same_event/
            # classify_subtype in core/event_identity.py) already runs at
            # temperature=0 for exactly this reason.
            kwargs["temperature"] = 0
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
        # scorer_model first (2026-10-28, AI-105); a call that fails outright
        # is asked once of openai.fallback_model instead -- gpt-6-luna since
        # 2026-10-29 (user: 所有回退全部改成 luna), so with luna scoring that is
        # one more try on luna.
        model = self._scorer_model
        try:
            raw = await self._call(user_message, model)
        except Exception as e:
            logger.warning("Scorer: %s failed for %s (%s: %s) -- asking %s", model, candidate.url,
                           type(e).__name__, str(e)[:200], self._fallback_model)
            model = self._fallback_model
            raw = await self._call(user_message, model)
        retried = False
        try:
            result = ScoreOutput.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            retried = True
            logger.warning("Scorer: malformed output for %s, retrying once: %s", candidate.url, e)
            retry_message = (
                f"{user_message}\n\nYour previous response could not be parsed as "
                f'{{"nexus_gate": "PASS or FAIL", "llm_score": float, "llm_comment": string}}. Error: {e}. Return valid JSON only.'
            )
            raw_retry = await self._call(retry_message, model)
            try:
                result = ScoreOutput.model_validate(json.loads(raw_retry))
            except (json.JSONDecodeError, ValidationError):
                logger.error("Scorer: gave up on %s after retry", candidate.url)
                self._log_call(candidate, user_message, None, None, retried, model)
                return None
        final = self._enforce_gate(candidate.url, result)
        self._log_call(candidate, user_message, result, final, retried, model)
        return final

    @staticmethod
    def _enforce_gate(url: str, result: ScoreOutput) -> ScoreOutput:
        """2026-09-08: hard code-level enforcement, not just prompt wording
        — a real production audit found the model's own stated reasoning
        repeatedly said a story "does not have a direct connection to the
        CCP or China" / "lacks a concrete link," yet still returned
        llm_score=5 anyway (Nigel Farage UK migrant-protest remarks, an
        Israeli minister's Oslo Accords comments, a Trump Space Force
        uniform story, a UK-Israel trade ban — none mention China/CCP at
        all). This happened with scoring_prompt.txt's own Nexus Gate
        already spelling out "only score below 5 when clearly unrelated"
        and several real negative examples already in the prompt — adding
        more prose examples had already been tried once this same day and
        didn't fully close it. Forcing an explicit nexus_gate field BEFORE
        the numeric score makes the gate decision and the score
        inconsistent-with-each-other in a way code can actually catch:
        if the model's own gate verdict is FAIL, the score is hard-capped
        below 5 regardless of what number it returned, rather than trusting
        the model to keep its own stated reasoning and its own number in
        sync. A FAIL that already scored below 5 is left untouched (no
        override needed); this only fires when the two disagree.

        Second layer, added the same day the nexus_gate field itself was:
        a spot-check right after deploying nexus_gate found a NEW case
        (a Hong Kong stock-listing story) where llm_comment again said
        "lacks a direct connection to the CCP" but the model wrote
        nexus_gate="PASS" anyway — the explicit field closes the
        omission case, not the case where the model fills in a field that
        still contradicts its own comment. _SELF_CONTRADICTION_PHRASES is
        the same kind of admission-phrase scan already used elsewhere in
        this codebase (see scoring_prompt.txt's own banned corroboration-
        language rule) — if llm_comment itself contains one of these, that
        overrides a PASS the same way an explicit FAIL does. Not a
        complete solution (a model could still phrase the same admission
        differently), but closes the exact real recurrence found so far."""
        is_fail = result.nexus_gate.strip().upper().startswith("FAIL")
        comment_lower = result.llm_comment.lower()
        admits_no_connection = any(phrase in comment_lower for phrase in _SELF_CONTRADICTION_PHRASES)
        if (is_fail or admits_no_connection) and result.llm_score >= 5:
            logger.warning(
                "Scorer: %s — nexus_gate=%s, comment admits no connection=%s, but llm_score=%.1f, overriding to 3.0 (model comment: %r)",
                url, result.nexus_gate, admits_no_connection, result.llm_score, result.llm_comment,
            )
            return result.model_copy(update={"llm_score": 3.0})
        return result
