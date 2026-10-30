# 0003. AI models: Haiku 5.5 for bulk work, Sonnet 5.5 for the daily write-up

- **Status:** Accepted
- **Date:** 2026-10-30

## Context
The AI does four jobs: translating non-English articles, judging lean (ADR 0002), writing short
breaking summaries, and writing the daily "how each side framed it" comparison. The total AI budget
is about $4/month. The first three are high-volume and routine; the last is once a day and needs
the most nuance.

## Options considered
### Claude Haiku 5.5 for everything
- ✅ Cheapest and fastest; comfortably inside the budget.
- ❌ The framing comparison is the product's headline feature and gains the most from a stronger model.

### Claude Sonnet 5.5 for everything
- ✅ Best quality throughout.
- ❌ Several times the cost per token; with breaking alerts added it would strain the budget.

### Hybrid: Haiku for bulk work, Sonnet for the daily framing write-up
- ✅ Spends money where quality is most visible.
- ✅ Keeps high-volume work cheap.
- ❌ Two models to configure and two prompts to tune.

## Decision
Haiku 5.5 handles translation, lean judging and breaking summaries. Sonnet 5.5 writes only the
daily framing comparison, once per day. Model IDs live in `config/settings.yaml`, not in code. The
bot calls the Anthropic API directly; Claude Code is only the development tool.

## Consequences
- `budget.py` tracks spend in SQLite and stops paid calls at $4/month, then sends a headline-only digest.
- A hard spend limit is also set in the Anthropic console as a second safety net.
- Prices must be checked from the official docs before setting the cap's per-token maths, never guessed.

## Revisit if
- Spend goes over $4 in two months out of three.
- A newer or cheaper model matches Sonnet's framing quality in a side-by-side test.
- Readers say the framing comparison isn't adding much over the summaries.
