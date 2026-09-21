"""
China Breaks — the separate publish cycle, ported from AM1ST's own
main_publish.py (which itself was ported from v1.4_am1st_notion_to_gettr_auto
posting.json). Runs independently of main.py's ingestion cycle, same as
AM1ST: main.py only ever writes candidates into the shared Notion
candidate pool; this process is the only thing that ever reads that pool
to actually pick something to post.

Cycle order (every publish.interval_seconds by default, 30 min — but see
compute_dynamic_interval() below, 2026-09-06, ported from AM1ST: the
actual wait each cycle scales 0.6x-1.3x that base on real-time news
volume, clamped to [15min, 39min], on top of which core/hot_topics.py's
manual fast lane can still cut things short for a human-flagged breaking
story; a cycle that finds nothing worth publishing just publishes
nothing — see run_cycle()'s widen-on-empty give-up, not a longer wait
until the next check):
  query eligible candidates (Notion: not sent, extraction not previously
     failed, this channel's own channel_name tag, <=24h old at the query
     level, llm_score>=4/5 depending on weekday/weekend — select_batch()
     then applies the real day-aware freshness ceiling: 12h on weekdays,
     24h on weekends, see agents/candidate_selector.py)
  -> tiered batch selection (fresh+high-score preferred, cascading
     fallback, 3-10 candidates) -> extraction/content-gen/rank/dedup on
     that batch; if nothing survives to publish, widen to the next
     batch_max-sized chunk of the still-untried eligible pool and repeat,
     up to publish.max_widen_attempts times (2026-09-06, ported from
     AM1ST — see run_cycle())
  -> full-text extraction + content generation for just this small batch
     (see agents/extractor.py's docstring for why this isn't done at
     ingestion time), dropping anything the writer judges "No comment".
     A candidate whose extraction fails is permanently excluded from
     future cycles (core/notion_candidates.py's mark_extraction_failed(),
     2026-09-06, ported from AM1ST) rather than re-tried every cycle.
  -> staleness classification (agents/staleness_checker.py, 2026-09-06,
     ported from AM1ST) — a separate LLM call, gated behind a free
     event_first_seen_at pre-filter, that classifies FRESH/OPINION/STALE
     before the Writer runs; STALE is dropped, OPINION is written but
     framed as analysis rather than breaking news
  -> deterministic priority formula (agents/priority_ranker.py, 2026-09-06,
     ported from AM1ST's own rewrite of its earlier LLM-based re-rank
     call, found unstable in production): llm_score + trending_bonus -
     freshness_penalty, given a read-only snapshot of Google News' current
     top world/China headlines as trending context (see
     agents/trending.py — never ingested/scored/published from directly)
  -> walk the ranked list, skipping anything that's a near-duplicate of
     content this channel already posted in the last 10 days (cosine
     threshold 0.70 — stricter than the ingestion side's 0.8, deliberately,
     since this is a fully-autonomous post) AND that
     core/event_identity.py's EventVerifier.same_event() also confirms is
     the same real-world occurrence, not just a lexically-similar
     next-stage development (2026-09-06, ported from AM1ST — see
     agents/posted_dedup_checker.py's docstring)
  -> the first survivor is the winner; mark it sent + record its embedding
     in the posted-history collection.

The Gettr publish call uses agents/gettr_publisher.py's GettrPublisher,
ported unchanged from AM1ST — text-only post.

Also fetches OG link-preview metadata (agents/og_metadata.py) for the
winner's own article URL right before publishing, so the post shows a
real preview card instead of a bare appended URL with no card — see
agents/gettr_publisher.py's docstring for the field names involved.

Three sources are the exception (2026-09-21): a Google News, SCMP or
ZeroHedge winner gets no URL appended and no link preview at all, and
instead carries a 1:1 card this bot draws and uploads itself. The card
is a split card (headline over the article's own photo) when
agents/card_photo.py could get a usable photo — SCMP and ZeroHedge, in
practice — and a text card when it could not, which is every Google
News item. Its headline is written by agents/card_headline.py rather
than excerpted from the caption. See agents/headline_card.py for which
sources and why, and _build_headline_card() below for the fallback when
the card can't be made at all.

The posted-dedup embedding (both the check in find_publishable and the
final write below) uses agents/posted_dedup_checker.py's
content_for_embedding() to strip the appended "\n\n{url}" suffix before
embedding — ported from AM1ST, where the literal URL text was found to
dilute the similarity score of a real duplicate pair just under the 0.70
threshold. The URL itself is still appended to the post that actually
goes out — only what gets embedded for comparison changed.

Usage:
  python3 main_publish.py              # normal run
  python3 main_publish.py --dry-run    # logs the winner, never touches Notion/Qdrant/Gettr
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

from agents.candidate_selector import select_batch
from agents.embedder import Embedder
from agents.extractor import Extractor
from agents.gettr_publisher import GettrPublisher
from agents.card_headline import CardHeadlineWriter
from agents.card_photo import fetch_card_photo
from agents.headline_card import card_attribution, make_split_card, make_text_card, uses_headline_card
from agents.media_uploader import MediaUploader
from agents.og_metadata import fetch_link_preview
from agents.posted_dedup_checker import content_for_embedding, find_publishable
from agents.priority_ranker import PriorityRanker, log_publish_outcome
from agents.staleness_checker import StalenessChecker
from agents.trending import fetch_trending_headlines
from agents.writer import Writer
from core.alerts import AlertNotifier
from core.config import load_config
from core.event_identity import EventVerifier
from core.notion_candidates import has_unpublished_hot_candidate, mark_extraction_failed, mark_send_status, mark_writer_rejected, query_eligible_candidates
from core.publish_cadence import compute_dynamic_interval
from core.notion_sources import load_rss_sources
from core.qdrant_store import EventStore, PostedHistoryStore, ensure_collection_with_retry
from core.redis_store import CaptionCache

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("main_publish")


def _build_background(matched: dict | None) -> str:
    """Formats a matched event's timeline + related_event_ids (2026-08-31,
    core/qdrant_store.py's EventStore) into a short plain-text Background
    for agents/writer.py's Writer.write(context=...) — see that method and
    prompts/content_gen_prompt.txt's "OPTIONAL BACKGROUND" section for how
    it's used. Most recent 3 of each, oldest to newest for the timeline
    (reads as a chronology). Returns "" if there's nothing to say — the
    caller then omits `context` entirely, reproducing today's behavior."""
    if not matched:
        return ""
    parts = []
    timeline = matched.get("timeline", [])[-3:]
    entries = [
        f"{e.get('summary', '')} ({datetime.fromtimestamp(e['ts'], tz=timezone.utc).strftime('%b %d')})"
        for e in timeline if e.get("ts") and e.get("summary")
    ]
    if entries:
        parts.append("Prior developments: " + "; ".join(entries) + ".")
    titles = [r.get("title") for r in matched.get("related_event_ids", [])[-3:] if r.get("title")]
    if titles:
        parts.append("Related storylines: " + "; ".join(titles) + ".")
    return " ".join(parts)


_CARD_SENTENCE_END = re.compile(r'(?<![A-Z]\.)(?<=[.!?])\s+(?=[A-Z"\u201c])')


def _caption_first_sentence(caption: str) -> str:
    """The pre-2026-09-21 card headline, kept only as the fallback for when
    agents/card_headline.py fails or returns something unusable. Trimmed at a
    clause rather than mid-word, with an ellipsis so a trimmed headline reads
    as trimmed and not as broken."""
    text = (caption or "").strip()
    if not text:
        return ""
    first = _CARD_SENTENCE_END.split(text, 1)[0].strip()
    if len(first) > 150:
        head = first[:150]
        at_comma = head.rsplit(",", 1)[0] if "," in head else ""
        first = (at_comma if len(at_comma) > 80 else head.rsplit(" ", 1)[0]).rstrip(" ,.;:") + "\u2026"
    return first


async def _build_headline_card(winner, uploader: MediaUploader,
                               headline_writer: CardHeadlineWriter, og: dict) -> dict | None:
    """Draws the winner's 1:1 card and uploads it, returning Gettr media
    metadata — or None if anything went wrong, in which case run_cycle falls
    back to the ordinary link post rather than dropping the publish.

    Two shapes: a split card when the article has a usable photo, a text card
    when it does not. Drawing is PIL, which is blocking, so it runs in the
    default executor."""
    headline, deck = await headline_writer.write(winner.post_content)
    if not headline:
        headline, deck = _caption_first_sentence(winner.post_content), ""
        logger.info("card: headline writer gave nothing, falling back to the caption for %s",
                    winner.url)
    if not headline:
        return None
    attribution = card_attribution(winner.author, winner.url, winner.title)
    photo = await fetch_card_photo(og.get("prev_img") or "", winner.url)

    path = f"/tmp/cb_card_{winner.url_hash or winner.page_id}.png"
    try:
        loop = asyncio.get_running_loop()
        if photo:
            await loop.run_in_executor(
                None, make_split_card, headline, photo, path, deck, attribution)
        else:
            await loop.run_in_executor(
                None, make_text_card, headline, path, deck, attribution)
        with open(path, "rb") as fh:
            blob = fh.read()
    except Exception:
        logger.exception("card: render failed for %s", winner.url)
        return None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    meta = await uploader.upload_png(blob, filename=os.path.basename(path))
    if meta is None:
        logger.warning("card: upload failed for %s", winner.url)
        return None
    logger.info("card: %s made for %s (%d bytes) — %.80s%s",
                "split" if photo else "text", winner.url, len(blob), headline,
                f" / deck: {deck[:60]}" if deck else "")
    return meta


async def run_cycle(
    config,
    embedder: Embedder,
    ranker: PriorityRanker,
    posted_store: PostedHistoryStore,
    event_store: EventStore,
    event_verifier: EventVerifier,
    publisher: GettrPublisher,
    uploader: MediaUploader,
    headline_writer: CardHeadlineWriter,
    extractor: Extractor,
    writer: Writer,
    staleness_checker: StalenessChecker,
    caption_cache: CaptionCache,
    dry_run: bool,
) -> bool:
    """Returns True iff this cycle actually published something — main()'s
    loop uses this to track how recently the channel last posted, so the
    hot-topic fast lane (below) can be kept from firing more often than
    dynamic_publish.min_interval_seconds even when a permanently-stuck
    hot-flagged candidate keeps re-qualifying every fast_poll_seconds (see
    AM1ST's own 2026-09-05 incident, ported fix: a paywalled article that
    could never actually extract kept is_hot=true and unsent, firing the
    fast lane every few minutes for hours straight — 31 real publishes
    instead of the ~4 the dynamic interval alone would have produced)."""
    candidates = await query_eligible_candidates(config)
    if not candidates:
        logger.info("run_cycle: no eligible candidates this cycle")
        return False

    sources = await load_rss_sources(config)
    trending_headlines = await fetch_trending_headlines()

    # Widen-on-empty (2026-09-06, ported from AM1ST): select_batch() only
    # ever looks at `remaining` — the pool shrinks each attempt as tried
    # page_ids are removed, so a widen never re-extracts/re-writes a
    # candidate it already paid for this cycle. Bounded by
    # max_widen_attempts (not unbounded — extraction+content-gen is real
    # per-candidate cost, not free): each attempt is up to batch_max
    # candidates, so the default of 3 tries up to 30 total before
    # accepting "nothing to publish this cycle" is the real outcome, not
    # just "the first 10 happened to all be duplicates/stale."
    remaining = candidates
    winner = None
    ranked_len = 0
    for attempt in range(1, config.publish.max_widen_attempts + 1):
        batch = select_batch(remaining, config)
        if not batch:
            logger.info("run_cycle: widen attempt %d — no more candidates left to try", attempt)
            break
        logger.info("run_cycle: widen attempt %d — selected batch of %d for extraction/content-gen", attempt, len(batch))
        tried_ids = {c.page_id for c in batch}
        remaining = [c for c in remaining if c.page_id not in tried_ids]

        generated = []
        for c in batch:
            text = await extractor.extract(c.url, sources)
            if not text:
                logger.info("run_cycle: %s dropped — full-text extraction failed (paywall/blocked/empty), refusing to publish off title+description alone", c.url)
                # 2026-09-06, ported from AM1ST: give up on this candidate
                # permanently instead of re-trying it (and re-paying the
                # same extraction cost) every future cycle until it ages
                # out — a source that fails once essentially never
                # succeeds on a later retry. query_eligible_candidates()
                # excludes extraction_failed=true so it's never re-selected.
                await mark_extraction_failed(config, c.page_id)
                continue
            c.content = text

            # Staleness classification (2026-09-06, ported from AM1ST) — a
            # separate, single-purpose call BEFORE the writer runs; see
            # agents/staleness_checker.py's docstring for why this isn't
            # folded into content_gen_prompt.txt. Three-way, not binary: a
            # genuine analysis piece with real argument/expert input
            # (OPINION) still gets written, just framed as opinion rather
            # than dropped outright; only a pure rehash with no new angle
            # (STALE) gets dropped.
            #
            # Gated behind a free pre-filter, not run unconditionally on
            # every candidate — this would otherwise double the LLM calls
            # for every article that reaches extraction, when only a
            # minority (ones about an already-old underlying event) are
            # actually at risk. Reuses event_first_seen_at — already
            # computed at ingestion time, zero extra cost. Only when that
            # gap clears staleness_check_hours_floor is there real
            # ambiguity worth spending the LLM call on; a freshly-first-seen
            # event skips the check entirely (treated as FRESH for free).
            first_seen = c.event_first_seen_at or c.published_at
            hours_since_first_seen = (datetime.now(timezone.utc) - first_seen).total_seconds() / 3600
            is_opinion = False
            if hours_since_first_seen >= config.publish.staleness_check_hours_floor:
                try:
                    verdict, verdict_raw = await staleness_checker.classify(c.title, c.content)
                except Exception:
                    logger.exception("run_cycle: staleness check failed for %s — failing open, treating as fresh", c.url)
                    verdict = "FRESH"
                if verdict == "STALE":
                    logger.info("run_cycle: %s dropped — stale rehash of an old event (%s)", c.url, verdict_raw.replace("\n", " "))
                    continue
                is_opinion = verdict == "OPINION"
                if is_opinion:
                    logger.info("run_cycle: %s classified OPINION — will write framed as analysis, not breaking news (%s)", c.url, verdict_raw.replace("\n", " "))

            # Background for the writer (2026-08-31) — peek() against the same
            # title+description embedding space main.py already uses, so this
            # is checked against every candidate in the batch (not just the
            # eventual winner, since the winner isn't known until after
            # ranking, but content-gen runs on the whole batch) — see
            # _build_background()'s docstring and agents/writer.py's `context`
            # param. Fails open to no background on any error, same as every
            # other best-effort Qdrant read in this codebase.
            background = ""
            try:
                title_desc_embedding = await embedder.embed(f"{c.title}\n{c.description}"[:6000])
                background = _build_background(await event_store.peek(title_desc_embedding))
            except Exception:
                logger.exception("run_cycle: failed to build writer background for %s — continuing without it", c.url)

            # 2026-09-15: cached by url_hash — see core/redis_store.py's
            # CaptionCache docstring. Without this, a candidate reconsidered
            # in a later cycle gets a freshly reworded caption each time,
            # drifting its posted-dedup embedding enough to flip
            # same_event()'s verdict on the same underlying pair (confirmed
            # in both AM1ST and Market Watcher).
            post_content = await caption_cache.get(c.url_hash)
            if post_content is None:
                post_content = await writer.write(c.title, c.content, context=background, is_opinion=is_opinion)
                if not Writer.is_no_comment(post_content):
                    await caption_cache.set(c.url_hash, post_content)
            if Writer.is_no_comment(post_content):
                logger.info("run_cycle: %s — writer returned No comment, dropped from batch", c.url)
                # 2026-09-08: permanent exclusion, same reasoning as
                # mark_extraction_failed() above — without this, a
                # candidate the Writer has already judged has no real
                # China/CCP connection stays eligible and gets
                # re-selected, re-extracted, and re-written every future
                # cycle until it ages out on its own (a real Bloomberg
                # Pakistan-missile-system story hit this 17 times over
                # ~10 hours before finally aging out).
                await mark_writer_rejected(config, c.page_id)
                continue
            # Link appended after generation, not counted against the writer's
            # word cap — the AI's own output stays pure caption text. A card
            # source gets no link at all (2026-09-21): the card replaces it,
            # and appending a URL that no longer has a preview would just put
            # a bare news.google.com/rss/articles/CBMi... blob under the image.
            # Decided here rather than at publish time so what gets embedded
            # for posted-dedup is the same shape either way — see
            # agents/posted_dedup_checker.py's content_for_embedding(), which
            # strips this exact suffix back off when it is present.
            c.post_content = post_content if uses_headline_card(c.author, c.url) else f"{post_content}\n\n{c.url}"
            generated.append(c)

        if not generated:
            logger.info("run_cycle: widen attempt %d — nothing survived extraction/content-gen", attempt)
            continue

        ranked = await ranker.rank(generated, trending_headlines)
        ranked_len = len(ranked)
        winner = await find_publishable(ranked, embedder, posted_store, event_verifier, config)
        if winner is not None:
            logger.info("run_cycle: widen attempt %d — found a publishable candidate", attempt)
            break
        logger.info("run_cycle: widen attempt %d — all candidates were duplicates/errored, widening", attempt)

    log_publish_outcome(ranked_len, winner)
    if winner is None:
        logger.info("run_cycle: no publishable candidate found after widening — nothing to publish this cycle")
        return False

    # A card source publishes the image and nothing else; anything else — and
    # a card source whose card could not be drawn or uploaded — publishes the
    # link with its OG preview, exactly as before. The fallback re-appends the
    # URL that the generation loop above deliberately left off, so a failed
    # card degrades to today's post rather than to a post with no way to reach
    # the article.
    # One OG fetch serves both paths: the link post needs its preview fields,
    # and the card path needs prev_img as the source of the article's photo.
    og = await fetch_link_preview(winner.url)

    card_meta = None
    if uses_headline_card(winner.author, winner.url):
        card_meta = await _build_headline_card(winner, uploader, headline_writer, og)
        if card_meta is None:
            logger.warning(
                "card: falling back to a link post for %s (source=%s)",
                winner.url, winner.author,
            )
            winner.post_content = f"{winner.post_content}\n\n{winner.url}"

    if card_meta is not None:
        post_id = await publisher.publish(
            winner.post_content, log_ref=winner.url, media=card_meta,
        )
    else:
        post_id = await publisher.publish(
            winner.post_content,
            log_ref=winner.url,
            prev_desc=og.get("prev_desc") or winner.description or None,
            prev_img=og.get("prev_img"),
            prev_src_link=og.get("prev_src_link") or winner.url,
            prev_ttl=og.get("prev_ttl") or winner.title,
        )
    published = post_id is not None
    logger.info(
        "run_cycle: publish %s for %s (post_id=%s, %s)",
        "succeeded" if published else "FAILED",
        winner.url,
        post_id,
        "card" if card_meta is not None else "link preview",
    )

    if published and not dry_run:
        await mark_send_status(config, winner.page_id)
        winner_embedding = await embedder.embed(content_for_embedding(winner.post_content, winner.url))
        await posted_store.write(
            winner.url, winner.url_hash, winner.post_content, int(winner.published_at.timestamp()), winner_embedding,
        )

        # Flag the underlying event as published (2026-08-07) — so a later
        # ingestion cycle's EventStore.peek() can drop a near-verbatim
        # rehash of it outright instead of only catching a duplicate at the
        # publish cycle's own, much shorter posted_dedup_window_hours check.
        # Re-embeds title+description (not post_content — this needs to
        # land in the same embedding space main.py's peek() already uses)
        # to find which event this candidate belongs to; skips silently if
        # no match is found (fail open, never blocks on this).
        try:
            title_desc_embedding = await embedder.embed(f"{winner.title}\n{winner.description}"[:6000])
            matched = await event_store.peek(title_desc_embedding)
            if matched and matched.get("event_id"):
                await event_store.mark_published(matched["event_id"])
        except Exception:
            logger.exception("run_cycle: failed to mark event as published for %s", winner.url)

    return published


async def main() -> None:
    load_dotenv()
    dry_run = "--dry-run" in sys.argv

    config = load_config("config/config.yaml")

    embedder = Embedder(config)
    ranker = PriorityRanker(config)
    posted_store = PostedHistoryStore(config)
    event_store = EventStore(config)
    event_verifier = EventVerifier(config)
    publisher = GettrPublisher(config, dry_run=dry_run)
    uploader = MediaUploader(config, dry_run=dry_run)
    headline_writer = CardHeadlineWriter(config)
    alerts = AlertNotifier(config)
    extractor = Extractor(config, alerts)
    writer = Writer(config)
    staleness_checker = StalenessChecker(config)
    caption_cache = CaptionCache(config)
    await ensure_collection_with_retry(posted_store, "chinabreaks_posting_news_embedding")
    await ensure_collection_with_retry(event_store, "chinabreaks_events")

    if dry_run:
        logger.info("Running in --dry-run mode: Notion/Qdrant writes will be logged, not sent")

    last_publish_monotonic: float | None = None
    try:
        while True:
            started = time.monotonic()
            published_this_cycle = False
            try:
                published_this_cycle = await asyncio.wait_for(
                    run_cycle(config, embedder, ranker, posted_store, event_store, event_verifier, publisher, uploader, headline_writer, extractor, writer, staleness_checker, caption_cache, dry_run),
                    timeout=config.cycle_timeout_seconds,
                )
            except asyncio.TimeoutError:
                # Same self-loop cutoff as main.py's ingestion cycle — this
                # process runs independently of it, so publishing must not
                # stall just because one cycle got stuck on e.g. a slow
                # extraction (2026-08-12 discussion).
                logger.error("run_cycle exceeded %ds — cutting it off, will retry next cycle", config.cycle_timeout_seconds)
            except Exception:
                logger.exception("run_cycle failed")
            if published_this_cycle:
                last_publish_monotonic = time.monotonic()
            logger.info("run_cycle: cycle took %.1fs", time.monotonic() - started)
            base_interval = await compute_dynamic_interval(config)
            jitter = base_interval * random.uniform(-0.1, 0.1)
            # Manual hot-topic fast lane (2026-08-31, core/hot_topics.py) —
            # instead of one flat sleep, wait in fast_poll_seconds chunks and
            # check in between whether a manually-flagged-hot candidate is
            # sitting unsent; if so, cut the wait short and run the next
            # cycle now instead of waiting out the full interval. The check
            # itself is a cheap, existence-only Notion query (no LLM cost),
            # so this is safe to run often.
            #
            # 2026-09-06, ported from AM1ST: a hard backstop regardless of
            # cause — the fast lane may never fire more often than
            # dynamic_publish.min_interval_seconds since the last actual
            # publish, even if mark_extraction_failed() somehow doesn't
            # stop a permanently-stuck hot-flagged candidate from
            # re-qualifying.
            remaining = base_interval + jitter
            while remaining > 0:
                chunk = min(config.hot_topics.fast_poll_seconds, remaining)
                await asyncio.sleep(chunk)
                remaining -= chunk
                if remaining <= 0:
                    break
                since_last_publish = (
                    time.monotonic() - last_publish_monotonic
                    if last_publish_monotonic is not None
                    else config.dynamic_publish.min_interval_seconds
                )
                if since_last_publish < config.dynamic_publish.min_interval_seconds:
                    continue
                if await has_unpublished_hot_candidate(config):
                    logger.info("run_cycle: unpublished hot-flagged candidate detected — triggering cycle early")
                    break
    finally:
        await posted_store.close()
        await event_store.close()
        await caption_cache.close()


if __name__ == "__main__":
    asyncio.run(main())
