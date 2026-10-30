# 0001. News sources: curated RSS feeds plus GDELT

- **Status:** Accepted
- **Date:** 2026-10-30

## Context
The bot has to show how outlets across the political spectrum *and* across regions cover the same
story, so the source list has to be balanced on purpose. The budget is $0–5/month, and the AI
already takes most of that. We also need a signal for breaking news.

## Options considered
### Curated RSS feeds + GDELT for breaking spikes
- ✅ Free, with no API keys or rate-limit plans for the feeds.
- ✅ We pick every outlet, so balance across lean and region is a deliberate choice we can explain.
- ✅ GDELT indexes world news in 100+ languages for free and shows when a story suddenly spikes globally.
- ❌ Feeds break or change URL without warning, so we need a health check (the `source-curator` agent).
- ❌ RSS often gives only a headline and summary, so the full text needs a separate, paywall-respecting fetch.

### RSS only
- ✅ Simplest; one kind of input.
- ❌ "Breaking" could only mean "many of *our* outlets at once", which misses stories big outside our list.

### Paid news API (NewsAPI, NewsData.io, etc.)
- ✅ One API for thousands of sources, with clean metadata.
- ❌ Free tiers are for development only or are delayed; usable plans cost well over the budget.
- ❌ We would still have to label every source's lean ourselves.

## Decision
Use a hand-curated list of about 50 RSS feeds in `config/sources.yaml`, with GDELT used only to
confirm breaking-news spikes. This gives deliberate balance at zero cost.

## Consequences
- `sources.yaml` becomes a key, reviewed file: every outlet is a visible editorial choice.
- We need a feed health check and a full-text fetcher that falls back to the RSS summary.
- Breaking alerts depend on GDELT being up; if it's down, breaking checks fall back to our own outlet count.

## Revisit if
- More than 20% of feeds break within a month.
- GDELT becomes unavailable or rate-limits us.
- The project gets funding or a sponsor that makes a paid API affordable.
