import pytest
from pydantic import ValidationError

from news_summariser.config import SourcesFile, load_sources

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
    sources = load_sources()
    assert len(sources) >= 30
    assert len({s.id for s in sources}) == len(sources)


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
