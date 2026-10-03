from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from core.config import AppConfig
from core.openai_client import create_openai_client
from core.date_context import date_block

NO_COMMENT = "No comment"

# Strips markdown emphasis (*_`#) and trailing punctuation before comparing —
# ported from AM1ST, where a real published post revealed the model
# sometimes wraps its "decline to write this" signal in markdown
# ("**No comment**"), which an exact-string match doesn't recognize as the
# same thing — that post then proceeded through ranking/dedup/publish as
# if it were real content. Kept here as a preventive measure from day one.
_MARKDOWN_RE = re.compile(r"[*_`#]+")


class Writer:
    """Content generation — prompts/content_gen_prompt.txt is China
    Breaks' own content-gen prompt (finalized by the user from this
    project's live n8n export plus a design doc, copied here verbatim,
    including its explicit actor-first — not media-outlet-name-first —
    opening rule). Wired to config.openai.chat_model (gpt-4o-mini) per
    this project's standing model-architecture rule — deliberately NOT
    gpt-4.1-mini, which is what the old n8n content-gen workflow actually
    used; gpt-4o-mini matches AM1ST (this codebase's architecture origin)
    and the project's own standing "gpt-4o-mini everywhere" rule instead.

    Deliberately a separate LLM call from Scorer, not merged into one
    request — mechanism ported unchanged from AM1ST: Scoring decides "is
    this worth reporting", this decides "how to write it."

    Called from the publish cycle only (same reasoning as
    agents/extractor.py's docstring): takes plain title/article text
    rather than a specific Candidate type so it works for whichever model
    the caller has on hand."""

    def __init__(self, config: AppConfig) -> None:
        self._client = create_openai_client(config)
        self._model = config.openai.chat_model
        self._system_prompt = Path(config.openai.content_gen_prompt_file).read_text(encoding="utf-8")
        self._system_sha = hashlib.sha256(self._system_prompt.encode("utf-8")).hexdigest()[:12]

    async def write(self, title: str, article: str, context: str = "", is_opinion: bool = False, published_at=None) -> str:
        """`context` (2026-08-31) — optional prior-developments/related-
        events summary for this story, built by main_publish.py from
        core/qdrant_store.py's EventStore (timeline + related_event_ids on
        the matched event, if any). Kept as its own labeled section in the
        USER message, separate from the static system prompt file (see
        prompts/content_gen_prompt.txt's "OPTIONAL BACKGROUND" section for
        the model-facing instructions on how to use it) — appended only
        when non-empty, so omitting it reproduces today's exact behavior.

        `is_opinion` (2026-09-06, ported from AM1ST) — set when
        agents/staleness_checker.py classified this article as OPINION: a
        genuine analysis/commentary piece about an older event with real
        argument/expert input, not a pure rehash (that gets dropped before
        write() is ever called) and not fresh news (is_opinion stays
        False). Appends a short framing instruction rather than asking
        Writer to detect this itself — AM1ST tried three earlier attempts
        at self-detection inside this same call and all three failed (see
        StalenessChecker's docstring); telling Writer HOW to frame
        something it's already been told IS opinion is a much simpler
        ask.
        `published_at` (2026-09-28) — the article's own publication date, used
        by core/date_context.py to resolve the source's relative dates before
        the model sees them. Optional and fails open: without it the message
        carries today's date alone, which is what it carried before.
        """
        user_message = (f"{date_block(article, published_at)}\n\n"
                        f"Title:  {title}\n\nArticle: {article}")
        if context:
            user_message += f"\n\nBackground: {context}"
        if is_opinion:
            user_message += (
                "\n\nNote: this article is analysis/commentary about an event "
                "that already happened, not a fresh news report. Frame the "
                "post accordingly — as opinion/analysis (e.g. \"Analysts "
                "argue...\", \"The debate centers on...\", naming the actual "
                "expert or source where the article does) — do not write it "
                "as if the underlying event just happened today, and do not "
                "use \"BREAKING.\""
            )
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        post = (resp.choices[0].message.content or "").strip()
        self._log_call(title, article, context, is_opinion, published_at, post)
        return post

    def _log_call(self, title, article, context, is_opinion, published_at, post) -> None:
        """One line per production call in logs/writer_calls.jsonl (2026-10-03),
        the input side of any later prompt A/B: re-fetching sources weeks
        later loses some to paywalls and edits. system_sha splits the log by
        prompt version. Logging only — a failure here never touches the post."""
        try:
            row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "system_sha": self._system_sha, "title": title, "article": article[:6000],
                   "context": context, "is_opinion": is_opinion,
                   "published_at": published_at.isoformat() if published_at else None, "post": post}
            with open("logs/writer_calls.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass

    @staticmethod
    def is_no_comment(text: str) -> bool:
        cleaned = _MARKDOWN_RE.sub("", text).strip().rstrip(".!").strip().lower()
        return cleaned == NO_COMMENT.lower()
