# 0009. Telegram delivery: python-telegram-bot, with per-story marks for safe re-runs

- **Status:** Accepted
- **Date:** 2026-10-31

## Context
v1 posts the daily digest with send-only calls from a GitHub Actions cron job in a public repo.
GitHub can re-run a failed job, and [ADR 0004](0004-hosting-github-actions-and-sqlite.md) requires
that a re-run never posts a message twice. `state.db` holds hashes and ids only, never text. Telegram
allows a bot 20 messages a minute in a group, and each message is at most 4,096 characters after
entity parsing. The AI budget is $4/month, so a retry shouldn't pay for the whole digest again.
Step 6 needs a Telegram client and a way for a re-run to know which messages are missing.

## Options considered
### Telegram client
#### python-telegram-bot (chosen)
- ✅ Typed models and a built-in error hierarchy (`RetryAfter`, `BadRequest`, `Forbidden`, …).
- ✅ v2's always-on bot (`/more`, `/topic`) can reuse it.
- ✅ Its only required dependency is httpx (`>=0.27,<0.29`), which we already lock at 0.28.1.
- ❌ Async-only since v20, so the sync pipeline calls it through `asyncio.run`.
- ❌ More than a send-only bot needs, and it can trail the Bot API (22.8 targets Bot API 10.0;
  the current Bot API is 10.3).

#### Plain httpx calls to the Bot API
- ✅ No new package, sync, about 40 lines; tests use `httpx.MockTransport`.
- ❌ We write our own error handling for 429s and 400s, and v2 would likely switch to a library.

#### aiogram
- ✅ Similar to python-telegram-bot.
- ❌ Also async, and nothing in the project points to it.

### How a re-run knows what is missing
#### Per-story marks, recomputed on retry (chosen)
- ✅ No message text is stored, so ADR 0004's "hashes and ids only" holds as written.
- ✅ Reuses ADR 0007's repeat check: a story already sent is skipped by selection like any repeat.
- ❌ A retry pays for AI again for the missing stories (a few cents), and those may not be the
  stories the first attempt would have sent.

#### Store the rendered messages
- ✅ A retry resends exactly what was planned, with no new AI calls.
- ❌ On a failed day the digest text sits in the committed `state.db`, bending ADR 0004.

#### All or nothing
- ✅ Simplest; nothing is ever duplicated.
- ❌ A failure after the second message loses the rest of the day until a manual `--force` rerun.

## Decision
- **python-telegram-bot 22.8**, used through `Bot` alone (no `Application`), wrapped in one
  `asyncio.run` at the send step. Messages are sent about one second apart to stay inside the
  group limit, and a `RetryAfter` is waited out inside the sender.
- **Per-story marks:** each story's `posted_stories` and `posted_articles` rows are written in one
  transaction right after that story's own message is sent. A retry re-runs the pipeline; selection
  skips the stories already sent as repeats, and a per-day record sends only the missing slots of
  each kind (top stories, one-sided and non-Western blindspots).
- **Per-day record:** migration `0004_digest_days` adds one row per IST date: started or done, and
  how many slots of each kind were sent. A day marked done is skipped. The header message is
  slot 0, so a retry never sends it twice.
- **Errors are handled by type.** `RetryAfter` is waited out (at most 60 seconds, up to 3 times);
  connection errors, where nothing was sent, are retried twice with backoff. `TimedOut` is never
  retried automatically, because Telegram may already have delivered the message. A `BadRequest`
  skips that one story and the run carries on; `Forbidden` and `InvalidToken` stop the run. If any
  slot failed, the run exits non-zero, so Actions shows red and a re-run fills only the gaps.
- **Ingest runs every 3 hours in v1.** ADR 0005 assumed ingest every 20 minutes through
  `breaking.yml`, which arrives in step 7. Until then `digest.yml` also runs ingest-only every 3
  hours, committing `state.db` each time, so busy feeds that list only their last few hours still
  reach the 24-hour window, and feed Issues open within a day. The digest itself runs at 01:15 UTC
  (about 07:00 IST) and posts as soon as it's ready.

## Consequences
- Re-runs are safe: a retry sends only the missing slots and pays AI only for those stories.
- This amends [ADR 0007](0007-posted-stories-in-state-db.md) without replacing it: posted rows are
  written per story right after its send, not all at once at the end. Chain links are written when
  the day is marked done.
- The workflow must commit `state.db` even when the job fails (`if: always()`), or a retry can't
  see what was already sent.
- An async library sits inside a sync pipeline, so there is one `asyncio.run` at the send step.

## Revisit if
- A retried day posts a duplicate story, or a visibly different set of stories, more than once a
  month.
- Telegram returns 429 on more than 1 run in 10 despite the one-second spacing.
- Step 8 (the v2 always-on bot) starts: check whether python-telegram-bot's `Application` fits a
  long-running bot.
- We need a Bot API feature python-telegram-bot doesn't support yet.
