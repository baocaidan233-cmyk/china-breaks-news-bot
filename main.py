"""
China Breaks — a CCP-exposure Gettr news feed, built by porting AM1ST's
current, fully-evolved Python architecture (RSS ingestion -> dedup ->
scoring -> extraction -> content-gen -> Gettr publish) to a new domain.
AM1ST itself is a from-scratch rebuild of an n8n workflow; China Breaks is
instead ported from AM1ST's OWN Python code (not from any n8n export) —
see README for the full lineage and the real Notion/Redis/Gettr facts
carried over from China Breaks' existing, still-running n8n system
(v4.0_chinabreaks_news_to_notion_branch2.json for ingestion,
v3.4_chinabreaks_notion_to_gettr.json for publishing), which this Python
port is meant to eventually replace — not yet, as of this build.

This is the INGESTION cycle only, mirroring AM1ST's own two-cycle design:
this cycle's job ends at writing a candidate into the shared Notion
candidate pool; main_publish.py is the separate, independently-scheduled
process that later reads that pool and decides what (if anything)
actually gets posted.

Pipeline order per cycle (cheapest filter first — mechanism identical to
AM1ST, only the domain and a handful of tuning numbers differ, see
core/config.py):
  load sources (Notion) -> fetch RSS (UTC-normalized, max_publish_age_hours
  publish-age filter, no per-feed or cross-source caps) -> URL-hash Redis dedup
  -> intra-batch semantic CLUSTERING (title+description; groups this
  batch's own candidates into local event clusters, not just a drop/keep
  decision — see Layer 2 below) -> per cluster: read-only peek at
  core/qdrant_store.py's EventStore (a separate Qdrant collection tracking
  heat_score/event_first_seen_at per underlying event, not per article),
  then a second opinion on that cosine match via core/event_identity.py
  (entity-overlap rule tier, LLM only for the residual ambiguous tier —
  cosine similar is not the same claim as same real-world event) for a
  heat_score PREVIEW to feed the AI score gate, plus per-candidate
  cross-cycle Qdrant dedup (vs title+description written in the last 72h)
  -> AI score gate (gpt-4o-mini, >=4) -> for each cluster with at least
  one scoring survivor: COMMIT that cluster's accumulated heat/sources to
  the EventStore (a cluster where nothing cleared the score gate never
  touches it at all) -> write survivors to Notion candidate pool ->
  Qdrant embedding write (the same title+description embeddings used for
  clustering, now persisted for future cross-cycle checks).

Full-text extraction and content generation are deliberately NOT part of
this cycle (same reasoning AM1ST arrived at — see agents/extractor.py's
docstring): every candidate that merely cleared the cheap
title+description score gate would otherwise pay for a full extraction +
LLM-written post, even though only a handful per 30-min publish cycle
would ever actually get posted before aging out of the 12h
candidate-pool window.

Usage:
  python3 main.py              # normal run
  python3 main.py --dry-run    # logs what would be added, never writes to Notion/Qdrant
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import sys
import time
from urllib.parse import urlparse
from collections import Counter
from datetime import datetime, timezone

from dotenv import load_dotenv

from agents.embedder import Embedder
from agents.extractor import Extractor
from agents.og_metadata import fetch_link_preview
from agents.rss_fetcher import fetch_all
from agents.scorer import Scorer
from agents.trending import fetch_trending_headlines
import core.haiku_budget  # noqa: F401 -- Haiku daily budget across the VM's bots (user 10-30), see the module
from core import fulltext
from core.config import load_config
from core.event_identity import EventVerifier, HubIndex, entity_tokens_with_fallback, event_identity_text, event_judge_text, extract_event_frame, has_china_signal, is_offtopic_url_section, log_decision
from core.hashing import cosine_similarity
from core.hot_topics import fetch_active_hot_topics
from core.notion_candidates import write_candidate
from core.notion_sources import load_rss_sources
from core.prescore import PreScorer, log_prescore_decision
from core.roundup import roundup_rule, newsletter_rule
from core.url_blocklist import blocklisted
from core.opinion import opinion_rule
from core.merged_judge import MergedJudge, log_verdict
from core.qdrant_store import EventStore, PostedHistoryStore, QdrantStore, ensure_collection_with_retry
from core.redis_store import RedisStore, StuckVersions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("main")

_TITLE_DUP_STRIP_RE = re.compile(r"<[^>]+>|[^\w]+", re.UNICODE)


def _looks_like_title_duplicate(title: str, description: str) -> bool:
    """True when `description` carries no real information beyond
    restating `title` — 2026-09-07, widening Layer 1.6's backfill trigger
    below. Real miss found via a Qdrant event-store audit: a Google News
    aggregator RSS entry's own "description" field is often just the
    title again, sometimes wrapped in an HTML link — non-empty, so it
    never tripped the OLD `if c.description: continue` skip, starving
    event_identity_text() (title + first sentence of description) of any
    real content. Confirmed root cause of a real production fragmentation
    (a single "China pumps $54B into banks/insurers" story split into 5
    separate chinabreaks_events, because several of its sources' own
    "description" was literally their headline repeated verbatim, giving
    both entity extraction and same_event()'s LLM comparison nothing to
    work with beyond two bare, differently-worded headlines).
    Deliberately loose (HTML-stripped, all punctuation/whitespace
    collapsed to a single separator, case-insensitive containment) rather
    than exact-match — found via testing that a real pair differing only
    by a trailing "- Source Name" dash-attribution (title has it,
    description doesn't) otherwise fails a naive substring check even
    though the description is obviously just the title again. The point
    is "this adds nothing new," and a wrapped/truncated/differently-
    punctuated repeat of the title is just as informationless as an exact
    copy."""
    if not description:
        return True
    clean_title = _TITLE_DUP_STRIP_RE.sub(" ", title).strip().lower()
    clean_desc = _TITLE_DUP_STRIP_RE.sub(" ", description).strip().lower()
    if not clean_title:
        return False
    return clean_title in clean_desc or clean_desc in clean_title


# Summaries (2026-10-26, from AM1ST): what counts as no summary.
_BOILERPLATE = re.compile(r"the post .{0,300} appeared first on .{0,80}$", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")


def _summary_text(description: str) -> str:
    """The summary without tags and without the RSS "The post ... appeared first on ..." line."""
    return _BOILERPLATE.sub("", " ".join(_TAG.sub(" ", description or "").split())).strip()


def _comparable(c) -> bool:
    """Layer 2 compares only articles with a real headline and summary."""
    return len((c.title or "").split()) >= 4 and len(_summary_text(c.description)) >= 40 and "{{" not in (c.description or "")


class _SilentAlerts:
    """agents/extractor.py raises a Notion alert when a cookie source fails; at
    ingestion that is just an article without text, not something to act on."""

    async def alert(self, page_id: str, message: str) -> None:
        return None


async def _fetch_full_text(config, candidates, sources, retried: set[str]):
    """Full text at ingestion (2026-10-26, from AM1ST AI-29, user 10-26: 你先把
    现在的架构移植到CB, 根据CB的频道情况调整): the text of every article that
    reached this point, saved for the publish cycle (core/fulltext.py). An
    article with no fetchable text is not scored. agents/extractor.py already
    renders the page in a browser when the plain fetch fails, so unlike AM1ST
    there is no second, browser-only pass here; what fails waits
    ingestion.fulltext_retry_minutes for one more try, then is dropped. Three
    at a time: the render service is shared with the other bots.
    Placed after this bot's cheap text filters (roundup, pre-score, URL
    section, China signal), so only articles that would be scored are fetched.
    A summary that only repeats the headline, or is shorter than
    ingestion.min_summary_chars, becomes the article's first paragraph.

    Returns the articles with text."""
    floor = config.ingestion.min_summary_chars
    extractor = Extractor(config, _SilentAlerts())
    gate = asyncio.Semaphore(3)
    ok: set[str] = set()
    how: Counter = Counter()
    wait: list = []

    async def fetch(c):
        async with gate:
            try:
                text = await extractor.extract(c.url, sources)
            except Exception:
                logger.exception("run_cycle: extraction failed for %s", c.url)
                text = None
        if not text:
            if c.url in extractor.blocklisted:   # resolved to a blocklisted section: no second try
                how["url_blocklist"] += 1
                return
            if c.url_hash in retried:
                how["given_up"] += 1
                logger.info("run_cycle: %s dropped — no article text on the second try (%s)", c.url, (c.title or "")[:120])
            else:
                how["retry_later"] += 1
                wait.append(c)
            return
        ok.add(c.url_hash)
        fulltext.save(config, c.url_hash, text)
        how["second_try" if c.url_hash in retried else "fetched"] += 1
        if _looks_like_title_duplicate(c.title or "", c.description or "") or len((c.description or "").strip()) < floor:
            paragraphs = [p.strip() for p in text.split("\n") if len(p.strip()) >= 60]
            c.description = (paragraphs[0] if paragraphs else text.strip())[:400]
            how["summary_from_text"] += 1

    await asyncio.gather(*(fetch(c) for c in candidates))
    fulltext.add_retry(config, wait)
    logger.info("run_cycle: full text for %d of %d: %s", len(ok), len(candidates), dict(how))
    return [c for c in candidates if c.url_hash in ok]


async def run_cycle(
    config, redis_store, qdrant_store, event_store, embedder, scorer, hub_index, event_verifier, dry_run,
    prescorer=None, merged_judge=None,
) -> None:
    sources = await load_rss_sources(config)
    if not sources:
        logger.info("run_cycle: no in_use sources — nothing to do")
        return

    candidates = await fetch_all(config, sources)
    if not candidates:
        return
    if config.blocked_domains:
        def _blocked(url: str) -> bool:
            host = (urlparse(url).hostname or "").lower()
            return any(host == d or host.endswith("." + d) for d in config.blocked_domains)
        kept = [c for c in candidates if not _blocked(c.url)]
        if len(kept) != len(candidates):
            logger.info("run_cycle: %d/%d dropped — blocked domain (%s)", len(candidates) - len(kept), len(candidates), ", ".join(config.blocked_domains))
        candidates = kept

    # --- Layer 1: exact-duplicate URL-hash dedup (Redis) ---
    survivors = [c for c in candidates if await redis_store.claim_new(c.url_hash)]
    logger.info("run_cycle: %d/%d new after URL-hash dedup", len(survivors), len(candidates))
    pruned = fulltext.prune(config)
    if pruned:
        logger.info("run_cycle: pruned %d saved full text(s)", pruned)
    if not survivors:
        return

    # --- Layer 1.5: no digests, rolling live pages or link lists
    # (2026-09-29, core/roundup.py) — user rule: a post has to be about one
    # thing, and a page that is several unrelated stories cannot be written
    # into one caption without the caption picking one and dropping the rest.
    # First, so a dropped page costs not even the og:description fetch. ---
    before_roundup = len(survivors)
    kept = []
    for c in survivors:
        rule = roundup_rule(c.title or "", c.url) or opinion_rule(c.title or "") or newsletter_rule(c.description or "", c.url)
        if rule:
            logger.info("run_cycle: %s dropped — %s (%s)", c.url, rule, (c.title or "")[:120])
        else:
            kept.append(c)
    survivors = kept
    if len(survivors) != before_roundup:
        logger.info("run_cycle: %d/%d survive the roundup filter", len(survivors), before_roundup)
    if not survivors:
        return

    # --- Layer 1.55: embedding pre-score (2026-09-30, core/prescore.py) —
    # drop a candidate whose title the model is confident cannot reach the
    # pool gate, before anything expensive touches it. It sits here rather
    # than beside the Scorer on purpose: the layers below spend an HTTP fetch,
    # a clustering embed and, when the rule layer is unsure, an LLM dedup
    # judge on every candidate that gets this far, and one dropped here costs
    # none of them. Title embeddings go out in one batched request. Any
    # failure scores everyone. ---
    if prescorer is not None and prescorer.enabled:
        try:
            vectors = await embedder.embed_many([(c.title or " ")[:500] for c in survivors])
            kept, decisions = [], Counter()
            for c, v in zip(survivors, vectors):
                decision, detail = prescorer.decide(c.url_hash, c.title or "", v)
                decisions[decision] += 1
                log_prescore_decision(config.prescore.log_path,
                                      {"url": c.url, "title": (c.title or "")[:160],
                                       "decision": decision, **detail})
                if decision != "skip":
                    kept.append(c)
            logger.info("run_cycle: pre-score %s (model %s) — %d/%d go on to be scored",
                        dict(decisions), prescorer.version, len(kept), len(survivors))
            survivors = kept
        except Exception:
            logger.exception("run_cycle: pre-score failed — scoring everything this cycle")
        if not survivors:
            return

    # --- Layer 1.6: description backfill via the article page's own
    # og:description meta tag (2026-08-07) — some RSS feeds give a title
    # with no description at all, which under-informs both clustering
    # (embeds on title alone) and the AI score gate (see
    # project_am1st_migration memory's "GOP donor memo scored only 5"
    # investigation). This is deliberately just the page's existing
    # meta-description tag (agents/og_metadata.py, already used for Gettr
    # preview cards) — NOT full-article extraction, which the user
    # explicitly rejected as a fix for this same problem and which stays
    # deferred to the publish cycle for cost reasons (agents/extractor.py's
    # docstring). A failed/empty fetch just leaves the candidate as-is. ---
    backfilled = 0
    empty_before = sum(1 for c in survivors if _looks_like_title_duplicate(c.title, c.description))
    for c in survivors:
        if not _looks_like_title_duplicate(c.title, c.description):
            continue
        try:
            og = await fetch_link_preview(c.url)
        except Exception:
            continue
        desc = og.get("prev_desc")
        if desc and not _looks_like_title_duplicate(c.title, desc):
            c.description = desc
            backfilled += 1
    if empty_before:
        logger.info("run_cycle: backfilled og:description for %d/%d description-less/title-duplicate candidate(s)", backfilled, empty_before)


    # 2026-09-14 — moved here from just before Scorer.score() below: this
    # was previously the LAST gate before the LLM call, but it's a pure
    # per-item text/URL check with no dependency on anything computed by
    # the semantic-dedup layer beneath it — running it here instead means
    # the ~70% of items with no China/CCP signal never pay for an
    # embedding call either (real audit found embeddings, not chat calls,
    # were this pipeline's single largest OpenAI call count). Doesn't
    # change which items ever reach the Scorer — has_china_signal() is a
    # deterministic per-item check, unaffected by clustering order — only
    # when the wasted ones get dropped. doc_freq/doc_count above are
    # deliberately computed from the FULL pre-filter survivors list, not
    # this narrower pool: verify_compatibility()'s lexical fallback needs a
    # genuinely generic background-vocabulary sample, and a pool already
    # narrowed to China/CCP-relevant text would make a biased one.
    scoring_pool = []
    offtopic_url_count = 0
    prefiltered_count = 0
    blocklist_count = 0
    for c in survivors:
        if is_offtopic_url_section(c.url):
            offtopic_url_count += 1
            continue
        # 2026-10-31 (core/url_blocklist.py): a Google News link is checked
        # again by the extractor once it resolves to the publisher.
        if blocklisted(c.url):
            blocklist_count += 1
            continue
        candidate_text = event_identity_text(c.title, c.description)
        if not has_china_signal(candidate_text):
            prefiltered_count += 1
            log_decision(config, {
                "check_type": "prefilter_reject",
                "candidate_url": c.url,
                "candidate_text": candidate_text,
            })
            continue
        scoring_pool.append(c)
    if offtopic_url_count:
        logger.info("run_cycle: %d/%d dropped — off-mission URL section (entertainment/lifestyle/etc.)", offtopic_url_count, len(survivors))
    if blocklist_count:
        logger.info("run_cycle: %d/%d dropped — URL on the blocklist", blocklist_count, len(survivors))
    if prefiltered_count:
        logger.info(
            "run_cycle: %d/%d skipped Scorer LLM call (no China/CCP signal, pre-filter)",
            prefiltered_count, len(survivors) - offtopic_url_count,
        )

    # --- Layer 1.8: full text (2026-10-26, from AM1ST AI-29) — no text, no
    # scoring; saved for the publish cycle. See _fetch_full_text(). ---
    # Articles whose text failed an hour ago get their second try here (they
    # passed the filters above then). Taken only now, so a cycle that ends
    # earlier leaves them waiting for the next one.
    retry = fulltext.take_due(config)
    if retry:
        logger.info("run_cycle: %d article(s) back for a second full-text try", len(retry))
    scoring_pool = await _fetch_full_text(config, scoring_pool + retry, sources, {c.url_hash for c in retry})
    if not scoring_pool:
        return

    # Manual breaking-news override (2026-08-31, core/hot_topics.py) — every
    # currently-live flag for this bot's own channel, embedded once per
    # cycle. Empty in the common case (no flag active right now); a failed
    # Notion read also comes back empty (fail open), so this never blocks
    # the cycle. See core/config.py's HotTopicsConfig docstring.
    hot_topic_texts = await fetch_active_hot_topics(config)
    hot_topics: list[tuple[str, list[float]]] = []
    for text in hot_topic_texts:
        try:
            hot_topics.append((text, await embedder.embed(text[:2000])))
        except Exception:
            logger.exception("run_cycle: failed to embed hot-topic flag %r, skipping it this cycle", text)
    if hot_topics:
        logger.info("run_cycle: %d active hot-topic flag(s) this cycle", len(hot_topics))

    # --- Layer 2: the same article inside this batch, by vector (2026-10-26,
    # from AM1ST AI-69). Only near-verbatim copies: cosine >=
    # ingestion.near_verbatim_cosine, and only between two articles that both
    # have a real headline (4+ words) and summary. Other outlets' reports of the
    # same event go on to the merged judge below. Replaces this bot's
    # intra-batch clustering, its event-store peek/judge, classify_subtype, the
    # event guards and cross-cycle dedup. ---
    near_verbatim = config.ingestion.near_verbatim_cosine
    texts = [f"{c.title}\n{c.description}"[:6000] for c in scoring_pool]
    try:
        vectors = await embedder.embed_many(texts)
    except Exception:
        logger.exception("run_cycle: batched embedding failed, embedding one by one")
        vectors = []
        for t in texts:
            try:
                vectors.append(await embedder.embed(t))
            except Exception:
                vectors.append(None)
    embedded: list[tuple] = []  # (candidate, embedding)
    for c, embedding in zip(scoring_pool, vectors):
        if embedding is None:
            logger.warning("run_cycle: %s dropped — embedding failed", c.url)
            continue
        twin = None
        if _comparable(c):
            for other, other_embedding in embedded:
                if _comparable(other) and cosine_similarity(embedding, other_embedding) >= near_verbatim:
                    twin = other
                    break
        if twin is not None:
            logger.info("run_cycle: %s dropped — same article as %s in this batch (cosine >= %.2f)", c.url, twin.url, near_verbatim)
            continue
        embedded.append((c, embedding))
    logger.info("run_cycle: %d/%d survive the same-article check", len(embedded), len(scoring_pool))

    # Manual hot-topic flags, per article (was per cluster; same threshold and log).
    hot_untils: dict[str, int] = {}
    if hot_topics:
        for c, embedding in embedded:
            best_hot_score, best_hot_text = max(
                ((cosine_similarity(embedding, emb), text) for text, emb in hot_topics), key=lambda x: x[0],
            )
            is_hot_match = best_hot_score >= config.hot_topics.match_threshold
            log_decision(config, {
                "check_type": "hot_topic_match", "cluster_representative_url": c.url,
                "best_hot_score": best_hot_score, "matched_flag_text": best_hot_text,
                "threshold": config.hot_topics.match_threshold, "matched": is_hot_match,
            })
            if is_hot_match:
                hot_untils[c.url_hash] = int(time.time()) + config.hot_topics.ttl_hours * 3600
                logger.info("run_cycle: %s matched an active hot-topic flag (%.3f)", c.url, best_hot_score)

    # --- Score gate (unchanged: gpt-4o-mini with this bot's own China/CCP
    # trending headlines). The scorer now runs before the event store is
    # consulted, so heat_score is 1.0 and "hours since first seen" is the
    # article's own age, as in AM1ST since 10-21; the publish cycle's 48h
    # timeliness check covers old news. ---
    trending_headlines = await fetch_trending_headlines()
    passed: list[tuple] = []  # (candidate, embedding)
    for c, embedding in embedded:
        try:
            score_output = await scorer.score(c, trending_headlines)
            if score_output is None:
                continue
            c.llm_score = score_output.llm_score
            c.llm_comment = score_output.llm_comment
            if c.llm_score >= config.openai.score_threshold:
                passed.append((c, embedding))
            else:
                logger.info("run_cycle: %s scored %.1f, below threshold — %s",
                            c.url, c.llm_score, (c.title or "")[:160])
        except Exception:
            logger.exception("run_cycle: unhandled error scoring %s, skipping this item", c.url)

    # --- Merged judge (2026-10-26, core/merged_judge.py, from AM1ST AI-69) ---
    # One Haiku call per article that cleared the score gate, oldest first and
    # one at a time, so a later article sees the earlier ones' verdicts: which
    # event it belongs to (or a new one); whether it tells our readers anything
    # our posts have not (NOTHING_NEW / NOT_WORTH with a named post -> dropped);
    # whether the same update is already waiting in Notion (-> recorded in the
    # event store only). Anything the judge cannot read or answer keeps the
    # article (fails open). This bot keeps no relay-site list, so a later report
    # of a queued update is only counted as one more version, never swapped in.
    passed.sort(key=lambda pair: pair[0].published_at)
    judge_ready = bool(merged_judge is not None and config.merged_judge.enabled and passed
                       and await merged_judge.prepare_cycle())
    added_count = folded_count = dropped_count = 0
    for c, embedding in passed:
        verdict = None
        if judge_ready:
            try:
                verdict = await merged_judge.judge(c.url, c.title, c.description, embedding)
            except Exception:
                logger.exception("run_cycle: merged judge failed on %s — kept", c.url)
            if verdict is not None:
                log_verdict(config, verdict.record)
        action = verdict.action if verdict is not None else "keep"
        if action == "drop":
            dropped_count += 1
            logger.info("run_cycle: %s dropped — %s, our post(s) %s already told readers (%s)", c.url, verdict.relation,
                        ",".join(verdict.record.get("covered_by", [])), (c.title or "")[:120])
        if dry_run:
            if action != "drop":
                logger.info("run_cycle: [dry-run] would %s: %s (score=%.1f, event=%s)",
                            "record in the event store only" if action == "fold" else "add to candidate pool",
                            c.url, c.llm_score, (verdict.event_id if verdict else None) or "new")
                if action == "fold":
                    folded_count += 1
                else:
                    added_count += 1
            continue
        try:
            matched = await event_store.get_by_id(verdict.event_id) if verdict is not None and verdict.event_id else None
            pub = int(c.published_at.timestamp())
            rep_text = event_identity_text(c.title, c.description)
            own_text = event_judge_text(c.title, c.description)
            seed_frame = {**extract_event_frame(rep_text), "canonical_title": c.title,
                          "canonical_summary": c.description or c.title}
            timeline_entry = ({"ts": pub, "source": c.source_name, "url": c.url, "summary": c.title}
                              if matched is not None and verdict.relation == "HAS_NEW" else None)
            tokens = entity_tokens_with_fallback(c.title, c.description)
            heat_score, first_seen_unix, event_id, core_entities, hot_until = await event_store.commit(
                matched, {c.source_name}, c.source_name, pub,
                representative=(embedding, c.source_name, pub, rep_text, tokens, c.url, own_text),
                extra_points=[], related_links=None, seed_frame=seed_frame, heat_multiplier=1.0,
                hot_until=hot_untils.get(c.url_hash, 0), timeline_entry=timeline_entry,
                in_notion=action == "keep",
                relation=verdict.relation if verdict is not None and verdict.relation != "ERROR" else None,
                new_info=(verdict.record.get("new_info", "") if verdict is not None and verdict.relation == "HAS_NEW" else ""),
                score=c.llm_score,
            )
            await hub_index.bump(event_id, core_entities)
            if judge_ready:
                merged_judge.note_added(c.url, own_text, embedding, action == "keep", event_id)
            if action == "fold" and verdict.fold_url:
                await event_store.add_version(verdict.fold_url)
        except Exception:
            logger.exception("run_cycle: event-store commit failed for %s, skipping this item", c.url)
            continue
        if action == "drop":
            continue
        if action == "fold":
            folded_count += 1
            logger.info("run_cycle: %s recorded in the event store only — same update as %s, already in Notion",
                        c.url, verdict.fold_url)
            continue

        # Cross-event storyline linking (2026-08-31, unchanged): this article's
        # specific entity tokens, reverse-looked-up against other events.
        this_event_title = (matched or {}).get("canonical_title", "") or c.title
        candidate_event_ids: set[str] = set()
        for tok in tokens:
            if await hub_index.token_score(tok) < config.entity_verifier.hub_event_count_threshold:
                candidate_event_ids |= await hub_index.token_events(tok)
        candidate_event_ids.discard(event_id)
        for other_event_id in candidate_event_ids:
            other = await event_store.get_by_id(other_event_id)
            if other is None:
                continue
            other_text = other.get("representative_text", "") or other.get("canonical_summary", "")
            related, llm_raw = await event_verifier.related_event(other_text, rep_text)
            log_decision(config, {
                "event_id": event_id, "linked_candidate_event_id": other_event_id,
                "llm_related_raw": llm_raw, "final_verdict": "RELATED" if related else "NOT_RELATED",
                "candidate_text": rep_text, "matched_representative_text": other_text,
            })
            if related:
                other_title = other.get("canonical_title", "") or other_text
                await event_store.link_related_events(event_id, this_event_title, other_event_id, other_title)
                logger.info("run_cycle: linked event %s <-> %s as related storyline", event_id, other_event_id)

        try:
            c.heat_score = heat_score
            c.event_first_seen_at = datetime.fromtimestamp(first_seen_unix, tz=timezone.utc)
            c.is_hot = hot_until > int(time.time())
            if not await write_candidate(config, c):
                logger.warning("run_cycle: candidate-pool write failed for %s", c.url)
                continue
            await qdrant_store.write_embedding(
                c.url, c.url_hash, f"{c.title}\n{c.description}"[:6000], int(c.published_at.timestamp()), embedding,
            )
            added_count += 1
            logger.info("run_cycle: added to candidate pool: %s (score=%.1f, event=%s)",
                        c.url, c.llm_score, "existing" if matched is not None else "new")
        except Exception:
            logger.exception("run_cycle: unhandled error writing %s, skipping this item", c.url)

    logger.info("run_cycle: %d added to candidate pool, %d recorded in the event store only, %d dropped by the merged judge this cycle",
                added_count, folded_count, dropped_count)


async def main() -> None:
    load_dotenv()
    dry_run = "--dry-run" in sys.argv

    config = load_config("config/config.yaml")

    redis_store = RedisStore(config)
    qdrant_store = QdrantStore(config)
    event_store = EventStore(config)
    hub_index = HubIndex(config)
    event_verifier = EventVerifier(config)
    await ensure_collection_with_retry(qdrant_store, "chinabreaks_embeddings")
    await ensure_collection_with_retry(event_store, "chinabreaks_events")
    embedder = Embedder(config)
    scorer = Scorer(config)
    prescorer = PreScorer(config)
    # The merged judge (2026-10-26, core/merged_judge.py): our posts are read
    # from the publish cycle's posted-history collection, never written here.
    posted_store = PostedHistoryStore(config)
    stuck_versions = StuckVersions(config)
    await ensure_collection_with_retry(posted_store, "china_break_news_posting_embedding")
    merged_judge = MergedJudge(config, event_store, posted_store, qdrant_store, stuck_versions)

    if dry_run:
        logger.info("Running in --dry-run mode: Notion/Qdrant writes will be logged, not sent")

    try:
        while True:
            started = time.monotonic()
            try:
                await asyncio.wait_for(
                    run_cycle(config, redis_store, qdrant_store, event_store, embedder, scorer, hub_index, event_verifier, dry_run, prescorer,
                              merged_judge),
                    timeout=config.cycle_timeout_seconds,
                )
            except asyncio.TimeoutError:
                # This process self-loops (see module docstring) instead of
                # being re-triggered externally like the old n8n workflow —
                # without this cutoff, one truly stuck cycle would freeze
                # every future cycle forever, since nothing else would ever
                # call run_cycle() again (2026-08-12 discussion).
                logger.error("run_cycle exceeded %ds — cutting it off, will retry next cycle", config.cycle_timeout_seconds)
            except Exception:
                logger.exception("run_cycle failed")
            logger.info("run_cycle: cycle took %.1fs", time.monotonic() - started)
            jitter = config.poll_interval_seconds * random.uniform(-0.1, 0.1)
            await asyncio.sleep(config.poll_interval_seconds + jitter)
    finally:
        await redis_store.close()
        await qdrant_store.close()
        await event_store.close()
        await posted_store.close()
        await hub_index.close()
        await stuck_versions.close()


if __name__ == "__main__":
    asyncio.run(main())
