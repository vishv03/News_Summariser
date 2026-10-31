"""Data passed between modules."""

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

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


class Story(BaseModel):
    """A group of articles about the same story, as returned by clustering (ADR 0006)."""

    # Frozen with a tuple, so a story can't be emptied after it's built: story_id and the dates
    # would then fail on min()/max(). Code that narrows a story builds a new one instead.
    model_config = ConfigDict(frozen=True)

    # Region, lean and ownership are looked up from sources.yaml by source_id, never copied here,
    # so a changed baseline can't leave stale values behind.
    articles: tuple[Article, ...] = Field(min_length=1)

    @property
    def story_id(self) -> str:
        """The earliest article's url_hash, so the id stays the same across runs."""
        # url_hash breaks ties, so two articles published in the same second always give one answer.
        return min(self.articles, key=lambda a: (a.published_at, a.url_hash)).url_hash

    @property
    def source_ids(self) -> set[str]:
        return {a.source_id for a in self.articles}

    @property
    def first_published(self) -> datetime:
        return min(a.published_at for a in self.articles)

    @property
    def last_published(self) -> datetime:
        return max(a.published_at for a in self.articles)


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
