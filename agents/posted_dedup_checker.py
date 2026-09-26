from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from agents.embedder import Embedder
from core.config import AppConfig
from core.event_identity import EventVerifier, entity_tokens, event_identity_text, has_date_conflict, log_decision
from core.models import PublishCandidate
from core.qdrant_store import PostedHistoryStore

logger = logging.getLogger(__name__)


def content_for_embedding(post_content: str, url: str) -> str:
    """post_content always has "\\n\\n{url}" appended after generation (see
    main_publish.py's run_cycle) — needed for the actual Gettr post, but
    embedding the literal URL string dilutes the semantic dedup signal.
    Real case caught 2026-08-06: two different sources' takes on the exact
    same event (a 2020 Maricopa County voter-data hack) scored 0.731 on
    caption text alone — comfortably over the 0.70 duplicate threshold —
    but only 0.698 with the URL included, missing the duplicate entirely.
    Strips the exact suffix that was appended, so dedup compares
    like-for-like; returns the input unchanged if that suffix isn't
    present (defensive, shouldn't happen given how post_content is built)."""
    suffix = f"\n\n{url}"
    return post_content[: -len(suffix)] if post_content.endswith(suffix) else post_content


async def find_publishable(
    ranked_batch: list[PublishCandidate],
    embedder: Embedder,
    posted_store: PostedHistoryStore,
    event_verifier: EventVerifier,
    config: AppConfig,
    on_duplicate: Callable[[PublishCandidate], Awaitable[None]] | None = None,
) -> PublishCandidate | None:
    """Walks `ranked_batch` in priority order (highest first) and returns the
    first candidate that is NOT a near-duplicate of something this channel
    already posted in the last publish.posted_dedup_window_hours. Duplicates
    (and candidates whose dedup check itself failed — see Fallback below)
    are skipped — never causes the whole cycle to abort. Returns None only if every candidate in the batch was a
    duplicate or errored (or the batch is empty) — the correct outcome for
    an actual all-duplicate batch is "publish nothing this cycle", not a
    fallback that fakes freshness.

    2026-09-06, ported from AM1ST's own 2026-09-05 fix: cosine similarity
    alone no longer decides a duplicate — it only decides whether to ASK. A
    real production audit on AM1ST's own feed found cosine > threshold
    flags genuine next-stage developments in an ongoing story as duplicates
    of the earlier stage just as often as it flags actual reprints (a state
    court ruling vs. its own federal appeal; a construction announcement vs.
    a legal challenge trying to halt it — both got silently dropped as
    duplicates in production, cascading the actual winner down to a much
    weaker, unrelated story). The exact same failure mode applies here: a
    CCP sanctions package vs. a court challenge to it, or a Belt-and-Road
    financing announcement vs. a recipient country's own confirmation, look
    equally similar in embedding space to cosine alone.

    core/event_identity.py's EventVerifier.same_event() already exists for
    exactly this question (used at ingestion time to decide whether two
    articles describe the same occurrence) and already carries this
    project's stage-distinction guidance (prompts/same_event_prompt.txt) —
    reused here as-is, no new prompt, no keyword heuristics. Only called
    when cosine > threshold (rare per publish cycle), so the added cost is
    negligible.

    Rule-tier pre-filter: before asking the LLM, check has_date_conflict()
    (core/event_identity.py) — if both sides name a single explicit "Month
    Day" date and they disagree, that's strong independent evidence of a
    different specific occurrence regardless of how similar the text
    otherwise reads, so skip the LLM call and treat as NOT duplicate
    outright. Deliberately NOT adding the mirror-image shortcut (skip the
    LLM and assume duplicate above some high cosine/entity-overlap cutoff)
    — AM1ST's own same-day data argued against it: a real story scored
    0.738-0.747 cosine against an older, unrelated story across several
    cycles (all wrongly auto-flagged "duplicate" under a pure-cosine rule)
    before same_event() correctly called it DIFFERENT_EVENT at a similar
    score — i.e. the exact cosine/entity-overlap range that would tempt a
    "confident duplicate" shortcut is also where a real false positive just
    happened. Revisit only once enough same_event()-adjudicated
    posted_dedup pairs accumulate to bucket-calibrate a genuinely safe
    floor, the same way no_overlap_llm_review_floor was calibrated — not
    before, and not by copying AM1ST's own number blind.

    Fallback: same_event() is a real OpenAI call with no fail-open wrapper
    of its own — any error (a timeout or a malformed response) propagates.
    Without this fallback, that exception would escape find_publishable()
    entirely and abort the WHOLE cycle via main_publish.py's run_cycle()
    exception handler — turning one transient API hiccup on candidate #1
    into zero publishes this cycle, even if candidates #2-#10 never needed
    an LLM call at all. Each candidate's whole dedup check (embedding,
    posted-history lookup, same_event()) is now wrapped in its own
    try/except: on any failure, that ONE candidate is skipped (logged as
    check_type=posted_dedup_error, not conflated with a real "duplicate"
    verdict) and the walk continues to the next-ranked candidate, so the
    cycle still very likely finds something to publish.

    2026-09-07: gray-zone entity-overlap widening added below `threshold`
    itself — real production miss found the same day: two Taiwan Coast
    Guard articles about the literal same incident (same vessel "3501",
    same responding ship, same location, same sortie count) scored 0.6986
    cosine against each other, missing the 0.70 cutoff by 0.0014, so
    `looks_similar` was False and neither has_date_conflict() nor
    same_event() ever got a chance to weigh in — both got published as
    separate posts 37 minutes apart. Same root cause and same fix shape as
    core/event_identity.py's cross_cycle_verdict() (built the same day
    for the ingestion side): a near-miss cosine score with real entity
    overlap still deserves the real adjudication tier below, not an
    automatic "not similar enough, keep it." This only widens WHEN to ask
    — it does not add a shortcut to assume duplicate, so the "don't guess
    on the high side" reasoning above still holds unchanged.

    2026-09-25, three changes, each from a measured failure:

    1. Every one of the top-5 posted matches is checked, not just the best.
       Before, a real duplicate that ranked second behind an unrelated post
       with closer caption wording was never looked at. Matches are walked
       most-similar first and the first confirmed duplicate ends the walk,
       so a candidate with one near match costs what it did before.

    2. The gray zone's same_event() call compares the two SOURCE articles'
       title + lead sentence (event_identity_text(), the ingestion side's
       own event-identity input) instead of our two captions. Every caption
       comes out of the same writer prompt with the same fixed opening, so
       two unrelated stories read alike to the judge. On 74 hand-labelled
       gray-zone pairs (2026-09-23, VM-02 ~/dedup_audit_20260923/) this took
       the judge from 58% to 81% correct, false kills 26 -> 6, misses 5 -> 8,
       McNemar p = 0.0015. Only the gray zone: pairs above the cosine
       threshold were never measured, so they still compare captions. A
       posted point written before its source text was stored falls back to
       captions too.

    2026-09-26: ranks 2-5 are only put to the judge at cosine >=
    publish.posted_dedup_other_match_floor (0.75). The closest match keeps
    the full gray zone.

    3. on_duplicate is awaited once per confirmed duplicate verdict. It is
       how main_publish.py retires a candidate that keeps coming back with
       the same verdict (3199 logged verdicts: 1855 re-judgments after a
       first duplicate, and 73 candidates published only after 1-8 straight
       duplicate verdicts — re-rolled until one slipped through). It is a
       callback so this module keeps knowing nothing about Notion, it is not
       called on the error path (an errored check is not a verdict), and a
       failing callback never costs the cycle its publish."""
    threshold = config.publish.posted_dedup_threshold
    gray_zone_floor = config.heat.related_threshold  # 0.6 — same constant the ingestion-side gray zone uses
    other_match_floor = config.publish.posted_dedup_other_match_floor  # 0.75 — see below

    for candidate in ranked_batch:
        comparisons: list[dict] = []
        deciding: dict | None = None
        top: dict | None = None
        try:
            candidate_content = content_for_embedding(candidate.post_content, candidate.url)
            candidate_source = event_identity_text(candidate.title, candidate.description)
            embedding = await embedder.embed(candidate_content)
            matches = await posted_store.similar_recent(embedding)
            top = matches[0] if matches else None
            candidate_entities = entity_tokens(candidate_content) if matches else set()

            for rank, m in enumerate(matches):
                if not m["url"]:
                    continue
                similarity = m["score"]
                # 2026-09-26: below the closest match, only a strong
                # similarity is worth a judge call. Every extra call is one
                # more chance for the judge to wrongly say "same": in the
                # first 19 hours of the top-5 walk, all the duplicates it
                # found at ranks 2-5 scored 0.62-0.67, and every one checked
                # was a different story. See PublishConfig.
                if rank > 0 and similarity < other_match_floor:
                    continue
                matched_content = content_for_embedding(m["content"], m["url"])
                cosine_flagged = similarity > threshold
                matched_entities: set[str] = set()
                in_gray_zone = False
                if not cosine_flagged and gray_zone_floor <= similarity:
                    matched_entities = entity_tokens(matched_content)
                    in_gray_zone = bool(candidate_entities & matched_entities)
                if not (cosine_flagged or in_gray_zone):
                    continue
                if not matched_entities:
                    matched_entities = entity_tokens(matched_content)

                use_source = in_gray_zone and bool(m["title"]) and bool(candidate.title)
                text_a = candidate_source if use_source else candidate_content
                text_b = event_identity_text(m["title"], m["description"]) if use_source else matched_content
                if has_date_conflict(candidate_content, matched_content):
                    is_duplicate = False
                    same_event_raw = "RULE: has_date_conflict() — explicit conflicting dates, skipped LLM call"
                    resolved_by = "date_conflict_rule"
                else:
                    is_duplicate, same_event_raw = await event_verifier.same_event(text_a, text_b)
                    resolved_by = "llm"
                comparison = {
                    "matched_url": m["url"],
                    "cosine_score": similarity,
                    "cosine_flagged": cosine_flagged,
                    "gray_zone_entity_overlap": in_gray_zone,
                    "judged_on": "source_title_lead" if use_source else "caption",
                    "resolved_by": resolved_by,
                    "same_event_raw": same_event_raw,
                    "verdict": "duplicate" if is_duplicate else "kept",
                    "matched_entities": sorted(matched_entities),
                    "entity_overlap": sorted(candidate_entities & matched_entities),
                    # The two texts exactly as same_event()/the rule tier saw
                    # them, so a verdict can be replayed offline.
                    "candidate_text": text_a,
                    "matched_text": text_b,
                }
                comparisons.append(comparison)
                if is_duplicate:
                    deciding = comparison
                    break
        except Exception:
            logger.exception(
                "find_publishable: dedup check failed for %s — skipping this candidate (not a confirmed verdict), trying next",
                candidate.url,
            )
            log_decision(config, {"check_type": "posted_dedup_error", "candidate_url": candidate.url})
            continue

        is_duplicate = deciding is not None
        if top is not None:
            # Top-level fields describe the comparison that decided the
            # verdict (the duplicate hit, else the first one asked, else the
            # top match) so older replay scripts keep reading the same shape;
            # `comparisons` holds every match that was actually asked about.
            shown = deciding or (comparisons[0] if comparisons else None)
            log_record = {
                "check_type": "posted_dedup",
                "candidate_url": candidate.url,
                "matched_url": shown["matched_url"] if shown else top["url"],
                "cosine_score": shown["cosine_score"] if shown else top["score"],
                "threshold": threshold,
                "cosine_flagged": shown["cosine_flagged"] if shown else top["score"] > threshold,
                "gray_zone_entity_overlap": shown["gray_zone_entity_overlap"] if shown else False,
                "final_verdict": "duplicate" if is_duplicate else "kept",
                "matches_seen": len(matches),
                "comparisons_asked": len(comparisons),
            }
            if shown:
                log_record.update({
                    "resolved_by": shown["resolved_by"],
                    "judged_on": shown["judged_on"],
                    "same_event_raw": shown["same_event_raw"],
                    "candidate_entities": sorted(candidate_entities),
                    "matched_entities": shown["matched_entities"],
                    "entity_overlap": shown["entity_overlap"],
                    "candidate_text": shown["candidate_text"],
                    "matched_text": shown["matched_text"],
                    "comparisons": comparisons,
                })
            log_decision(config, log_record)

        if is_duplicate:
            logger.info(
                "find_publishable: %s dropped — same_event() confirmed duplicate of already-posted content (cosine=%.3f, threshold=%.2f, gray_zone_entity_overlap=%s, judged_on=%s, match %d of %d, matched %s)",
                candidate.url,
                deciding["cosine_score"],
                threshold,
                deciding["gray_zone_entity_overlap"],
                deciding["judged_on"],
                len(comparisons),
                len(matches),
                deciding["matched_url"],
            )
            if on_duplicate is not None:
                try:
                    await on_duplicate(candidate)
                except Exception:
                    logger.exception("find_publishable: on_duplicate callback failed for %s — continuing", candidate.url)
            continue
        if comparisons:
            logger.info(
                "find_publishable: %s flagged against %d posted match(es) but every one was judged DIFFERENT — not treating as duplicate (closest %s, cosine=%.3f)",
                candidate.url,
                len(comparisons),
                comparisons[0]["matched_url"],
                comparisons[0]["cosine_score"],
            )

        logger.info("find_publishable: %s selected (priority_score=%.1f)", candidate.url, candidate.priority_score)
        return candidate

    logger.info("find_publishable: all %d candidate(s) were duplicates — nothing to publish this cycle", len(ranked_batch))
    return None
