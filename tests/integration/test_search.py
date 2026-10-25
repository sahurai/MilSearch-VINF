# ==============================================================================
# INDEX AND SEARCH TESTS — MilSearch
# ==============================================================================
# PURPOSE:
#     Build a small index in a temporary directory and test the search on it.
#
# RESPONSIBILITIES:
#     1. Make sure that the index files round-trip (dictionary offsets, postings, positions).
#     2. Make sure that each ranking method puts the best document first.
#     3. Make sure that phrases, "all" mode and filters limit the result.
#     4. Make sure that the idf table of the search evaluation gives rare terms more weight.
#
# DEPENDENCIES:
#     indexer.build — index builder.
#     indexer.search — search engine.
# ==============================================================================

from __future__ import annotations

from pathlib import Path

import pytest

from indexer.build import build_index, save_index
from indexer.ranking import METHODS
from indexer.search import MATCH_ALL, SearchEngine
from reports.search_eval import compute_idf_table

# ==============================================================================
# CONSTANTS
# ==============================================================================

_DOCUMENTS = [
    {"id": "armor-1", "category": "armor", "name": "M1 Abrams", "type": "Main Battle Tank (MBT)",
     "country": "United States", "year": 1980, "max_speed_kmh": 67.0,
     "text": "The M1 Abrams is a main battle tank with a gas turbine."},
    {"id": "armor-58", "category": "armor", "name": "Leopard 1", "type": "Main Battle Tank (MBT)",
     "country": "Germany", "year": 1965, "max_speed_kmh": 65.0,
     "text": "A battle tank. The tank is main equipment of the army."},
    {"id": "ships-1", "category": "ships", "name": "USS Nimitz", "type": "Nuclear-Powered Aircraft Carrier",
     "country": "United States", "year": 1975, "speed_kts": 30.0,
     "text": "The aircraft carrier served in the Gulf War."},
]


# ==============================================================================
# HELPERS
# ==============================================================================

def _build_engine(index_dir: Path) -> SearchEngine:
    """
    Build the sample index in a directory and open a search engine on it.

    Steps:
        1. Build the postings and the rows of the sample documents.
        2. Write the index files and open the engine.

    Returns:
        SearchEngine on the sample index.
    """
    # 1. Build the postings and the rows of the sample documents
    postings, rows = build_index(_DOCUMENTS, {})
    meta = {"documents_count": len(rows), "avg_length": sum(row["length"] for row in rows) / len(rows)}

    # 2. Write the index files and open the engine
    save_index(postings, rows, meta, index_dir)
    return SearchEngine(index_dir)


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

@pytest.mark.parametrize("method", METHODS)
def test_each_method_ranks_the_named_document_first(tmp_path: Path, method: str) -> None:
    """
    For "abrams tank", every method ranks M1 Abrams first.

    Steps:
        1. Search with the method.

    Returns:
        None.
    """
    # 1. Search with the method
    hits = _build_engine(tmp_path).search("abrams tank", method=method)
    assert hits[0].row["name"] == "M1 Abrams"
    assert hits[0].score > 0


def test_idf_table_gives_rare_terms_more_weight(tmp_path: Path) -> None:
    """
    In the idf table, a rare term has a higher idf and a larger share than a frequent term.

    Steps:
        1. Compute the idf table of "abrams tank" on the sample index.

    Returns:
        None.
    """
    # 1. Compute the idf table of "abrams tank" on the sample index
    rows = {row["term"]: row for row in compute_idf_table(_build_engine(tmp_path), "abrams tank")}
    assert rows["abram"]["df"] == 1
    assert rows["tank"]["df"] == 2
    assert rows["abram"]["idf_classic"] > rows["tank"]["idf_classic"] > 0
    assert rows["abram"]["share_classic"] > rows["abram"]["share_smooth"] > 0.5


def test_postings_round_trip_with_positions(tmp_path: Path) -> None:
    """
    The postings read by byte offset are the same as the postings in memory.

    Steps:
        1. Build the index and read the postings of one term from disk.

    Returns:
        None.
    """
    # 1. Build the index and read the postings of one term from disk
    engine = _build_engine(tmp_path)
    postings, _ = build_index(_DOCUMENTS, {})
    assert engine.load_postings("tank") == [(doc, tf, positions) for doc, tf, positions in postings["tank"]]
    assert engine.load_postings("no-such-term") == []


def test_phrase_all_mode_and_filters_limit_the_result(tmp_path: Path) -> None:
    """
    A phrase needs adjacent terms; "all" needs every term; filters remove rows.

    Steps:
        1. Search a phrase that only one text has in this order.
        2. Search in "all" mode.
        3. Search with a numeric filter and with a filter-only query.

    Returns:
        None.
    """
    engine = _build_engine(tmp_path)

    # 1. Search a phrase that only one text has in this order
    assert [hit.row["name"] for hit in engine.search('"gas turbine"')] == ["M1 Abrams"]

    # 2. Search in "all" mode
    assert [hit.row["name"] for hit in engine.search("carrier gulf", match_mode=MATCH_ALL)] == ["USS Nimitz"]

    # 3. Search with a numeric filter and with a filter-only query
    assert [hit.row["name"] for hit in engine.search("tank max_speed_kmh>66")] == ["M1 Abrams"]
    assert [hit.row["name"] for hit in engine.search("category:armor country:germany")] == ["Leopard 1"]


def test_unknown_filter_field_is_an_error(tmp_path: Path) -> None:
    """
    A filter on a field that no document has raises ValueError.

    Steps:
        1. Search with an unknown field.

    Returns:
        None.
    """
    # 1. Search with an unknown field
    with pytest.raises(ValueError):
        _build_engine(tmp_path).search("tank colour:green")
