# 0008. Judge, summary and budget: anthropic SDK, rubric structure, spend log in state.db

- **Status:** Accepted
- **Date:** 2026-10-31

## Context
Step 5 adds the bot's first paid AI calls: Haiku 5.5 judges the lean of every article in the ~8
digest stories, and Sonnet 5.5 writes the framing comparison (ADR 0003). Spend must stay under
$4/month and be enforced in code, because the job runs unattended on GitHub Actions. `state.db` is
committed to a public repo, so the spend record may hold numbers only, never keys or article text.
ADR 0002 fixed *that* the AI judges against an outlet baseline, but not the rubric's structure.
An estimate from the official prices (Haiku 5.5 $0.10/$0.50 and Sonnet 5.5 $2/$10 per million
input/output tokens, checked 2026-10-31) showed that Sonnet reading full articles would use most
or all of the cap on its own.

## Options considered
### What the Sonnet write-up reads
#### The judge's compact output only, at low effort (chosen)
- ✅ Est. ~$3/month for all 8 stories; keeps ADR 0003 as it is.
- ❌ Sonnet can't quote anything the judge missed.

#### Sonnet for the top 5, Haiku for the 3 blindspots
- ✅ Est. ~$2/month; blindspots are one-sided, so they have less framing to compare.
- ❌ Two quality levels in one digest; amends ADR 0003. Kept as the fallback.

#### Haiku for everything
- ✅ Est. ~$0.50/month.
- ❌ Weaker framing comparisons, the headline feature; reverses ADR 0003.

### Rubric structure (`prompts/judge.md`)
#### Lean axis
- **The outlet's own axis (chosen):** the same axis as its baseline (us, uk, national or local), so
  the "explain a shift of ±1 or more" rule compares like with like. Scores from different axes
  can't share one chart.
- *One global left/right axis:* easy to compare, but doesn't match the baselines, and "left"
  differs a lot by country.
- *Both:* twice the rubric and tokens, and two numbers readers may confuse.

#### Framing tags
- **Fixed list plus `other:<word>` (chosen):** conflict, human_cost, economic, security,
  responsibility, morality, legal, political_strategy, sovereignty, evidence. 1–3 per article, most
  prominent first. Consistent from day to day, and checkable with pydantic.
- *Free text:* more nuance, but can't be counted, and drift is hard to see.
- *An academic set (Media Frames Corpus):* well documented, but built for US policy news.

#### Loaded words
- **Exact quote plus a neutral alternative (chosen):** at most 3 per article. Words inside direct
  quotes (someone else's speech) aren't flagged. Words in non-English articles are quoted in the
  original with an English gloss. Having to name the swap cuts false positives, which readers would
  see.
- *Quote only:* fewer tokens, but more borderline flags and no visible reason.
- *Only words another outlet in the story said neutrally:* most defensible, but misses words every
  outlet uses.

#### Text with no clear slant
- **"Insufficient", with no score (chosen):** the digest falls back to the outlet's baseline,
  labelled as baseline. The ±1 shift rule applies only to scored articles. Honest about what was
  actually judged.
- *Default to the baseline:* every article gets a number, but it reflects reputation, not the piece.
- *Score 0:* most short summaries would become centre, and nearly every one would need a shift
  explanation.

#### Contested terms (added after the bias-auditor review)
- **Flag one side's contested term (chosen):** for example "terrorists" or "freedom fighters", with
  the term major wire copy uses as the neutral alternative. Within a story, never flag only one
  side's terms. These are the words that matter most for a framing comparison.
- *Flag, with any neutral term the model picks:* the alternative may vary from day to day.
- *Flag neither side's term:* hides the most telling words, and "just as loaded" is a judgement
  the model would make unevenly.

#### Anchoring on the baseline (added after the bias-auditor review)
- **A one-sentence `reason` for every score (chosen):** the judge still sees the baseline (ADR
  0002), but agreeing with it needs the same evidence as moving away, so scores don't drift toward
  each outlet's reputation. Costs about 20 output tokens per article.
- *Hide the baseline from the judge:* no anchoring, but contradicts ADR 0002 and scores may flip
  from day to day.
- *A reason only for shifts of ±1 or more:* the cheapest answer is always "same as the baseline".

#### Confidence
- **low / medium / high, each defined in the rubric (chosen):** models are poorly calibrated on
  exact numbers.
- *A number from 0 to 1:* false precision. *No field:* faint and clear slants look the same.

### Translation
- **Inside the Haiku judge call (chosen):** the judge reads the original language and writes in
  English. No extra calls, and loaded words are judged before translation can soften them.
- *A separate translation pass stored in cache.db:* English headlines would be available, but extra
  cost, and translation can change the very wording being judged.
- *No translation:* no guarantee the output is in English.

### Spend record
- **One row per API call in a `spend` table in `state.db` (chosen):** time, model, purpose (judge,
  summary, breaking), input/output/cache token counts from the response's `usage`, and the cost in
  USD from a price table in config. Month-to-date is a sum. Auditable, with no secrets.
- *One row per day per model:* tiny, but you can't see which call made spend jump.
- *Anthropic's Usage & Cost Admin API:* exact, but needs an admin key in a public repo's Actions,
  and its data lags.

### When the cap is checked
- **Pre-flight estimate (chosen):** before any paid call, month-to-date plus an estimate of today's
  run (the most expensive day of the last 7, or $0.25 with no history). If that passes $4, the whole
  digest is headline-only. Never half a digest.
- *Before each call:* uses the budget to the last cent, but gives a partial digest.
- *Start of run only:* simplest, but the day that crosses the cap can overshoot by a full day.

### Where the story's facts come from
- **The judge writes a 2–3 sentence neutral fact line per story (chosen):** Sonnet still reads only
  judge output. About 100 Haiku output tokens per story.
- *Send Sonnet the article titles:* headlines are the most slanted part of an article.
- *No fact summary:* readers would get framing with no plain account of what happened.

### What Sonnet writes
- **The headline, one line per side with coverage, and one "what differs" sentence (chosen):** the
  facts come from the judge, and the loaded-language line is picked in code from the judge's flags.
- *Everything, facts included:* more output, and a second paraphrase can drift from the judge.
- *Side lines only:* no "what differs" sentence, the most useful comparison for readers.

### When a call fails
- **Judge:** a bad, refused, cut-off or incomplete reply is retried once, and the retry only fills
  gaps. Articles still missing are "not judged" and show their baseline, labelled. If nothing came
  back, the story is headline-only. *Rejected:* replacing the story with the next-ranked one
  (changes selection), and failing the whole run.
- **Sonnet:** retried once; then the story falls back to the judge's fact line and takes, with no
  "what differs" sentence. *Rejected:* a headline-only story (wastes judging already paid for), and
  failing the whole run.

### After code review (decided by the owner)
- **Spend rows are committed as soon as each call is logged**, so a crash later in the run can't
  roll back the record of money already spent.
- **The judge's reply is checked article by article.** One broken judgment no longer throws away
  the rest; only the broken article counts as missing, and the retry fills just that gap.
- **`other:` tags** may be one lowercase word or a term joined with `-` or `_`.
- **Refusals aren't retried**: the same input would almost always be refused again, and both calls
  would be billed. Bad, cut-off and incomplete replies are still retried once.
- **`--dry-run --live` spend goes straight to the real `state.db`** through a separate connection
  committed per call; everything else in a dry run stays in the in-memory copy.
- **The run estimate counts only scheduled digest spend** (judge and summary, not `--live` test
  calls or breaking alerts), via a `dev` flag on each spend row. All spend still counts toward the
  monthly cap.

### Paid calls during `--dry-run`
- **Free by default, real with `--live` (chosen):** `--dry-run` uses canned judge and summary output,
  so routine checks after every pipeline change cost nothing. `--dry-run --live` makes real calls and
  logs them to `spend`, but still posts nothing.
- *Always real, and logged:* tests the real prompts every time, but costs ~$0.10–0.15 per run.
- *Real, not logged:* the cap would undercount real spend from the same API key.

### Which clock defines the month and the day
- **UTC (chosen):** every time in `state.db` is already UTC (ADR 0005), and the month should line
  up with the bill. The 07:00 IST digest runs inside one UTC day.
- *IST:* matches readers' calendars, but the month boundary is 5.5 hours away from UTC's.

### API client
- **The official `anthropic` SDK (chosen):** built-in retries for 429/5xx, typed errors, `usage`
  for the spend log, and `messages.parse()` to validate structured output into pydantic models.
- *httpx directly (already installed):* no new package, but we'd own retries and parsing, and API
  changes would break silently.

## Decision
- Add `anthropic` as a dependency. Model IDs and per-token prices live in `config/settings.yaml`,
  not in code, and prices are copied from the official pricing page.
- `prompts/judge.md` follows the rubric structure above. Its wording is the owner's, and changing it
  needs a new ADR (ADR 0002).
- Haiku judges all articles in a story in one call, in their original languages, and returns
  English output validated by pydantic. Sonnet writes each story's comparison from that output
  only, at low effort.
- `state.db` gets migration `0003` with a `spend` table, one row per API call. `budget.py` writes
  a row after each call and runs the pre-flight check before a run's first paid call.
- Pruning keeps every spend row from the current calendar month, and deletes older rows after 30
  days, so the monthly sum is always complete.

## Consequences
- Sonnet can't mention anything the judge missed, so the write-up is only as good as the judge's
  output.
- Short RSS summaries mean many articles may come back "insufficient". The render step (step 6)
  must label the baseline it falls back to.
- No full English translation of non-English articles exists anywhere.
- The logged cost is our own calculation, not the bill. Prices must be updated by hand when
  Anthropic changes them, and the Anthropic console's hard spend limit stays as the backstop.
- `state.db` grows by about 20 spend rows a day.

## Revisit if
- Any month's logged spend passes $4, or a week averages over $0.13 a day ($4 ÷ 30). Then switch
  to Sonnet for the top 5 and Haiku for the blindspots.
- Over a week, more than half of judged articles come back "insufficient". The judge then needs more
  text, for example freely available full text where robots.txt allows it.
- `other:` tags appear on more than 1 in 10 articles. The tag list needs updating.
- Logged spend differs from the Anthropic console by more than 10% in a month. The price table or
  the token accounting is wrong.
