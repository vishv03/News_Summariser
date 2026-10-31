import json
from datetime import timedelta

from conftest import NOW

from news_summariser.config import Source
from news_summariser.issues import sync_feed_issues
from news_summariser.models import FeedHealth


class FakeGh:
    """Stands in for the gh CLI: answers `issue list` with fixed Issues and records every call."""

    def __init__(self, open_issues: list[dict[str, object]]) -> None:
        self.open_issues = open_issues
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        return json.dumps(self.open_issues) if args[:2] == ["issue", "list"] else ""


def _down(source_id: str) -> FeedHealth:
    return FeedHealth(
        source_id=source_id,
        last_checked_at=NOW,
        problem="HTTP 404",
        strikes=3,
        unhealthy_since=NOW - timedelta(hours=7),
    )


def test_opens_one_issue_per_feed_and_closes_on_recovery(sources: dict[str, Source]) -> None:
    gh = FakeGh(
        [
            {"number": 4, "title": "Feed unhealthy: bbc"},
            {"number": 5, "title": "Feed unhealthy: nyt"},
            # A fuzzy search hit that isn't one of ours.
            {"number": 6, "title": "Unrelated: Feed unhealthy: guardian"},
        ]
    )
    health = {
        "nyt": _down("nyt"),  # already has an Issue: nothing new
        "guardian": _down("guardian"),  # needs one
        "bbc": FeedHealth(source_id="bbc", last_checked_at=NOW),  # recovered
        "foxnews": FeedHealth(source_id="foxnews", last_checked_at=NOW),  # healthy, no Issue
    }

    actions = sync_feed_issues(health, sources, gh)

    assert actions == ["closed Issue #4 for bbc", "opened Issue for guardian"]
    create = next(c for c in gh.calls if c[:2] == ["issue", "create"])
    assert create[create.index("--title") + 1] == "Feed unhealthy: guardian"
    assert "HTTP 404" in create[create.index("--body") + 1]


def test_not_yet_long_enough_opens_nothing(sources: dict[str, Source]) -> None:
    gh = FakeGh([])
    brief = _down("nyt").model_copy(update={"unhealthy_since": NOW - timedelta(hours=1)})

    assert sync_feed_issues({"nyt": brief}, sources, gh) == []
