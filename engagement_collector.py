#!/usr/bin/env python3
"""Hourly engagement + follower snapshot for this channel's own Gettr feed.

Ported from AM1ST's copy, which exists because every content signal it tested
came back inside the noise band. The binding constraint is sample size, not
analysis: Gettr's posts endpoint is hard-capped at 200 posts, about two days at
this channel's volume, and nothing smaller than roughly a 1.5x effect is
measurable at that n while every candidate signal measured sits at 1.0-1.3x.

China Breaks needs it for a specific reason. Three changes shipped on
2026-09-22 aimed at the editors' target of a higher like floor — a
publish-ranking bonus for stories with an American stake, a new scoring theme
for the CCP's reach onto foreign soil, and a rewrite of the writer prompt.
Only the last could be verified immediately, and only on register (abstract
evaluative adjectives 80% -> 56%, p=0.0002 over 90 paired generations), not on
engagement. The us_stake signal itself sits at p~0.06 on n=99. Whether any of
it moves likes is currently unanswerable, and stays unanswerable until there is
history to answer it with.

Measured baseline the day this started, on posts at least 48h old: median 16
likes, mean 17.1, p75 19, 20% at 20 or above. Likes settle within 12-24 hours
and barely move after. Target is 30.

Writes two append-only JSONL files under logs/:
  engagement_snapshots.jsonl  one row per post per CHANGE (not per run --
                              re-writing 200 unchanged rows hourly would add
                              ~1MB/day of nothing). A post's row is emitted on
                              first sight and then only when lk/cm/sh move, so
                              the file is itself the growth curve.
  follower_history.jsonl      one row per run, unconditionally.

API notes (see reference_gettr_undocumented_api): the posts endpoint needs
fp=f_uo or it returns an empty list for a busy account, `aux.post` is a dict
keyed by post id rather than a list, and the handle must be the lowercase
internal `_id` from /s/uinf/, not the display `ousername`.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HANDLE = os.environ.get("GETTR_COLLECT_HANDLE", "gettrfoodofficial")
LOG_DIR = Path(__file__).resolve().parent / "logs"
SNAP_PATH = LOG_DIR / "engagement_snapshots.jsonl"
FOLLOWER_PATH = LOG_DIR / "follower_history.jsonl"
STATE_PATH = LOG_DIR / ".engagement_last_seen.json"
INCL = urllib.parse.quote("posts|stats|userinfo|shared|liked")
UA = {"User-Agent": "Mozilla/5.0"}
MAX_POSTS = 200  # the endpoint's own hard cap; offset >= 200 returns []


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_profile(handle: str) -> dict:
    return _get(f"https://api.gettr.com/s/uinf/{handle}").get("result", {}).get("data", {})


def fetch_posts(handle: str, want: int = MAX_POSTS) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    offset = 0
    while len(rows) < want:
        url = (f"https://api.gettr.com/u/user/{handle}/posts"
               f"?offset={offset}&max=20&dir=fwd&incl={INCL}&fp=f_uo")
        result = _get(url).get("result", {})
        listing = result.get("data", {}).get("list", [])
        if not listing:
            break
        aux = result.get("aux", {})
        posts = aux.get("post") or {}
        stats = aux.get("s_pst") or {}
        for item in listing:
            pid = (item.get("activity") or {}).get("tgt_id") or item.get("_id")
            post = posts.get(pid)
            if not isinstance(post, dict) or pid in seen:
                continue
            seen.add(pid)
            s = stats.get(pid) or {}
            rows.append({
                "post_id": pid,
                "cdate": post.get("cdate"),
                "lk": s.get("lkbpst", 0),
                "cm": s.get("cm", 0),
                "sh": s.get("shbpst", 0),
                "src": post.get("prevsrc") or "",
                "ttl": (post.get("ttl") or "")[:200],
                # Two fields AM1ST's copy does not keep, because this channel
                # has two questions its does not. Both were changed on
                # 2026-09-22 on evidence that cannot yet be confirmed against
                # real engagement, and neither can be re-derived later: the
                # API only ever shows the newest 200 posts, so a post's text
                # is gone the moment it falls out of that window.
                #   txt   lets us_stake_bonus (agents/us_stake.py, p~0.06 at
                #         n=99) and the content_gen_prompt rewrite be re-tested
                #         against settled likes instead of assumed
                #   imgs   separates the split photo card from the text card
                "txt": (post.get("txt") or "")[:600],
                "imgs": bool(post.get("imgs")),
            })
        offset += len(listing)
        time.sleep(0.4)
    return rows


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
    except Exception as e:
        print(f"warn: could not persist state ({e}) -- next run re-emits every post", file=sys.stderr)


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = int(time.time())

    try:
        profile = fetch_profile(HANDLE)
    except Exception as e:
        print(f"profile fetch failed: {e}", file=sys.stderr)
        profile = {}
    if profile:
        with FOLLOWER_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": now, "followers": profile.get("flg"),
                                "following": profile.get("flw")}) + "\n")

    try:
        posts = fetch_posts(HANDLE)
    except Exception as e:
        print(f"posts fetch failed: {e}", file=sys.stderr)
        return 1
    if not posts:
        print("no posts returned -- check fp=f_uo and that the handle is the lowercase internal id",
              file=sys.stderr)
        return 1

    state = load_state()
    written = 0
    with SNAP_PATH.open("a", encoding="utf-8") as f:
        for p in posts:
            sig = [p["lk"], p["cm"], p["sh"]]
            if state.get(p["post_id"]) == sig:
                continue  # unchanged since last run -- the point of the state file
            state[p["post_id"]] = sig
            f.write(json.dumps({"ts": now, **p}, ensure_ascii=False) + "\n")
            written += 1

    # Keep state bounded: the API only ever shows the newest MAX_POSTS, so
    # anything older can never come back and its key is dead weight.
    live = {p["post_id"] for p in posts}
    state = {k: v for k, v in state.items() if k in live}
    save_state(state)

    print(f"{len(posts)} posts fetched, {written} changed rows written, "
          f"followers={profile.get('flg')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
