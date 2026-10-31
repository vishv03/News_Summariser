"""Choose the digest's top stories and blindspots. Every rule here is the project owner's."""

import logging
import math
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Literal

import numpy as np

from news_summariser.config import Source
from news_summariser.models import (
    ChainLink,
    FeedHealth,
    Pick,
    PostedStory,
    Selection,
    Story,
    Topic,
)

logger = logging.getLogger(__name__)

# All rules below were decided by the project owner on 2026-10-31.

# A story qualifies while it is still being covered (any article in the last 24 hours) and keeps
# all its articles, so the judge still sees how outlets framed it on day one.
ELIGIBLE_WINDOW = timedelta(hours=24)
# Comparing left, right and international framing needs at least three voices. Applies to top
# stories and both blindspot pools.
MIN_OUTLETS = 3

TOP_COUNT = 5
# Politics leads most days; a cap keeps it from taking every slot without letting a weak story
# from a quiet area push out a strong one.
TOPIC_CAP = 2

# Two one-sided slots and one non-Western slot; an empty slot is filled from the other pool.
ONE_SIDED_SLOTS = 2
NON_WESTERN_SLOTS = 1
# Measured among us/uk/national outlets only: 22 of 38 outlets are `local`, so counting them would
# make most stories "one-sided International" and crowd out the left/right blindspot.
ONE_SIDED_SHARE = 0.75
# By audience region, so latam counts as non-Western and RT (region europe) doesn't.
WESTERN_REGIONS = frozenset({"us", "europe"})
# Offline feeds don't count as not covering. One or two broken feeds are normal on any day, so
# only skip a blindspot when fewer than half of the missing side's outlets are healthy.
MIN_HEALTHY_SHARE = 0.5

# A repeat comes back as an update only with this many outlets not in the version we posted: a
# stand-in for "a big development", measured as spread rather than news.
UPDATE_NEW_OUTLETS = 3

# Each story goes to the area whose description its average article vector is closest to. One
# sentence each, listing what belongs there, was chosen over bare labels and example headlines:
# on 53 real eligible stories (31 Oct 2026) it put about 3 in the wrong area, against about 6 for
# labels and 14 for headlines. Revisit after the first week of digests if more than about 1 in 10
# posted stories get the wrong area.
TOPIC_DESCRIPTIONS: dict[Topic, str] = {
    "world_politics": (
        "Elections, election campaigns, voters and polls, governments, diplomacy, wars, military"
        " conflicts, ceasefires, peace talks, humanitarian aid and international relations."
    ),
    "economy": (
        "Markets, trade, companies, jobs, inflation, interest rates, budgets, energy prices,"
        " cryptocurrency, banking and financial fraud."
    ),
    "science_tech_climate": (
        "Scientific research, technology, AI, space, health and medicine, climate change and the"
        " environment."
    ),
    "culture_society": (
        "Arts, sport, religion, education, crime, migration, disasters, accidents and crashes, and"
        " everyday life."
    ),
}

Side = Literal["left", "right", "international"]


def side_of(source: Source) -> Side | None:
    """Which side an outlet is on, from its baseline lean; None for centre."""
    if source.lean_axis == "local":
        return "international"
    if source.baseline_lean < 0:
        return "left"
    if source.baseline_lean > 0:
        return "right"
    # Centre outlets count as coverage but belong to no side.
    return None


def score(story: Story, sources: Mapping[str, Source]) -> float:
    """Rank a story by log2(1 + outlets) x regions x sides present."""
    outlets = story.source_ids
    regions = {sources[o].region for o in outlets}
    sides = {side for o in outlets if (side := side_of(sources[o])) is not None}
    # log2 damps outlet count, so reach and balance outweigh one region covering a story heavily.
    return math.log2(1 + len(outlets)) * len(regions) * len(sides)


def topic_of(
    story: Story, vectors: Mapping[str, np.ndarray], topic_vectors: Mapping[Topic, np.ndarray]
) -> Topic:
    """Return the topic whose description is closest to the story's average article vector."""
    centre = np.mean([vectors[a.url_hash] for a in story.articles], axis=0)
    # Topic vectors are unit length, so the largest dot product is the largest cosine similarity
    # whatever the centre's length.
    return max(topic_vectors, key=lambda t: float(topic_vectors[t] @ centre))


def _one_sided(story: Story, sources: Mapping[str, Source]) -> Side | None:
    axis = [sources[o] for o in story.source_ids if sources[o].lean_axis != "local"]
    if len(axis) < MIN_OUTLETS:
        return None
    for side in ("left", "right"):
        # Centre outlets stay in the total, so centre coverage makes a story less one-sided.
        if sum(side_of(s) == side for s in axis) >= ONE_SIDED_SHARE * len(axis):
            return side
    return None


def _enough_healthy(missing: Iterable[Source], health: Mapping[str, FeedHealth]) -> bool:
    missing = list(missing)
    # No health row means never fetched: its silence can't prove a blindspot, so it isn't healthy.
    healthy = sum(1 for s in missing if (h := health.get(s.id)) is not None and h.healthy)
    return bool(missing) and healthy >= MIN_HEALTHY_SHARE * len(missing)


def _latest_match(
    story: Story, posted_by_hash: Mapping[str, list[PostedStory]]
) -> PostedStory | None:
    matches = [p for a in story.articles for p in posted_by_hash.get(a.url_hash, [])]
    # The latest post is the version readers last saw, so new outlets are counted against it.
    return max(matches, key=lambda p: (p.posted_at, p.id), default=None)


def _enabled_sources_only(stories: list[Story], sources: Mapping[str, Source]) -> list[Story]:
    # A source turned off (or deleted) keeps its articles in the cache for up to 48 hours. Turning
    # it off should stop it counting at once, and an unknown source has no region or lean to use.
    kept: list[Story] = []
    dropped = 0
    for story in stories:
        articles = tuple(
            a for a in story.articles if a.source_id in sources and sources[a.source_id].enabled
        )
        dropped += len(story.articles) - len(articles)
        if len(articles) == len(story.articles):
            kept.append(story)
        elif articles:
            kept.append(Story(articles=articles))
    if dropped:
        logger.warning("left out %d articles from disabled or unknown sources", dropped)
    return kept


def _rank(pick: Pick) -> tuple[float, float, str]:
    # Ties go to the most recent article, then story_id, so the same input always gives one answer.
    return (-pick.score, -pick.story.last_published.timestamp(), pick.story.story_id)


def select(
    stories: list[Story],
    vectors: Mapping[str, np.ndarray],
    topic_vectors: Mapping[Topic, np.ndarray],
    sources: Mapping[str, Source],
    health: Mapping[str, FeedHealth],
    posted: list[PostedStory],
    now: datetime,
) -> Selection:
    """Choose up to 5 top stories and 3 blindspots, and the chain links for skipped repeats."""
    # Topics are picked by dot product, which only ranks like cosine similarity on unit vectors;
    # unnormalised ones would quietly pull stories toward the longest description.
    if not all(np.isclose(np.linalg.norm(v), 1.0, atol=1e-3) for v in topic_vectors.values()):
        raise ValueError("topic vectors must be unit length (embed with normalize_embeddings)")
    stories = _enabled_sources_only(stories, sources)

    posted_by_hash: dict[str, list[PostedStory]] = {}
    for p in posted:
        for h in p.url_hashes:
            posted_by_hash.setdefault(h, []).append(p)

    matches: dict[str, tuple[Story, PostedStory]] = {}
    candidates: list[Pick] = []
    for story in stories:
        match = _latest_match(story, posted_by_hash)
        update_of = None
        if match is not None:
            matches[story.story_id] = (story, match)
            if len(story.source_ids - match.posted_outlets) < UPDATE_NEW_OUTLETS:
                continue
            update_of = match.id
        if story.last_published < now - ELIGIBLE_WINDOW or len(story.source_ids) < MIN_OUTLETS:
            continue
        candidates.append(
            Pick(
                story=story,
                topic=topic_of(story, vectors, topic_vectors),
                score=score(story, sources),
                kind="top",
                update_of=update_of,
            )
        )
    candidates.sort(key=_rank)

    top: list[Pick] = []
    per_topic: Counter[Topic] = Counter()
    for pick in candidates:
        if len(top) == TOP_COUNT:
            break
        if per_topic[pick.topic] < TOPIC_CAP:
            top.append(pick)
            per_topic[pick.topic] += 1

    in_top = {p.story.story_id for p in top}
    blindspots = _blindspots(
        [c for c in candidates if c.story.story_id not in in_top], sources, health
    )

    picked = {p.story.story_id for p in (*top, *blindspots)}
    chain_links = tuple(
        ChainLink(posted_story_id=match.id, url_hash=a.url_hash, source_id=a.source_id)
        for story_id, (story, match) in matches.items()
        if story_id not in picked
        for a in story.articles
        if a.url_hash not in match.url_hashes
    )
    return Selection(top=tuple(top), blindspots=tuple(blindspots), chain_links=chain_links)


def _blindspots(
    rest: list[Pick], sources: Mapping[str, Source], health: Mapping[str, FeedHealth]
) -> list[Pick]:
    enabled = [s for s in sources.values() if s.enabled]
    # For a non-Western blindspot, the missing side is every Western outlet.
    west_healthy = _enough_healthy((s for s in enabled if s.region in WESTERN_REGIONS), health)

    non_western: list[Pick] = []
    one_sided: list[Pick] = []
    for pick in rest:
        outlets = [sources[o] for o in pick.story.source_ids]
        if west_healthy and all(s.region not in WESTERN_REGIONS for s in outlets):
            non_western.append(pick.model_copy(update={"kind": "non_western"}))
        side = _one_sided(pick.story, sources)
        if side is not None:
            missing_side = "right" if side == "left" else "left"
            if _enough_healthy((s for s in enabled if side_of(s) == missing_side), health):
                one_sided.append(pick.model_copy(update={"kind": "one_sided"}))

    # The non-Western slot is filled first; a story picked there leaves the one-sided pool.
    chosen = non_western[:NON_WESTERN_SLOTS]
    taken = {p.story.story_id for p in chosen}
    one_sided = [p for p in one_sided if p.story.story_id not in taken]
    chosen += one_sided[:ONE_SIDED_SLOTS]
    taken |= {p.story.story_id for p in one_sided[:ONE_SIDED_SLOTS]}
    # An empty slot is filled from the other pool, so the digest shows 3 whenever 3 exist.
    # A short one-sided pool is topped up from non-Western leftovers; otherwise any gap is the
    # non-Western slot, topped up from one-sided leftovers.
    if len(one_sided) < ONE_SIDED_SLOTS:
        spare = non_western[NON_WESTERN_SLOTS:]
    else:
        spare = one_sided[ONE_SIDED_SLOTS:]
    for pick in spare:
        if len(chosen) == NON_WESTERN_SLOTS + ONE_SIDED_SLOTS:
            break
        # A story in both pools may already be in as one-sided.
        if pick.story.story_id not in taken:
            chosen.append(pick)
            taken.add(pick.story.story_id)
    return sorted(chosen, key=_rank)
