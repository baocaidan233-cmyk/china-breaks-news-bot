from __future__ import annotations

import re
from pathlib import Path

from core.config import AppConfig
from core.openai_client import create_openai_client

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

    async def write(self, title: str, article: str, context: str = "") -> str:
        """`context` (2026-08-31) — optional prior-developments/related-
        events summary for this story, built by main_publish.py from
        core/qdrant_store.py's EventStore (timeline + related_event_ids on
        the matched event, if any). Kept as its own labeled section in the
        USER message, separate from the static system prompt file (see
        prompts/content_gen_prompt.txt's "OPTIONAL BACKGROUND" section for
        the model-facing instructions on how to use it) — appended only
        when non-empty, so omitting it reproduces today's exact behavior."""
        user_message = f"Title:  {title}\n\nArticle: {article}"
        if context:
            user_message += f"\n\nBackground: {context}"
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return (resp.choices[0].message.content or "").strip()

    @staticmethod
    def is_no_comment(text: str) -> bool:
        cleaned = _MARKDOWN_RE.sub("", text).strip().rstrip(".!").strip().lower()
        return cleaned == NO_COMMENT.lower()
