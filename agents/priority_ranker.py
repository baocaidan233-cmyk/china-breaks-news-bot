from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from agents.embedder import Embedder
from core.config import AppConfig
from core.hashing import cosine_similarity
from core.models import PublishCandidate

logger = logging.getLogger(__name__)

_LOG_PATH = Path("logs/priority_rank_decisions.jsonl")

# Trending-overlap cosine thresholds — carried over from
# core/hot_topics.py's HotTopicsConfig.match_threshold (0.5), which this
# project already independently uses (not a value copied over just for
# this ranker). 0.65 is a second, higher band for an unusually tight
# match, ported from AM1ST's own value — not independently validated on
# this project's own embeddings, a reasonable starting point.
_TRENDING_SIM_HIGH = 0.65
_TRENDING_SIM_LOW = 0.5

# Freshness decay coefficient — logarithmic, not linear: a
# candidate_max_age_hours-old story loses a bounded amount, an hour-old
# story loses ~1, an 18-minutes-old story loses ~0.14 — proportionate to
# the ~5-11 range llm_score+trending_bonus produces, without dominating
# it. Ported from AM1ST's own value — a generic curve-shape constant, not
# content-calibrated, so no adaptation needed.
_FRESHNESS_DECAY_K = 1.5


def _log_decision(record: dict) -> None:
    """Same append-only JSONL convention as core/event_identity.py's
    log_decision(), kept separate (own file, own module) since this isn't
    an event-identity decision — it's the publish-time ranking breakdown,
    recorded so this formula's real behavior can be reviewed once this
    project has its own real publish-cycle history, the same way AM1ST
    used its own log to find and fix this formula's heat_score
    double-counting bug (see class docstring)."""
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        full = {"logged_at": int(time.time()), **record}
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(full, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("PriorityRanker: failed to log decision — continuing (fail open)")


def log_publish_outcome(batch_size: int, winner: PublishCandidate | None) -> None:
    """Called once per publish cycle, after find_publishable() resolves —
    links the per-candidate breakdowns above to what actually got
    published (or didn't), so a review can walk
    logs/priority_rank_decisions.jsonl and see both "how was this batch
    scored" and "which one won" without cross-referencing main_publish's
    own log."""
    _log_decision({
        "check_type": "publish_outcome",
        "batch_size": batch_size,
        "winner_page_id": winner.page_id if winner else None,
        "winner_url": winner.url if winner else None,
        "winner_priority_score": winner.priority_score if winner else None,
    })


class PriorityRanker:
    """Formula-based priority ranking (2026-09-06, ported from AM1ST's own
    2026-09-04 rewrite, replacing the previous LLM-based holistic 1-10
    rank call this project had inherited).

    Why AM1ST replaced its own LLM ranker: a live audit found it was
    unstable in exactly the range that decides most publish cycles.
    Re-running the SAME batch through the SAME ranker 3x with nothing
    else changed produced different scores for the same candidate — real,
    reproducible run-to-run noise, not just batch-composition sensitivity
    (which was ALSO independently confirmed: one specific candidate
    scored 0 in one batch and a stable 4 in a later batch with different
    competing candidates — meaning priority_score was only ever
    meaningful relative to whatever else happened to be in that specific
    LLM call, not a portable, independently-checkable number). This is a
    reliability bug fix, not a US-politics judgment call — the same
    instability risk applies regardless of what the channel covers, so
    it's ported as architecture rather than re-discovered from scratch on
    this project's own future incident.

    Every input here is already a reliable, already-computed number:
    llm_score (ingestion-side editorial severity — this project's own
    scoring_prompt.txt) and hours_since_update (this candidate's own
    freshness, deliberately NOT hours_old/event age — a fresh update in a
    long-running story should be judged on its own freshness, not
    discounted for the event's total age). The only thing that isn't
    already a number is "does this topic overlap a trending headline" —
    that reduces cleanly to an embedding cosine-similarity check, no LLM
    judgment needed. Removing the LLM call removes the only place noise
    could enter.

    Deliberately NO separate heat_score bonus — AM1ST's own first version
    of this rewrite added one, then removed it the same week after a
    127-real-cycle audit found it double-counted heat_score (already one
    of scoring_prompt.txt's own section-2 corroboration bullets — a story
    can reach band 6/7/8 purely off heat_score thresholds) and biased
    wins toward big, already-corroborated outlets over stories that broke
    something genuinely engaging first but hadn't been corroborated yet.
    Ported straight to the corrected (no heat_bonus) formula rather than
    reintroducing AM1ST's own already-identified mistake. heat_score is
    still logged per candidate for visibility; it just doesn't feed
    priority_score.

    is_hot still sorts ahead of priority_score unconditionally, same as
    before — a manually-flagged breaking candidate always wins the slot.

    Every scored candidate's full breakdown is appended to
    logs/priority_rank_decisions.jsonl (one line per candidate per
    cycle), plus one summary line per cycle (see log_publish_outcome())
    recording which candidate, if any, actually got published — this
    project has no real data yet, so this logging is what will let a
    future pass judge whether _TRENDING_SIM_HIGH/_TRENDING_SIM_LOW/
    _FRESHNESS_DECAY_K need their own recalibration, the same way AM1ST
    used its own log to catch the heat_bonus problem."""

    def __init__(self, config: AppConfig) -> None:
        self._embedder = Embedder(config)

    async def rank(self, batch: list[PublishCandidate], trending_headlines: list[str] | None = None) -> list[PublishCandidate]:
        if not batch:
            return []
        trending_headlines = trending_headlines or []
        trending_embeddings = [await self._embedder.embed(h) for h in trending_headlines if h]

        now = datetime.now(timezone.utc)
        scored: list[tuple[PublishCandidate, float]] = []
        for c in batch:
            hours_since_update = max(0.0, (now - c.published_at).total_seconds() / 3600)

            best_sim = 0.0
            trending_bonus = 0.0
            if trending_embeddings:
                cand_embedding = await self._embedder.embed((c.post_content or c.title)[:2000])
                best_sim = max(cosine_similarity(cand_embedding, e) for e in trending_embeddings)
                if best_sim >= _TRENDING_SIM_HIGH:
                    trending_bonus = 2.0
                elif best_sim >= _TRENDING_SIM_LOW:
                    trending_bonus = 1.0

            freshness_penalty = _FRESHNESS_DECAY_K * math.log(1 + hours_since_update)
            priority_score = c.llm_score + trending_bonus - freshness_penalty

            _log_decision({
                "page_id": c.page_id,
                "url": c.url,
                "title": c.title,
                "llm_score": c.llm_score,
                "heat_score": c.heat_score,
                "trending_max_similarity": round(best_sim, 4),
                "trending_bonus": trending_bonus,
                "hours_since_update": round(hours_since_update, 2),
                "freshness_penalty": round(freshness_penalty, 3),
                "priority_score": round(priority_score, 3),
                "is_hot": c.is_hot,
            })

            scored.append((c.model_copy(update={"priority_score": priority_score}), hours_since_update))

        scored.sort(key=lambda item: (item[0].is_hot, item[0].priority_score, -item[1]), reverse=True)
        return [c for c, _ in scored]
