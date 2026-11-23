# ==============================================================================
# LUCENE SCHEMA TESTS — MilSearch
# ==============================================================================
# PURPOSE:
#     Test the fields of the Lucene document and the numeric filters of a query.
#     The schema does not import PyLucene, so these tests run without the JVM.
#
# RESPONSIBILITIES:
#     1. Make sure that the default fields and boosts are the zones of the own index.
#     2. Make sure that the stop word file of the Lucene analyzer is the list of the own tokenizer.
#     3. Test the field values of a document and the production count from Wikipedia.
#     4. Test the split of numeric conditions off a query and the operator cleanup.
# ==============================================================================

from __future__ import annotations

import json

import pytest

from indexer.build import ZONE_WEIGHTS
from indexer.tokenizer import STOP_WORDS
from lucene_index.schema import (
    DEFAULT_FIELD_BOOSTS,
    FIELDS,
    KEYWORD_FIELDS,
    NUMERIC_FIELDS,
    NumericFilter,
    clean_operators,
    extract_numeric_filters,
    get_field_values,
    parse_count,
)

# ==============================================================================
# CONSTANTS
# ==============================================================================

_STOPWORDS_PATH = "lucene_index/resources/stopwords.txt"
_DOCUMENT = {
    "id": "Armor-37", "category": "armor", "name": "Leopard 2", "type": "Main Battle Tank (MBT)",
    "country": "Germany", "country_original": "West Germany", "year": 1979, "weight_kg": 62300.0,
    "crew_count": 4.0, "is_range_unlimited": False, "roles": ["MAIN BATTLE TANK", "FRONTLINE ASSET"],
    "url": "https://www.militaryfactory.com/armor/detail.php?armor_id=37", "text": "The Leopard 2 is a tank.",
    "wiki_title": "Leopard 2", "wiki_names": ["Leopard 2", "Leopard II"], "wiki_number_built": "3,600+",
    "wiki_wars": "War in Afghanistan", "wiki_designer": "Krauss-Maffei", "wiki_inlinks_count": 812,
}


# ==============================================================================
# TESTS
# ==============================================================================

def test_field_names_are_unique() -> None:
    names = [spec.name for spec in FIELDS]
    assert len(names) == len(set(names))


def test_default_fields_are_the_zones_of_the_own_index() -> None:
    assert DEFAULT_FIELD_BOOSTS == ZONE_WEIGHTS
    assert all(spec.purpose for spec in FIELDS)


def test_stop_word_file_is_the_own_stop_word_list() -> None:
    with open(_STOPWORDS_PATH, encoding="utf-8") as file_handle:
        words = {line.strip() for line in file_handle if line.strip() and not line.startswith("#")}
    assert words == set(STOP_WORDS)


@pytest.mark.parametrize("value, expected", [
    ("3,600+", 3600), ("Approx. 262,000", 262000), ("9,037 (1939–1945)", 9037), ("approx. 10.000", 10000),
    ("2 prototypes", 2), ("1952-1955, (commissioned 1954)", None), ("many thousands", None), (None, None),
    ("20,000,000 as of 2020", 20000000),
])
def test_parse_count(value: str | None, expected: int | None) -> None:
    assert parse_count(value) == expected


def test_field_values_of_a_document() -> None:
    values = get_field_values(_DOCUMENT, ["Leo 2"])
    assert values["id"] == ["armor-37"]
    assert values["category"] == ["armor"]
    assert values["has_wiki"] == ["true"]
    assert values["country"] == ["Germany West Germany"]
    assert values["anchors"] == ["Leo 2"]
    assert values["roles"] == ["MAIN BATTLE TANK\nFRONTLINE ASSET"]
    assert values["wiki_names"] == ["Leopard 2\nLeopard II"]
    assert values["wiki_designer"] == ["Krauss-Maffei"]
    assert values["year"] == [1979]
    assert values["weight_kg"] == [62300.0]
    assert values["wiki_built"] == [3600]
    assert values["wiki_inlinks_count"] == [812]
    assert "is_range_unlimited" not in values and "caliber" not in values
    display = json.loads(values["display"][0])
    assert display["name"] == "Leopard 2" and display["wiki_title"] == "Leopard 2" and "text" not in display


def test_field_kinds() -> None:
    assert set(KEYWORD_FIELDS) == {"id", "category", "has_wiki"}
    assert NUMERIC_FIELDS["year"] == "int" and NUMERIC_FIELDS["weight_kg"] == "float"


@pytest.mark.parametrize("query, text, expected", [
    ("year:[1939 TO 1945] tank", "tank", NumericFilter("year", 1939, 1945)),
    ("weight_kg:{50000 TO *]", "", NumericFilter("weight_kg", 50000, None, include_low=False)),
    ("tank year>=1990", "tank", NumericFilter("year", 1990, None)),
    ("tank year:>1990", "tank", NumericFilter("year", 1990, None, include_low=False)),
    ("max_speed_kmh<60 tank", "tank", NumericFilter("max_speed_kmh", None, 60, include_high=False)),
    ("submarine year:1939..1945", "submarine", NumericFilter("year", 1939, 1945)),
    ("tiger -year:1944", "tiger", NumericFilter("year", 1944, 1944, negated=True)),
    ("panzer* AND year:1939..1945", "panzer*", NumericFilter("year", 1939, 1945)),
    ("tank AND NOT weight_kg<100", "tank", NumericFilter("weight_kg", None, 100, include_high=False, negated=True)),
])
def test_extract_numeric_filters(query: str, text: str, expected: NumericFilter) -> None:
    assert extract_numeric_filters(query) == (text, [expected])


@pytest.mark.parametrize("query", ["country:germany submarine", "caliber:7.62 rifle", "\"main battle tank\"", "t-80"])
def test_extract_numeric_filters_keeps_other_clauses(query: str) -> None:
    assert extract_numeric_filters(query) == (query, [])


@pytest.mark.parametrize("text, expected", [
    ("panzer* AND", "panzer*"), ("AND tank", "tank"), ("( OR tank) AND x", "(tank) AND x"),
    ("tank AND AND x", "tank AND x"), ("tank NOT", "tank"), ("tiger OR panther", "tiger OR panther"),
])
def test_clean_operators(text: str, expected: str) -> None:
    assert clean_operators(text) == expected
