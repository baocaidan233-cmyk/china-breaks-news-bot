from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from core.config import AppConfig
from core.models import PublishCandidate

# Which timezone's calendar day decides "weekday vs weekend" — kept as
# US/Eastern, ported unchanged from AM1ST: this channel posts to Gettr, a
# US-based platform with a largely US/English-speaking audience, and the
# standing "news day" reference used elsewhere in this project's sibling
# bots is US/Eastern, not UTC or wherever this process happens to run.
_DAY_TZ = ZoneInfo("America/New_York")

# Independent of publish.candidate_min_score (the floor used by the Notion
# query) — AM1ST's own "batch of top 5" tier boundary, ported unchanged:
# a hardcoded 7 regardless of what the floor is set to.
_TIER1_MIN_SCORE = 7.0


def _is_weekday(now: datetime) -> bool:
    return now.astimezone(_DAY_TZ).weekday() < 5  # Mon=0 ... Sun=6


def select_batch(candidates: list[PublishCandidate], config: AppConfig) -> list[PublishCandidate]:
    """Tiered batch selection — mechanism ported unchanged from AM1ST's own
    select_batch(): prefer fresh+high-scoring, progressively relax until at
    least batch_min survive (or give up and just take the newest ones),
    capped at batch_max. `candidates` should come straight from the Notion
    eligibility query (send_status/channel_name/age/score gate already
    applied there, at the lower of weekday/weekend_min_score so both are
    actually fetched). AM1ST's own "former president Trump" stale-phrasing
    filter doesn't apply to this content stream and is deliberately not
    part of this pipeline at all — see README.

    Weekday/weekend-aware floor: weekdays see much more real news volume,
    so prefer publish.weekday_min_score first and only relax to the lower
    publish.weekend_min_score if that doesn't fill batch_min. Weekends see
    much less volume, so go straight to weekend_min_score — being picky
    first would usually just waste a tier.

    The score-based tiers (1-4) keep cascading until batch_max is full, not
    just until batch_min is met — a moderately-scored-but-currently-trending
    story could otherwise end up excluded from the batch entirely if 3+
    higher-scoring fresh stories already existed that cycle: by the time
    the priority-ranker sees trending context (agents/priority_ranker.py),
    it's too late, that story was never in the running. Filling the full
    batch_max with every genuinely scored candidate (not just the top 3)
    gives the AI re-rank step, which DOES see trending_headlines, a real
    chance to surface it. Tier 5 (the pure "newest regardless of score"
    last resort) still gates on batch_min only — it exists for when
    there's nothing real to pick from at all, not to pad out a batch that
    already has legitimate candidates.

    Hard, unconditional published_at ceiling (AM1ST's own "iron rule": only
    same-day-ish news, publish nothing rather than something stale) —
    every tier below, including the hot-topic force-include and the
    batch_min last-resort fallback, only ever draws from `candidates` after
    this filter — none of them can bypass it. candidate_max_age_hours is
    reused here deliberately, not a new number: on a genuinely slow news
    day this can leave the batch under batch_min, even empty — that's the
    accepted trade-off, not a bug to work around."""
    pub = config.publish
    now = datetime.now(timezone.utc)
    is_weekday = _is_weekday(now)
    preferred_floor = pub.weekday_min_score if is_weekday else pub.weekend_min_score
    fallback_floor = pub.weekend_min_score

    def hours_old(c: PublishCandidate) -> float:
        return (now - c.published_at).total_seconds() / 3600

    candidates = [c for c in candidates if hours_old(c) <= pub.candidate_max_age_hours]

    fresh = [c for c in candidates if hours_old(c) <= pub.fresh_hours]

    batch: list[PublishCandidate] = sorted(
        (c for c in fresh if c.llm_score >= _TIER1_MIN_SCORE),
        key=lambda c: c.llm_score,
        reverse=True,
    )

    if len(batch) < pub.batch_max:
        picked_ids = {c.page_id for c in batch}
        fill = sorted(
            (c for c in fresh if preferred_floor <= c.llm_score < _TIER1_MIN_SCORE and c.page_id not in picked_ids),
            key=lambda c: c.llm_score,
            reverse=True,
        )
        batch.extend(fill[: pub.batch_max - len(batch)])

    if len(batch) < pub.batch_max:
        picked_ids = {c.page_id for c in batch}
        fill = sorted(
            (c for c in candidates if c.llm_score >= preferred_floor and c.page_id not in picked_ids),
            key=lambda c: c.llm_score,
            reverse=True,
        )
        batch.extend(fill[: pub.batch_max - len(batch)])

    if is_weekday and len(batch) < pub.batch_max:
        # Weekday-only extra fallback: still room in the batch, so relax
        # down to the weekend's lower floor before giving up on score entirely.
        picked_ids = {c.page_id for c in batch}
        fill = sorted(
            (c for c in candidates if fallback_floor <= c.llm_score < preferred_floor and c.page_id not in picked_ids),
            key=lambda c: c.llm_score,
            reverse=True,
        )
        batch.extend(fill[: pub.batch_max - len(batch)])

    if len(batch) < pub.batch_min:
        # Last resort: newest overall, regardless of score.
        batch = sorted(candidates, key=hours_old)[: pub.batch_min]

    # Manual hot-topic force-include (2026-08-31, core/hot_topics.py) — a
    # candidate the user has explicitly flagged as breaking must never be
    # silently excluded from the batch just because its llm_score tier
    # didn't make the cut above; by the time agents/priority_ranker.py sees
    # it, it's too late (same reasoning as the 2026-08-06 tier-cascade
    # change above, applied to a stronger signal). Evicts the current
    # lowest-scored non-hot member if the batch is already full, rather
    # than growing past batch_max.
    picked_ids = {c.page_id for c in batch}
    missing_hot = [c for c in candidates if c.is_hot and c.page_id not in picked_ids]
    for c in missing_hot:
        if len(batch) < pub.batch_max:
            batch.append(c)
            continue
        evict_idx = min(
            (i for i, b in enumerate(batch) if not b.is_hot),
            key=lambda i: batch[i].llm_score,
            default=None,
        )
        if evict_idx is not None:
            batch[evict_idx] = c

    return batch[: pub.batch_max]
