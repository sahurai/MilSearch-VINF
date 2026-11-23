# ==============================================================================
# LUCENE INDEX TESTS — MilSearch
# ==============================================================================
# PURPOSE:
#     Build a small Lucene index and test the query types of the Lucene search.
#     The tests need PyLucene: run them in Docker with "python -m tools.lucene test".
#     Without PyLucene (the local virtual environment), pytest skips this file.
#
# RESPONSIBILITIES:
#     1. Make sure that the analyzer matches compound tokens and calibers as the own tokenizer does.
#     2. Test Boolean, phrase, proximity, fuzzy, wildcard, range, field and keyword queries.
#     3. Test BM25F scoring, sorting by a numeric field and the errors of a wrong query.
# ==============================================================================

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("lucene")

from lucene_index.build import build_lucene_index  # noqa: E402  (after the PyLucene check)
from lucene_index.search import SCORING_BM25F, SCORING_FIELDS, LuceneSearchEngine  # noqa: E402

# ==============================================================================
# CONSTANTS
# ==============================================================================

_DOCUMENTS = [
    {"id": "smallarms-19", "category": "smallarms", "name": "Kalashnikov AK-47", "type": "Assault Rifle",
     "country": "Soviet Union", "year": 1949, "caliber": "7.62x39mm", "weight_kg": 4.3,
     "text": "The AK-47 is a gas-operated assault rifle.", "url": "u1",
     "wiki_title": "AK-47", "wiki_names": ["AK-47", "Avtomat Kalashnikova"], "wiki_number_built": "75,000,000",
     "wiki_designer": "Mikhail Kalashnikov"},
    {"id": "armor-37", "category": "armor", "name": "Leopard 2", "type": "Main Battle Tank (MBT)",
     "country": "Germany", "year": 1979, "weight_kg": 62300.0, "url": "u2",
     "text": "The Leopard 2 is a main battle tank of Germany."},
    {"id": "armor-1", "category": "armor", "name": "M1 Abrams", "type": "Main Battle Tank (MBT)",
     "country": "United States", "year": 1980, "weight_kg": 63086.0, "url": "u3",
     "text": "The M1 Abrams is an American main battle tank with a gas turbine."},
    {"id": "ships-1", "category": "ships", "name": "Type VII U-Boat", "type": "Diesel-Electric Attack Submarine",
     "country": "Germany", "year": 1936, "url": "u4", "text": "A German submarine of World War 2."},
    {"id": "armor-100", "category": "armor", "name": "SdKfz 171 Panzer V / Panther", "type": "Medium Tank",
     "country": "Germany", "year": 1943, "weight_kg": 44800.0, "url": "u5",
     "text": "The Panther was a German medium tank, the battle tank of the war."},
]


# ==============================================================================
# FIXTURES
# ==============================================================================

@pytest.fixture(scope="module")
def engine(tmp_path_factory: pytest.TempPathFactory) -> LuceneSearchEngine:
    index_dir: Path = tmp_path_factory.mktemp("lucene") / "index"
    stats = build_lucene_index(_DOCUMENTS, {"u2": ["Leo 2"]}, index_dir)
    assert stats["documents_count"] == len(_DOCUMENTS)
    search_engine = LuceneSearchEngine(index_dir)
    yield search_engine
    search_engine.close()


def _names(engine: LuceneSearchEngine, query: str, **options: object) -> list[str]:
    return [hit.row["name"] for hit in engine.search(query, **options).hits]


# ==============================================================================
# TESTS
# ==============================================================================

def test_analyzer_matches_compound_tokens_and_calibers(engine: LuceneSearchEngine) -> None:
    assert engine.analyze("AK-47 rifles") == ["ak-47", "ak47", "ak", "47", "rifle"]
    assert engine.analyze("7.62x39mm Škoda") == ["7.62x39mm", "7.62x39", "skoda"]
    assert engine.analyze("AK-47 rifles", main_only=True) == ["ak-47", "rifle"]
    assert _names(engine, "ak47")[0] == "Kalashnikov AK-47"
    assert _names(engine, "7.62x39")[0] == "Kalashnikov AK-47"


def test_boolean_queries(engine: LuceneSearchEngine) -> None:
    assert set(_names(engine, "abrams OR leopard")) == {"M1 Abrams", "Leopard 2"}
    assert _names(engine, "tank AND germany AND battle", scoring=SCORING_FIELDS)[0] in ("Leopard 2", "SdKfz 171 Panzer V / Panther")
    assert "Leopard 2" not in _names(engine, "\"main battle tank\" NOT country:germany")


def test_phrase_and_proximity(engine: LuceneSearchEngine) -> None:
    assert set(_names(engine, "\"main battle tank\"")) == {"Leopard 2", "M1 Abrams"}
    assert _names(engine, "\"gas rifle\"") == []
    assert _names(engine, "\"gas rifle\"~3") == ["Kalashnikov AK-47"]


def test_fuzzy_and_wildcard(engine: LuceneSearchEngine) -> None:
    assert _names(engine, "kalashnikof~1")[0] == "Kalashnikov AK-47"
    assert _names(engine, "panz*") == ["SdKfz 171 Panzer V / Panther"]


def test_numeric_range_and_keyword_filters(engine: LuceneSearchEngine) -> None:
    assert set(_names(engine, "category:armor weight_kg:[60000 TO *]")) == {"Leopard 2", "M1 Abrams"}
    assert _names(engine, "tank year>=1980") == ["M1 Abrams"]
    assert _names(engine, "germany year:1930..1940") == ["Type VII U-Boat"]
    assert _names(engine, "wiki_built>=1000000") == ["Kalashnikov AK-47"]
    assert engine.search("category:armor").total_hits == 3
    assert engine.search("-category:armor").total_hits == 2


def test_field_query_on_wikipedia_attribute(engine: LuceneSearchEngine) -> None:
    assert _names(engine, "wiki_designer:kalashnikov") == ["Kalashnikov AK-47"]
    assert _names(engine, "avtomat")[0] == "Kalashnikov AK-47"
    assert _names(engine, "leo")[0] == "Leopard 2"


def test_bm25f_and_auto_scoring(engine: LuceneSearchEngine) -> None:
    result = engine.search("german submarine")
    assert result.scoring == SCORING_BM25F
    assert result.hits[0].row["name"] == "Type VII U-Boat"
    assert engine.search("german AND submarine").scoring == SCORING_FIELDS
    with pytest.raises(ValueError):
        engine.search("tank AND germany", scoring=SCORING_BM25F)


def test_sort_by_numeric_field(engine: LuceneSearchEngine) -> None:
    assert _names(engine, "category:armor", sort="weight_kg:desc") == ["M1 Abrams", "Leopard 2",
                                                                      "SdKfz 171 Panzer V / Panther"]
    with pytest.raises(ValueError):
        engine.search("tank", sort="name")


def test_wrong_queries_raise_value_error(engine: LuceneSearchEngine) -> None:
    with pytest.raises(ValueError, match="unknown field"):
        engine.search("foo:bar tank")
    with pytest.raises(ValueError, match="syntax"):
        engine.search("tank AND (")
