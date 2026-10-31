"""Open a GitHub Issue for a feed that has stayed unhealthy, and close it on recovery (ADR 0005)."""

import json
import subprocess
from collections.abc import Callable, Mapping

from news_summariser.config import Source
from news_summariser.models import FeedHealth

# A fixed title per feed is how an open Issue is found again, so there is never more than one.
TITLE_PREFIX = "Feed unhealthy: "

# Runs `gh` with these arguments and returns its stdout; replaced in tests.
Runner = Callable[[list[str]], str]


def run_gh(args: list[str]) -> str:
    """Run the GitHub CLI (preinstalled on Actions runners, authenticated by GH_TOKEN)."""
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def open_feed_issues(run: Runner) -> dict[str, int]:
    """Return open feed Issues as {source_id: issue number}."""
    out = run(
        [
            "issue",
            "list",
            "--state",
            "open",
            "--search",
            f'in:title "{TITLE_PREFIX.strip()}"',
            "--json",
            "number,title",
            "--limit",
            "200",
        ]
    )
    # The search is fuzzy, so only exact title matches count.
    return {
        issue["title"].removeprefix(TITLE_PREFIX): issue["number"]
        for issue in json.loads(out)
        if issue["title"].startswith(TITLE_PREFIX)
    }


def _body(source: Source, health: FeedHealth) -> str:
    lines = [
        f"**{source.name}** (`{source.id}`) has been unhealthy since {health.unhealthy_since}"
        f" ({health.strikes} runs in a row).",
        "",
        f"- Feed: {source.url}",
        f"- Problem: {health.problem}",
        f"- Last success: {health.last_success_at or 'never'}",
        f"- Newest item: {health.newest_item_at or 'unknown'}",
    ]
    if health.redirected_to:
        lines.append(f"- Redirects to: {health.redirected_to} (update the `url`, never the `id`)")
    lines += [
        "",
        "`config/sources.yaml` is never edited automatically. This Issue closes itself when the"
        " feed recovers.",
    ]
    return "\n".join(lines)


def sync_feed_issues(
    health: Mapping[str, FeedHealth], sources: Mapping[str, Source], run: Runner = run_gh
) -> list[str]:
    """Open and close feed Issues to match health; return a line per action taken."""
    open_issues = open_feed_issues(run)
    actions: list[str] = []
    for source_id, feed in sorted(health.items()):
        source = sources.get(source_id)
        if source is None or not source.enabled:
            continue
        if feed.needs_issue and source_id not in open_issues:
            run(
                [
                    "issue",
                    "create",
                    "--title",
                    f"{TITLE_PREFIX}{source_id}",
                    "--body",
                    _body(source, feed),
                ]
            )
            actions.append(f"opened Issue for {source_id}")
        elif feed.healthy and source_id in open_issues:
            number = str(open_issues[source_id])
            run(["issue", "close", number, "--comment", f"Recovered at {feed.last_checked_at}."])
            actions.append(f"closed Issue #{number} for {source_id}")
    return actions
