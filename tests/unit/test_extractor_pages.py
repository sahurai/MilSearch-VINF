# ==============================================================================
# EXTRACTOR TESTS ON REAL PAGES — MilSearch
# ==============================================================================
# PURPOSE:
#     Test the regex extractor on 20 real MilitaryFactory detail pages
#     (7 small arms, 7 armor, 6 ships) against hand-checked expected values.
#
# RESPONSIBILITIES:
#     1. Load the saved pages and the expected values from tests/fixtures.
#     2. Compare the title block, the country normalization and the key numeric fields.
#     3. Compare the list fields (roles) and the counts of the long lists.
#
# DEPENDENCIES:
#     extractor.parse — function under test.
#
# INVARIANTS:
#     Each expected value was checked by hand against two sources: the entry of the object
#     in the A-Z catalog page (name, type, year, flag) and the plain text of the detail page
#     (spec values). The pages include the special cases: towed artillery without engine,
#     a nuclear carrier with unlimited range, a page without body text, an implausible
#     submerged speed on the site, and historical countries (Nazi Germany, Imperial Germany).
# ==============================================================================

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from extractor.parse import extract_document

# ==============================================================================
# CONSTANTS
# ==============================================================================

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
_EXPECTED: dict[str, dict[str, Any]] = json.loads(  # Any: expected values have mixed types.
    (_FIXTURES_DIR / "expected_documents.json").read_text(encoding="utf-8")
)
_COUNT_FIELDS: dict[str, str] = {
    "operators_count": "operators",
    "variants_count": "variants",
    "ships_in_class_count": "ships_in_class",
}


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def test_fixture_set_has_at_least_twenty_pages_of_all_categories() -> None:
    """
    The fixture set has 20 or more pages and all three categories.

    Steps:
        1. Count the expected documents and their categories.

    Returns:
        None.
    """
    # 1. Count the expected documents and their categories
    assert len(_EXPECTED) >= 20
    assert {expected["category"] for expected in _EXPECTED.values()} == {"smallarms", "armor", "ships"}


@pytest.mark.parametrize("doc_id", sorted(_EXPECTED))
def test_extractor_gives_expected_values(doc_id: str) -> None:
    """
    The extractor gives the hand-checked values for one real page.

    Steps:
        1. Extract the document from the saved page.
        2. Compare each expected field; compare list lengths for the count fields.

    Returns:
        None.
    """
    expected = _EXPECTED[doc_id]

    # 1. Extract the document from the saved page
    page = (_FIXTURES_DIR / "pages" / f"{doc_id}.html").read_text(encoding="utf-8")
    document = extract_document(page, expected["category"], f"data/raw/{expected['category']}/{doc_id}.html")
    assert document is not None

    # 2. Compare each expected field; compare list lengths for the count fields
    for field_name, expected_value in expected.items():
        if field_name in _COUNT_FIELDS:
            actual = len(document[_COUNT_FIELDS[field_name]])
        elif field_name == "has_text":
            actual = bool(document["text"])
        else:
            actual = document[field_name]
        assert actual == expected_value, f"{doc_id}: {field_name}"
