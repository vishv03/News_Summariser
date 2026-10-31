"""Load and validate config/sources.yaml."""

from collections import Counter
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

SOURCES_PATH = Path("config/sources.yaml")

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
