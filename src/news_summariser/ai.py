"""One structured Claude call with its cost logged first (ADR 0008), for judge and summary."""

import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import anthropic
from anthropic import Anthropic
from anthropic.types import JSONOutputFormatParam
from pydantic import BaseModel

from news_summariser import budget
from news_summariser.config import ModelSettings, Settings
from news_summariser.models import SpendPurpose

log = logging.getLogger(__name__)

# Ample for a story's worth of output plus thinking; a reply cut off here counts as bad.
MAX_TOKENS = 16_000


class Refused(Exception):
    """The model declined the request; retrying the same input won't help."""


def load_prompt(path: Path) -> str:
    """Read a prompt file, without its leading maintainer comment."""
    text = path.read_text(encoding="utf-8")
    # The comment is for people editing the file; the model doesn't need it.
    return re.sub(r"\A\s*<!--.*?-->\s*", "", text, flags=re.DOTALL)


def output_format(model: type[BaseModel]) -> JSONOutputFormatParam:
    """The API's structured-output format for a pydantic model."""
    return JSONOutputFormatParam(
        type="json_schema", schema=anthropic.transform_schema(model.model_json_schema())
    )


def ask(
    client: Anthropic,
    spend_conn: sqlite3.Connection,
    settings: Settings,
    model: ModelSettings,
    purpose: SpendPurpose,
    prompt: str,
    message: str,
    reply_format: JSONOutputFormatParam,
    now: datetime,
    *,
    dev: bool = False,
) -> str | None:
    """Make one call and log its cost; return the reply text, or None if it was cut off.

    Raises Refused for a refusal, and anthropic.APIError when the SDK's own retries ran out.
    """
    response = client.messages.create(
        model=model.id,
        max_tokens=MAX_TOKENS,
        # Cached: every story in a run shares this system prompt, so calls after the first read
        # it at a tenth of the price.
        system=[{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": message}],
        output_config={"effort": model.effort, "format": reply_format},
    )
    # Logged (and committed) before anything can fail, so every billed call is on record.
    usage = budget.usage_from_api(response.usage)
    budget.record_call(spend_conn, settings, model.id, purpose, usage, now, dev=dev)
    if response.stop_reason == "refusal":
        raise Refused(str(response.stop_details))
    if response.stop_reason != "end_turn":
        # max_tokens: a cut-off reply can't be valid JSON.
        log.warning("%s reply stopped early: %s", purpose, response.stop_reason)
        return None
    return "".join(block.text for block in response.content if block.type == "text")
