"""Load and validate config/sources.yaml and config/settings.yaml."""

from collections import Counter
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SOURCES_PATH = Path("config/sources.yaml")
SETTINGS_PATH = Path("config/settings.yaml")

Region = Literal["us", "europe", "south_asia", "middle_east", "africa", "east_asia", "latam"]


class Source(BaseModel):
    """One outlet from sources.yaml."""

    # forbid: a typo in a field name should fail loudly, not be silently ignored
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str
    url: str = Field(pattern=r"^https?://")
    region: Region
    country: str = Field(pattern=r"^[A-Z]{2}$")
    language: str = Field(pattern=r"^[a-z]{2}$")
    lean_axis: Literal["us", "uk", "national", "local"]
    baseline_lean: int = Field(ge=-2, le=2)
    ownership: Literal["private", "public_service", "state"]
    gov_aligned: bool
    reliability_note: str | None = None
    note: str
    # Turning a source off instead of deleting it keeps its entry, reasoning and history keyed
    # on its id, so it can be switched back on if its feed recovers.
    enabled: bool = True


class SourcesFile(BaseModel):
    """The whole sources.yaml file."""

    model_config = ConfigDict(extra="forbid")

    sources: list[Source]

    @field_validator("sources")
    @classmethod
    def _unique_ids(cls, sources: list[Source]) -> list[Source]:
        # History and dedupe are keyed on id, so a duplicate would merge two outlets' records.
        counts = Counter(s.id for s in sources)
        dupes = [source_id for source_id, n in counts.items() if n > 1]
        if dupes:
            raise ValueError(f"duplicate source ids: {sorted(dupes)}")
        return sources


def load_sources(path: Path = SOURCES_PATH, *, include_disabled: bool = False) -> list[Source]:
    """Read sources.yaml and return validated sources, enabled ones only unless asked."""
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    sources = SourcesFile.model_validate(data).sources
    return sources if include_disabled else [s for s in sources if s.enabled]


Effort = Literal["low", "medium", "high", "xhigh", "max"]


class ModelSettings(BaseModel):
    """Which model does one job, and at what effort."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^claude-[a-z0-9-]+$")
    effort: Effort


class Models(BaseModel):
    """The model for each AI job (ADR 0003)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    judge: ModelSettings
    summary: ModelSettings


class Prices(BaseModel):
    """USD per million tokens for one model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache_write_5m: float = Field(ge=0)
    cache_read: float = Field(ge=0)


class BudgetSettings(BaseModel):
    """The monthly cap and the pre-flight estimate (ADR 0008)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    monthly_cap_usd: float = Field(gt=0)
    estimate_days: int = Field(ge=1)
    estimate_without_history_usd: float = Field(gt=0)


class Settings(BaseModel):
    """The whole settings.yaml file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    models: Models
    prices: dict[str, Prices]
    budget: BudgetSettings

    @model_validator(mode="after")
    def _every_model_priced(self) -> "Settings":
        # A model with no price would be costed at $0, and the cap would never trip.
        unpriced = {self.models.judge.id, self.models.summary.id} - self.prices.keys()
        if unpriced:
            raise ValueError(f"no prices for models: {sorted(unpriced)}")
        return self


def load_settings(path: Path = SETTINGS_PATH) -> Settings:
    """Read settings.yaml and return validated settings."""
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Settings.model_validate(data)
