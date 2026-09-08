# Threshold calibration tracker

China Breaks was architecturally ported from AM1ST, which reuses the same
mechanisms but covers a different topic/source mix (US-politics vs
CCP-exposure, different language spread, different real volume). Every
threshold below started as AM1ST's own real-data-calibrated number, carried
over as a starting point, NOT independently validated for this channel.
2026-09-08: user explicit instruction — these are two different channels,
their thresholds should not just stay identical forever; track status here
and revisit each once real chinabreaks production data exists to check it
against, rather than guessing now.

Update this file whenever a threshold below is checked or changed — add the
date, what real data was used, and the outcome, whether or not the value
actually changed.

## Confirmed / recalibrated with real chinabreaks data (DONE)

| Threshold | Value | Confirmed |
|---|---|---|
| `dynamic_publish.hot_score_floor` | 8.0 → **7.0** | 2026-09-08, real 48h/572-candidate Notion sample — old value never once crossed `busy_count` in 91 real cycles |
| `dynamic_publish.busy_scale` | 0.6 → **0.5** | 2026-09-08, so the busy tier actually reaches the 15min floor |
| `dynamic_publish.quiet_count` (1) / `busy_count` (8) | unchanged | 2026-09-08, simulated against real 48h timestamp data — quiet 25.5%/normal 42.6%/busy 31.9%, a healthy real split, no change needed |
| `publish.posted_dedup_threshold` | 0.70 → **0.80** | 2026-09-07, real duplicate-post audit (7 real Gettr posts) |
| `heat.related_threshold` / posted-dedup gray zone | aligned to 0.6–0.8 | 2026-09-07, real duplicate-post audit |
| `redis.key_prefix`, `qdrant.collection`, `qdrant.posted_collection` | repointed to the real old-system collections | 2026-09-08, production launch — see git history |

## Still pending — inherited from AM1ST, not yet checked against real chinabreaks data

| Threshold | Current value | What it controls | What would confirm/recalibrate it |
|---|---|---|---|
| `dedup.semantic_threshold` | 0.8 | Cosine floor for intra-batch + cross-cycle "same article" dedup | Sample real candidate pairs (duplicate vs genuinely-different) once enough real volume exists, hand-judge, check where 0.8 actually separates them — this channel's heavier non-English/translated source mix (Malay, Russian, Chinese) could shift cosine distributions vs AM1ST's mostly-English sources |
| `entity_verifier.restatement_cosine_floor` | 0.92 | Cosine floor for treating a same-event update as pure RESTATEMENT (low heat weight) | Repeat the chinabreaks_events merge/split audit done 2026-09-08 (which found 3 fragmentation causes, since fixed) at a larger scale once the event store has more real volume |
| `entity_verifier.no_overlap_llm_review_floor` | 0.75 | Cosine floor below which a NO_OVERLAP rule-tier verdict is trusted without an LLM call | Same real chinabreaks_events audit as above |
| `entity_verifier.weighted_overlap_threshold` | 0.15 | IDF-weighted keyword overlap floor for the FAIL_OPEN branch | Same audit; this one came from North_Korea_News by way of AM1ST, furthest removed from this channel's own data |
| `hot_topics.match_threshold` | 0.5 | Cosine floor for "this candidate matches a manually-flagged hot topic" | Needs real hot_topics-table usage on this channel to accumulate (user-curated table) — check real match/no-match cosine scores once there's a meaningful sample |
| `publish.weekday_max_age_hours` / `weekend_max_age_hours` | 12 / 24 | How far back the publish-side Notion query looks, day-of-week-aware | AM1ST's own choice was US/Eastern-weekend-calibrated; pull chinabreaks' own real day-of-week candidate-volume pattern once 2+ weeks of real data exist and check whether its weekend drop looks similar |
| `publish.staleness_check_hours_floor` | 72 | How old an event must be before StalenessChecker's LLM call runs at all | Reuses dedup.cross_cycle_window_hours' 72h by convention, not independently chosen — check real STALE/OPINION/FRESH verdict distribution over time to see if 72h is gating at the right point |
| `publish.max_widen_attempts` | 3 | How many batch_max-sized chunks of the eligible pool to try before giving up on a publish cycle | Check real attempt-outcome distribution (how often attempt 1 vs 2 vs 3 succeeds, how often all 3 exhaust) once enough real publish cycles have run |
| `poll_interval_seconds` | 600 (10 min) | Ingestion-cycle frequency | Judgment call on how fast this channel's own RSS sources actually update; no evidence yet it needs to differ from AM1ST |
| `cycle_timeout_seconds` | 540 (9 min) | Hard cutoff for a stuck ingestion/publish cycle | Check real observed cycle-duration distribution — this channel's broader multi-language source pool could genuinely run longer per cycle than AM1ST's |
| `heat.major_outlets` list / `major_outlet_weight` (2.0) | see config.yaml | Which outlets count extra toward the heat/corroboration score | Swapped to non-CCP trusted outlets as a reasonable starting point (2026-09-06), never checked against real corroboration-scoring outcomes |

## How to use this file

When picking one of these up: pull real data the same way the DONE section's
entries did (a real Notion/Qdrant/log query, not a guess), write the finding
and outcome into the DONE table above (even if the conclusion is "confirmed
correct, no change"), and delete the row from the pending table.
