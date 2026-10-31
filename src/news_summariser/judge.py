"""Judge each article's lean and framing with Claude Haiku (ADR 0002, ADR 0008)."""

import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import anthropic
import pydantic
from anthropic import Anthropic
from anthropic.types import JSONOutputFormatParam

from news_summariser import budget
from news_summariser.config import Settings, Source
from news_summariser.models import ArticleJudgment, JudgedStory, JudgeReply, Story

log = logging.getLogger(__name__)

JUDGE_PROMPT_PATH = Path("prompts/judge.md")

# Ample for ~15 articles plus medium-effort thinking; a reply cut off here is retried as bad.
MAX_TOKENS = 16_000
# One retry for a bad or incomplete reply (ADR 0008). The SDK already retries 429s and 5xx itself.
ATTEMPTS = 2

# Built once: the API constrains the reply to this schema, and pydantic then checks the rules a
# schema can't express (see ArticleJudgment).
_REPLY_FORMAT = JSONOutputFormatParam(
    type="json_schema", schema=anthropic.transform_schema(JudgeReply.model_json_schema())
)


def load_prompt(path: Path = JUDGE_PROMPT_PATH) -> str:
    """Read the rubric, without its maintainer comment."""
    text = path.read_text(encoding="utf-8")
    # The leading HTML comment is for people editing the file; the model doesn't need it.
    return re.sub(r"\A\s*<!--.*?-->\s*", "", text, flags=re.DOTALL)


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
    return json.dumps({"articles": articles}, ensure_ascii=False, indent=1)


def _ask(
    client: Anthropic,
    conn: sqlite3.Connection,
    settings: Settings,
    prompt: str,
    message: str,
    now: datetime,
) -> JudgeReply | None:
    """Make one judge call and log its cost; return None for a refusal or a bad reply."""
    model = settings.models.judge
    response = client.messages.create(
        model=model.id,
        max_tokens=MAX_TOKENS,
        # Cached: every story in a run shares this system prompt, so calls after the first read
        # it at a tenth of the price.
        system=[{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": message}],
        output_config={"effort": model.effort, "format": _REPLY_FORMAT},
    )
    # Logged before anything can fail, so every billed call reaches the spend table.
    usage = budget.usage_from_api(response.usage)
    budget.record_call(conn, settings, model.id, "judge", usage, now)
    if response.stop_reason != "end_turn":
        # refusal, or max_tokens (a cut-off reply can't be valid JSON).
        log.warning("judge reply stopped early: %s", response.stop_reason)
        return None
    text = "".join(block.text for block in response.content if block.type == "text")
    try:
        return JudgeReply.model_validate_json(text)
    except pydantic.ValidationError as e:
        log.warning("judge reply failed validation: %s", e)
        return None


def judge_story(
    client: Anthropic,
    conn: sqlite3.Connection,
    settings: Settings,
    prompt: str,
    story: Story,
    sources: dict[str, Source],
    now: datetime,
) -> JudgedStory:
    """Judge every article in one story; articles still unjudged after a retry are marked so."""
    ids = _article_ids(story)
    message = _user_message(story, ids, sources)
    judgments: dict[str, ArticleJudgment] = {}
    for _ in range(ATTEMPTS):
        try:
            reply = _ask(client, conn, settings, prompt, message, now)
        except anthropic.APIError as e:
            # Already retried by the SDK; another try now is unlikely to do better.
            log.warning("judge call failed for story %s: %s", story.story_id, e)
            break
        if reply is not None:
            # A retry fills gaps only: the first good answer per article is kept, and ids the
            # judge invented are dropped.
            for j in reply.judgments:
                url_hash = ids.get(j.article_id)
                if url_hash is not None and url_hash not in judgments:
                    judgments[url_hash] = j
        if len(judgments) == len(ids):
            break
    missing = frozenset(ids.values()) - judgments.keys()
    if missing:
        log.warning(
            "story %s: %d of %d articles not judged; their baselines will be shown",
            story.story_id,
            len(missing),
            len(ids),
        )
    return JudgedStory(story_id=story.story_id, judgments=judgments, not_judged=missing)


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
    return JudgedStory(story_id=story.story_id, judgments=judgments)
