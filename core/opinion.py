"""Chinese-language opinion pieces are not published (user, 2026-10-12).

User, on 童文薰觀點》要讓他們轉彎 先抽銀根 (newtalk): "中文媒体的观点文章，应该屏蔽。"
Same line as 10-10 ("中国学者、专家、时评人对中共的观点/分析，不要发"). Over
10/03-10/12, 31 Chinese titles in writer_calls.jsonl carried an opinion-ish
word; the ones that are columns all name themselves the same way: a byline
followed by 觀點 / 專欄 / 投書 and a colon or 》 ("梁幼祥觀點：", "自由觀點》",
"觀點投書：", "許詠翔專欄："). A word like 社論 or 論壇 inside a news headline
("WSJ社論揭中國間諜…", "雙城論壇能續辦嗎？") is not a column and is not matched.
Only the title is used. A closing "（作者為…）" author line was tried on
10-12 and taken out the same day: user, "未必是评论文章".

English-language analysis is not touched here: the user allows American
warnings about the CCP, and that is a judgement about who is speaking, not a
title pattern."""
from __future__ import annotations

import re

# A byline of at most 15 characters, then the column word, then ： : 》 or 〉.
_TITLE_RE = re.compile(r"^[^：:》〉｜|]{0,15}(?:觀點|观点|專欄|专栏|投書|投书)\s*[：:》〉]")


def opinion_rule(title: str) -> str | None:
    """The reason this is a Chinese-language opinion piece, or None."""
    if _TITLE_RE.search((title or "").strip()):
        return "Chinese opinion column (title)"
    return None
