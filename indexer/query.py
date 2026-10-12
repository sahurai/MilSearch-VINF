# ==============================================================================
# QUERY PARSER — MilSearch
# ==============================================================================
# PURPOSE:
#     Change a query string into free-text terms, phrases and field filters,
#     and test a document against the filters.
#
# RESPONSIBILITIES:
#     1. Extract field filters: text match, numeric comparison and numeric range.
#     2. Extract quoted phrases.
#     3. Change the remaining text into index terms with the index tokenizer.
#     4. Test the filters on a document row.
#
# DEPENDENCIES:
#     extractor.countries — canonical country names for country filters.
#     indexer.tokenizer — terms from text.
#
# USAGE:
#     main battle tank max_speed_kmh>60
#     "aircraft carrier" "gulf war" category:ships
#     submarine country:germany year:1939..1945
#     assault rifle caliber:7.62x39
# ==============================================================================

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from extractor.countries import get_canonical_country
from indexer.tokenizer import get_term_groups

# ==============================================================================
# CONSTANTS
# ==============================================================================

# field:"quoted value" | field>=number | field<=number | field>number | field<number | field:value
_FILTER_RE = re.compile(
    r'(?P<field>[a-z_]+)(?P<operator>>=|<=|>|<|:)(?:"(?P<quoted>[^"]*)"|(?P<value>[^\s"]+))',
    re.IGNORECASE,
)
_PHRASE_RE = re.compile(r'"([^"]+)"')
_RANGE_RE = re.compile(r"^(-?\d+(?:\.\d+)?)\.\.(-?\d+(?:\.\d+)?)$")

# A filter on one of these fields also tests the field with the original site value.
_PAIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "country": ("country", "country_original"),
    "operators": ("operators", "operators_original"),
}


# ==============================================================================
# TYPES
# ==============================================================================

@dataclass(frozen=True)
class FieldFilter:
    """
    One field condition of a query.

    Responsibilities:
        1. Carry the field name, the operator (":", ">", ">=", "<", "<=") and the raw value.
    """

    field_name: str
    operator: str
    value: str


@dataclass(frozen=True)
class ParsedQuery:
    """
    Query split into its parts.

    Responsibilities:
        1. Carry the free-text terms, the phrases (as term lists) and the field filters.
    """

    terms: list[str] = field(default_factory=list)
    phrases: list[list[str]] = field(default_factory=list)
    filters: list[FieldFilter] = field(default_factory=list)


Row = dict[str, Any]  # Any: a document row holds str, int, float and list values.


# ==============================================================================
# HELPERS
# ==============================================================================

def _get_main_terms(text: str) -> list[str]:
    """
    Get the main term of each token of a query text.

    Steps:
        1. Take the first term of each term group (the full token, stemmed).

    Returns:
        Main terms in text order.
    """
    # 1. Take the first term of each term group (the full token, stemmed)
    return [group[0] for group in get_term_groups(text) if group]


def _matches_text(row_value: Any, wanted: str) -> bool:  # Any: str, number or list from JSON.
    """
    Test if a row value contains a wanted text (case-insensitive).

    Steps:
        1. Test each item of a list, or the value itself.

    Returns:
        True when the wanted text is a substring of the value or of a list item.
    """
    # 1. Test each item of a list, or the value itself
    items = row_value if isinstance(row_value, list) else [row_value]
    return any(item is not None and wanted in str(item).casefold() for item in items)


def _matches_number(row_value: Any, operator: str, wanted: str) -> bool:  # Any: number or None from JSON.
    """
    Test a numeric condition (comparison or "a..b" range).

    Steps:
        1. Reject a row without a number.
        2. Test the range or the comparison.

    Returns:
        True when the condition is true.
    """
    # 1. Reject a row without a number
    if not isinstance(row_value, (int, float)) or isinstance(row_value, bool):
        return False

    # 2. Test the range or the comparison
    value_range = _RANGE_RE.match(wanted)
    if operator == ":" and value_range:
        return float(value_range.group(1)) <= row_value <= float(value_range.group(2))
    number = float(wanted)
    return {
        ":": row_value == number,
        ">": row_value > number,
        ">=": row_value >= number,
        "<": row_value < number,
        "<=": row_value <= number,
    }[operator]


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def parse_query(query: str) -> ParsedQuery:
    """
    Split a query into terms, phrases and field filters.

    Steps:
        1. Extract the field filters and remove them from the text.
        2. Extract the quoted phrases and remove them from the text.
        3. Change the remaining text into main terms.

    Returns:
        ParsedQuery.
    """
    # 1. Extract the field filters and remove them from the text
    filters = [
        FieldFilter(match["field"].lower(), match["operator"], match["quoted"] if match["quoted"] is not None else match["value"])
        for match in _FILTER_RE.finditer(query)
    ]
    rest = _FILTER_RE.sub(" ", query)

    # 2. Extract the quoted phrases and remove them from the text
    phrases = [terms for terms in (_get_main_terms(text) for text in _PHRASE_RE.findall(rest)) if terms]
    rest = _PHRASE_RE.sub(" ", rest)

    # 3. Change the remaining text into main terms
    return ParsedQuery(terms=_get_main_terms(rest), phrases=phrases, filters=filters)


def matches_filter(row: Row, field_filter: FieldFilter) -> bool:
    """
    Test one field filter on a document row.

    Steps:
        1. Use a numeric test for a comparison operator or a numeric range.
        2. Use a text test for ":"; country names are compared in canonical form too.

    Returns:
        True when the row matches the filter.
    """
    field_names = _PAIRED_FIELDS.get(field_filter.field_name, (field_filter.field_name,))

    # 1. Use a numeric test for a comparison operator or a numeric range
    if field_filter.operator != ":" or _RANGE_RE.match(field_filter.value):
        return any(_matches_number(row.get(name), field_filter.operator, field_filter.value) for name in field_names)
    if isinstance(row.get(field_names[0]), (int, float)) and not isinstance(row.get(field_names[0]), bool):
        return _matches_number(row.get(field_names[0]), ":", field_filter.value)

    # 2. Use a text test for ":"; country names are compared in canonical form too
    wanted = {field_filter.value.casefold()}
    if field_filter.field_name in _PAIRED_FIELDS:
        wanted.add((get_canonical_country(field_filter.value) or "").casefold())
    return any(_matches_text(row.get(name), text) for name in field_names for text in wanted if text)


def matches_filters(row: Row, filters: list[FieldFilter]) -> bool:
    """
    Test all field filters on a document row.

    Steps:
        1. Require every filter to match.

    Returns:
        True when the row matches all filters.
    """
    # 1. Require every filter to match
    return all(matches_filter(row, field_filter) for field_filter in filters)
