from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from urllib.parse import urlparse, urlunparse

import jieba

from core.language import _cjk_ratio, _is_cjk_char


def sha256_url_hash(url: str) -> str:
    """SHA256 of the URL with query string/fragment stripped, full hex digest.

    Exact-duplicate dedup layer — URL only (not URL+title, not URL+image),
    per standing rule: a channel only ever compares against its own history.
    """
    parsed = urlparse(url)
    clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two embedding vectors, in [-1, 1]."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# 2026-08-20: ported from North_Korea_News's core/hashing.py (commit
# 1caea63, "Add IDF-weighted keyword overlap as a semantic-dedup support
# signal") — that bot missed a real duplicate because two writeups of the
# same event (same official quoted, different phrasing) only scored 0.7172
# cosine, under its 0.8 dedup bar. Generic-word overlap alone false-
# positived on ~34% of same-band pairs there (validated against 642 real
# published items) until weighted by rarity — IDF-weighted overlap instead
# discounts words that are common across the whole corpus ("North Korea",
# "Trump") and rewards ones that are rare/identifying.
#
# Used here (by way of AM1ST, which this codebase was ported from) as a
# second, entity-independent lexical signal for core/event_identity.py's
# verify_compatibility() FAIL_OPEN branch — that branch previously just
# blindly trusted whatever cosine match EventStore handed it whenever NER
# extracted zero entity tokens (very short text, or a genuine NER miss),
# with no independent check at all. NOTE: the specific threshold below is
# carried over from North_Korea_News's own real-data validation by way of
# AM1ST, not (yet) independently validated against China Breaks' own
# historical chinabreaks_events data — see EntityVerifierConfig's docstring.
def tokenize(text: str) -> set[str]:
    """Lowercase word tokens, punctuation/digits stripped. Deliberately not
    capitalization-based — North_Korea_News tried extracting capitalized
    words as a proper-noun proxy first and rejected it (34% false-positive
    rate on real data): generic domain nouns are capitalized in English
    regardless of specificity. idf() below, not capitalization, is what
    actually separates a generic/expected word from a rare/identifying
    one.

    2026-09-07: the plain `[a-z']+` regex alone returns an empty set for
    any CJK text — confirmed on real China Breaks production data (a
    Traditional Chinese Nepal/Tibet flood pair, genuinely the same event,
    logged weighted_overlap_score 0.0 purely because this function found
    zero tokens on either side, not because the two articles didn't share
    real words). Chinese has no whitespace between words, so a bare regex
    can't segment it at all; jieba.cut() gives real word boundaries, same
    fix already applied to entity_tokens() in core/event_identity.py. Only
    run when the text is actually majority-CJK (same validated ratio check
    that module uses) — cheap to skip otherwise. Single-character CJK
    tokens are dropped (jieba's own function words — "的"/"了"/"在" etc —
    are almost always length 1; this is a blunt filter, not a real stopword
    list, but keeps this fallback from drowning in particles)."""
    tokens = set(re.findall(r"[a-z']+", text.lower()))
    if _cjk_ratio(text) > 0.05:
        tokens |= {
            word for word in jieba.cut(text)
            if len(word) > 1 and all(_is_cjk_char(ch) for ch in word)
        }
    return tokens


def idf(token: str, doc_freq: Counter, doc_count: int) -> float:
    """Inverse document frequency — how surprising it is to see this token,
    estimated from how many of the corpus's `doc_count` documents contained
    it at least once. A token in nearly every document carries ~0 weight;
    one confined to a couple of documents carries a large one. +1 smoothing
    avoids dividing by zero for a token unseen in the corpus."""
    return math.log((doc_count + 1) / (doc_freq.get(token, 0) + 1))


def weighted_overlap(tokens_a: set[str], tokens_b: set[str], doc_freq: Counter, doc_count: int) -> float:
    """IDF-weighted Jaccard (Ruzicka similarity) between two token sets, in
    [0, 1] — shared tokens' IDF weight divided by the union's IDF weight."""
    shared = tokens_a & tokens_b
    union = tokens_a | tokens_b
    if not union:
        return 0.0
    shared_weight = sum(idf(t, doc_freq, doc_count) for t in shared)
    union_weight = sum(idf(t, doc_freq, doc_count) for t in union)
    if union_weight == 0:
        return 0.0
    return shared_weight / union_weight
