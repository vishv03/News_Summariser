"""Cost every paid API call and enforce the monthly cap (ADR 0008)."""

import logging
import sqlite3
from datetime import UTC, datetime, timedelta

from anthropic.types import Usage

from news_summariser import store
from news_summariser.config import Prices, Settings
from news_summariser.models import SpendPurpose, SpendRecord, TokenUsage

log = logging.getLogger(__name__)

_PER_MILLION = 1_000_000


def usage_from_api(usage: Usage) -> TokenUsage:
    """Convert the SDK's usage object into our token counts."""
    return TokenUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        # None when the request used no caching.
        cache_write_tokens=usage.cache_creation_input_tokens or 0,
        cache_read_tokens=usage.cache_read_input_tokens or 0,
    )


def call_cost(usage: TokenUsage, prices: Prices) -> float:
    """Return one call's cost in USD."""
    return (
        usage.input_tokens * prices.input
        + usage.output_tokens * prices.output
        + usage.cache_write_tokens * prices.cache_write_5m
        + usage.cache_read_tokens * prices.cache_read
    ) / _PER_MILLION


def record_call(
    conn: sqlite3.Connection,
    settings: Settings,
    model: str,
    purpose: SpendPurpose,
    usage: TokenUsage,
    now: datetime,
) -> float:
    """Cost one API call, store it in the spend table and return its cost."""
    # A KeyError here would mean the money was spent but never logged; load_settings() checks
    # every configured model has a price, so this only fails for a model outside settings.yaml.
    cost = call_cost(usage, settings.prices[model])
    store.add_spend(
        conn, SpendRecord(at=now, model=model, purpose=purpose, usage=usage, cost_usd=cost)
    )
    return cost


def _day_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def month_to_date(conn: sqlite3.Connection, now: datetime) -> float:
    """Return USD spent so far this UTC calendar month."""
    return store.spend_since(conn, _day_start(now).replace(day=1))


def run_estimate(conn: sqlite3.Connection, settings: Settings, now: datetime) -> float:
    """Estimate today's run: the most expensive of the last N UTC days, or a fixed amount."""
    today = _day_start(now)
    # Whole days before today only: today's total is partial until the run has happened.
    days = store.daily_spend(conn, today - timedelta(days=settings.budget.estimate_days), today)
    if not days:
        return settings.budget.estimate_without_history_usd
    return max(days.values())


def can_afford_run(conn: sqlite3.Connection, settings: Settings, now: datetime) -> bool:
    """Pre-flight check: False means today's digest must be headline-only."""
    spent = month_to_date(conn, now)
    estimate = run_estimate(conn, settings, now)
    cap = settings.budget.monthly_cap_usd
    if spent + estimate > cap:
        # A whole headline-only digest rather than a partial one (ADR 0008).
        log.warning(
            "AI budget: $%.2f spent this month + $%.2f estimated for this run would pass the"
            " $%.2f cap; sending a headline-only digest",
            spent,
            estimate,
            cap,
        )
        return False
    return True
