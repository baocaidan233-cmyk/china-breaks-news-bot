"""Money amounts in the caption: read from the source and written by code.

Ported 2026-10-11 from China_Scandal_News_EN's core/money.py (Linear AI-54);
keep the copies in step (also Market_Watcher's core/money_tags.py).

The writer gets a numbered tag per amount found in the source ([AMT1], ...)
and writes the tag; fill() swaps each tag for wording built here. check()
reads every money figure the model typed itself and refuses the caption if
any of them isn't one of the source's amounts.

Why: over 30 days 8 China Breaks posts got a magnitude wrong, every one from
a Chinese-language source (about 16% of those that cite money): 91.4億美元 ->
"$91.4 billion", 600億元 -> "600 billion yuan", 300萬至400萬歐元 -> "€400,000".
No other source language had one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Approximate units of each currency per US dollar.
_PER_USD = {
    "CNY": 7.2, "HKD": 7.8, "TWD": 31.0, "MOP": 8.0, "SGD": 1.33,
    "MYR": 4.5, "EUR": 0.92, "GBP": 0.78, "JPY": 150.0, "KRW": 1380.0,
    "AUD": 1.5, "CAD": 1.37, "RUB": 90.0, "USD": 1.0,
}

# Sites whose bare 元 means Hong Kong dollars (they write 人民币 for RMB).
_HK_SITES = {"hkoncc", "hk01", "stheadline", "stheadline_ent"}

_TRAD = str.maketrans({
    "萬": "万", "億": "亿", "兩": "两", "幣": "币", "圓": "元", "臺": "台",
    "門": "门", "韓": "韩", "盧": "卢", "歐": "欧", "鎊": "镑", "塊": "块",
    "餘": "余", "約": "约", "達": "达", "將": "将", "幾": "几", "紙": "纸",
    "灣": "湾", "東": "东", "華": "华", "國": "国",
})

_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_SMALL_UNIT = {"十": 10, "百": 100, "千": 1000}
_NUM_CHARS = "0-9.零〇一二两三四五六七八九十百千万亿兆"
_RUN = re.compile(rf"(?<![A-Za-z0-9])[{_NUM_CHARS}]*[0-9零〇一二两三四五六七八九十百千][{_NUM_CHARS}]*")

# Longest first: 美元 contains 元, 新台币 contains 台币.
_CN_CURRENCY = [
    ("人民币元", "CNY"), ("人民币", "CNY"), ("新台币", "TWD"), ("台币", "TWD"),
    ("澳门元", "MOP"), ("澳门币", "MOP"), ("葡币", "MOP"),
    ("港元", "HKD"), ("港币", "HKD"), ("港纸", "HKD"),
    ("美元", "USD"), ("美金", "USD"),
    ("新加坡元", "SGD"), ("新元", "SGD"), ("新币", "SGD"),
    ("澳元", "AUD"), ("澳币", "AUD"), ("加元", "CAD"), ("加币", "CAD"),
    ("欧元", "EUR"), ("英镑", "GBP"), ("日元", "JPY"), ("日圆", "JPY"), ("日币", "JPY"),
    ("韩元", "KRW"), ("韩币", "KRW"), ("卢布", "RUB"), ("令吉", "MYR"), ("马币", "MYR"),
    ("卢比", "RUPEE"), ("比索", "PESO"), ("披索", "PESO"),
    ("人民元", "CNY"), ("米ドル", "USD"), ("ドル", "USD"), ("ユーロ", "EUR"), ("ウォン", "KRW"),
    ("ルーブル", "RUB"), ("円", "JPY"),
    ("달러", "USD"), ("유로", "EUR"), ("위안", "CNY"), ("엔", "JPY"), ("루블", "RUB"), ("원", "KRW"),
    ("块钱", None), ("块", None), ("元", None),
]
_EN_CURRENCY = [
    (r"hong kong dollars?|hk dollars?|hkd", "HKD"),
    (r"new taiwan dollars?|taiwan dollars?|nt dollars?|twd", "TWD"),
    (r"singapore dollars?|sgd", "SGD"),
    (r"australian dollars?|aud", "AUD"),
    (r"canadian dollars?|cad", "CAD"),
    (r"macau patacas?|macanese patacas?|patacas?|mop", "MOP"),
    (r"us dollars?|u\.s\. dollars?|usd|dollars?", "USD"),
    (r"yuan|rmb|renminbi|cny", "CNY"),
    # Not bare "pounds" (weight: "4 pounds of meth") or "won" (the verb).
    (r"euros?|eur", "EUR"), (r"british pounds?|pounds sterling|gbp", "GBP"),
    (r"yen|jpy", "JPY"), (r"south korean won|krw", "KRW"), (r"roubles?|rubles?", "RUB"),
    (r"ringgit|myr", "MYR"), (r"rupees?", "RUPEE"), (r"pesos?", "PESO"),
]
_EN_CURRENCY_RE = re.compile(
    r"^\s*(" + "|".join(p for p, _ in _EN_CURRENCY) + r")\b", re.I)
_PREFIX_RE = re.compile(
    r"(US\$|HK\$|NT\$|S\$|A\$|C\$|MOP\$?|\$|¥|￥|€|£|RMB|CNY|HKD|USD|TWD|"
    r"人民币|港币|港元|新台币|台币|美元)\s*$")
_PREFIX_CODE = {
    "US$": "USD", "HK$": "HKD", "NT$": "TWD", "S$": "SGD", "A$": "AUD", "C$": "CAD",
    # ¥ is yuan or yen depending on the story: no currency, compared by value.
    "MOP$": "MOP", "MOP": "MOP", "$": "USD", "¥": None, "￥": None, "€": "EUR", "£": "GBP",
    "RMB": "CNY", "CNY": "CNY", "HKD": "HKD", "USD": "USD", "TWD": "TWD",
    "人民币": "CNY", "港币": "HKD", "港元": "HKD", "新台币": "TWD", "台币": "TWD", "美元": "USD",
}
_EN_UNIT_RE = re.compile(r"^(?:\s*(trillion|billion|million|thousand)\b|(bn|mn|[kmb])\b)", re.I)
_EN_MULT = {"trillion": 10 ** 12, "billion": 10 ** 9, "million": 10 ** 6, "thousand": 10 ** 3,
            "bn": 10 ** 9, "mn": 10 ** 6, "b": 10 ** 9, "m": 10 ** 6, "k": 10 ** 3}

_APPROX_BEFORE = [
    (r"(?:(?:超过|超出|逾|超)了?|more than|over|in excess of)\s*$", "more than"),
    (r"(?:将近|接近|近|nearly|almost|close to)\s*$", "nearly"),
    (r"(?:至少|起码|at least)\s*$", "at least"),
    (r"(?:约合|大约|大概|约|약|about|roughly|approximately|around|some)\s*$", "about"),
]
_APPROX_AFTER = [
    (r"^(?:多|余)", "more than"),
    (r"^(?:来|左右|上下)", "about"),
]
_VAGUE_BEFORE = re.compile(r"(?:数|几|好几)\s*$")
_RATE_NEAR = re.compile(
    r"[=＝]\s*$|汇率|兑美元|美元兑|对美元|兑换|中间价|환율|달러당|원/달러|원·달러|為替|円相場|ドル円|円安|円高|"
    r"exchange rate|against the (?:dollar|yen|won|yuan)|per (?:dollar|euro)", re.I)
_RANGE_JOIN = re.compile(r"^\s*(?:到|至|-|–|—|～|~|to)\s*$")

_TW_MARKERS = ("台湾", "台北", "新北", "桃园", "台中", "台南", "高雄", "新竹", "立法院",
               "民进党", "国民党", "民众党", "赖清德", "地检署", "北检")
_US_MARKERS = ("纽约", "洛杉矶", "旧金山", "加州", "德州", "联邦", "法拉盛", "皇后区",
               "布鲁克林", "曼哈顿", "华盛顿", "芝加哥", "西雅图", "美国")
_CN_MARKERS = ("中国", "大陆", "中共", "北京", "上海", "广东", "深圳", "省", "市委",
               "纪委", "人民法院", "检察院")


@dataclass
class Amount:
    raw: str
    value: float
    currency: str | None
    approx: str = ""
    high: float | None = None  # set for a range, "3到5万元"
    rate: bool = False  # an exchange rate ("1,354.70 won"), shown without a conversion

    def usd(self, v: float) -> float | None:
        rate = _PER_USD.get(self.currency or "")
        return v / rate if rate else None


_KO_UNIT = {"조": "兆", "억": "亿", "만": "万", "천": "千"}


def _normalise(text: str) -> str:
    text = text.translate(_TRAD)
    # 兆 as a unit prefix (千兆字节 = GB, 兆赫 = MHz), not 10^12. Replaced
    # by 吉/麦, not G/M: a digit right after a letter is not read as a number.
    text = re.sub(r"千兆(?=字节|位|比特|赫|网)|(?<=每)千兆", "吉", text)
    text = re.sub(r"兆(?=字节|位|比特|赫|瓦|帕)|(?<=每)兆", "麦", text)
    # Korean units onto the Chinese ones: "1조 2000억원" -> "1兆2000亿원".
    text = re.sub(r"(?<=[\d천만억조])\s*(조|억|만|천)", lambda m: _KO_UNIT[m.group(1)], text)
    text = re.sub(r"(?<=[兆亿])\s+(?=[\d.]+[亿万千])", "", text)
    text = re.sub(r"(?<=\d)[,，](?=\d{3})", "", text)            # 18,200
    text = re.sub(r"(?<=\d)\s+(?=[万亿兆])", "", text)               # 1,750 亿美元
    text = re.sub(r"个(?=[万亿兆])", "", text)                      # 9个亿
    text = re.sub(r"(?<=[\d一二两三四五六七八九十百千])(多|余|来)([万亿兆])", r"\2\1", text)  # 1400余万 -> 1400万余
    return text


def _parse_run(run: str) -> float | None:
    # 兆 is 10^12 in money (Taiwan/HK usage; the mainland writes 万亿).
    big, total, section, cur = 0.0, 0.0, 0.0, None
    i = 0
    while i < len(run):
        ch = run[i]
        if ch.isdigit() or ch == ".":
            j = i
            while j < len(run) and (run[j].isdigit() or run[j] == "."):
                j += 1
            try:
                cur = float(run[i:j])
            except ValueError:
                return None
            i = j
            continue
        if ch in _CN_DIGIT:
            cur = float(_CN_DIGIT[ch])
        elif ch in _SMALL_UNIT:
            section += (cur if cur is not None else 1) * _SMALL_UNIT[ch]
            cur = None
        elif ch == "万":
            total += (section + (cur or 0)) * 10 ** 4
            section, cur = 0.0, None
        elif ch == "亿":
            total = (total + section + (cur or 0)) * 10 ** 8
            section, cur = 0.0, None
        elif ch == "兆":
            big += (total + section + (cur or 0)) * 10 ** 12
            total, section, cur = 0.0, 0.0, None
        else:
            return None
        i += 1
    value = round(big + total + section + (cur or 0), 4)
    return value or None


def _local_currency(text: str, source: str | None) -> str | None:
    """What a bare 元/块 means for this story."""
    if (source or "") in _HK_SITES:
        return "HKD"
    if (source or "") == "xjpy":
        return "SGD"
    tw = sum(text.count(m) for m in _TW_MARKERS)
    us = sum(text.count(m) for m in _US_MARKERS)
    cn = sum(text.count(m) for m in _CN_MARKERS)
    if tw > cn and tw > us:
        return "TWD"
    if us > cn and us > tw and (source or "") == "worldjournal":
        return "USD"
    return "CNY"


def _scan(text: str, local: str | None) -> list[tuple[int, int, Amount | None, float, bool]]:
    """Every number run: (start, end, amount-or-None, value, has_cn_unit)."""
    out = []
    for m in _RUN.finditer(text):
        run = m.group(0).rstrip(".")
        run = re.sub(r"(?<=[万亿兆])[零〇一二两三四五六七八九]$", "", run)  # 十万一拳
        if not run or run in ("十", "百", "千", "万", "亿", "兆") or run.startswith("."):
            continue
        value = _parse_run(run)
        if not value:
            continue
        end = m.start() + len(run)
        after = text[end:end + 40]
        before = text[max(0, m.start() - 12):m.start()]

        unit = _EN_UNIT_RE.match(after)
        if unit:
            value *= _EN_MULT[(unit.group(1) or unit.group(2)).lower()]
            after = after[unit.end():]
            end += unit.end()

        approx = ""
        for pat, word in _APPROX_AFTER:
            a = re.match(pat, after)
            if a:
                approx, after, end = word, after[a.end():], end + a.end()
                break

        currency: str | None = None
        found = False
        cn_tail = after.lstrip()
        for token, code in _CN_CURRENCY:
            if cn_tail.startswith(token):
                currency, found = (code or local), True
                end += (len(after) - len(cn_tail)) + len(token)
                if code is None:  # "100亿元人民币": the currency named after 元
                    named = re.match(r"(人民币|新台币|港币|美金|美元)", cn_tail[len(token):])
                    if named:
                        currency = _PREFIX_CODE.get(named.group(1), "USD")
                        end += named.end()
                break
        if not found and unit:
            # "455 billion won", "5 million pounds": after a size word these
            # are currencies, not the verb or the weight.
            word = re.match(r"\s*(won|pounds?)\b", after, re.I)
            if word:
                currency = "KRW" if word.group(1).lower() == "won" else "GBP"
                found = True
                end += word.end()
        if not found:
            en = _EN_CURRENCY_RE.match(after)
            if en:
                word = en.group(1).lower()
                currency = next(c for p, c in _EN_CURRENCY if re.fullmatch(p, word, re.I))
                found = True
                end += en.end()
        pre = _PREFIX_RE.search(before)
        start = m.start()
        if pre:
            code = _PREFIX_CODE[pre.group(1)]
            if not found or currency in (local, "CNY"):  # 人民币五百万元
                currency = code
            found = True
            start = m.start() - (len(before) - pre.start())
            before = before[:pre.start()]
        if not approx:
            for pat, word in _APPROX_BEFORE:
                if re.search(pat, before, re.I):
                    approx = word
                    break
        vague = bool(_VAGUE_BEFORE.search(before))
        amount = None
        if found and not vague:
            amount = Amount(raw=text[start:end].strip(), value=value, currency=currency, approx=approx,
                            rate=bool(re.match(r"\s*[=＝]", text[end:end + 3])
                                      or _RATE_NEAR.search(text[max(0, start - 12):start])))
        out.append((start, end, amount, value, bool(re.search("[万亿兆]", run)) or bool(unit)))
    return out


def extract(text: str, source: str | None = None) -> list[Amount]:
    """Money amounts in the source, in order, without repeats."""
    text = _normalise(text)
    local = _local_currency(text, source)
    runs = _scan(text, local)
    amounts: list[Amount] = []
    skip = set()
    for i, (start, end, amount, value, has_unit) in enumerate(runs):
        if i in skip:
            continue
        # "3到5万元", "41.7万至90.8万港元": the first end takes the second's currency.
        if i + 1 < len(runs):
            n_start, n_end, n_amount, n_value, n_has_unit = runs[i + 1]
            if n_amount and _RANGE_JOIN.match(text[end:n_start]) and (amount is None or amount.currency == n_amount.currency):
                low = value
                if not has_unit and n_has_unit:
                    unit = 10 ** 8 if "亿" in text[n_start:n_end] else 10 ** 4
                    if low * unit <= n_value:
                        low *= unit
                if low < n_value:
                    amounts.append(Amount(raw=text[start:n_end].strip(), value=low,
                                          currency=n_amount.currency, high=n_value,
                                          approx=(amount.approx if amount else "")))
                    skip.add(i + 1)
                    continue
        if amount:
            amounts.append(amount)
    unique: list[Amount] = []
    for a in amounts:
        if not any(u.value == a.value and u.high == a.high and u.currency == a.currency for u in unique):
            unique.append(a)
    return unique


# ---- wording ---------------------------------------------------------------

def _trim(x: float, places: int) -> str:
    return f"{x:.{places}f}".rstrip("0").rstrip(".")


def _number(v: float) -> str:
    if v >= 10 ** 12:
        return f"{_trim(v / 10 ** 12, 2)} trillion"
    if v >= 10 ** 9:
        return f"{_trim(v / 10 ** 9, 2)} billion"
    if v >= 10 ** 6:
        return f"{_trim(v / 10 ** 6, 2)} million"
    if v != int(v) and v < 10 ** 4:
        return f"{v:,.2f}"
    return f"{v:,.0f}"


def _round2(v: float) -> float:
    """Two significant figures, for an approximate conversion."""
    if v <= 0:
        return v
    from math import floor, log10
    digits = floor(log10(v))
    return round(v, -digits + 1)


def _usd_number(v: float) -> str:
    v = _round2(v)
    if v < 10:
        return _trim(v, 2)
    return _number(v)


_NATIVE = {
    "CNY": "{} yuan", "HKD": "HK${}", "TWD": "NT${}", "USD": "${}", "MOP": "{} patacas",
    "SGD": "S${}", "AUD": "A${}", "CAD": "C${}", "EUR": "€{}", "GBP": "£{}",
    "JPY": "{} yen", "KRW": "{} won", "RUB": "{} rubles", "MYR": "{} ringgit",
    "RUPEE": "{} rupees", "PESO": "{} pesos",  # several countries' units: no rate, no conversion
}


def _native(a: Amount) -> str:
    fmt = _NATIVE.get(a.currency or "", "{}")
    if a.high is not None:
        return fmt.format(f"{_number(a.value)} to {_number(a.high)}") if fmt.startswith("{}") \
            else f"{fmt.format(_number(a.value))} to {fmt.format(_number(a.high))}"
    return fmt.format(_number(a.value))


def _usd(a: Amount) -> str | None:
    lo = a.usd(a.value)
    if lo is None:
        return None
    if a.high is not None:
        return f"${_usd_number(lo)} to ${_usd_number(a.usd(a.high))}"
    return f"${_usd_number(lo)}"


def render(a: Amount, *, short: bool = False, lead_word: bool = True) -> str:
    """Body wording, or the shorter title wording (dollars only)."""
    lead = f"{a.approx} " if a.approx and lead_word else ""
    # No conversion for an exchange rate, or for under a dollar.
    usd = None if a.rate or (a.usd(a.value) or 0) < 1 else _usd(a)
    if a.currency == "USD" or usd is None:
        return lead + _native(a)
    if short:
        return lead + usd
    return f"{lead}{_native(a)} (about {usd})"


_CJK = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]")


def has_cjk(amounts: list[Amount]) -> bool:
    """Any amount written in Chinese, Japanese or Korean: the sources where
    the model's 亿/万/억 conversions go wrong."""
    return any(_CJK.search(a.raw) for a in amounts)


def tagged(amounts: list[Amount]) -> list[Amount]:
    """The amounts that get a tag, in tag order: not exchange rates, and
    nothing under $10. A rate is the price of one dollar or euro, so it
    converts to about $1 however it is written ("157円27銭", "1300원대",
    "1.1215美元"), which catches the rates no nearby word marks. Given rate
    tags, the model put one on a bond yield ("the 10-year yield rose to 159
    yen") and wrote broken tags; small sums it copies correctly anyway, and
    check() still holds them to the source. The 10x errors are all 万/亿."""
    def usd(a: Amount) -> float | None:
        return a.usd(a.value)
    return [a for a in amounts if not a.rate and (usd(a) is None or usd(a) >= 10)]


def tag_instructions(amounts: list[Amount], note: str = "") -> str:
    amounts = tagged(amounts)
    if not amounts:
        return (
            "\n\nMoney: the source gives no money amount this code could read. "
            "Don't put any money figure from this story in the post."
        )
    lines = [
        f"[AMT{i}]: source says \"{a.raw}\" -> body reads \"{render(a)}\"; title reads \"{render(a, short=True)}\""
        for i, a in enumerate(amounts, 1)
    ]
    return (
        "\n\nMoney: every amount in this story is written into the post by code, "
        "not by you. Where you mention one, write only its tag, e.g. [AMT1]; after "
        "you finish, the tag is replaced with the wording shown. Never type a "
        "story amount yourself, in any currency, in digits or in words, and don't "
        "put $, yuan, 'about' or 'more than' around a tag (the wording already "
        "has them). Mention only the amounts that matter, and don't add any "
        "money figure of your own." + (f" {note}" if note else "") +
        " Amounts in this story:\n" + "\n".join(lines)
    )


_TAG = re.compile(r"\[AMT(\d+)\]")
_LOOSE_TAG = re.compile(r"\[\s*AMT?\s*(\d+)\s*\]")


def _fix_tags(text: str) -> str:
    """"[AM9]" / "[AMT 9]" are the model's typos for [AMT9]."""
    return _LOOSE_TAG.sub(r"[AMT\1]", text)
_TAG_SYMBOL_BEFORE = re.compile(r"(?:(?:US|HK|NT)?\$|¥)$")
_TAG_APPROX_BEFORE = re.compile(
    r"\b(?:about|nearly|almost|over|more than|roughly|approximately|around|some|at least)\s+$", re.I)
_TAG_WRAP_AFTER = re.compile(
    r"^\s*(?:yuan|rmb|dollars|usd|hkd|hong kong dollars|patacas)\b", re.I)


def fill(text: str, amounts: list[Amount], *, short: bool = False) -> str:
    amounts = tagged(amounts)
    text = _fix_tags(text)
    out, pos = [], 0
    for m in _TAG.finditer(text):
        a = amounts[int(m.group(1)) - 1]
        before = _TAG_SYMBOL_BEFORE.sub("", text[pos:m.start()])
        out.append(before)
        # "over [AMT1]" / "about [AMT1]": keep the model's word, drop ours.
        out.append(render(a, short=short, lead_word=not _TAG_APPROX_BEFORE.search(before)))
        pos = m.end()
        tail = _TAG_WRAP_AFTER.match(text[pos:])
        if tail:
            pos += tail.end()
    out.append(text[pos:])
    return "".join(out)


# ---- check -----------------------------------------------------------------

_WAGE_AFTER = re.compile(r"^\s*(?:/|an|a|per)\s*hour", re.I)
_TOLERANCE = (0.88, 1.13)  # rounding only; 6.05 vs 5 million must fail


def _matches(written: Amount, source: Amount) -> bool:
    targets = [source.value] + ([source.high] if source.high is not None else [])
    for t in targets:
        # Same number, other currency label: copied, not converted
        # (€43 billion written "$43 billion" is within 8% after conversion).
        if (written.currency and source.currency and written.currency != source.currency
                and abs(written.value / t - 1) < 0.03):
            continue
        if written.currency == source.currency or written.currency is None or source.currency is None:
            pairs = [(written.value, t)]
        else:
            w, s = written.usd(written.value), source.usd(t)
            pairs = [(w, s)] if w is not None and s is not None else []
        for w, s in pairs:
            if s and _TOLERANCE[0] <= w / s <= _TOLERANCE[1]:
                return True
    return False


def _bare_figures(source_text: str) -> list[Amount]:
    """万/亿 figures with no currency next to them ("最贵20万", "9.6万亿"):
    not tagged, but a post that copies one correctly is not refused."""
    norm = _normalise(source_text)
    return [Amount(raw=norm[s:e], value=v, currency=None)
            for s, e, a, v, has_unit in _scan(norm, None) if a is None and has_unit]


def check(text: str, amounts: list[Amount], source_text: str = "") -> str | None:
    """None if fine, else why the post must not go out."""
    known = amounts + _bare_figures(source_text)
    amounts = tagged(amounts)
    text = _fix_tags(text)
    broken = re.search(r"\[\s*A[A-Z]{0,3}\s*\d*\s*\]", _TAG.sub(" ", text))
    if broken:
        return f"malformed tag {broken.group(0)}"
    for m in _TAG.finditer(text):
        n = int(m.group(1))
        if not 1 <= n <= len(amounts):
            return f"unknown tag {m.group(0)}"
    stripped = _TAG.sub(" ", text)
    if re.search(r"[一-鿿]", stripped):
        cn = [a for a in extract(stripped) if re.search(r"[一-鿿]", a.raw)]
        if cn:
            return f"Chinese amount left in the post: {cn[0].raw}"
    norm = _normalise(stripped)
    for start, end, amount, value, _ in _scan(norm, "CNY"):
        if amount is None:
            continue
        if amount.currency == "USD" and amount.value < 100 and _WAGE_AFTER.match(norm[end:]):
            continue  # "$7.25 an hour", a U.S. reference wage
        if not any(_matches(amount, s) for s in known):
            return f"amount the model typed itself does not match the source: {amount.raw}"
    return None
