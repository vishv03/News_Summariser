"""Judge each article's lean and framing with Claude Haiku (ADR 0002, ADR 0008)."""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

import anthropic
import pydantic
from anthropic import Anthropic
from pydantic import BaseModel

from news_summariser import ai
from news_summariser.config import Settings, Source
from news_summariser.models import ArticleJudgment, JudgedStory, JudgeReply, Story

log = logging.getLogger(__name__)

JUDGE_PROMPT_PATH = Path("prompts/judge.md")

# One retry for a bad or incomplete reply (ADR 0008). The SDK already retries 429s and 5xx itself,
# and a refusal or a cut-off reply isn't retried: the same input would fail the same way.
ATTEMPTS = 2

# A 35-article story ran past MAX_TOKENS twice and lost every judgment (2026-10-31). Ten articles
# a call keeps each reply well inside the limit, and a failed batch costs only its own articles.
BATCH_SIZE = 10

# The API constrains the reply to the full schema; pydantic then checks each article on its own,
# so one broken judgment doesn't throw away the rest (see ArticleJudgment for the rules).
_REPLY_FORMAT = ai.output_format(JudgeReply)


class _LooseReply(BaseModel):
    facts: str
    judgments: list[dict[str, object]]


def load_prompt(path: Path = JUDGE_PROMPT_PATH) -> str:
    """Read the rubric, without its maintainer comment."""
    return ai.load_prompt(path)


def _article_ids(story: Story) -> dict[str, str]:
    # Short ids instead of 64-character url_hashes: fewer tokens, and easier to copy back exactly.
    return {f"a{i}": a.url_hash for i, a in enumerate(story.articles, start=1)}


def _user_message(story: Story, ids: dict[str, str], sources: dict[str, Source]) -> str:
    by_hash = {a.url_hash: a for a in story.articles}
    articles = []
    for article_id, url_hash in ids.items():
        article = by_hash[url_hash]
        source = sources[article.source_id]
        articles.append(
            {
                "article_id": article_id,
                "outlet": source.name,
                "state_media": source.ownership == "state",
                "lean_axis": source.lean_axis,
                "baseline_lean": source.baseline_lean,
                "reliability_note": source.reliability_note,
                "language": article.language,
                "title": article.title,
                "summary": article.summary,
            }
        )
    # Compact: whitespace would be billed as input tokens on every call.
    return json.dumps({"articles": articles}, ensure_ascii=False, separators=(",", ":"))


def _parse(text: str, story_id: str) -> tuple[str, list[ArticleJudgment]] | None:
    """Check the reply's shape, then each article on its own; None if the shape is wrong."""
    try:
        reply = _LooseReply.model_validate_json(text)
    except pydantic.ValidationError as e:
        log.warning("judge reply for story %s failed validation: %s", story_id, e)
        return None
    judgments = []
    for raw in reply.judgments:
        try:
            judgments.append(ArticleJudgment.model_validate(raw))
        except pydantic.ValidationError as e:
            log.warning("story %s: rejected judgment %s: %s", story_id, raw.get("article_id"), e)
    return reply.facts, judgments


def _judge_batch(
    client: Anthropic,
    spend_conn: sqlite3.Connection,
    settings: Settings,
    prompt: str,
    story: Story,
    ids: dict[str, str],
    sources: dict[str, Source],
    now: datetime,
    *,
    dev: bool,
) -> tuple[str | None, dict[str, ArticleJudgment]]:
    """One batch's facts line and judgments by url_hash, with one retry for gaps."""
    message = _user_message(story, ids, sources)
    judgments: dict[str, ArticleJudgment] = {}
    facts: str | None = None
    for _ in range(ATTEMPTS):
        try:
            text = ai.ask(
                client,
                spend_conn,
                settings,
                settings.models.judge,
                "judge",
                prompt,
                message,
                _REPLY_FORMAT,
                now,
                dev=dev,
            )
        except ai.Refused as e:
            log.warning("judge refused story %s: %s", story.story_id, e)
            break
        except anthropic.APIError as e:
            # Already retried by the SDK; another try now is unlikely to do better.
            log.warning("judge call failed for story %s: %s", story.story_id, e)
            break
        if text is None:
            # Cut off at MAX_TOKENS: the same input would be cut off again.
            break
        parsed = _parse(text, story.story_id)
        if parsed is not None:
            # A retry fills gaps only: the first good answer per article (and for the facts) is
            # kept, and ids the judge invented, or from another batch, are dropped.
            facts = facts or parsed[0]
            for j in parsed[1]:
                url_hash = ids.get(j.article_id)
                if url_hash is not None and url_hash not in judgments:
                    judgments[url_hash] = j
        if len(judgments) == len(ids):
            break
    return facts, judgments


def judge_story(
    client: Anthropic,
    spend_conn: sqlite3.Connection,
    settings: Settings,
    prompt: str,
    story: Story,
    sources: dict[str, Source],
    now: datetime,
    *,
    dev: bool = False,
) -> JudgedStory:
    """Judge every article in one story; articles still unjudged after a retry are marked so."""
    ids = _article_ids(story)
    judgments: dict[str, ArticleJudgment] = {}
    facts: str | None = None
    items = list(ids.items())
    for start in range(0, len(items), BATCH_SIZE):
        # Ids keep their story-wide numbers (a11, a12, …), so no two batches share one.
        batch = dict(items[start : start + BATCH_SIZE])
        batch_facts, batch_judgments = _judge_batch(
            client, spend_conn, settings, prompt, story, batch, sources, now, dev=dev
        )
        # The facts line comes from the first batch that wrote one.
        facts = facts or batch_facts
        judgments |= batch_judgments
    missing = frozenset(ids.values()) - judgments.keys()
    if missing:
        log.warning(
            "story %s: %d of %d articles not judged; their baselines will be shown",
            story.story_id,
            len(missing),
            len(ids),
        )
    return JudgedStory(
        story_id=story.story_id, facts=facts, judgments=judgments, not_judged=missing
    )


def canned_judgment(story: Story) -> JudgedStory:
    """Placeholder judgments for a free --dry-run: no API call, nothing logged."""
    judgments = {
        a.url_hash: ArticleJudgment(
            article_id=f"a{i}",
            lean="insufficient",
            framing_tags=["other:dryrun"],
            loaded_words=[],
            take="Dry run: placeholder, no AI call was made.",
        )
        for i, a in enumerate(story.articles, start=1)
    }
    return JudgedStory(
        story_id=story.story_id,
        facts="Dry run: placeholder facts, no AI call was made.",
        judgments=judgments,
    )
