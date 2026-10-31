import math
from datetime import timedelta

import numpy as np
import pytest
from conftest import NOW, MakeArticle

from news_summariser.config import Region, Source
from news_summariser.models import FeedHealth, Pick, PostedStory, Selection, Story, Topic
from news_summariser.selection import TOPIC_DESCRIPTIONS, score, select, topic_of

TOPICS: list[Topic] = list(TOPIC_DESCRIPTIONS)
# One axis per topic, so an article "about" a topic is that topic's unit vector.
TOPIC_VECTORS: dict[Topic, np.ndarray] = {t: np.eye(len(TOPICS))[i] for i, t in enumerate(TOPICS)}


def _source(
    source_id: str, region: Region, axis: str, lean: int = 0, *, enabled: bool = True
) -> Source:
    return Source.model_validate(
        {
            "id": source_id,
            "name": source_id,
            "url": f"https://example.com/{source_id}.xml",
            "region": region,
            "country": "XX",
            "language": "en",
            "lean_axis": axis,
            "baseline_lean": lean,
            "ownership": "private",
            "gov_aligned": False,
            "note": "made up for tests",
            "enabled": enabled,
        }
    )


# Western (us/europe): 4 left, 3 right, 1 centre. Non-Western: 4 local, 3 latam national (left).
SOURCES = {
    s.id: s
    for s in [
        _source("l1", "us", "us", -1),
        _source("l2", "us", "us", -1),
        _source("l3", "us", "us", -2),
        _source("l4", "europe", "uk", -1),
        _source("r1", "us", "us", 1),
        _source("r2", "us", "us", 1),
        _source("r3", "europe", "uk", 2),
        _source("c1", "us", "us", 0),
        _source("in1", "south_asia", "local"),
        _source("in2", "africa", "local"),
        _source("in3", "east_asia", "local"),
        _source("in4", "middle_east", "local"),
        _source("la1", "latam", "national", -1),
        _source("la2", "latam", "national", -1),
        _source("la3", "latam", "national", -1),
    ]
}
HEALTHY = {sid: FeedHealth(source_id=sid, last_checked_at=NOW) for sid in SOURCES}


class World:
    """Builds stories plus the vectors select() needs for them."""

    def __init__(self, make_article: MakeArticle) -> None:
        self.make_article = make_article
        self.vectors: dict[str, np.ndarray] = {}

    def story(
        self, name: str, outlets: list[str], topic: Topic = "world_politics", hours_ago: int = 1
    ) -> Story:
        articles = []
        for o in outlets:
            a = self.make_article(
                f"{name}-{o}", source_id=o, published_at=NOW - timedelta(hours=hours_ago)
            )
            self.vectors[a.url_hash] = TOPIC_VECTORS[topic]
            articles.append(a)
        return Story(articles=tuple(articles))

    def fillers(self) -> list[Story]:
        """Five high-scoring stories that fill the top 5, so others are left for blindspots."""
        wide = ["l1", "r1", "r3", "c1", "in1", "in2", "in3", "in4"]
        topics: list[Topic] = ["world_politics", "world_politics", "economy", "economy"]
        topics.append("science_tech_climate")
        return [self.story(f"fill{i}", wide, t) for i, t in enumerate(topics)]

    def select(
        self,
        stories: list[Story],
        health: dict[str, FeedHealth] = HEALTHY,
        posted: list[PostedStory] | None = None,
    ) -> Selection:
        return select(stories, self.vectors, TOPIC_VECTORS, SOURCES, health, posted or [], NOW)


@pytest.fixture
def world(make_article: MakeArticle) -> World:
    return World(make_article)


def _names(picks: tuple[Pick, ...]) -> list[str]:
    return [p.story.story_id.split("-")[0] for p in picks]


def test_score_is_damped_outlets_times_regions_times_sides(world: World) -> None:
    # 3 outlets, 3 regions (us, europe, south_asia), 3 sides (left, right, international).
    story = world.story("s", ["l1", "r3", "in1"])
    assert score(story, SOURCES) == pytest.approx(math.log2(4) * 3 * 3)
    # Centre counts as an outlet and a region-mate, but adds no side.
    centred = world.story("c", ["l1", "l2", "c1"])
    assert score(centred, SOURCES) == pytest.approx(math.log2(4) * 1 * 1)


def test_topic_is_the_closest_description(world: World) -> None:
    story = world.story("s", ["l1", "r1"], topic="economy")
    assert topic_of(story, world.vectors, TOPIC_VECTORS) == "economy"


def test_needs_three_outlets_and_an_article_in_the_last_24_hours(world: World) -> None:
    fresh = world.story("fresh", ["l1", "r1", "in1"])
    small = world.story("small", ["l1", "r1"])
    stale = world.story("stale", ["l1", "r1", "in1"], hours_ago=25)

    assert _names(world.select([fresh, small, stale]).top) == ["fresh"]


def test_old_articles_stay_when_the_story_is_still_running(
    world: World, make_article: MakeArticle
) -> None:
    old = world.story("run", ["l1", "r1"], hours_ago=40)
    new = make_article("run-in1", source_id="in1", published_at=NOW - timedelta(hours=2))
    world.vectors[new.url_hash] = TOPIC_VECTORS["world_politics"]
    story = Story(articles=(*old.articles, new))

    (pick,) = world.select([story]).top
    assert len(pick.story.articles) == 3


def test_at_most_two_stories_per_topic(world: World) -> None:
    wide = ["l1", "r1", "in1", "in2"]
    stories = [world.story(f"pol{i}", wide) for i in range(3)]
    stories.append(world.story("eco", ["l1", "r1", "in1"], topic="economy"))

    top = world.select(stories).top

    assert [p.topic for p in top].count("world_politics") == 2
    assert "eco" in _names(top)
    assert len(top) == 3


def test_ties_go_to_the_most_recent_article(world: World) -> None:
    older = world.story("older", ["l1", "r1", "in1"], hours_ago=5)
    newer = world.story("newer", ["l2", "r2", "in2"], hours_ago=1)

    assert _names(world.select([older, newer]).top) == ["newer", "older"]


@pytest.mark.parametrize(
    ("outlets", "one_sided"),
    [
        (["l1", "l2", "l3"], True),
        (["l1", "l2", "l3", "c1"], True),  # 3 of 4 is exactly 75%
        (["l1", "l2", "r1"], False),  # 67%
        (["l1", "l2", "c1", "r1"], False),  # centre stays in the total: 50%
        (["l1", "l2", "in1", "in2", "in3"], False),  # only 2 axis outlets
        (["r1", "r2", "r3"], True),
    ],
)
def test_one_sided_counts_axis_outlets_only(
    world: World, outlets: list[str], one_sided: bool
) -> None:
    story = world.story("b", outlets, topic="culture_society")

    blindspots = world.select([*world.fillers(), story]).blindspots

    assert [p.kind for p in blindspots] == (["one_sided"] if one_sided else [])


def test_non_western_needs_every_outlet_outside_us_and_europe(world: World) -> None:
    only_south = world.story("south", ["in1", "in2", "la1"], topic="culture_society")
    mixed = world.story("mixed", ["in1", "in2", "r1"], topic="culture_society")

    blindspots = world.select([*world.fillers(), only_south, mixed]).blindspots

    assert [(n, p.kind) for n, p in zip(_names(blindspots), blindspots, strict=True)] == [
        ("south", "non_western")
    ]


def test_top_stories_are_never_blindspots(world: World) -> None:
    story = world.story("b", ["l1", "l2", "l3"])

    selection = world.select([story])

    assert _names(selection.top) == ["b"]
    assert selection.blindspots == ()


def test_slots_split_two_one_sided_and_one_non_western(world: World) -> None:
    left = [world.story(f"left{i}", ["l1", "l2", "l3"], "culture_society") for i in range(3)]
    south = [world.story(f"south{i}", ["in1", "in2", "in3"], "culture_society") for i in range(2)]

    blindspots = world.select([*world.fillers(), *left, *south]).blindspots

    assert sorted(p.kind for p in blindspots) == ["non_western", "one_sided", "one_sided"]


def test_an_empty_pool_is_filled_from_the_other(world: World) -> None:
    south = [world.story(f"south{i}", ["in1", "in2", "in3"], "culture_society") for i in range(3)]

    blindspots = world.select([*world.fillers(), *south]).blindspots

    assert [p.kind for p in blindspots] == ["non_western"] * 3


def test_a_story_in_both_pools_is_picked_once(world: World) -> None:
    # Latin American left outlets: non-Western and one-sided at once.
    both = world.story("both", ["la1", "la2", "la3"], "culture_society")

    blindspots = world.select([*world.fillers(), both]).blindspots

    assert [(n, p.kind) for n, p in zip(_names(blindspots), blindspots, strict=True)] == [
        ("both", "non_western")
    ]


def _unhealthy(*source_ids: str) -> dict[str, FeedHealth]:
    health = dict(HEALTHY)
    for sid in source_ids:
        health[sid] = FeedHealth(source_id=sid, last_checked_at=NOW, problem="down")
    return health


@pytest.mark.parametrize(
    ("down", "kept"),
    [
        ((), True),
        (("r1",), True),  # 2 of 3 right-leaning feeds healthy
        (("r1", "r2"), False),  # 1 of 3: fewer than half
    ],
)
def test_one_sided_is_skipped_when_the_missing_side_is_offline(
    world: World, down: tuple[str, ...], kept: bool
) -> None:
    story = world.story("b", ["l1", "l2", "l3"], "culture_society")

    blindspots = world.select([*world.fillers(), story], health=_unhealthy(*down)).blindspots

    assert bool(blindspots) == kept


def test_a_feed_with_no_health_record_counts_as_unhealthy(world: World) -> None:
    story = world.story("b", ["l1", "l2", "l3"], "culture_society")
    health = {sid: h for sid, h in HEALTHY.items() if sid not in {"r1", "r2"}}

    assert world.select([*world.fillers(), story], health=health).blindspots == ()


def test_non_western_is_skipped_when_most_western_feeds_are_offline(world: World) -> None:
    story = world.story("south", ["in1", "in2", "in3"], "culture_society")
    # 5 of the 8 Western feeds down.
    health = _unhealthy("l1", "l2", "l3", "l4", "r1")

    assert world.select([*world.fillers(), story], health=health).blindspots == ()


def _posted(story: Story, post_id: int = 1, hours_ago: int = 24) -> PostedStory:
    return PostedStory(
        id=post_id,
        posted_at=NOW - timedelta(hours=hours_ago),
        kind="top",
        posted_outlets=frozenset(story.source_ids),
        url_hashes=frozenset(a.url_hash for a in story.articles),
    )


def test_a_repeat_is_skipped_and_its_new_articles_are_chained(world: World) -> None:
    yesterday = world.story("s", ["l1", "r1", "in1"], hours_ago=20)
    today = world.story("s", ["l1", "r1", "in1", "in2", "in3"])
    # Same story, grown by two outlets: not enough for an update.
    story = Story(articles=(*yesterday.articles, *today.articles[3:]))

    selection = world.select([story], posted=[_posted(yesterday)])

    assert selection.top == ()
    assert {(c.posted_story_id, c.url_hash) for c in selection.chain_links} == {
        (1, "s-in2"),
        (1, "s-in3"),
    }


def test_three_new_outlets_bring_a_story_back_as_an_update(world: World) -> None:
    yesterday = world.story("s", ["l1", "r1", "in1"], hours_ago=20)
    grown = world.story("s", ["l1", "r1", "in1", "in2", "in3", "in4"])
    story = Story(articles=(*yesterday.articles, *grown.articles[3:]))

    selection = world.select([story], posted=[_posted(yesterday)])

    (pick,) = selection.top
    assert pick.update_of == 1
    # A picked update is written as a new post by step 6, not chained.
    assert selection.chain_links == ()


def test_chained_outlets_still_count_as_new_for_an_update(world: World) -> None:
    yesterday = world.story("s", ["l1", "r1", "in1"], hours_ago=30)
    grown = world.story("s", ["l1", "r1", "in1", "in2", "in3", "in4"])
    story = Story(articles=(*yesterday.articles, *grown.articles[3:]))
    # in2 was chained in an earlier run: it matches, but readers never saw it.
    posted = _posted(yesterday).model_copy(
        update={"url_hashes": frozenset({*_posted(yesterday).url_hashes, "s-in2"})}
    )

    (pick,) = world.select([story], posted=[posted]).top

    assert pick.update_of == 1


def test_new_outlets_are_counted_against_the_latest_post(world: World) -> None:
    first = world.story("s", ["l1", "r1", "in1"], hours_ago=40)
    grown = world.story("s", ["l1", "r1", "in1", "in2", "in3", "in4"])
    story = Story(articles=(*first.articles, *grown.articles[3:]))
    older = _posted(first, post_id=1, hours_ago=40)
    # The update already showed in2-in4, so nothing is new against it.
    update = PostedStory(
        id=2,
        posted_at=NOW - timedelta(hours=20),
        kind="top",
        posted_outlets=frozenset(story.source_ids),
        url_hashes=frozenset(a.url_hash for a in story.articles),
    )

    selection = world.select([story], posted=[older, update])

    assert selection.top == ()
    assert selection.chain_links == ()


def test_overlap_with_a_non_eligible_story_still_chains(world: World) -> None:
    yesterday = world.story("s", ["l1", "r1", "in1"], hours_ago=30)
    late = world.story("s", ["l1", "r1", "in1", "in2"], hours_ago=26)
    story = Story(articles=(*yesterday.articles, late.articles[3]))

    selection = world.select([story], posted=[_posted(yesterday)])

    assert [c.url_hash for c in selection.chain_links] == ["s-in2"]


def test_an_empty_non_western_slot_is_filled_by_a_third_one_sided(world: World) -> None:
    left = [world.story(f"left{i}", ["l1", "l2", "l3"], "culture_society") for i in range(3)]

    blindspots = world.select([*world.fillers(), *left]).blindspots

    assert [p.kind for p in blindspots] == ["one_sided"] * 3


def test_disabled_and_unknown_sources_are_left_out(world: World) -> None:
    sources = {**SOURCES, "in1": SOURCES["in1"].model_copy(update={"enabled": False})}
    # in1 is disabled and "gone" isn't in sources.yaml at all: only 2 outlets remain.
    story = world.story("s", ["l1", "r1", "in1", "gone"])

    selection = select([story], world.vectors, TOPIC_VECTORS, sources, HEALTHY, [], NOW)

    assert selection.top == ()


def test_topic_vectors_must_be_unit_length(world: World) -> None:
    doubled: dict[Topic, np.ndarray] = {t: v * 2 for t, v in TOPIC_VECTORS.items()}

    with pytest.raises(ValueError, match="unit length"):
        select([], world.vectors, doubled, SOURCES, HEALTHY, [], NOW)
