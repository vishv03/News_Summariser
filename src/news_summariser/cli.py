"""Command-line entry point: python -m news_summariser <command>."""

import argparse
from contextlib import closing

from news_summariser.config import load_sources
from news_summariser.ingest import run_ingest
from news_summariser.store import CACHE_PATH, STATE_PATH, open_db


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


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run a command."""
    parser = argparse.ArgumentParser(prog="news_summariser")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest", help="fetch feeds, store new articles, record health")
    ingest.add_argument(
        "--dry-run", action="store_true", help="fetch for real but write nothing to data/"
    )
    args = parser.parse_args(argv)

    if args.command == "ingest":
        return _ingest(args.dry_run)
    parser.error(f"unknown command {args.command}")
