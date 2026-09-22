"""Does this story put something American concretely at stake?

Measured, not assumed. Over 99 of this channel's own posts old enough for
engagement to have settled (2026-09-22):

    with an American subject    n=39   median 17 likes   28% reached 20+
    without                     n=60   median 15 likes   12% reached 20+

Permutation test over 20,000 relabellings: the median gap (+2) lands at
p=0.089, the 20+-rate gap (+17 points) at p=0.059. Excluding the WASHINGTON
category entirely — in case the effect were just "US-politics stories mention
US words" — the split holds at 28% vs 13%.

It is also the one thing that separates this channel from the US accounts whose
China posts outperform their own baselines: 56% of their China posts carry an
American subject against 39% of ours (Gateway Pundit 113 likes on China vs 86
overall, Bannon 418 vs 390, Presler 441 vs 382, n=78 China posts across six
channels).

Two deliberate limits:

**This is a BONUS, never a gate.** The editors' instruction, 2026-09-22: "inside
China 和别的国家跟中国，这些继续保留，这是 China breaks 频道，不是 maga 频道."
CCP internal politics and third-country coverage are this channel's own beat and
must keep reaching the feed on their own merits. All this does is break ties in
the publish-cycle ranking.

**The pattern is the one that was measured, verbatim.** Tightening it to
something more semantically careful would be a different detector with no
evidence behind it. n=99 at p≈0.06 is suggestive, not settled — this needs a
re-test once the engagement collector has a few weeks of history, and the
`ranking.us_stake_bonus` knob is there so it can be turned off in one line.
"""

from __future__ import annotations

import re

_US_STAKE = re.compile(
    r"\b(u\.s\.|us |usa|america|american|washington|white house|trump|congress|"
    r"senate|pentagon|fbi|cia|state department|treasury|commerce department|doj|"
    r"nvidia|apple|boeing|tesla|microsoft|google|intel|texas|florida|california|"
    r"governor|senator|lawmaker|republican|gop)",
    re.IGNORECASE,
)
# No trailing \b on purpose. Adding one silently dropped "U.S." — \b after a
# period needs a word character next, and "U.S. Treasury" has a space — which
# cut the match rate from the measured 39% to 21%. This is the pattern the
# measurement above was made with, character for character.


def has_us_stake(text: str) -> bool:
    """True when the post text names an American actor, institution, company or
    state. Runs on the generated post copy, not the source article — what the
    reader actually sees is what the measurement was made on."""
    return bool(_US_STAKE.search(text or ""))
