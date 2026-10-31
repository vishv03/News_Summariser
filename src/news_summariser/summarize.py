"""Write each story's framing comparison with Claude Sonnet (ADR 0003, ADR 0008)."""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

import anthropic
import pydantic
from anthropic import Anthropic

from news_summariser import ai
from news_summariser.config import Settings, Source
from news_summariser.models import JudgedStory, Story, StoryWriteup
from news_summariser.selection import side_of

log = logging.getLogger(__name__)

SUMMARY_PROMPT_PATH = Path("prompts/summary.md")

# One retry for a bad reply, as for the judge (refusals aren't retried); after that the story
# falls back to the judge's output (ADR 0008).
ATTEMPTS = 2

_WRITEUP_FORMAT = ai.output_format(StoryWriteup)


def load_prompt(path: Path = SUMMARY_PROMPT_PATH) -> str:
    """Read the write-up prompt, without its maintainer comment."""
    return ai.load_prompt(path)


def _user_message(story: Story, judged: JudgedStory, sources: dict[str, Source]) -> str:
    articles = []
    for article in story.articles:
        j = judged.judgments.get(article.url_hash)
        if j is None:
            # Not judged: there's no take to describe, so Sonnet doesn't see it (ADR 0008).
            continue
        source = sources[article.source_id]
        articles.append(
            {
                "outlet": source.name,
                "state_media": source.ownership == "state",
                "side": side_of(source) or "centre",
                "lean": j.lean,
                "framing_tags": j.framing_tags,
                "loaded_words": [w.model_dump(exclude_none=True) for w in j.loaded_words],
                "take": j.take,
            }
        )
    # Compact: whitespace would be billed as input tokens on every call.
    return json.dumps(
        {"facts": judged.facts, "articles": articles}, ensure_ascii=False, separators=(",", ":")
    )


def _drop_absent_sides(
    writeup: StoryWriteup, story: Story, judged: JudgedStory, sources: dict[str, Source]
) -> StoryWriteup:
    # A line for a side with no judged article can only be invented, so it is removed.
    present = {
        side_of(sources[a.source_id]) for a in story.articles if a.url_hash in judged.judgments
    }
    changes = {
        side: None
        for side in ("left", "right", "international")
        if side not in present and getattr(writeup, side) is not None
    }
    if changes:
        log.warning("write-up for story %s described absent sides: %s", judged.story_id, changes)
    return writeup.model_copy(update=changes)


def write_story(
    client: Anthropic,
    spend_conn: sqlite3.Connection,
    settings: Settings,
    prompt: str,
    story: Story,
    judged: JudgedStory,
    sources: dict[str, Source],
    now: datetime,
    *,
    dev: bool = False,
) -> StoryWriteup | None:
    """Write one story's comparison; None means render falls back to the judge's output."""
    if judged.facts is None or not judged.judgments:
        # Nothing was judged, so there's nothing to compare; the story is headline-only.
        return None
    message = _user_message(story, judged, sources)
    for _ in range(ATTEMPTS):
        try:
            text = ai.ask(
                client,
                spend_conn,
                settings,
                settings.models.summary,
                "summary",
                prompt,
                message,
                _WRITEUP_FORMAT,
                now,
                dev=dev,
            )
        except ai.Refused as e:
            log.warning("write-up refused for story %s: %s", judged.story_id, e)
            return None
        except anthropic.APIError as e:
            log.warning("write-up call failed for story %s: %s", judged.story_id, e)
            return None
        if text is None:
            continue
        try:
            writeup = StoryWriteup.model_validate_json(text)
        except pydantic.ValidationError as e:
            log.warning("write-up for story %s failed validation: %s", judged.story_id, e)
            continue
        return _drop_absent_sides(writeup, story, judged, sources)
    log.warning("no write-up for story %s; falling back to the judge's output", judged.story_id)
    return None


def canned_writeup(judged: JudgedStory) -> StoryWriteup:
    """A placeholder write-up for a free --dry-run: no API call, nothing logged."""
    placeholder = "Dry run: placeholder, no AI call was made."
    return StoryWriteup(
        headline=f"Dry run: story {judged.story_id[:8]}",
        left=placeholder,
        right=placeholder,
        international=placeholder,
        differs=placeholder,
    )
