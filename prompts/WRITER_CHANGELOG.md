China Breaks writer, changed one step at a time (from 2026-10-03), following
the DailyNews writer process: every step gets its live-from time, commit and
what to watch, so posts can be compared before and after.

Where the comparison comes from:

- `logs/writer_calls.jsonl` (from step 1): one line per production writer
  call with the exact input (title, article cut at 6,000, background,
  opinion flag, published_at), the prompt fingerprint `system_sha`, and the
  post. Split windows by `system_sha`, not by clock: the prompt is read once,
  when `china-breaks-publish` starts.
- `logs/engagement_snapshots.jsonl`: likes/comments/shares per post, hourly.
- Offline A/B material for each step lives on VM-02 in `~/cb_writer_ab/`
  (articles, both prompts, generations, blind judgments).

| # | Live from (UTC) | Commit | What changed | Why | What to watch |
|---|---|---|---|---|---|
| 1 | 2026-10-03 (see commit time) | this commit | Line 1 = whoever actually did the thing in the article. When the Party acted, name the organ that acted (Xi, the foreign ministry, a PLA command, state security); "the CCP"/"Beijing" only when the article names no one more specific. When someone else acted (Japan's navy, a Texas court, Rubio, Taiwan's coast guard) they are the subject and the Party is the target, beneficiary or stake. The stance section's "name the Party as the actor or target" now says the same. Plus input logging (`writer_calls.jsonl`, no effect on posts). | 70% of published posts opened "The CCP…", including things the Party never did ("The CCP used a vessel to drive away Chinese fishing boats" — it was Taiwan's coast guard). Offline, 70 published sources × 3 runs per arm, blind-judged against the article: first-sentence subject wrong 35%→18%, any CCP misattribution (wrong subject or a CCP action/reaction the article doesn't give) 45%→30%, unsupported facts 18%→14%; every run moved the same way; per article 19 better / 6 worse (sign test p=0.015). "The CCP" openings 79%→51%. Cost: tell rate (", signaling/underscoring…", significant/growing…) 36%→43%, all three runs higher — the model adds a trailing clause to bring the Party back in when it is no longer the subject. | Share of posts opening "The CCP"; trailing-clause tells; engagement against the week before (time-of-day normalised, likes+comments+shares). |

Still wrong after step 1 (from the blind judgments, for the next steps):
the stance/framing sections push the model to invent a CCP role the article
never gives ("CCP efforts to extend influence" on a San Francisco
flag-raising; a CCP reaction to Taiwan's F-16V delivery that the article does
not report; "CCP-backed" Moonshot); Traditional-Chinese names converted to
pinyin produce wrong romanisations of Korean names (Lee Jae-myung → "Li
Zaiming"); misspelt MFA spokesperson names.
