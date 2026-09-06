from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class NotionSourceProps(BaseModel):
    """China Breaks' real source database is "rss_n8n_chinabreaks_to_notion"
    (id 22e16dc99f32808fb86ec094a71fe7af). It has TWO separate active-row
    checkbox columns, both CONFIRMED real (read directly from the live
    n8n export's two ingestion pipelines, 2026-09-05 — see
    project_china_breaks_bot memory): "in_use" gates the major/mainstream-
    media source list, "In_use_2" gates the minor/niche-media source list.
    Since this Python port deliberately runs ONE unified pipeline instead
    of mirroring that major/minor split (the user's own call — see that
    same memory entry), load_rss_sources() below ORs the two together so
    no real source row gets silently dropped just because it's flagged
    under only one of the two checkboxes.

    Every OTHER field below (feed_url/name/cookie/domain) is carried over
    unverified from AM1ST's own source-table column names as a best-guess
    placeholder, NOT independently confirmed against this table's real
    live schema — AM1ST itself once had to correct guessed property names
    after a live schema read (see project_am1st_migration memory), so
    treat these the same way: re-verify against a live schema read before
    this bot's first real run."""

    in_use_major: str = "in_use"
    in_use_minor: str = "In_use_2"
    feed_url: str = "RSS"  # UNVERIFIED placeholder — carried over from AM1ST, re-check against a live schema read
    name: str = "Name"  # UNVERIFIED placeholder — carried over from AM1ST, re-check against a live schema read
    cookie: str = "cookie"  # UNVERIFIED placeholder — carried over from AM1ST, re-check against a live schema read
    domain: str = "website"  # UNVERIFIED placeholder — carried over from AM1ST, re-check against a live schema read


class NotionCandidateProps(BaseModel):
    """Column names of the shared candidate-pool database — real id
    22e16dc99f32808788e8dec5cd9107ca, seen named both "Chinadaily_news_Channel"
    and "china_breaks_Channel" (same id). CONFIRMED real schema, read
    directly from the old n8n workflow's "Create a database page" node
    2026-09-04: author (rich_text), description (rich_text), published_at
    (date), Title (title), url (url), url_to_image (url), post_content
    (rich_text), llm_score (number), llm_comment (rich_text), content
    (rich_text), url_hash (rich_text), plus from the publish-side workflow:
    send_status (checkbox), channel_name (select, value "ChinaBreaks"),
    priority (number, used to sort the old n8n publish query).

    Every field below that has a confirmed real counterpart is mapped to
    it. heat_score/event_first_seen_at/is_hot have NO confirmed counterpart
    in the real schema above — this port's own EventStore/hot-topics
    mechanism (inherited from AM1ST) needs somewhere to write them, so
    they're left as same-named placeholders; add these three columns to
    the real database (or re-map these three fields to existing ones)
    before this bot's first real run. url_to_image and priority exist on
    the real table (written/read by the OLD n8n system) but aren't used by
    this port's own architecture, which keeps AM1ST's design of an
    in-memory-only PriorityRanker pass rather than writing a priority
    score back to Notion — see README for this known gap.

    channel_name is a real addition versus AM1ST's own candidate-pool
    model: this database's name/schema strongly suggests it is SHARED
    across more than one China-focused channel (the two names it's been
    seen under), the same way AM1ST's hot-topics table is shared across
    sibling bots — see notion.channel_name below. Every row this bot
    writes must be tagged, and every query this bot runs must filter on
    it, or this bot would read/mix in another channel's own candidates."""

    title: str = "Title"
    url: str = "url"
    author: str = "author"
    description: str = "description"
    published_at: str = "published_at"
    post_content: str = "post_content"
    llm_score: str = "llm_score"
    llm_comment: str = "llm_comment"
    content: str = "content"
    url_hash: str = "url_hash"
    send_status: str = "send_status"  # checkbox — set true only once the publish cycle actually posts it
    channel_name: str = "channel_name"  # select — this row's owning channel; see notion.channel_name and the class docstring above
    heat_score: str = "heat_score"  # number — NOT in the confirmed real schema, see class docstring; corroboration signal, see HeatConfig
    event_first_seen_at: str = "event_first_seen_at"  # date — NOT in the confirmed real schema, see class docstring
    is_hot: str = "is_hot"  # checkbox — NOT in the confirmed real schema, see class docstring; set from the manual hot-topic flag match, see HotTopicsConfig


class NotionHotTopicProps(BaseModel):
    """Column names of the small shared "热点标记" (hot-topic flag) database
    (notion.hot_topics_db_id) that AM1ST and its other sibling bots already
    read from — a table the user edits directly, NOT written by any bot.
    Multiple sibling bots read the same table, each filtering to its own
    tag in the `channel` multi-select column via HotTopicsConfig.channel_name
    — see core/hot_topics.py. China Breaks has no independently-confirmed
    db id for this table yet (notion.hot_topics_db_id is left blank below,
    same as every other unconfigured id in this file) — if this bot is
    meant to share AM1ST's existing table, point it at that same db id."""

    name: str = "Name"
    channel: str = "Channel"
    active: str = "In_Use"


class NotionConfig(BaseModel):
    api_key: str = ""  # env: NOTION_API_KEY — used for source_db_id (and alerts, which live on source rows)
    candidate_api_key: str = ""  # env: NOTION_CANDIDATE_API_KEY — separate integration for candidate_db_id, since the two databases don't have to share one integration's Connections. Falls back to api_key if left blank.
    hot_topics_api_key: str = ""  # env: NOTION_HOT_TOPICS_API_KEY — separate integration for hot_topics_db_id, since this table is shared across multiple sibling bots' own Notion connections. Falls back to api_key if left blank.
    source_db_id: str = ""  # env: NOTION_SOURCE_DB_ID — real id 22e16dc99f32808fb86ec094a71fe7af ("rss_n8n_chinabreaks_to_notion"), see README; left blank here, set via .env
    candidate_db_id: str = ""  # env: NOTION_CANDIDATE_DB_ID — real id 22e16dc99f32808788e8dec5cd9107ca ("Chinadaily_news_Channel" / "china_breaks_Channel"), see README; left blank here, set via .env
    hot_topics_db_id: str = ""  # env: NOTION_HOT_TOPICS_DB_ID — shared across sibling bots, see NotionHotTopicProps
    alert_user_id: str = ""  # env: NOTION_ALERT_USER_ID
    channel_name: str = "ChinaBreaks"  # this bot's tag in the shared candidate-pool database's channel_name select column (real confirmed value "ChinaBreaks") — written on every row this bot creates, and filtered on by every publish-side query, so a shared database never mixes in another channel's candidates. Distinct from hot_topics.channel_name below (a different shared table, same string value by coincidence).
    source_props: NotionSourceProps = Field(default_factory=NotionSourceProps)
    candidate_props: NotionCandidateProps = Field(default_factory=NotionCandidateProps)
    hot_topics_props: NotionHotTopicProps = Field(default_factory=NotionHotTopicProps)

    @property
    def candidate_key(self) -> str:
        return self.candidate_api_key or self.api_key

    @property
    def hot_topics_key(self) -> str:
        return self.hot_topics_api_key or self.api_key


class RedisConfig(BaseModel):
    url: str = ""  # env: REDIS_URL (Upstash rediss:// connection string)
    # Real production URL-hash dedup key prefix already in use by the live
    # n8n system, confirmed 2026-09-04 — deliberately reused (not a fresh
    # namespace) so this Python port recognizes URLs the still-running old
    # system already claimed, the moment it starts running: this is a safe,
    # idempotent membership check either way.
    key_prefix: str = "newsroom:chinabreaks:url_hash:"
    ttl_seconds: int = 864000  # 10 days — standing dedup architecture default, unchanged from AM1ST


class OpenAIConfig(BaseModel):
    api_key: str = ""  # env: OPENAI_API_KEY
    fallback_api_key: str = ""  # env: OPENAI_API_KEY_FALLBACK — only used if the primary key hits RateLimitError (rate limit or exhausted quota), see core/openai_client.py
    # gpt-4o-mini for every OpenAI-backed call in this codebase (Writer
    # prose, Scorer, PriorityRanker, EventVerifier) — the project's own
    # standing "gpt-4o-mini everywhere" model-architecture rule. AM1ST's
    # own history (see its agents/scorer.py) already tried moving the
    # non-prose judgment calls onto a cheaper gpt-5-nano model and reverted
    # after a live multi-cycle test found real judgment-quality failures in
    # every one of those roles — that lesson is inherited here from day
    # one, not re-tested: nothing in this codebase should reference
    # gpt-5-nano without a fresh, judgment-quality-focused live test first.
    chat_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    scoring_prompt_file: str = "prompts/scoring_prompt.txt"
    content_gen_prompt_file: str = "prompts/content_gen_prompt.txt"
    # Ingestion-side AI score gate — real calibrated value from the old
    # n8n system's global_config node (effectively "score > 3", i.e. a
    # minimum passing score of 4) — much looser than AM1ST's own >=5 gate.
    # Carried over as this bot's starting default, not guessed.
    score_threshold: float = 4.0

    @property
    def api_keys(self) -> list[str]:
        """Ordered list handed to core.openai_client.FallbackOpenAI — primary
        first, then the fallback if one is configured and actually different."""
        keys = [self.api_key]
        if self.fallback_api_key and self.fallback_api_key != self.api_key:
            keys.append(self.fallback_api_key)
        return keys


class DedupConfig(BaseModel):
    semantic_threshold: float = 0.8  # standing dedup architecture default, unchanged from AM1ST — not independently recalibrated for China Breaks yet


class EntityVerifierConfig(BaseModel):
    """Second-opinion check on top of EventStore.peek()'s cosine match —
    the full mechanism (rule tier / LLM tier split, Top-K walk, subtype
    weighting, the various fragmentation fixes below) is ported unchanged
    from AM1ST, where it was validated on AM1ST's own real historical
    am1st_events data before being written this way (rule tier — entity
    overlap, excluding tokens that have been the core of
    >=hub_event_count_threshold different past events — resolves ~2/3 of
    cosine-matched candidates reliably there; the remaining ~1/3
    ("AMBIGUOUS" — every shared token is a known multi-event hub) goes to
    a same-event LLM call). China Breaks inherits this mechanism as a real
    starting point, per the user's explicit instruction to carry over
    AM1ST's hard-won correctness fixes rather than re-derive them — but
    every threshold below is AM1ST's own number, NOT yet independently
    validated against chinabreaks_events data, since that collection has
    no history yet. That LLM call intentionally does NOT also ask for the
    update-subtype (CORE_UPDATE/CORROBORATION/RESTATEMENT) in the same
    prompt — an ablation test (on AM1ST's data) found asking both at once
    biases the model toward SAME_EVENT ~30% of the time (it seems to
    pre-assume "same" so it has something to classify), which corrupts the
    one judgment that actually gates merging. Subtype is a separate,
    second call, only made when the first call says SAME_EVENT, purely to
    enrich the logged training record for future distillation — nothing
    downstream reads it yet.

    Deliberately NOT perfect — the user's explicit call on AM1ST (2026-08-09),
    inherited here: a known residual miss rate (e.g. two different
    candidates sharing only generic tokens like a country name) is
    acceptable, not worth chasing with more entity-rarity formulas or
    bigger blocklists. Every rule-tier AMBIGUOUS case and its LLM verdict
    gets logged (log_path) specifically so those residual misses become
    future hard-negative training data instead of silently recurring
    forever — this weekend's live test data is exactly what that logging
    is for."""

    hub_event_count_threshold: int = 2  # a shared token counts as real evidence only if it's been the CORE of fewer than this many distinct past events
    pair_cooccur_max: int = 1  # two individually-hub tokens can still count as evidence if this exact PAIR has co-occurred as a joint core in at most this many past events
    min_doc_freq_for_core: int = 2  # a token must appear in at least this many of an event's OWN accumulated articles to join its persisted core_entities (not a ratio — a ratio lets a single one-off token qualify as "core" while an event still only has 1-2 articles, see the same design note)
    hub_key_prefix: str = "chinabreaks:hub:"  # Redis key namespace for the token/pair historical-hub-count index — separate namespace from redis.key_prefix's URL-hash dedup, same REDIS_URL
    same_event_prompt_file: str = "prompts/same_event_prompt.txt"
    update_subtype_prompt_file: str = "prompts/update_subtype_prompt.txt"
    related_event_prompt_file: str = "prompts/related_event_prompt.txt"  # EventVerifier.related_event() — see core/event_identity.py
    log_path: str = "logs/event_identity_decisions.jsonl"

    # Top-K event-candidate verification (ported from AM1ST's 2026-08-14 P0
    # redesign) — the single most cosine-similar historical event isn't
    # guaranteed to be the true match; a real match can rank #2+ if the
    # top-ranked candidate is a coincidentally-closer but actually-unrelated
    # event. main.py walks up to top_k candidates in cosine-descending
    # order, verifying each with the same rule/LLM tiers as before, and
    # only creates a new event once every candidate is rejected (NO_OVERLAP
    # or entity-verifier DIFFERENT_EVENT) — not after just the first one.
    top_k: int = 5

    # Subtype-weighted heat — applied as a multiplier on a matched
    # cluster's incremental heat contribution (never on a brand-new
    # event's 1.0 baseline) — a real new development should move the
    # needle more than an outlet just repeating yesterday's line in
    # different words. Not applied to canonical_title/canonical_summary/
    # timeline state — those stay seed-only/never-rewritten (see
    # EventStore.commit()'s docstring); this only touches heat weighting.
    subtype_restatement_weight: float = 0.2   # repeats an already-known fact, different wording/outlet — barely moves heat
    subtype_corroboration_weight: float = 1.0  # independent new source confirming the same facts
    subtype_core_update_weight: float = 1.5    # genuine new fact/decision/status — weighted above plain corroboration

    # Skip the classify_subtype() LLM call entirely when A/B are this
    # cosine-similar AND name the same places/facilities and the same
    # numbers (see no_conflicting_specifics() in core/event_identity.py) —
    # AM1ST found the LLM asked to classify byte-identical A/B text still
    # hallucinating a "new development" instead of answering RESTATEMENT.
    # High cosine alone isn't sufficient: "China sanctions Country A" vs
    # "China sanctions Country B" (or "3 vessels seized" vs "5 vessels
    # seized") can score just as high on cosine while describing a
    # materially different fact — the location/number check guards
    # against that.
    restatement_cosine_floor: float = 0.92

    # verify_compatibility()'s NO_OVERLAP short-circuit (entity overlap ==
    # empty set -> confident DIFFERENT_EVENT, no LLM) was found, via a full
    # audit of AM1ST's own am1st_events, to wrongly fragment real duplicate
    # events whenever the two articles refer to the same actor/institution
    # through different surface forms with zero shared words (a role vs. a
    # name, or an institution vs. the government it acts under). No
    # dictionary or NER fix can close this — the shared word simply
    # doesn't exist in either text. At this cosine floor, zero overlap is
    # downgraded from NO_OVERLAP to AMBIGUOUS (one extra same_event() LLM
    # call) instead of confidently rejecting — the LLM's world knowledge
    # (who currently holds an office, which ministry executes which
    # policy) covers exactly the gap entity-overlap can't. Deliberately NOT
    # set below the existing peek_top_k() candidate floor (heat.related_threshold,
    # 0.6) — this only reconsiders candidates already deemed plausible on
    # semantic grounds. 0.75 is AM1ST's own audited value, inherited as a
    # starting point — not independently re-validated on China Breaks data.
    no_overlap_llm_review_floor: float = 0.75

    # IDF-weighted keyword overlap — a second, entity-independent lexical
    # signal for verify_compatibility()'s FAIL_OPEN branch (new_tokens from
    # NER came back empty — very short text, or a genuine extraction miss).
    # That branch would otherwise blindly trust whatever cosine match it
    # was handed with zero independent check. Originally ported into AM1ST
    # from North_Korea_News's core/hashing.py — same IDF-over-a-corpus
    # math, no LLM call, no new database (the corpus is this cycle's own
    # batch of candidate titles+descriptions, built in-memory in main.py).
    # NOTE: this threshold is carried over from North_Korea_News's own
    # real-item validation by way of AM1ST, NOT yet independently validated
    # against China Breaks' own historical chinabreaks_events data the way
    # every other threshold in this class was tuned on AM1ST's — treat as a
    # starting point, revisit once real chinabreaks decisions have been
    # logged and reviewed.
    weighted_overlap_threshold: float = 0.15


class HotTopicsConfig(BaseModel):
    """Manual breaking-news override — the user, not any automatic
    heat_score threshold, tells the system a specific topic matters right
    now, by adding/editing a row in a small shared Notion database
    (notion.hot_topics_db_id) multiple sibling bots read from, each
    filtering to their own `channel_name` tag in that row's Channel
    multi-select column (core/hot_topics.py). Mechanism ported unchanged
    from AM1ST.

    A row counts as currently live only while its In_Use checkbox is
    checked AND its own last_edited_time is within ttl_hours —
    deliberately anchored to last_edited_time, not created_time: nudging a
    still-developing topic (toggle the checkbox, edit the title) keeps it
    live without creating a new row, and forgetting to ever uncheck it
    can't leave it live forever.

    main.py embeds every currently-live topic text once per ingestion
    cycle and compares against each local cluster's representative
    embedding (match_threshold). A match sets that event's hot_until
    (core/qdrant_store.py's EventStore) — which then flows through
    unchanged to every future corroborating article on the SAME event via
    EventStore.commit()'s usual "carry forward from matched" pattern, so
    later follow-up coverage inherits hot status automatically without
    needing to re-match against the original flag text every time. Not
    yet independently validated against real chinabreaks_events data — a
    starting point, like weighted_overlap_threshold above."""

    channel_name: str = "ChinaBreaks"  # this bot's own tag in the shared table's Channel multi-select column — matches the real Notion candidate-pool channel_name select value confirmed for this project
    # Cosine floor for "this candidate is about a currently-flagged hot
    # topic," on text-embedding-3-small (openai.embedding_model). AM1ST's
    # own calibrated value (live embedding tests, 2026-08-31), inherited
    # here as a starting point — not independently validated against real
    # chinabreaks_events data.
    match_threshold: float = 0.5
    ttl_hours: int = 24  # both the Notion flag's own last_edited_time freshness window AND how long a matched event stays "hot" after its last matching commit
    fast_poll_seconds: int = 180  # main_publish.py's short-poll interval while an unpublished is_hot candidate exists, instead of waiting out the full publish.interval_seconds — see main_publish.py


class HeatConfig(BaseModel):
    """Corroboration/heat scoring — event aggregation, mechanism ported
    unchanged from AM1ST (see project_am1st_migration memory's "event
    aggregation" note for the full design history). An article's own
    published_at is a poor proxy for how fresh the underlying news event
    actually is: one wire service can break something, others rehash it
    hours or days later — each with a recent published_at despite covering
    old news.

    core/qdrant_store.py's EventStore maintains a dedicated Qdrant
    collection (qdrant.events_collection) where each event is a GROUP of
    points sharing one event_id — not one fixed vector — following the
    standard "topic tracking" pattern from TDT (Topic Detection and
    Tracking) research: a topic/event is represented by a small evolving
    set of representative documents, not a single centroid, so that (a)
    articles using different phrasing than the very first report can still
    be recognized as the same event (mitigates cluster fragmentation), and
    (b) the representative set stays current as a multi-day story evolves
    (mitigates semantic drift). heat_score/first_seen_at/last_updated_at/
    sources are kept in sync across every point of an event via a
    filtered payload update, not stored per-point independently.

    A near-duplicate (score >= dedup.semantic_threshold, dropped from the
    candidate pool) still bumps its matched event's heat tally before being
    dropped — that credit must not be lost just because the article itself
    isn't worth its own candidate-pool row. Only a genuinely new-angle
    match (related_threshold <= score < dedup.semantic_threshold) becomes
    an additional representative point, since a near-duplicate doesn't add
    matching robustness."""

    related_threshold: float = 0.6  # cosine similarity floor for "same event" — below dedup.semantic_threshold on purpose, that band is "duplicate," this one is "corroborating"
    window_hours: int = 240  # 10 days — matches redis.ttl_seconds' 10-day convention; deliberately much wider than qdrant.cross_cycle_window_hours (72h, the plain dedup check's reach) so a multi-day-evolving event doesn't get treated as "expired" and fragmented into a phantom duplicate event
    # Domain adaptation (not an AM1ST value carried over as-is): AM1ST's
    # own major_outlets list is US-domestic wire/TV outlets (Reuters, CNN,
    # Fox News, ...), which has no particular bearing on corroboration
    # weight for a CCP-exposure feed. Replaced with the non-CCP outlets
    # this project's own content_gen_prompt.txt already names as trusted
    # sources for China coverage (see that file's "SOURCE RULES" section)
    # — not independently calibrated, a reasonable starting point pending
    # real chinabreaks_events data.
    major_outlets: list[str] = Field(
        default_factory=lambda: [
            "Reuters", "AFP", "DW", "Nikkei", "Kyodo", "VOA", "RFA", "CNA",
        ]
    )
    major_outlet_weight: float = 2.0  # per-corroborating-source weight if its source_name is in major_outlets, vs 1.0 for any other source


class QdrantConfig(BaseModel):
    url: str = ""  # env: QDRANT_URL
    api_key: str = ""  # env: QDRANT_API_KEY
    collection: str = "chinabreaks_embeddings"  # ingestion-side cross-cycle dedup cache (title+description)
    posted_collection: str = "chinabreaks_posting_news_embedding"  # publish-side "already posted" cache (post_content) — separate collection, separate purpose, see core/qdrant_store.py's PostedHistoryStore
    events_collection: str = "chinabreaks_events"  # event aggregation collection, see HeatConfig/EventStore — a genuinely different kind of thing from the two collections above (a group of points per underlying event, not one point per article)
    cross_cycle_window_hours: int = 72
    cleanup_retention_days: int = 10
    timeout_seconds: int = 15  # AsyncQdrantClient has no timeout by default — AM1ST hit a real stalled-request hang without this; ported as a preventive default


class RssConfig(BaseModel):
    """Per-feed cap — matches the real n8n system's global_config node
    value, carried over as this bot's starting default: cap each
    individual feed at max_items_per_feed entries.

    There is deliberately no overall cross-source cap (removed 2026-09-06,
    matching AM1ST, which never had one) — this channel's own source pool
    is dominated by general-news outlets where a live sample found only
    ~16.5% of raw items are China/CCP-relevant, and an aggregate
    recency-sorted cutoff was discarding exactly those rare relevant items
    on any busy news day, with no regard for relevance. The Redis/semantic
    dedup layers and the LLM score gate are what should decide which
    candidates survive, not a blind truncation before they're ever seen."""

    max_items_per_feed: int = 150


class ExtractionConfig(BaseModel):
    """No external service — see agents/extractor.py's docstring for why
    (the old n8n-svr extract-premium service is unmaintained and broken for
    most paywalled sources). Self-contained httpx+trafilatura instead."""

    timeout_seconds: int = 20
    min_text_length: int = 200  # below this, treat extraction as failed (bot-block/JS-wall pages are usually a few dozen chars)


class GettrConfig(BaseModel):
    user_id: str = ""  # env: GETTR_USER_ID — leave blank; the real live channel's identity (username "chinabreaks", userId "gettrfoodofficial") is documented in README as reference info only, never as a committed value
    user_token: str = ""  # env: GETTR_USER_TOKEN — never commit a real token anywhere
    api_url: str = "https://gettr.com/api/u/post"


class PublishConfig(BaseModel):
    """Tuning for the separate publish cycle (main_publish.py) — selects and
    posts exactly one candidate per interval. Mechanism ported unchanged
    from AM1ST (a deliberate simplification of the old n8n design, which
    could post an entire surviving batch, down to "publish exactly 1,
    walking down the priority order on duplicates").

    interval_seconds/candidate_max_age_hours/fresh_hours/batch_min/
    batch_max/posted_dedup_window_hours/posted_dedup_threshold are AM1ST's
    own confirmed values, inherited unchanged as a starting point.
    candidate_min_score/weekday_min_score/weekend_min_score are shifted
    down by 1 point from AM1ST's own 5.0/6.0/5.0 — NOT an independently
    confirmed real value, but a deliberate internal-consistency fix: this
    project's own ingestion score_threshold is 4.0 (vs AM1ST's 5.0, see
    OpenAIConfig), and leaving the publish-side floor at 5.0 unchanged
    would create a dead band (every candidate scored 4.0-4.9 could enter
    the ingestion candidate pool but could never clear the publish
    query's own floor to ever be considered for posting). Flagged here so
    this weekend's retuning pass can revisit it once real data exists."""

    interval_seconds: int = 1800  # 30 minutes — deployment note: when this process is actually scheduled on a host, stagger its start time so cycles land near :02/:32 past the hour rather than AM1ST's :17/:47, so the two don't hit shared APIs at the exact same instant if ever run on the same machine. Not something main_publish.py's own self-looping code anchors to a wall-clock minute today (see its main() loop) — a deployment-time concern, out of scope for this code-only build.
    candidate_min_score: float = 4.0  # Notion query floor — see class docstring's "shifted down by 1" note
    weekday_min_score: float = 5.0  # weekdays: heavier real news volume, prefer this floor first — see class docstring
    weekend_min_score: float = 4.0  # weekends: lighter volume, use this floor directly — see class docstring
    candidate_max_age_hours: int = 12  # candidate pool eligibility window
    fresh_hours: int = 4  # freshness tier line used by the batch-selection cascade
    batch_min: int = 3
    batch_max: int = 10
    priority_rank_prompt_file: str = "prompts/priority_rank_prompt.txt"
    posted_dedup_window_hours: int = 240  # 10 days — matches heat.window_hours so both "have we already covered this" checks agree on how long an event stays "recent"
    posted_dedup_threshold: float = 0.70  # stricter than the ingestion side's 0.8 — deliberate: fully autonomous posting should err toward under-posting


class AppConfig(BaseModel):
    notion: NotionConfig = Field(default_factory=NotionConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    entity_verifier: EntityVerifierConfig = Field(default_factory=EntityVerifierConfig)
    hot_topics: HotTopicsConfig = Field(default_factory=HotTopicsConfig)
    heat: HeatConfig = Field(default_factory=HeatConfig)
    qdrant: QdrantConfig = Field(default_factory=QdrantConfig)
    rss: RssConfig = Field(default_factory=RssConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    gettr: GettrConfig = Field(default_factory=GettrConfig)
    publish: PublishConfig = Field(default_factory=PublishConfig)
    # Candidate freshness window — real calibrated value from the old n8n
    # system's global_config node (~6 hours), vs AM1ST's own 3h. Carried
    # over as this bot's starting default, not guessed.
    max_publish_age_hours: int = 6
    poll_interval_seconds: int = 600
    cycle_timeout_seconds: int = 540  # 9 min — hard-cuts a stuck cycle so the next one always starts on schedule (main.py and main_publish.py loops both apply this, independently) — AM1ST's own real-world-derived value, inherited
    alert_cooldown_seconds: int = 21600


_ENV_OVERRIDES = {
    ("notion", "api_key"): "NOTION_API_KEY",
    ("notion", "candidate_api_key"): "NOTION_CANDIDATE_API_KEY",
    ("notion", "hot_topics_api_key"): "NOTION_HOT_TOPICS_API_KEY",
    ("notion", "source_db_id"): "NOTION_SOURCE_DB_ID",
    ("notion", "candidate_db_id"): "NOTION_CANDIDATE_DB_ID",
    ("notion", "hot_topics_db_id"): "NOTION_HOT_TOPICS_DB_ID",
    ("notion", "alert_user_id"): "NOTION_ALERT_USER_ID",
    ("redis", "url"): "REDIS_URL",
    ("openai", "api_key"): "OPENAI_API_KEY",
    ("openai", "fallback_api_key"): "OPENAI_API_KEY_FALLBACK",
    ("qdrant", "url"): "QDRANT_URL",
    ("qdrant", "api_key"): "QDRANT_API_KEY",
    ("gettr", "user_id"): "GETTR_USER_ID",
    ("gettr", "user_token"): "GETTR_USER_TOKEN",
}


def load_config(path: str = "config/config.yaml") -> AppConfig:
    """Load YAML config, then apply environment variable overrides for secrets.

    Env vars always win over the YAML file, so real credentials never need to
    be committed to config.yaml — set them in .env / the deployment environment.
    """
    raw: dict = {}
    p = Path(path)
    if p.exists():
        with p.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    config = AppConfig.model_validate(raw)

    for (section, field), env_var in _ENV_OVERRIDES.items():
        value = os.environ.get(env_var)
        if value:
            section_obj = getattr(config, section)
            setattr(section_obj, field, value)

    return config
