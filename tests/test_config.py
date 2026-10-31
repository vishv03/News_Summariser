from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from news_summariser.config import Settings, SourcesFile, load_settings, load_sources

SOURCE = {
    "id": "example",
    "name": "Example",
    "url": "https://example.com/feed",
    "region": "europe",
    "country": "GB",
    "language": "en",
    "lean_axis": "uk",
    "baseline_lean": 0,
    "ownership": "private",
    "gov_aligned": False,
    "note": "test",
}


def test_real_sources_file_loads() -> None:
    sources = load_sources(include_disabled=True)
    assert len(sources) >= 30
    assert len({s.id for s in sources}) == len(sources)


def test_disabled_sources_skipped_unless_asked(tmp_path: Path) -> None:
    path = tmp_path / "sources.yaml"
    off = {**SOURCE, "id": "off", "enabled": False}
    path.write_text(yaml.safe_dump({"sources": [SOURCE, off]}), encoding="utf-8")
    assert [s.id for s in load_sources(path)] == ["example"]
    assert [s.id for s in load_sources(path, include_disabled=True)] == ["example", "off"]


def test_duplicate_ids_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate source ids"):
        SourcesFile.model_validate({"sources": [SOURCE, SOURCE]})


@pytest.mark.parametrize(
    ("field", "value"),
    [("baseline_lean", 3), ("region", "mars"), ("url", "ftp://x"), ("id", "Bad-Id")],
)
def test_bad_values_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        SourcesFile.model_validate({"sources": [{**SOURCE, field: value}]})


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        SourcesFile.model_validate({"sources": [{**SOURCE, "baseline_laen": 1}]})


PRICES = {"input": 1.0, "output": 2.0, "cache_write_5m": 1.25, "cache_read": 0.1}
SETTINGS = {
    "models": {
        "judge": {"id": "claude-haiku-5-5", "effort": "medium"},
        "summary": {"id": "claude-sonnet-5-5", "effort": "low"},
    },
    "prices": {"claude-haiku-5-5": PRICES, "claude-sonnet-5-5": PRICES},
    "budget": {"monthly_cap_usd": 4.0, "estimate_days": 7, "estimate_without_history_usd": 0.25},
}


def test_real_settings_file_loads() -> None:
    settings = load_settings()
    assert settings.budget.monthly_cap_usd == 4.0
    assert settings.models.judge.id in settings.prices


def test_unpriced_model_rejected() -> None:
    data = {**SETTINGS, "prices": {"claude-haiku-5-5": PRICES}}
    with pytest.raises(ValidationError, match="no prices for models"):
        Settings.model_validate(data)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("budget", "monthly_cap_usd", 0),
        ("budget", "estimate_days", 0),
        ("budget", "monthly_cap_usd_typo", 4.0),
    ],
)
def test_bad_budget_rejected(section: str, field: str, value: object) -> None:
    data = {**SETTINGS, section: {**SETTINGS[section], field: value}}
    with pytest.raises(ValidationError):
        Settings.model_validate(data)


def test_bad_effort_rejected() -> None:
    models = {**SETTINGS["models"], "judge": {"id": "claude-haiku-5-5", "effort": "extreme"}}
    with pytest.raises(ValidationError):
        Settings.model_validate({**SETTINGS, "models": models})
