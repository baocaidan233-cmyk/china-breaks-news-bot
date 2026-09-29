"""Digests, rolling live pages and link lists — the channel does not run them.

User decision, already standing on Leading News and AM1ST: a post has to be
about one thing. A page that is several unrelated stories cannot be written
into one caption without the caption picking one of them and dropping the
rest, or describing the page instead of the news.

Ported from AM1ST 2026-09-29 and re-measured here, which changed it. AM1ST's
English-only rule found 2 of this channel's 1836 pooled candidates. The
roundups this channel actually gets are Chinese, they all scored 5 or above,
and every one of them was on its way to being published:

- "VOA今日焦点 (2026年9月26日)" is 486 characters reading "《VOA今日焦点》重点
  新闻内容包括：" and then six unrelated headlines separated by semicolons.
  Three of these scored 7.
- "中美峰会、PMI及其他本周不容错过的要闻" is Yahoo Finance's week-ahead: the Xi
  summit, Costco earnings, PMI, crude inventories and Treasury yields. 6.
- "界面晚报 |" is a Chinese evening digest, its items separated by 「；」.

A bare 直播 is NOT a pattern here, and that is the whole reason for measuring
rather than porting: "特朗普在习近平访美期间打断电视直播，令中国官员感到不满"
means Trump interrupted a television broadcast. It is one story, it scored 6,
and a naive 直播 rule would have dropped it.

Measured on this channel's own 1836 pooled candidates (2026-09-29): 8 hits,
0.44%, all of them at or above the pool gate and all verified genuine.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# One named event covered live or in full is a single subject, whatever the
# title calls itself.
_SINGLE_EVENT = re.compile(
    r"(?i)\b(rally|speech|remarks|address|announcement|press conference|news conference|"
    r"briefing room|hearing|testimony|interview|debate|summit|signing|roundtable|"
    r"ceremony|launch|vote|votes|verdict|ruling)\b")

# Columns whose brand name contains a digest word but which run one subject.
# A keep-list, so a name missing from it costs one story rather than letting a
# real digest through; add to it when one shows up.
_SINGLE_SUBJECT_COLUMNS = ("breitbart business digest",)

_TITLE_RE = re.compile(
    r"(\b(morning|evening|midday|weekly|weekend|daily|nightly)\s+"
    r"(briefing|brief|digest|round-?up|wrap|rundown|summary)\b"
    r"|\b(news|war|executive|business)\s+(briefing|digest|round-?up|wrap|rundown|summary)\b"
    r"|\bnews\s+(in brief|summary)\b"
    r"|\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+"
    r"(briefing|summary|round-?up|digest)\b"
    r"|\bnews live\s*[:|–-]|\bliveblog\b|\blive blog\b|\bas it happened\b"
    r"|\blive updates?\s*[:|–-]|–\s*(europe|us|uk|world|business|markets?|politics|sport)\s+live\b"
    r"|\band other [\w'’\s]{0,24}(news|stories|headlines|developments|updates|items)\b"
    r"|\bweek in review\b|\bthe week in\b|\btop (stories|headlines)\b|\bnews quiz\b"
    r"|\bopen thread\b|\bmorning minute\b"
    r"|\broundup\s*:|\bdigest\s*:|\bwrap\s*:)", re.IGNORECASE)

# "briefing" is deliberately absent: the White House briefing room is a place.
_URL_RE = re.compile(
    r"/[^/]*(live-?blog|live-?updates?|live-news|newsblog|live-?ticker|digest|round-?up)[^/]*(?=/|$)",
    re.IGNORECASE)
# A whole section served under /live/ is the rolling page, not one story.
_URL_LIVE_SECTION = re.compile(r"/(business|markets?|politics|world|news|sport)/live/", re.IGNORECASE)
# One entry of a live blog on its own page is a single story; the rolling page
# that collects them is not.
# Chinese, Japanese and Korean digest names. Measured, not assumed: a bare
# 直播 is absent on purpose, because 「打断电视直播」 is one story about a
# television broadcast being interrupted, and it scored 6.
_CJK_TITLE_RE = re.compile(
    r"今日焦点|新闻速递|每日简报|战争简报|新闻汇总|快讯汇总|一周回顾|本周回顾|一图看懂"
    r"|(界面|财新|第一财经|新浪|腾讯|网易|凤凰)?(早报|晚报|午报)\s*[|\uff5c:\uff1a]"
    r"|^\s*(早报|晚报)"
    r"|(及其他|以及其他)[^\uff0c\u3002]{0,12}(要闻|新闻|头条)|本周[^\uff0c\u3002]{0,8}要闻|今日要闻|一周要闻"
    r"|\u671d\u520a|きょうのニュース|ニュース一覧"
    r"|\ub274\uc2a4\s?\ube0c\ub9ac\ud551|\ubaa8\ub2dd\s?\ube0c\ub9ac\ud551|\uc8fc\uc694\s?\ub274\uc2a4")

_LIVE_ENTRY_RE = re.compile(r"/(live-blog-update|liveblog_entry|liveblog-entry)/", re.IGNORECASE)


def roundup_rule(title: str, url: str) -> str | None:
    """The name of the rule this candidate breaks, or None."""
    text = title or ""
    lowered = text.lower()
    if any(name in lowered for name in _SINGLE_SUBJECT_COLUMNS):
        return None
    if _SINGLE_EVENT.search(text):
        return None
    if _TITLE_RE.search(text):
        return "title_roundup"
    if _CJK_TITLE_RE.search(text):
        return "cjk_roundup"
    path = urlparse(url or "").path or ""
    if _LIVE_ENTRY_RE.search(path):
        return None
    if _URL_RE.search(path):
        return "url_roundup"
    if _URL_LIVE_SECTION.search(path):
        return "url_live_section"
    return None
