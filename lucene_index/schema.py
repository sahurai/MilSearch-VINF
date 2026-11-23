# ==============================================================================
# LUCENE SCHEMA — MilSearch
# ==============================================================================
# PURPOSE:
#     Define the fields of the Lucene document and change a MilSearch document into
#     field values. Split the numeric filters off a query string. This module does not
#     import PyLucene, so that the tests can run without the JVM.
#
# RESPONSIBILITIES:
#     1. List the fields: kind (text, keyword, int, float, stored), query boost and purpose.
#     2. Change one document (with its anchor texts and Wikipedia fields) into field values.
#     3. Read the production count from the Wikipedia attribute "number_built".
#     4. Split numeric conditions (year>=1940, weight_kg:[50000 TO *], year:1939..1945) off a query.
#
# DEPENDENCIES:
#     indexer.build — zone weights and zone texts (the same text as in the own index).
#
# FIELD KINDS:
#     text     TextField: analyzed text, positions for phrase queries, BM25 score.
#     keyword  StringField: one exact token, filters (category:ships).
#     int      IntPoint + NumericDocValuesField + StoredField: range queries and sorting.
#     float    DoublePoint + DoubleDocValuesField + StoredField: range queries and sorting.
#     stored   StoredField: only for the result display, not searchable.
#
# INVARIANTS:
#     The default search fields and their boosts are the zones and weights of the own index.
#     Field names are unique.
# ==============================================================================

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from indexer.build import ZONE_WEIGHTS, get_zone_text

# ==============================================================================
# TYPES
# ==============================================================================

Document = dict[str, Any]  # Any: a document holds str, int, float, list and dict values.

KIND_TEXT = "text"
KIND_KEYWORD = "keyword"
KIND_INT = "int"
KIND_FLOAT = "float"
KIND_STORED = "stored"


@dataclass(frozen=True)
class FieldSpec:
    """
    One field of the Lucene document.

    Responsibilities:
        1. Carry the name, the kind, the boost in the default query (0 = not a default field) and the purpose.
    """

    name: str
    kind: str
    boost: float = 0.0
    purpose: str = ""


@dataclass(frozen=True)
class NumericFilter:
    """
    One numeric condition of a query, for a point range query.

    Responsibilities:
        1. Carry the field, the bounds (None = open) and if each bound is inclusive.
        2. Carry if the condition must not match (a "-" before the clause).
    """

    field_name: str
    low: float | None
    high: float | None
    include_low: bool = True
    include_high: bool = True
    negated: bool = False


# ==============================================================================
# CONSTANTS
# ==============================================================================

# Purpose of each default search field (zone of the own index).
_ZONE_PURPOSES: dict[str, str] = {
    "name": "name of the weapon, vehicle or ship; the strongest signal",
    "type": "type (main battle tank, assault rifle, destroyer)",
    "roles": "roles from the site (frontline asset, anti-tank)",
    "country": "country of origin, canonical and original name",
    "caliber": "caliber (7.62x39mm); compound tokens",
    "anchors": "anchor texts of links between MilitaryFactory detail pages",
    "wiki_names": "Wikipedia title, redirects, anchor texts and Slovak title",
    "manufacturer": "manufacturer",
    "operators": "countries that use the item",
    "site_categories": "categories of the site",
    "summary": "short description of the site",
    "wiki_abstract": "lead section of the English and the Slovak Wikipedia page",
    "wiki_wars": "wars from the Wikipedia infobox",
    "armament": "armament",
    "variants": "variants",
    "ships_in_class": "ships of the class",
    "specs": "specification table",
    "text": "full text of the detail page",
}

# Wikipedia attributes that are searchable only by field (wiki_fate:sunk), not in the default query.
WIKI_ATTRIBUTE_FIELDS: tuple[str, ...] = (
    "wiki_designer", "wiki_unit_cost", "wiki_number_built", "wiki_production_date", "wiki_service",
    "wiki_used_by", "wiki_laid_down", "wiki_launched", "wiki_commissioned", "wiki_decommissioned",
    "wiki_fate", "wiki_status",
)

# Numeric fields of the extractor (data/parsed); all are floats in the documents.
EXTRACTED_NUMBER_FIELDS: tuple[str, ...] = (
    "weight_kg", "length_m", "width_m", "height_m", "length_mm", "barrel_length_mm",
    "max_speed_kmh", "range_km", "engine_hp", "crew_count", "total_production",
    "effective_range_m", "muzzle_velocity_mps", "rate_of_fire_rpm",
    "displacement_tons", "beam_m", "draught_m", "speed_kts", "submerged_speed_kts",
)

FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("id", KIND_KEYWORD, purpose="document ID (armor-37); exact lookup"),
    FieldSpec("category", KIND_KEYWORD, purpose="smallarms, armor or ships; filter"),
    FieldSpec("has_wiki", KIND_KEYWORD, purpose="true when the join found a Wikipedia page; filter"),
    *(FieldSpec(zone, KIND_TEXT, weight, _ZONE_PURPOSES[zone]) for zone, weight in ZONE_WEIGHTS.items()),
    *(FieldSpec(name, KIND_TEXT, purpose="Wikipedia infobox attribute; field query") for name in WIKI_ATTRIBUTE_FIELDS),
    FieldSpec("year", KIND_INT, purpose="year of introduction; range query, sorting"),
    FieldSpec("wiki_built", KIND_INT, purpose="number built from Wikipedia; range query, sorting"),
    FieldSpec("wiki_inlinks_count", KIND_INT, purpose="Wikipedia pages that link to the page; sorting"),
    *(FieldSpec(name, KIND_FLOAT, purpose="extracted number; range query, sorting") for name in EXTRACTED_NUMBER_FIELDS),
    FieldSpec("display", KIND_STORED, purpose="JSON of the fields for the result line"),
)

FIELDS_BY_NAME: dict[str, FieldSpec] = {spec.name: spec for spec in FIELDS}
DEFAULT_FIELD_BOOSTS: dict[str, float] = {spec.name: spec.boost for spec in FIELDS if spec.boost > 0}
KEYWORD_FIELDS: tuple[str, ...] = tuple(spec.name for spec in FIELDS if spec.kind == KIND_KEYWORD)
NUMERIC_FIELDS: dict[str, str] = {spec.name: spec.kind for spec in FIELDS if spec.kind in (KIND_INT, KIND_FLOAT)}

# Document fields that the result line shows (indexer.search._format_hit).
_DISPLAY_KEYS: tuple[str, ...] = (
    "id", "category", "name", "url", "country", "year", "caliber", "effective_range_m", "muzzle_velocity_mps",
    "weight_kg", "max_speed_kmh", "engine_hp", "crew_count", "displacement_tons", "speed_kts", "length_m",
    "wiki_title", "wiki_url", "sk_title", "sk_url",
)

# "325,000", "Approx. 262,000", "20,000+", "9,037 (1939–1945)" -> the first number of the value.
_COUNT_RE = re.compile(r"\d{1,3}(?:[,.]\d{3})+(?!\d)|\d+")
_YEAR_RANGE_RE = re.compile(r"^\s*(1[5-9]\d\d|20\d\d)\s*[-–]")  # "1952-1955": years, not a count.
_MAX_COUNT = 100_000_000

_NUMBER = r"-?\d+(?:\.\d+)?"
_BOUND = rf"(?:{_NUMBER}|\*)"
# [NOT |+|-]field:[low TO high] | field:{low TO high} | field>=x (also field:>=x) | field:a..b | field:x
_NUMERIC_CLAUSE_RE = re.compile(
    rf"(?P<not>\bNOT\s+)?(?<![\w.:])(?P<sign>[+-]?)(?P<field>[A-Za-z_]+)"
    rf"(?:"
    rf":(?P<open>[\[{{])\s*(?P<low>{_BOUND})\s+TO\s+(?P<high>{_BOUND})\s*(?P<close>[\]}}])"
    rf"|:?(?P<operator>>=|<=|>|<)(?P<value>{_NUMBER})"
    rf"|:(?P<range_low>{_NUMBER})\.\.(?P<range_high>{_NUMBER})"
    rf"|:(?P<exact>{_NUMBER})(?![\w.])"
    rf")"
)
_OPERATOR = r"(?:AND|OR|&&|\|\|)"
_LEADING_OPERATOR_RE = re.compile(rf"(^|\()\s*{_OPERATOR}(?:\s+|$)")
_TRAILING_OPERATOR_RE = re.compile(rf"(?:(?<=\s)|^)(?:{_OPERATOR}|NOT)\s*(\)|$)")
_DOUBLE_OPERATOR_RE = re.compile(rf"({_OPERATOR})\s+{_OPERATOR}(?=\s|$)")


# ==============================================================================
# HELPERS
# ==============================================================================

def parse_count(value: str | None) -> int | None:
    """
    Read a production count from the Wikipedia attribute "number_built".

    Steps:
        1. Reject an empty value and a value that starts with a year range ("1952-1955").
        2. Take the first number; remove the thousands separators.

    Returns:
        Count, or None when the value has no plausible number.
    """
    # 1. Reject an empty value and a value that starts with a year range ("1952-1955")
    if not value or _YEAR_RANGE_RE.match(value):
        return None

    # 2. Take the first number; remove the thousands separators
    match = _COUNT_RE.search(value)
    if not match:
        return None
    count = int(re.sub(r"[,.]", "", match.group(0)))
    return count if 0 < count <= _MAX_COUNT else None


def _get_number(value: Any) -> float | None:  # Any: a JSON value of any type.
    """
    Get a finite number from a document value.

    Steps:
        1. Accept int and float (not bool) when the value is finite.

    Returns:
        Number, or None.
    """
    # 1. Accept int and float (not bool) when the value is finite
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def get_field_values(document: Document, anchor_texts: list[str]) -> dict[str, list[Any]]:  # Any: str, int, float.
    """
    Change one document into the values of the Lucene fields.

    Steps:
        1. Keyword fields: ID, category and the join flag (lowercase).
        2. Text fields: the zone texts of the own index and the Wikipedia attributes.
        3. Numeric fields: year, extracted numbers, Wikipedia count and inlinks.
        4. Stored field: JSON of the display fields.

    Returns:
        Map from field name to its values; a field without a value is not in the map.
    """
    values: dict[str, list[Any]] = {}

    # 1. Keyword fields: ID, category and the join flag (lowercase)
    values["id"] = [str(document["id"]).lower()]
    values["category"] = [str(document["category"]).lower()]
    values["has_wiki"] = ["true" if document.get("wiki_title") else "false"]

    # 2. Text fields: the zone texts of the own index and the Wikipedia attributes
    for zone in ZONE_WEIGHTS:
        text = get_zone_text(document, zone, anchor_texts)
        if text:
            values[zone] = [text]
    for name in WIKI_ATTRIBUTE_FIELDS:
        if document.get(name):
            values[name] = [str(document[name])]

    # 3. Numeric fields: year, extracted numbers, Wikipedia count and inlinks
    if isinstance(document.get("year"), int):
        values["year"] = [document["year"]]
    for name in EXTRACTED_NUMBER_FIELDS:
        number = _get_number(document.get(name))
        if number is not None:
            values[name] = [number]
    built = parse_count(document.get("wiki_number_built"))
    if built is not None:
        values["wiki_built"] = [built]
    if document.get("wiki_inlinks_count"):
        values["wiki_inlinks_count"] = [int(document["wiki_inlinks_count"])]

    # 4. Stored field: JSON of the display fields
    display = {key: document[key] for key in _DISPLAY_KEYS if document.get(key) is not None}
    values["display"] = [json.dumps(display, ensure_ascii=False)]
    return values


def extract_numeric_filters(query: str, numeric_fields: dict[str, str] = NUMERIC_FIELDS) -> tuple[str, list[NumericFilter]]:
    """
    Split the numeric conditions off a query string.

    The classic Lucene query parser makes a text range from "year:[1939 TO 1945]". A numeric
    field needs a point range query, so the numeric conditions are taken out here and become
    filter clauses. The old syntax of the own index (year>=1940, year:1939..1945) also works.

    Steps:
        1. Find each numeric clause; keep clauses of other fields in the text.
        2. Change the clause into bounds: [a TO b] / {a TO b}, comparison, a..b, exact value.

    Returns:
        (query text without the numeric clauses, numeric filters).
    """
    filters: list[NumericFilter] = []

    def replace(match: re.Match[str]) -> str:
        # 1. Find each numeric clause; keep clauses of other fields in the text
        field_name = match["field"].lower()
        if field_name not in numeric_fields:
            return match.group(0)
        negated = match["sign"] == "-" or bool(match["not"])

        # 2. Change the clause into bounds
        if match["open"]:
            low = None if match["low"] == "*" else float(match["low"])
            high = None if match["high"] == "*" else float(match["high"])
            filters.append(NumericFilter(field_name, low, high, match["open"] == "[", match["close"] == "]", negated))
        elif match["operator"]:
            number = float(match["value"])
            operator = match["operator"]
            if operator.startswith(">"):
                filters.append(NumericFilter(field_name, number, None, operator == ">=", True, negated))
            else:
                filters.append(NumericFilter(field_name, None, number, True, operator == "<=", negated))
        elif match["range_low"]:
            filters.append(NumericFilter(field_name, float(match["range_low"]), float(match["range_high"]),
                                         negated=negated))
        else:
            number = float(match["exact"])
            filters.append(NumericFilter(field_name, number, number, negated=negated))
        return " "

    rest = _NUMERIC_CLAUSE_RE.sub(replace, query)
    return clean_operators(rest) if filters else " ".join(rest.split()), filters


def clean_operators(text: str) -> str:
    """
    Remove the Boolean operators that lost an operand after a clause was taken out
    ("panzer* AND " -> "panzer*", "( OR tank)" -> "(tank)").

    Steps:
        1. Remove operators at the start, at the end, after "(" and before ")".
        2. Keep only the first of two operators in a row.

    Returns:
        Text with valid operator positions and single spaces.
    """
    text = " ".join(text.split())
    previous = None
    while text != previous:
        previous = text
        # 1. Remove operators at the start, at the end, after "(" and before ")"
        text = _LEADING_OPERATOR_RE.sub(r"\1", text)
        text = _TRAILING_OPERATOR_RE.sub(r"\1", text)

        # 2. Keep only the first of two operators in a row
        text = _DOUBLE_OPERATOR_RE.sub(r"\1", text)
        text = " ".join(text.split())
    return text
