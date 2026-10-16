"""Pre-publish fact check (2026-10-13, Linear AI-74).

Claude Haiku reads the article and the caption and labels each factual claim
in the caption SUPPORTED / CONTRADICTED / NOT_FOUND / UNCLEAR against the
article only. The writer's own characterisation ("part of Beijing's push",
"another bloc ally") is house style and is not checked -- user, 10-13: "只管
文章内事实".

Measured offline on 250 posts already checked by hand (AI-51): blocking on
FAIL only, Haiku caught 25-27 of 39 posts with a real factual error and
flagged 12-15 of 197 the hand check had passed, about half of which turned out
to be real errors too. 4o-mini and 5.4-mini, on the same posts, caught 9-11.
A FAIL gets one correction with these reasons (main_publish.py's
_fact_checked): since 2026-10-15 that correction is Claude Haiku's own
(FactChecker.fix), never the writer's, and it is checked again; a second FAIL
drops the candidate.

When Haiku is broken -- the call raises, times out, is refused, or the check's
answer is unreadable -- the same check or correction runs once on gpt-4o-mini
(config.openai.chat_model), same prompt, same message (2026-10-16, Linear
AI-89, user: 坏了fallback到4o mini). A usable answer Haiku gave is never
second-guessed by 4o-mini. Fails open: no key, or both models failing, returns
verdict "ERROR", which the caller treats like PASS. Every check is logged to
logs/fact_check.jsonl with the model that answered."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from core.config import AppConfig
from core.openai_client import create_openai_client

logger = logging.getLogger(__name__)

# Words a post never has and a note about the post always has: Haiku answering
# the checker instead of correcting (DailyNews, offline 10-15: "ADAspace is not
# stated as the launcher; the article says China launched 10 satellites.").
_NOT_A_POST = re.compile(r"\b(?:the|this) (?:article|post)\b|\bnot stated\b|\b[SP]\d{1,2}\b"
                         # Its own working pasted in (news channels, offline 10-15:
                         # "Wait, I must return the corrected post only. Here it is: ...").
                         r"|\bcorrected post\b|(?:^|\n)\s*Wait\b|\bhere it is\b|\bI (?:should|must|need to) (?:return|output)\b",
                         re.I)


def _new_note(text: str, post: str):
    """The first note-like phrase in text that the post did not already carry.
    China Breaks drafts write "The article says ..." themselves; a correction
    keeping that phrase is not Haiku commenting (10-16: two good corrections
    dropped for it)."""
    had = post.lower()
    return next((m for m in _NOT_A_POST.finditer(text) if m.group(0).lower() not in had), None)

_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["claims", "verdict"],
    "properties": {
        "claims": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "label", "reason"],
            "properties": {"text": {"type": "string"},
                           "label": {"type": "string", "enum": ["SUPPORTED", "CONTRADICTED", "NOT_FOUND", "UNCLEAR"]},
                           "reason": {"type": "string"}}}},
        "verdict": {"type": "string", "enum": ["PASS", "FAIL", "UNSURE"]},
    },
}


_VERDICTS = ("PASS", "FAIL", "UNSURE")
_LABELS = ("SUPPORTED", "CONTRADICTED", "NOT_FOUND", "UNCLEAR")


def _parse(text: str) -> tuple[str, list, list[str]]:
    """(verdict, claims, problems) from a checker's JSON answer; raises on an
    answer that does not fit _SCHEMA, which counts as the model being broken."""
    data = json.loads(text)
    verdict, claims = data["verdict"], data["claims"]
    if verdict not in _VERDICTS or not isinstance(claims, list) \
            or any(c.get("label") not in _LABELS or "text" not in c or "reason" not in c for c in claims):
        raise ValueError("answer does not fit the schema")
    problems = [f"\"{c['text']}\": {c['reason']}" for c in claims
                if c["label"] in ("CONTRADICTED", "NOT_FOUND")]
    return verdict, claims, problems


class FactChecker:
    def __init__(self, config: AppConfig) -> None:
        fc = config.fact_check
        self.enabled = fc.enabled and bool(fc.api_key)
        self._model = fc.model
        self._effort = fc.effort
        self._system = Path(fc.prompt_file).read_text(encoding="utf-8") if self.enabled else ""
        self._fix_system = (Path("prompts/fact_fix_prompt.txt").read_text(encoding="utf-8")
                            if self.enabled else "")
        self._client = (anthropic.AsyncAnthropic(api_key=fc.api_key, timeout=fc.timeout_seconds, max_retries=1)
                        if self.enabled else None)
        if fc.enabled and not fc.api_key:
            logger.warning("FactChecker: ANTHROPIC_API_KEY not set -- fact check off")
        # gpt-4o-mini, for when Haiku is broken (2026-10-16, AI-89). Without an
        # OpenAI key there is simply no fallback; nothing else changes.
        self._timeout = fc.timeout_seconds
        self._fallback_model = config.openai.chat_model
        self._openai = None
        if self.enabled and config.openai.api_key:
            try:
                self._openai = create_openai_client(config)
            except Exception as e:
                logger.warning("FactChecker: no %s fallback (%s)", self._fallback_model, type(e).__name__)

    async def check(self, title: str, article: str, post: str, url: str = "", stage: str = "draft",
                    ignore: list[str] | tuple = ()) -> dict:
        """{"verdict": PASS|FAIL|UNSURE|ERROR|OFF, "problems": [ "claim: reason", ...]}"""
        # Text our own code wrote into the caption (money_tags.conversions()),
        # taken out so the checker only reads what the writer claimed.
        checked = post
        for s in ignore:
            checked = checked.replace(s, "")
        if not self.enabled:
            return {"verdict": "OFF", "problems": []}
        user = (f"TODAY: {datetime.now(timezone.utc).date().isoformat()}\n\nARTICLE TITLE: {title}"
                f"\n\nARTICLE:\n{article}\n\nPOST:\n{checked}")
        verdict, problems, claims, usage, model = "ERROR", [], [], None, self._model
        parsed = None
        try:
            resp = await self._client.messages.create(
                model=self._model, max_tokens=16000, system=self._system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self._effort, "format": {"type": "json_schema", "schema": _SCHEMA}})
            usage = {"in": resp.usage.input_tokens, "out": resp.usage.output_tokens}
            if resp.stop_reason == "refusal":
                logger.warning("FactChecker: Haiku refused for %s -- trying %s", url, self._fallback_model)
            else:
                parsed = _parse(next(b.text for b in resp.content if b.type == "text"))
        except Exception as e:
            logger.warning("FactChecker: Haiku %s for %s -- trying %s", type(e).__name__, url, self._fallback_model)
        if parsed is None and self._openai is not None:
            model, usage = self._fallback_model, None
            try:
                resp = await self._openai.chat.completions.create(
                    model=self._fallback_model, max_tokens=4000, timeout=self._timeout,
                    messages=[{"role": "system", "content": self._system}, {"role": "user", "content": user}],
                    response_format={"type": "json_schema",
                                     "json_schema": {"name": "fact_check", "schema": _SCHEMA, "strict": True}})
                usage = {"in": resp.usage.prompt_tokens, "out": resp.usage.completion_tokens}
                msg = resp.choices[0].message
                if getattr(msg, "refusal", None):
                    raise ValueError("refusal")
                parsed = _parse(msg.content or "")
            except Exception as e:  # fails open: a broken check never stops a post
                logger.warning("FactChecker: %s fallback %s for %s -- letting it through",
                               self._fallback_model, type(e).__name__, url)
        elif parsed is None:
            logger.warning("FactChecker: no fallback for %s -- letting it through", url)
        if parsed is not None:
            verdict, claims, problems = parsed
        self._log(url, stage, post, verdict, claims, usage, model)
        return {"verdict": verdict, "problems": problems}

    async def fix(self, title: str, article: str, post: str, problems: list[str]) -> str | None:
        """Claude Haiku's correction of a post this check failed (2026-10-15, user:
        四个新闻频道也应该让 haiku 重写 -- the writer model only writes the first
        draft), or None -- unchanged, a note instead of a post, or both Haiku and
        its gpt-4o-mini fallback refused or erroring (2026-10-16, AI-89) -- and
        the caller drops the candidate. The prompt is
        prompts/fact_fix_prompt.txt. Ported from DailyNews b671d57, where on 99
        live drafts the check failed Haiku's corrections passed 90 against 89
        for the writer model's rewrites."""
        if not self.enabled or not problems:
            return None
        user = (f"ARTICLE TITLE: {title}\n\nARTICLE:\n{article}\n\nPOST:\n{post}"
                "\n\nPROBLEMS:\n" + "\n".join(f"- {p}" for p in problems))
        text = None
        try:
            resp = await self._client.messages.create(
                model=self._model, max_tokens=4000, system=self._fix_system,
                messages=[{"role": "user", "content": user}], output_config={"effort": "low"})
            if resp.stop_reason == "refusal":
                logger.warning("FactChecker: Haiku fix refused -- trying %s", self._fallback_model)
            else:
                text = next((b.text for b in resp.content if b.type == "text"), "")
        except Exception as e:
            logger.warning("FactChecker: Haiku fix failed (%s) -- trying %s", type(e).__name__, self._fallback_model)
        # Haiku broken (2026-10-16, AI-89): the same correction once on
        # gpt-4o-mini. An answer Haiku did give, however unusable, is not a
        # breakage and does not fall back.
        if text is None:
            if self._openai is None:
                return None
            try:
                resp = await self._openai.chat.completions.create(
                    model=self._fallback_model, max_tokens=4000, timeout=self._timeout,
                    messages=[{"role": "system", "content": self._fix_system}, {"role": "user", "content": user}])
                msg = resp.choices[0].message
                if getattr(msg, "refusal", None):
                    raise ValueError("refusal")
                text = msg.content or ""
                logger.info("FactChecker: fix written by %s", self._fallback_model)
            except Exception as e:
                logger.warning("FactChecker: %s fix failed too (%s)", self._fallback_model, type(e).__name__)
                return None
        # Paragraphs kept, each one's whitespace tidied; "exports,." after a cut.
        text = "\n\n".join(" ".join(p.split()) for p in re.split(r"\n\s*\n", text.strip()) if p.strip())
        text = re.sub(r"\s*,\s*([.!?])", r"\1", text)
        note = _new_note(text, post)
        if not text or " ".join(text.split()) == " ".join(post.split()) or note:
            logger.warning("FactChecker: fix unusable (%s): %r",
                           "a note, not a post" if note else "empty or unchanged", text[:200])
            return None
        return text

    @staticmethod
    def _log(url, stage, post, verdict, claims, usage, model="") -> None:
        try:
            row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "url": url, "stage": stage,
                   "verdict": verdict, "post": post, "claims": claims, "usage": usage, "model": model}
            with open("logs/fact_check.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass
