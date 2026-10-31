"""Data passed between modules."""

import re
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# An Issue opens only after this many unhealthy runs in a row AND this long unhealthy (ADR 0005).
# Once breaking.yml runs every 20 minutes, 3 runs alone would be one hour: too twitchy for short
# outages. (In v1, ingest runs every 3 hours, so 3 runs already take 9; ADR 0009.)
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


SpendPurpose = Literal["judge", "summary", "breaking"]


class TokenUsage(BaseModel):
    """Token counts for one API call, as billed."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(ge=0)
    # Includes thinking tokens: they're billed as output.
    output_tokens: int = Field(ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)


class SpendRecord(BaseModel):
    """One paid API call, as stored in state.db (ADR 0008)."""

    model_config = ConfigDict(frozen=True)

    at: datetime
    model: str
    purpose: SpendPurpose
    usage: TokenUsage
    cost_usd: float = Field(ge=0)
    # A --dry-run --live call: it counts toward the cap, but not toward the run estimate.
    dev: bool = False


Topic = Literal["world_politics", "economy", "science_tech_climate", "culture_society"]
# top: one of the top 5. one_sided: a blindspot covered mostly by Left or Right.
# non_western: a blindspot covered only by outlets outside the us and europe regions.
PickKind = Literal["top", "one_sided", "non_western"]


class PostedStory(BaseModel):
    """A story already sent in a digest, as read from state.db (ADR 0007)."""

    model_config = ConfigDict(frozen=True)

    id: int
    posted_at: datetime
    kind: PickKind
    # Outlets in the story when it was sent; the new-outlets rule counts against these.
    posted_outlets: frozenset[str]
    # Posted and chained articles alike: any overlap means the same story.
    url_hashes: frozenset[str]


class Pick(BaseModel):
    """One story chosen for the digest."""

    model_config = ConfigDict(frozen=True)

    story: Story
    topic: Topic
    score: float
    kind: PickKind
    # The posted story this one updates, or None when it's new.
    update_of: int | None = None


class ChainLink(BaseModel):
    """An article from a skipped repeat, to be added to its posted story after the send."""

    model_config = ConfigDict(frozen=True)

    posted_story_id: int
    url_hash: str
    source_id: str


class Selection(BaseModel):
    """What select() chose, plus the chain links step 6 writes after sending."""

    model_config = ConfigDict(frozen=True)

    top: tuple[Pick, ...]
    blindspots: tuple[Pick, ...]
    chain_links: tuple[ChainLink, ...]


class DigestDay(BaseModel):
    """How far one day's digest got, as read from state.db (ADR 0009)."""

    model_config = ConfigDict(frozen=True)

    day: date
    header_sent: bool = False
    top_sent: int = 0
    one_sided_sent: int = 0
    non_western_sent: int = 0
    done: bool = False

    @property
    def blindspots_sent(self) -> int:
        return self.one_sided_sent + self.non_western_sent


class Button(BaseModel):
    """A link button under a message."""

    model_config = ConfigDict(frozen=True)

    label: str
    url: str


class RenderedMessage(BaseModel):
    """One Telegram message, ready to send: HTML text plus link buttons."""

    model_config = ConfigDict(frozen=True)

    text: str
    buttons: tuple[Button, ...] = ()


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


# The fixed list in prompts/judge.md (ADR 0008), or "other:<word>" for anything else.
FRAMING_TAG_PATTERN = (
    r"(conflict|human_cost|economic|security|responsibility|morality|legal|political_strategy"
    r"|sovereignty|evidence|other:[a-z]+(?:[-_][a-z]+)*)"
)


class LoadedWord(BaseModel):
    """A word or phrase the judge found loaded, with a neutral alternative."""

    model_config = ConfigDict(frozen=True)

    quote: str = Field(description="The exact words, in the article's original language.")
    neutral: str = Field(description="A neutral alternative, in English.")
    gloss: str | None = Field(
        default=None, description="English translation of the quote; only for non-English text."
    )


class ArticleJudgment(BaseModel):
    """The judge's verdict on one article, following prompts/judge.md."""

    model_config = ConfigDict(frozen=True)

    article_id: str
    # Text, not a number: a JSON schema enum can't mix numbers with "insufficient".
    lean: Literal["-2", "-1", "0", "+1", "+2", "insufficient"]
    reason: str | None = Field(
        default=None, description="Required for every score; null when lean is insufficient."
    )
    confidence: Literal["low", "medium", "high"] | None = Field(
        default=None, description="Required for every score; null when lean is insufficient."
    )
    framing_tags: list[str] = Field(min_length=1, max_length=3)
    loaded_words: list[LoadedWord] = Field(max_length=3)
    take: str

    @model_validator(mode="after")
    def _check_rubric(self) -> "ArticleJudgment":
        # These rules can't be written as one JSON schema, so pydantic checks them; a reply that
        # breaks them counts as bad and is retried once.
        scored = self.lean != "insufficient"
        if scored and (self.reason is None or self.confidence is None):
            raise ValueError("a score needs a reason and a confidence")
        if not scored and self.confidence is not None:
            raise ValueError("insufficient has no confidence")
        bad_tags = [t for t in self.framing_tags if not re.fullmatch(FRAMING_TAG_PATTERN, t)]
        if bad_tags:
            raise ValueError(f"unknown framing tags: {bad_tags}")
        return self

    @property
    def score(self) -> int | None:
        """The lean as a number, or None when insufficient."""
        return None if self.lean == "insufficient" else int(self.lean)


class JudgeReply(BaseModel):
    """What the judge returns for one story: a neutral fact line and one entry per article."""

    facts: str = Field(description="2-3 neutral sentences on what happened, in English.")
    judgments: list[ArticleJudgment]


class JudgedStory(BaseModel):
    """A story's judgments, keyed by article url_hash."""

    model_config = ConfigDict(frozen=True)

    story_id: str
    # None when no judge reply came back at all; the story is then headline-only.
    facts: str | None
    judgments: dict[str, ArticleJudgment]
    # Articles the judge failed on, even after a retry: the digest shows their outlet's baseline,
    # labelled as such. When every article is here, the story is headline-only.
    not_judged: frozenset[str] = frozenset()


class StoryWriteup(BaseModel):
    """Sonnet's comparison for one story, following prompts/summary.md."""

    model_config = ConfigDict(frozen=True)

    headline: str = Field(description="At most 12 words, neutral.")
    left: str | None = Field(description="How left outlets framed it; null when none covered it.")
    right: str | None = Field(description="How right outlets framed it; null when none covered it.")
    international: str | None = Field(
        description="How international outlets framed it; null when none covered it."
    )
    differs: str = Field(description="The clearest difference between the sides' framing.")
