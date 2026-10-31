import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from anthropic.types import Usage

from news_summariser import budget, store
from news_summariser.config import Settings
from news_summariser.models import SpendPurpose, SpendRecord, TokenUsage

# Mid-month and mid-day UTC, so "this month" and "today" both have room either side.
NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)


def _spend(
    conn: sqlite3.Connection,
    at: datetime,
    cost: float,
    *,
    purpose: SpendPurpose = "judge",
    dev: bool = False,
) -> None:
    usage = TokenUsage(input_tokens=1, output_tokens=1)
    record = SpendRecord(
        at=at, model="claude-haiku-5-5", purpose=purpose, usage=usage, cost_usd=cost, dev=dev
    )
    store.add_spend(conn, record)


def test_usage_from_api_treats_missing_cache_counts_as_zero() -> None:
    usage = Usage(input_tokens=100, output_tokens=50)
    assert budget.usage_from_api(usage) == TokenUsage(input_tokens=100, output_tokens=50)


def test_call_cost_uses_every_token_kind(settings: Settings) -> None:
    usage = TokenUsage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_write_tokens=1_000_000,
        cache_read_tokens=1_000_000,
    )
    # Haiku 5.5: 0.10 + 0.50 + 0.125 + 0.01 per million tokens.
    assert budget.call_cost(usage, settings.prices["claude-haiku-5-5"]) == pytest.approx(0.735)


def test_record_call_stores_the_cost(state: sqlite3.Connection, settings: Settings) -> None:
    usage = TokenUsage(input_tokens=2_000, output_tokens=1_000)
    cost = budget.record_call(state, settings, "claude-sonnet-5-5", "summary", usage, NOW)
    # Sonnet 5.5: 2,000 x $2/M + 1,000 x $10/M.
    assert cost == pytest.approx(0.014)
    assert budget.month_to_date(state, NOW) == pytest.approx(0.014)


def test_month_to_date_counts_only_this_utc_month(state: sqlite3.Connection) -> None:
    _spend(state, datetime(2026, 9, 30, 23, 59, tzinfo=UTC), 1.0)
    _spend(state, datetime(2026, 10, 1, 0, 0, tzinfo=UTC), 0.5)
    assert budget.month_to_date(state, NOW) == pytest.approx(0.5)


def test_estimate_without_history(state: sqlite3.Connection, settings: Settings) -> None:
    assert budget.run_estimate(state, settings, NOW) == settings.budget.estimate_without_history_usd


def test_estimate_is_the_most_expensive_recent_day(
    state: sqlite3.Connection, settings: Settings
) -> None:
    _spend(state, NOW - timedelta(days=2), 0.10)
    _spend(state, NOW - timedelta(days=3), 0.07)
    _spend(state, NOW - timedelta(days=3), 0.05)  # same day: 0.12 in total
    assert budget.run_estimate(state, settings, NOW) == pytest.approx(0.12)


def test_estimate_ignores_today_and_days_beyond_the_window(
    state: sqlite3.Connection, settings: Settings
) -> None:
    _spend(state, NOW - timedelta(hours=1), 9.0)  # today: partial, so not an estimate
    _spend(state, NOW - timedelta(days=settings.budget.estimate_days + 1), 9.0)
    _spend(state, NOW - timedelta(days=1), 0.08)
    assert budget.run_estimate(state, settings, NOW) == pytest.approx(0.08)


def test_estimate_ignores_dev_runs_and_breaking_alerts(
    state: sqlite3.Connection, settings: Settings
) -> None:
    _spend(state, NOW - timedelta(days=1), 0.75, dev=True)  # a day of --live testing
    _spend(state, NOW - timedelta(days=2), 0.50, purpose="breaking")
    _spend(state, NOW - timedelta(days=2), 0.04, purpose="summary")
    assert budget.run_estimate(state, settings, NOW) == pytest.approx(0.04)
    # ...but every real cent still counts toward the cap.
    assert budget.month_to_date(state, NOW) == pytest.approx(1.29)


def test_record_call_commits_at_once(tmp_path: Path, settings: Settings) -> None:
    path = tmp_path / "state.db"
    conn, _ = store.open_db(path, "state")
    usage = TokenUsage(input_tokens=1_000, output_tokens=0)
    budget.record_call(conn, settings, "claude-haiku-5-5", "judge", usage, NOW)
    # Another connection sees the row, so a crash in this one couldn't roll it back.
    other, _ = store.open_db(path, "state")
    assert store.spend_since(other, NOW) == pytest.approx(0.0001)


def test_can_afford_run_under_cap(state: sqlite3.Connection, settings: Settings) -> None:
    _spend(state, NOW - timedelta(days=1), 1.0)
    assert budget.can_afford_run(state, settings, NOW)


def test_cannot_afford_run_when_estimate_would_pass_cap(
    state: sqlite3.Connection, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    cap = settings.budget.monthly_cap_usd
    _spend(state, NOW - timedelta(days=1), 0.20)
    _spend(state, datetime(2026, 10, 1, tzinfo=UTC), cap - 0.30)  # 0.10 left after both
    with caplog.at_level(logging.WARNING):
        assert not budget.can_afford_run(state, settings, NOW)
    assert "headline-only" in caplog.text


def test_prune_keeps_the_whole_current_month(state: sqlite3.Connection) -> None:
    cache, _ = store.open_db(Path("unused"), "cache", dry_run=True)
    now = datetime(2026, 10, 31, 12, tzinfo=UTC)
    _spend(state, datetime(2026, 10, 1, tzinfo=UTC), 1.0)  # over 30 days old, but this month
    _spend(state, datetime(2026, 9, 15, tzinfo=UTC), 1.0)  # last month and over 30 days old
    store.prune(state, cache, now)
    assert store.spend_since(state, datetime(2026, 1, 1, tzinfo=UTC)) == pytest.approx(1.0)
