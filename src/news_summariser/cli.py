"""Command-line entry point: python -m news_summariser <command>."""

import argparse
import logging
from contextlib import closing

from news_summariser.config import load_sources
from news_summariser.digest import run_digest
from news_summariser.ingest import run_ingest
from news_summariser.issues import sync_feed_issues
from news_summariser.store import CACHE_PATH, STATE_PATH, get_health, open_db


def _ingest(dry_run: bool) -> int:
    sources = load_sources()
    state, _ = open_db(STATE_PATH, "state", dry_run=dry_run)
    with closing(state):
        cache, cache_is_new = open_db(CACHE_PATH, "cache", dry_run=dry_run)
        with closing(cache):
            # After a cache eviction, a 304 would leave the empty cache unfilled, so fetch all.
            results = run_ingest(sources, state, cache, conditional=not cache_is_new)

    print(f"{'source':<16}{'status':>7}{'items':>7}{'new':>6}  problem")
    for outcome, health in results:
        status = "304" if outcome.not_modified else str(outcome.status or "-")
        flag = "  [issue]" if health.needs_issue else ""
        problem = health.problem or ""
        if health.redirected_to:
            problem = f"{problem}  (redirects to {health.redirected_to})".strip()
        print(
            f"{outcome.source_id:<16}{status:>7}{outcome.item_count:>7}{outcome.new_count:>6}"
            f"  {problem}{flag}"
        )
    unhealthy = sum(not h.healthy for _, h in results)
    new = sum(o.new_count for o, _ in results)
    print(f"\n{len(results)} feeds, {new} new articles, {unhealthy} unhealthy")
    if dry_run:
        print("Dry run: nothing written to data/.")
    return 0


def _feed_issues() -> int:
    sources = {s.id: s for s in load_sources(include_disabled=True)}
    state, _ = open_db(STATE_PATH, "state", dry_run=True)
    with closing(state):
        health = get_health(state)
    for line in sync_feed_issues(health, sources) or ["no feed Issues to open or close"]:
        print(line)
    return 0


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # httpx logs every request URL at INFO, and a Telegram URL contains the bot token. Actions
    # logs are public, so these stay at WARNING (CLAUDE.md: never log tokens).
    for name in ("httpx", "httpcore", "telegram"):
        logging.getLogger(name).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run a command."""
    parser = argparse.ArgumentParser(prog="news_summariser")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest", help="fetch feeds, store new articles, record health")
    ingest.add_argument(
        "--dry-run", action="store_true", help="fetch for real but write nothing to data/"
    )
    digest = commands.add_parser("digest", help="build the daily digest and post it to Telegram")
    digest.add_argument(
        "--dry-run", action="store_true", help="print the digest; post nothing, write nothing"
    )
    digest.add_argument(
        "--live",
        action="store_true",
        help="with --dry-run: make real AI calls (spend is logged as dev) instead of canned ones",
    )
    commands.add_parser(
        "feed-issues", help="open or close GitHub Issues for feeds, from state.db's feed health"
    )
    args = parser.parse_args(argv)
    _setup_logging()

    if args.command == "ingest":
        return _ingest(args.dry_run)
    if args.command == "digest":
        if args.live and not args.dry_run:
            parser.error("--live only goes with --dry-run")
        return run_digest(dry_run=args.dry_run, live=args.live)
    if args.command == "feed-issues":
        return _feed_issues()
    parser.error(f"unknown command {args.command}")
