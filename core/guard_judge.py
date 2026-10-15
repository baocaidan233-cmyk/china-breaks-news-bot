"""Second opinion before an event-store guard drops an article (2026-10-15,
port of AM1ST's core/guard_judge.py, Linear AI-69/AI-70).

The two guards in main.py ("already-published event", "stale event with
nothing new") drop on the subtype judge's RESTATEMENT / CORROBORATION alone.
On AM1ST that dropped 49 of 220 articles wrongly (22%): major developments
and articles that were not the same event at all. With the event judge now
merging follow-ups into their event (prompts/same_event_store_prompt.txt),
more of those would reach the guards, so the guards ask Claude Haiku first
(prompts/guard_judge_prompt.txt, this channel's own list of what counts as a
major development) and drop only on NO_NEW.

Haiku unable to answer (no credit, an error) means the guard drops as it did
before. A cluster released by the first guard is not judged again by the
second. Releases are logged as check_type event_guard_release."""
from __future__ import annotations

import logging
from pathlib import Path

from core import haiku
from core.event_identity import log_decision

logger = logging.getLogger(__name__)

_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["verdict", "reason", "quote"],
    "properties": {"verdict": {"type": "string", "enum": ["DIFFERENT_EVENT", "MAJOR_DEVELOPMENT", "NO_NEW"]},
                   "reason": {"type": "string"}, "quote": {"type": "string"}},
}
_prompt: str | None = None
_seen: dict[tuple, tuple[str, dict]] = {}


async def guard_releases(config, guard: str, members, matched: dict, new_text: str) -> bool:
    """True when Haiku says the cluster is a major development or a different
    event, so the guard must not drop it."""
    global _prompt
    ev = config.entity_verifier
    if not (ev.guard_judge_enabled and ev.haiku_api_key):
        return False
    key = (members[0][0].url, matched.get("event_id"))
    if key not in _seen:
        if _prompt is None:
            _prompt = Path(ev.guard_judge_prompt_file).read_text(encoding="utf-8")
        event_text = matched.get("text") or matched.get("representative_text", "")
        d = await haiku.ask(ev.haiku_api_key, _prompt, f"TEXT A:\n{event_text}\n\nTEXT B:\n{new_text}", _SCHEMA)
        verdict = (d or {}).get("verdict") or "ERROR"
        if verdict == "ERROR":  # not remembered: the next cycle asks again
            return False
        if len(_seen) > 5000:
            _seen.clear()
        _seen[key] = (verdict, d)
    verdict, d = _seen[key]
    released = verdict in ("MAJOR_DEVELOPMENT", "DIFFERENT_EVENT")
    log_decision(config, {
        "check_type": "event_guard_release" if released else "event_guard_judged",
        "guard": guard, "guard_judge": verdict, "guard_judge_raw": d,
        "candidate_url": members[0][0].url, "event_id": matched.get("event_id"),
        "cosine_score": matched.get("_score"), "candidate_text": new_text,
        "matched_text": matched.get("text") or matched.get("representative_text", ""),
    })
    if released:
        logger.info("run_cycle: %s kept — %s guard, but the guard judge says %s (%s)",
                    members[0][0].url, guard, verdict, (d.get("reason") or "")[:160])
    return released
