"""AI-89 (2026-10-16): the caption cache's fact-check marker, the money check on
Haiku's correction, the checks on the card headline/deck, and the fact check's
gpt-4o-mini fallback. No network, no API keys: model clients are fakes.

Runs under pytest, or directly: python3 tests/test_caption_checks_ai89.py.
A third-party module that is not installed (the laptop has no redis,
anthropic or langdetect) is stubbed; on the VM the real ones are used."""
import asyncio
import importlib
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _stub_if_missing(name: str, **attrs) -> None:
    try:
        importlib.import_module(name)
    except Exception:
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        sys.modules[name] = mod


class _LangDetectException(Exception):
    pass


_stub_if_missing("redis")
_stub_if_missing("redis.asyncio", from_url=lambda *a, **k: None)
sys.modules["redis"].asyncio = sys.modules["redis.asyncio"]
_stub_if_missing("anthropic", AsyncAnthropic=object)
_stub_if_missing("langdetect", DetectorFactory=types.SimpleNamespace(seed=0),
                 LangDetectException=_LangDetectException, detect=lambda text: "en")
_stub_if_missing("core.config", AppConfig=object)  # pydantic on Python < 3.10

from core import money_tags  # noqa: E402
from core.redis_store import CaptionCache  # noqa: E402
from core import fact_check  # noqa: E402
from agents.card_headline import card_text_problem  # noqa: E402


# ---- CaptionCache marker ---------------------------------------------------

class FakeRedis:
    def __init__(self, fail: bool = False):
        self.data, self.fail = {}, fail

    async def get(self, key):
        if self.fail:
            raise ConnectionError("down")
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        if self.fail:
            raise ConnectionError("down")
        self.data[key] = value

    async def delete(self, *keys):
        if self.fail:
            raise ConnectionError("down")
        for k in keys:
            self.data.pop(k, None)


def _cache(client) -> CaptionCache:
    cache = CaptionCache.__new__(CaptionCache)
    cache._prefix, cache._ttl, cache._client = "chinabreaks:caption:", 172800, client
    return cache


# ---- money in Haiku's correction --------------------------------------------

_FILLED = "Beijing fined Alibaba 12.2 billion yuan (about $1.7 billion) on Tuesday, the CCP said."


def test_correction_may_keep_or_delete_amounts():
    for after in ("Beijing fined Alibaba 12.2 billion yuan (about $1.7 billion), the CCP said.",
                  "Beijing fined Alibaba 12.2 billion yuan, the CCP said.",
                  "Beijing fined Alibaba more than 12.2 billion yuan, the CCP said.",
                  "Beijing fined Alibaba, the CCP said."):
        assert money_tags.added_amounts(_FILLED, after) == [], after


def test_correction_may_not_add_or_change_an_amount():
    for after, raw in (("Beijing fined Alibaba 1.22 billion yuan, the CCP said.", "1.22 billion yuan"),
                       ("Beijing fined Alibaba $12.2 billion, the CCP said.", "$12.2 billion"),
                       ("Beijing fined Alibaba 12.2 billion yuan and HK$300 million.", "HK$300 million")):
        added = money_tags.added_amounts(_FILLED, after)
        assert [a.raw for a in added] == [raw], (after, added)


# ---- card headline / deck ---------------------------------------------------

_CAPTION = ("The U.S. Treasury sanctioned two officials over abuses of Uyghur detainees, and Beijing's "
            "foreign ministry said on Tuesday the move breaks 1,200 pages of trade commitments, up 4.50% "
            "from last year.")
_ARTICLE = "The Treasury Department announced sanctions on Tuesday. " * 10


def test_card_text_supported_by_the_caption_passes():
    assert card_text_problem("Beijing's ministry condemns US sanctions over Uyghurs",
                             "The foreign ministry cites 1200 pages of commitments, up 4.5%",
                             _CAPTION, _ARTICLE) is None


def test_card_text_refusals():
    cases = (
        (("Beijing condemns US sanctions over 3 officials", ""), "number"),
        (("Beijing condemns US sanctions over Xinjiang officials", ""), "name"),
        (("Beijing condemns sanctions", "Wang Yi says the move is illegal"), "name"),
        (("Beijing condemns Biden sanctions on officials", ""), "invented-biden"),
        (("Beijing condemns sanctions from former President Trump", ""), "caption_guard"),
        (("Beijing condemns 美国 sanctions on officials", ""), "not English"),
    )
    for (headline, deck), why in cases:
        problem = card_text_problem(headline, deck, _CAPTION, _ARTICLE)
        assert problem and why in problem, (headline, deck, problem)


# ---- fact check: gpt-4o-mini when Haiku is broken ---------------------------

NS = types.SimpleNamespace
_PASS = json.dumps({"claims": [{"text": "a", "label": "SUPPORTED", "reason": "r"}], "verdict": "PASS"})
_FAIL = json.dumps({"claims": [{"text": "a", "label": "CONTRADICTED", "reason": "r"}], "verdict": "FAIL"})


class FakeHaiku:
    def __init__(self, outcome):
        self.outcome, self.calls = outcome, 0
        self.messages = self

    async def create(self, **kw):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        stop, text = self.outcome
        return NS(stop_reason=stop, content=[NS(type="text", text=text)],
                  usage=NS(input_tokens=1, output_tokens=1))


class FakeOpenAI:
    def __init__(self, outcome):
        self.outcome, self.calls = outcome, 0
        self.chat = NS(completions=self)

    async def create(self, **kw):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return NS(choices=[NS(message=NS(content=self.outcome, refusal=None))],
                  usage=NS(prompt_tokens=1, completion_tokens=1))


def _checker(haiku, openai):
    fc = fact_check.FactChecker.__new__(fact_check.FactChecker)
    fc.enabled, fc._model, fc._effort, fc._system, fc._fix_system = True, "claude-haiku-5-5", "low", "S", "F"
    fc._client, fc._openai, fc._fallback_model, fc._timeout = haiku, openai, "gpt-4o-mini", 60.0
    fc.rows = []
    fc._log = lambda *a: fc.rows.append(a)
    return fc


def test_check_falls_back_when_haiku_is_broken():
    async def run():
        for haiku in (FakeHaiku(TimeoutError()), FakeHaiku(("refusal", "")), FakeHaiku(("end_turn", "not json"))):
            fc = _checker(haiku, FakeOpenAI(_FAIL))
            out = await fc.check("T", "A", "P", "u")
            assert out["verdict"] == "FAIL" and out["problems"], out
            assert fc.rows[-1][-1] == "gpt-4o-mini"
    asyncio.run(run())


def test_check_does_not_fall_back_on_a_real_answer_and_errors_when_both_fail():
    async def run():
        openai = FakeOpenAI(_FAIL)
        fc = _checker(FakeHaiku(("end_turn", _PASS)), openai)
        assert (await fc.check("T", "A", "P", "u"))["verdict"] == "PASS"
        assert openai.calls == 0 and fc.rows[-1][-1] == "claude-haiku-5-5"
        fc = _checker(FakeHaiku(TimeoutError()), FakeOpenAI(TimeoutError()))
        assert (await fc.check("T", "A", "P", "u"))["verdict"] == "ERROR"
        fc = _checker(FakeHaiku(TimeoutError()), None)  # no OpenAI key: no fallback
        assert (await fc.check("T", "A", "P", "u"))["verdict"] == "ERROR"
    asyncio.run(run())


def test_fix_falls_back_only_when_haiku_is_broken():
    async def run():
        fc = _checker(FakeHaiku(TimeoutError()), FakeOpenAI("Beijing said Y."))
        assert await fc.fix("T", "A", "Beijing said X.", ["p"]) == "Beijing said Y."
        openai = FakeOpenAI("Beijing said Y.")
        fc = _checker(FakeHaiku(("end_turn", "Beijing said X.")), openai)  # unchanged: unusable, not broken
        assert await fc.fix("T", "A", "Beijing said X.", ["p"]) is None and openai.calls == 0
        fc = _checker(FakeHaiku(("refusal", "")), FakeOpenAI(TimeoutError()))
        assert await fc.fix("T", "A", "Beijing said X.", ["p"]) is None
    asyncio.run(run())


# ---- CaptionCache marker: stored in the caption's own value (10-16) --------
# One key per caption, so a cache hit stays one Redis command (Upstash bills
# per command); is_checked() after get() needs no call of its own.

class _MarkRedis:
    def __init__(self, fail=False):
        self.data, self.fail, self.calls = {}, fail, 0

    async def get(self, key):
        self.calls += 1
        if self.fail:
            raise ConnectionError("down")
        return self.data.get(key)

    async def set(self, key, value, ex=None, nx=False):
        self.calls += 1
        if self.fail:
            raise ConnectionError("down")
        self.data[key] = value

    async def delete(self, *keys):
        self.calls += 1
        if self.fail:
            raise ConnectionError("down")
        for k in keys:
            self.data.pop(k, None)


def _mark_cache(client):
    c = CaptionCache.__new__(CaptionCache)
    c._prefix, c._ttl, c._client, c._checked = "test:caption:", 172800, client, {}
    return c


def test_marker_rides_in_the_caption_value():
    async def run():
        r = _MarkRedis()
        await _mark_cache(r).set("h1", "Beijing said X.")
        assert r.data == {"test:caption:h1": "Beijing said X."}          # unchecked: stored as before
        c = _mark_cache(r)
        assert await c.get("h1") == "Beijing said X."
        assert not await c.is_checked("h1", "Beijing said X.")           # cached before the marker existed
        await c.set("h1", "Beijing said X.", checked=True)
        assert list(r.data) == ["test:caption:h1"]                       # no second key
        c = _mark_cache(r)
        assert await c.get("h1") == "Beijing said X."                    # the marker never leaks into the text
        n = r.calls
        assert await c.is_checked("h1", "Beijing said X.")
        assert r.calls == n                                              # answered without a Redis call
        assert not await c.is_checked("h1", "Beijing said Y.")           # text changed after its check
        await c.mark_checked("h1", "Beijing said Z.")
        assert await _mark_cache(r).is_checked("h1", "Beijing said Z.")  # without a get() first: one GET
        await c.delete("h1")
        assert r.data == {} and await c.get("h1") is None
        assert not await c.is_checked("h1", "Beijing said Z.")
    asyncio.run(run())


def test_marker_fails_open():
    async def run():
        down = _mark_cache(_MarkRedis(fail=True))
        assert await down.get("h1") is None
        assert not await down.is_checked("h1", "x")
        await down.set("h1", "x", checked=True)   # no raise
        await down.mark_checked("h1", "x")
        await down.delete("h1")
        assert not await down.is_checked("h1", "x")
        none = _mark_cache(None)
        assert not await none.is_checked("h1", "x")
        await none.mark_checked("h1", "x")
        await none.delete("h1")
    asyncio.run(run())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
