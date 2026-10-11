"""Unit checks for core/money_tags.py. Run: python3 scripts/test_money_tags.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import money_tags as money  # noqa: E402


def amounts(text, source=None):
    return [(a.value, a.high, a.currency, a.approx) for a in money.extract(text, source)]


def bodies(text, source=None):
    return [money.render(a) for a in money.extract(text, source)]


failures = 0


def eq(got, want, label):
    global failures
    if got != want:
        failures += 1
        print(f"FAIL {label}\n  got:  {got!r}\n  want: {want!r}")


# The post that started this (p44k6ftbfed, 2026-10-11).
src = "因受贿6050万余元，判处其有期徒刑十四年，并处罚金人民币五百万元。"
eq(bodies(src), ["more than 60.5 million yuan (about $8.4 million)", "5 million yuan (about $690,000)"], "zhou xianwang")

# Shapes the old regex missed, from the 30-day audit.
eq(amounts("恒大现对境外债权人欠款约450亿美元"), [(4.5e10, None, "USD", "about")], "450亿美元")
eq(amounts("面临高达22.43亿港元"), [(2.243e9, None, "HKD", "")], "22.43亿港元")
eq(amounts("非法获利高达243万5226元"), [(2435226, None, "CNY", "")], "243万5226元")
eq(amounts("最终仅赔偿500多万元"), [(5e6, None, "CNY", "more than")], "500多万元")
eq(amounts("1400余万元"), [(1.4e7, None, "CNY", "more than")], "1400余万元")
eq(amounts("合计损失逾1.18亿澳门元"), [(1.18e8, None, "MOP", "more than")], "澳门元")
eq(amounts("总投资超过1.56亿元"), [(1.56e8, None, "CNY", "more than")], "1.56亿元")
eq(amounts("一纸书籍可以直接赚取高达四千多万元"), [(4e7, None, "CNY", "more than")], "四千多万元")
eq(amounts("部分售价居然超过了百万港元"), [(1e6, None, "HKD", "more than")], "百万港元")
eq(amounts("需缴纳3到5万元"), [(3e4, 5e4, "CNY", "")], "range 3到5万")
eq(amounts("罚款金额从41.7万港元到90.8万港元"), [(4.17e5, 9.08e5, "HKD", "")], "range HKD")
eq(amounts("挪用63,000新元"), [(63000, None, "SGD", "")], "SGD")
eq(amounts("1亿3150万元"), [(1.315e8, None, "CNY", "")], "1亿3150万")
eq(amounts("9个亿"), [], "no currency, no amount")
eq(amounts("投资金额高达数十亿元"), [], "vague 数十亿 skipped")
eq(amounts("2020年到2023年，他一共3次"), [], "years are not money")

# Traditional script, HK site: bare 元 is HKD, 人民幣 stays CNY.
eq(amounts("被騙600萬元，另轉賬人民幣20萬元", "hkoncc"), [(6e6, None, "HKD", ""), (2e5, None, "CNY", "")], "hk site")
# Taiwan story: bare 元 is NT$.
eq(amounts("台北地检署：她获准以100万元交保，台湾检方表示", "worldjournal"), [(1e6, None, "TWD", "")], "taiwan")
# English source text.
eq(amounts("He was fined 5 million yuan and paid $1.2 million to victims"),
   [(5e6, None, "CNY", ""), (1.2e6, None, "USD", "")], "english source")
eq(amounts("seized 4 pounds of meth"), [], "pounds is weight")

eq(bodies("仍拖欠巴基斯坦4230亿卢比"), ["423 billion rupees"], "rupee, no conversion")

# From the China_Breaks / Leading_News survey (2026-10-11).
eq(amounts("营收达1,750 億美元"), [(1.75e11, None, "USD", "")], "space before 亿")
eq(amounts("台积电市值1兆1225億元，台北股市", "worldjournal"), [(1.1225e12, None, "TWD", "")], "兆")
eq(amounts("罚款3.19億披索"), [(3.19e8, None, "PESO", "")], "披索")
eq(amounts("台湾检方查获100億元人民幣，台北", "worldjournal"), [(1e10, None, "CNY", "")], "元人民币 after")

# Japanese / Korean (Market_Watcher sources).
eq(amounts("投資額は40.8億ドル、うち60億ドル"), [(4.08e9, None, "USD", ""), (6e9, None, "USD", "")], "億ドル")
eq(amounts("売上高は3兆2000億円"), [(3.2e12, None, "JPY", "")], "兆億円")
eq(amounts("455억원 규모"), [(4.55e10, None, "KRW", "")], "억원")
eq(amounts("1조 2000억원을 투자"), [(1.2e12, None, "KRW", "")], "조 억원")
eq(amounts("약 3천억원"), [(3e11, None, "KRW", "about")], "천억원")
eq(amounts("40억 달러"), [(4e9, None, "USD", "")], "억 달러")
eq(amounts("2만명이 참가"), [], "people, not money")

# Market_Watcher replay cases.
kr = money.extract("4조2000억원 중 455억원")
eq(money.check("to return 455 billion won of the 4.2 trillion won", kr) is not None, True, "455 billion won caught")
eq(money.check("to return 45.5 billion won of the 4.2 trillion won", kr), None, "45.5 billion won ok")
jp = money.extract("経済損失は80兆円に上る")
eq(money.check("could reach ¥80 trillion", jp), None, "yen sign")
eq(amounts("每千兆字节4美元，兆赫"), [(4, None, "USD", "")], "千兆字节 is not 10^15")
eq(amounts("提出了每千兆4美元中高区间的价位"), [(4, None, "USD", "")], "每千兆 is per GB")
eq(amounts("seized 4 pounds of meth, worth 5 million pounds"), [(5e6, None, "GBP", "")], "pounds after million")

# Exchange rates: native wording, no conversion.
fx = money.extract("원/달러 환율은 1354.7원으로 전 거래일보다 11.3원 내렸다")
eq([money.render(a) for a in fx], ["1,354.70 won", "11.30 won"], "rates not converted")
eq(bodies("每斤3毛钱，罚款2元"), ["2 yuan"], "under a dollar, no conversion")
jpfx = money.extract("円相場は1ドル=157円台、損失は80兆円")
eq([(a.value, a.rate) for a in jpfx], [(1.0, True), (157.0, True), (8e13, False)], "1ドル=157円 is a rate")
eq(money.fill("Losses hit [AMT1].", jpfx), "Losses hit 80 trillion yen (about $530 billion).", "rates take no tag number")
eq(money.check("The dollar rose to 157 yen; losses hit [AMT1].", jpfx), None, "copied rate passes")
eq(money.check("the rate fell to [AM8].", jpfx) is not None, True, "typo tag past the end")
eq(money.check("losses hit [AM1].", jpfx), None, "typo tag [AM1] accepted")
eq(money.fill("losses hit [AM1].", jpfx), "losses hit 80 trillion yen (about $530 billion).", "typo tag filled")
eq(money.check("losses hit [AMX1].", jpfx) is not None, True, "malformed tag")

# A copied number under the wrong currency label.
eu = money.extract("规模430亿欧元")
eq(money.check("a €43 billion package", eu), None, "euro kept")
eq(money.check("a $46.7 billion package", eu), None, "euro converted")
eq(money.check("a $43 billion package", eu) is not None, True, "euro relabelled as dollars")

# Wording.
a = money.extract("赔偿20万元")
eq(money.render(a[0]), "200,000 yuan (about $28,000)", "small amount")
eq(money.render(a[0], short=True), "$28,000", "title form")
eq(bodies("收47万美元"), ["$470,000"], "usd no conversion")
eq(bodies("需缴纳3到5万元"), ["30,000 to 50,000 yuan (about $4,200 to $6,900)"], "range wording")
eq(bodies("被骗600万港元", "hk01"), ["HK$6 million (about $770,000)"], "hkd wording")

# fill(): stray $ / yuan / about around a tag are dropped.
amts = money.extract(src)
eq(money.fill("He took $[AMT1] yuan and was fined [AMT2].", amts),
   "He took more than 60.5 million yuan (about $8.4 million) and was fined 5 million yuan (about $690,000).", "fill")
eq(money.fill("Fined about [AMT2].", amts), "Fined about 5 million yuan (about $690,000).", "fill keeps model's about")
eq(money.fill("Ex-Mayor Jailed Over [AMT1] in Bribes", amts, short=True),
   "Ex-Mayor Jailed Over $8.4 million in Bribes", "fill title")

# check(): the model typing its own figure.
eq(money.check("He took [AMT1] and was fined [AMT2].", amts), None, "tags only")
eq(money.check("He took $8.4 million in bribes.", amts), None, "own figure, correct")
eq(money.check("He took 60.5 million yuan in bribes.", amts), None, "own yuan figure, correct")
eq(money.check("He took 6.05 million yuan in bribes.", amts) is not None, True, "one zero short is caught")
eq(money.check("He was fined 500,000 yuan.", amts) is not None, True, "fine one zero short is caught")
eq(money.check("He took [AMT3].", amts) is not None, True, "unknown tag")
fxw = money.extract("한때 1550원대까지 하락했던 달러당 원화값이 1300원대 중반, 수출액 455억원")
eq([money.render(a) for a in money.tagged(fxw)], ["45.5 billion won (about $33 million)"], "rates get no tag")
eq(money.check("He took 6050万元.", amts) is not None, True, "chinese left in post")
eq(money.check("A U.S. worker earns at least $7.25 an hour.", amts), None, "us minimum wage")
eq(money.check("A U.S. official taking $50,000 would be finished.", amts) is not None, True, "invented us figure")
eq(money.check("He spent 14 years in office and 8 doctors were punished in 2020.", amts), None, "no money")
eq(money.check("Police seized 4 pounds of meth.", []), None, "weight")
ev = money.extract("恒大现对境外债权人欠款约450亿美元")
eq(money.check("Evergrande owes $450 billion.", ev) is not None, True, "10x is caught")
eq(money.check("Evergrande owes $45 billion.", ev), None, "right figure passes")

# A bare 万/亿 figure in the source (no 元) may be copied, not mis-scaled.
bare = "收费公路总债务更是达到9.6万亿，最贵的墓地要20万"
eq(money.check("Toll-road debt hit 9.6 trillion yuan.", money.extract(bare), bare), None, "bare figure copied")
eq(money.check("Toll-road debt hit 96 trillion yuan.", money.extract(bare), bare) is not None, True, "bare figure 10x")

eq(money.has_cjk(money.extract("罚款500万元")), True, "cjk source")
eq(money.has_cjk(money.extract("fined $5 million")), False, "english source")

print("all passed" if not failures else f"{failures} failed")
sys.exit(1 if failures else 0)
