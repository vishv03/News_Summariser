"""Data passed between modules."""

from datetime import datetime, timedelta

from pydantic import BaseModel

# An Issue opens only after this many unhealthy runs in a row AND this long unhealthy (ADR 0005).
# Runs are every 20 minutes, so 3 runs alone would be one hour: too twitchy for short outages.
STRIKES_FOR_ISSUE = 3
UNHEALTHY_FOR_ISSUE = timedelta(hours=6)


class Article(BaseModel):
    """One feed item, as stored in cache.db."""

    url_hash: str
    source_id: str
    url: str
    title: str
    summary: str
    language: str
    # The feed's date, or when we first saw the item if the feed gives none.
    published_at: datetime
    fetched_at: datetime


class FetchOutcome(BaseModel):
    """What happened when one feed was fetched and parsed."""

    source_id: str
    status: int | None = None
    error: str | None = None
    not_modified: bool = False
    item_count: int = 0
    new_count: int = 0
    newest_item_at: datetime | None = None
    redirected_to: str | None = None
    etag: str | None = None
    last_modified: str | None = None


class FeedHealth(BaseModel):
    """A source's current health, one row per source in state.db."""

    source_id: str
    last_checked_at: datetime
    last_success_at: datetime | None = None
    # The newest item's own date (or first-seen time if undated); drives the 48-hour stale rule.
    newest_item_at: datetime | None = None
    last_status: int | None = None
    problem: str | None = None
    strikes: int = 0
    unhealthy_since: datetime | None = None
    redirected_to: str | None = None
    etag: str | None = None
    last_modified: str | None = None

    @property
    def healthy(self) -> bool:
        return self.problem is None

    @property
    def needs_issue(self) -> bool:
        """True when this feed has been unhealthy long enough to open a GitHub Issue."""
        return (
            self.strikes >= STRIKES_FOR_ISSUE
            and self.unhealthy_since is not None
            and self.last_checked_at - self.unhealthy_since >= UNHEALTHY_FOR_ISSUE
        )
