# 0002. Lean judging: AI per article, against an outlet baseline

- **Status:** Accepted
- **Date:** 2026-10-30

## Context
Each story shows left, right and international framing, so every article needs a lean label.
Outlet reputations are a rough guide, but a centrist outlet can run a slanted op-ed, and "left" and
"right" mean different things in different countries. Labels also need to be stable enough that
readers trust them.

## Options considered
### Fixed outlet labels only (from AllSides / Ad Fontes / MBFC as a guide)
- ✅ Free, transparent and consistent.
- ❌ Misses article-level slant; one label per outlet is crude.

### Pure AI judgment per article
- ✅ Judges what's actually written, not the outlet's reputation.
- ❌ Labels can flip day to day for the same outlet, which confuses readers.
- ❌ Without a reference point, model drift is hard to spot.

### AI judgment per article against an outlet baseline
- ✅ Catches surprises ("usually centre-left, but this piece reads right").
- ✅ The baseline anchors the model, and large shifts must be explained, so drift is visible.
- ❌ Costs AI tokens per article, and needs a carefully written rubric.

## Decision
Every outlet has a `baseline_lean` from -2 to +2 in `config/sources.yaml`. The AI judges all
articles in a story together using a fixed rubric (`prompts/judge.md`): lean score, framing tags,
loaded words and confidence. Any shift of ±1 or more from the baseline must come with a reason.
The output is structured JSON checked with pydantic.

## Consequences
- To keep costs down, only articles in stories that make the digest are judged (~60–80/day), not every article ingested.
- The rubric is a core, owner-written file; changing it changes every score, so it needs an ADR.
- Baselines are an editorial choice and will be visible in a public repo; the README must explain the method and its limits.

## Revisit if
- Spot checks find the AI disagreeing with sensible human judgment on more than ~1 in 5 articles.
- The same outlet's scores swing a lot without an obvious reason.
- Judging cost becomes the largest part of the budget.
