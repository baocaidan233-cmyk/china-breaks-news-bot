from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.config import AppConfig
from core.openai_client import create_openai_client


class StalenessChecker:
    """Separate, single-purpose LLM call (2026-09-06, ported from AM1ST's
    own 2026-09-05 addition) — classifies an article as FRESH / OPINION /
    STALE, BEFORE Writer ever runs on it. Real AM1ST incident: two op-eds
    analyzing deals that were 9 days and 2 weeks old (a Venezuela oil
    deal, an Ethiopia defense agreement) both got written up as if
    breaking news — the same failure mode applies here to, say, an old
    sanctions-package or trade-deal analysis piece resurfacing weeks
    later.

    Three verdicts, not just stale/fresh, per AM1ST's own real-world
    lesson: a genuine analysis piece with real argument/expert input has
    editorial value and shouldn't be discarded outright just because the
    underlying event is old; it should be published, but framed as
    opinion/analysis rather than as if it just happened. Only a pure
    rehash with no real new angle (STALE) gets dropped entirely.

    Deliberately NOT folded into content_gen_prompt.txt's own
    instructions — AM1ST tried three separate ways to make its Writer
    self-police this in the same call and all three failed on real test
    articles; its own SUBSTANCE REQUIREMENT rule kept winning regardless
    of how the staleness rule was phrased or where it was placed in the
    prompt. Matches this codebase's own established precedent:
    EventVerifier.classify_subtype() is a deliberately separate call from
    same_event(), for the same "one call juggling multiple judgments is
    less reliable than separate, single-purpose calls" reason.

    Runs on config.openai.chat_model, same as every other judgment call
    in this codebase."""

    def __init__(self, config: AppConfig) -> None:
        self._client = create_openai_client(config)
        self._model = config.openai.chat_model
        self._prompt = Path(config.openai.staleness_check_prompt_file).read_text(encoding="utf-8")

    async def classify(self, title: str, article: str) -> tuple[str, str]:
        """Returns (verdict, raw) where verdict is one of "FRESH", "OPINION",
        "STALE" — falls back to "FRESH" (fail open, never blocks a real
        candidate on an unparseable response) if the model's output doesn't
        contain a recognized verdict."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        user_message = self._prompt.format(today=today, title=title, article=article[:6000])
        kwargs = dict(model=self._model, messages=[{"role": "user", "content": user_message}])
        if self._model.startswith("gpt-5"):
            kwargs["max_completion_tokens"] = 80
            kwargs["reasoning_effort"] = "minimal"
        else:
            kwargs["temperature"] = 0
            kwargs["max_tokens"] = 80
        resp = await self._client.chat.completions.create(**kwargs)
        raw = (resp.choices[0].message.content or "").strip()
        verdict = ""
        for line in raw.splitlines():
            if line.upper().startswith("VERDICT"):
                verdict = line.split(":", 1)[1].strip().upper()
                break
        for candidate in ("STALE", "OPINION", "FRESH"):
            if verdict.startswith(candidate):
                return candidate, raw
        return "FRESH", raw
