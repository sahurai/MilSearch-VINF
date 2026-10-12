# ==============================================================================
# QUERY PARSER TESTS — MilSearch
# ==============================================================================
# PURPOSE:
#     Test the query syntax: free text, phrases and field filters.
#
# RESPONSIBILITIES:
#     1. Make sure that filters, phrases and free terms are separated.
#     2. Make sure that text, numeric and range filters match the correct rows.
#     3. Make sure that a country filter also matches historical names.
#
# DEPENDENCIES:
#     indexer.query — functions under test.
# ==============================================================================

from __future__ import annotations

from indexer.query import FieldFilter, matches_filters, parse_query

# ==============================================================================
# CONSTANTS
# ==============================================================================

_TYPE_VII = {
    "name": "Type VII U-Boat", "country": "Germany", "country_original": "Nazi Germany",
    "year": 1936, "speed_kts": 18.0, "roles": ["SUBMERGED ATTACK"],
}


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def test_separates_filters_phrases_and_terms() -> None:
    """
    A query with a phrase, two filters and free text is split into its parts.

    Steps:
        1. Parse a mixed query.

    Returns:
        None.
    """
    # 1. Parse a mixed query
    parsed = parse_query('"main battle tanks" German max_speed_kmh>60 country:"Nazi Germany"')
    assert parsed.phrases == [["main", "battle", "tank"]]
    assert parsed.terms == ["german"]
    assert parsed.filters == [
        FieldFilter("max_speed_kmh", ">", "60"),
        FieldFilter("country", ":", "Nazi Germany"),
    ]


def test_numeric_and_range_filters() -> None:
    """
    Comparison and range filters test the numeric fields.

    Steps:
        1. Test true and false numeric conditions.

    Returns:
        None.
    """
    # 1. Test true and false numeric conditions
    assert matches_filters(_TYPE_VII, parse_query("year:1930..1945").filters)
    assert matches_filters(_TYPE_VII, parse_query("speed_kts>=18 year:1936").filters)
    assert not matches_filters(_TYPE_VII, parse_query("speed_kts>20").filters)
    assert not matches_filters({"speed_kts": None}, parse_query("speed_kts<100").filters)


def test_text_filters_and_historical_country_names() -> None:
    """
    Text filters are case-insensitive substrings; "Imperial Germany" matches the canonical "Germany".

    Steps:
        1. Test text filters on a string field, a list field and the country field.

    Returns:
        None.
    """
    # 1. Test text filters on a string field, a list field and the country field
    assert matches_filters(_TYPE_VII, parse_query("name:u-boat roles:submerged").filters)
    assert matches_filters(_TYPE_VII, parse_query('country:"Imperial Germany"').filters)
    assert matches_filters(_TYPE_VII, parse_query("country:nazi").filters)
    assert not matches_filters(_TYPE_VII, parse_query("country:japan").filters)
