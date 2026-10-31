"""Turn the day's picks into Telegram messages: HTML text plus link buttons (ADR 0009)."""

import html
from collections.abc import Mapping
from datetime import date

from news_summariser.config import Source
from news_summariser.models import (
    Article,
    ArticleJudgment,
    Button,
    FeedHealth,
    JudgedStory,
    Pick,
    RenderedMessage,
    StoryWriteup,
)

# Private helpers are reused on purpose: a story's blindspot label and its "view unavailable"
# line must follow exactly the rules selection used to pick it (owner decision, step 6).
from news_summariser.selection import Side, _enough_healthy, _one_sided, side_of

# Telegram counts 4,096 characters after entity parsing; raw HTML is always at least as long as
# what it parses to, so checking the HTML is safe.
MAX_CHARS = 4096
# Last-resort cut for free text when a message is still too long without its optional lines.
CLIP_CHARS = 300

SIDES: tuple[Side, ...] = ("left", "right", "international")
ICON: dict[Side, str] = {"left": "🔵", "right": "🔴", "international": "🌍"}
NAME: dict[Side, str] = {"left": "Left", "right": "Right", "international": "International"}
STATE_ICON = "🏛"
CENTRE_ICON = "⚪"

LEGEND = (
    "🔵 Left · 🔴 Right · 🌍 International · ⚪ Centre · 🏛 State media\n"
    "Scores run from −2 (left) to +2 (right) on each outlet's own scale. "
    "<i>baseline</i> means the AI couldn't judge that article, so the outlet's usual lean is shown."
)


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _clip(text: str, limit: int | None) -> str:
    if limit is None or len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _judgment(article: Article, judged: JudgedStory | None) -> ArticleJudgment | None:
    return judged.judgments.get(article.url_hash) if judged is not None else None


def _lean(article: Article, judged: JudgedStory | None, source: Source) -> tuple[int, bool]:
    """The article's lean and whether it is the outlet's baseline rather than a judgment."""
    judgment = _judgment(article, judged)
    if judgment is not None and judgment.score is not None:
        return judgment.score, False
    # Not judged, or judged "insufficient": ADR 0008 shows the baseline, labelled.
    return source.baseline_lean, True


def _strongest(
    articles: list[Article], side: Side, judged: JudgedStory | None, sources: Mapping[str, Source]
) -> Article:
    """The side's most strongly leaning article: the clearest example of its framing."""

    def strength(a: Article) -> int:
        value, _ = _lean(a, judged, sources[a.source_id])
        if side == "left":
            return -value
        if side == "right":
            return value
        return abs(value)

    # Ties go to the earliest article, then url_hash, so the same input always gives one answer.
    return min(articles, key=lambda a: (-strength(a), a.published_at, a.url_hash))


def _score_text(value: int) -> str:
    return f"{value:+d}".replace("-", "−") if value else "0"


def _outlets_line(
    articles: list[Article], judged: JudgedStory | None, sources: Mapping[str, Source]
) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for a in sorted(articles, key=lambda a: sources[a.source_id].name):
        if a.source_id in seen:
            continue
        seen.add(a.source_id)
        source = sources[a.source_id]
        value, baseline = _lean(a, judged, source)
        state = f"{STATE_ICON} " if source.ownership == "state" else ""
        label = f"{state}{_esc(source.name)} {_score_text(value)}"
        parts.append(f"{label} (baseline)" if baseline else label)
    return " · ".join(parts)


def _group(
    story_articles: tuple[Article, ...], sources: Mapping[str, Source]
) -> dict[Side | None, list[Article]]:
    groups: dict[Side | None, list[Article]] = {}
    for a in story_articles:
        groups.setdefault(side_of(sources[a.source_id]), []).append(a)
    return groups


def _label(pick: Pick, sources: Mapping[str, Source]) -> str | None:
    if pick.kind == "non_western":
        return "🌏 <b>Blindspot:</b> big outside the West"
    if pick.kind == "one_sided":
        side = _one_sided(pick.story, sources)
        if side is not None:
            return f"🔦 <b>Blindspot:</b> covered mostly by the {side}"
        return "🔦 <b>Blindspot</b>"
    return None


def _offline_line(
    side: Side, sources: Mapping[str, Source], health: Mapping[str, FeedHealth]
) -> str | None:
    side_sources = [s for s in sources.values() if s.enabled and side_of(s) == side]
    # Same threshold selection uses for blindspots, so render never calls an outage a blindspot
    # or the other way round (owner decision, step 6).
    if not side_sources or _enough_healthy(side_sources, health):
        return None
    offline = sum(1 for s in side_sources if (h := health.get(s.id)) is None or not h.healthy)
    return f"{ICON[side]} <b>{NAME[side]}:</b> view unavailable ({offline} feeds offline)"


def _loaded_line(
    strongest: Mapping[Side, Article], judged: JudgedStory | None, sources: Mapping[str, Source]
) -> str | None:
    # One flag per side, from the article its button opens, so the line stays balanced and
    # matches what readers see when they tap (owner decision, step 6).
    flags: list[str] = []
    for side in SIDES:
        article = strongest.get(side)
        judgment = _judgment(article, judged) if article is not None else None
        if article is None or judgment is None or not judgment.loaded_words:
            continue
        word = judgment.loaded_words[0]
        gloss = f" (“{_esc(word.gloss)}”)" if word.gloss else ""
        flags.append(
            f"“{_esc(word.quote)}”{gloss} → {_esc(word.neutral)}"
            f" ({_esc(sources[article.source_id].name)})"
        )
    return "⚠ <b>Loaded language:</b> " + " · ".join(flags) if flags else None


def _buttons(
    strongest: Mapping[Side, Article],
    story_articles: tuple[Article, ...],
    sources: Mapping[str, Source],
) -> tuple[Button, ...]:
    buttons = [
        Button(label=f"{ICON[side]} {sources[a.source_id].name}", url=a.url)
        for side in SIDES
        if (a := strongest.get(side)) is not None
    ]
    local = min(
        (a for a in story_articles if a.language != "en"),
        key=lambda a: (a.published_at, a.url_hash),
        default=None,
    )
    if local is not None and local.url not in {b.url for b in buttons}:
        buttons.append(
            Button(label=f"🗣 {sources[local.source_id].name} ({local.language})", url=local.url)
        )
    return tuple(buttons)


def _story_text(
    pick: Pick,
    judged: JudgedStory | None,
    writeup: StoryWriteup | None,
    sources: Mapping[str, Source],
    health: Mapping[str, FeedHealth],
    strongest: Mapping[Side, Article],
    *,
    with_outlets: bool,
    with_loaded: bool,
    clip: int | None,
) -> str:
    story = pick.story
    groups = _group(story.articles, sources)
    earliest = min(story.articles, key=lambda a: (a.published_at, a.url_hash))
    lines: list[str] = []

    label = _label(pick, sources)
    if label:
        lines.append(label)
    if pick.update_of is not None:
        lines.append("🔄 <b>Update</b> on a story posted earlier")

    if judged is None or judged.facts is None:
        # Headline-only: the budget is spent, or the judge returned nothing (ADR 0008). Outlets'
        # own titles and links, no AI text.
        lines.append(f"📰 <b>{_esc(_clip(earliest.title, clip))}</b>")
        for side in (*SIDES, None):
            for a in groups.get(side, []):
                icon = ICON[side] if side is not None else CENTRE_ICON
                lines.append(
                    f'{icon} <a href="{html.escape(a.url)}">{_esc(_clip(a.title, clip))}</a>'
                    f" ({_esc(sources[a.source_id].name)})"
                )
        return "\n".join(lines)

    # Without a write-up, the headline is the earliest article's title (ADR 0008).
    headline = writeup.headline if writeup is not None else earliest.title
    lines.append(f"📰 <b>{_esc(_clip(headline, clip))}</b>")
    lines.append(_esc(_clip(judged.facts, clip)))
    lines.append("")
    for side in SIDES:
        articles = groups.get(side)
        if not articles:
            offline = _offline_line(side, sources, health)
            if offline:
                lines.append(offline)
            continue
        framing = getattr(writeup, side) if writeup is not None else None
        if framing is None:
            # Sonnet failed (or left this side out): fall back to the judge's take on the
            # article this side's button opens, or, if that one wasn't judged, on the side's
            # strongest article that was.
            judged_here = [a for a in articles if _judgment(a, judged) is not None]
            if judged_here:
                source_article = (
                    strongest[side]
                    if strongest[side] in judged_here
                    else _strongest(judged_here, side, judged, sources)
                )
                judgment = _judgment(source_article, judged)
                framing = judgment.take if judgment is not None else None
        text = _esc(_clip(framing, clip)) if framing else "<i>no framing available</i>"
        lines.append(f"{ICON[side]} <b>{NAME[side]}:</b> {text}")
        if with_outlets:
            lines.append(f"<i>{_outlets_line(articles, judged, sources)}</i>")
    if with_outlets and groups.get(None):
        lines.append(
            f"{CENTRE_ICON} <i>Also covering: {_outlets_line(groups[None], judged, sources)}</i>"
        )
    if with_loaded:
        loaded = _loaded_line(strongest, judged, sources)
        if loaded:
            lines.append(loaded)
    if writeup is not None:
        lines.append(f"🔍 <b>What differs:</b> {_esc(_clip(writeup.differs, clip))}")
    return "\n".join(lines)


def render_story(
    pick: Pick,
    judged: JudgedStory | None,
    writeup: StoryWriteup | None,
    sources: Mapping[str, Source],
    health: Mapping[str, FeedHealth],
) -> RenderedMessage:
    """Render one story; judged=None means headline-only (budget reached)."""
    groups = _group(pick.story.articles, sources)
    strongest: dict[Side, Article] = {
        side: _strongest(groups[side], side, judged, sources) for side in SIDES if groups.get(side)
    }
    # Optional lines go first, then free text is clipped: a story with many outlets must still
    # fit in one message rather than fail to send.
    for with_outlets, with_loaded, clip in (
        (True, True, None),
        (False, True, None),
        (False, False, None),
        (False, False, CLIP_CHARS),
    ):
        text = _story_text(
            pick,
            judged,
            writeup,
            sources,
            health,
            strongest,
            with_outlets=with_outlets,
            with_loaded=with_loaded,
            clip=clip,
        )
        if len(text) <= MAX_CHARS:
            break
    return RenderedMessage(text=text, buttons=_buttons(strongest, pick.story.articles, sources))


def render_header(
    day: date,
    top_count: int,
    blindspot_count: int,
    *,
    headline_only: bool,
    sources: Mapping[str, Source],
    health: Mapping[str, FeedHealth],
) -> RenderedMessage:
    """Render the day's first message: date, counts, legend and notices."""
    lines = [
        f"📰 <b>Daily digest · {day:%a} {day.day} {day:%b}</b>",
        f"{top_count} stories + {blindspot_count} blindspots",
    ]
    notices: list[str] = []
    if headline_only:
        notices.append("⚠ This month's AI budget is used up: headlines and links only today.")
    enabled = [s for s in sources.values() if s.enabled]
    offline = sum(1 for s in enabled if (h := health.get(s.id)) is not None and not h.healthy)
    if offline:
        notices.append(f"⚠ {offline} of {len(enabled)} feeds are offline today.")
    if notices:
        lines += ["", *notices]
    lines += ["", LEGEND]
    return RenderedMessage(text="\n".join(lines))
