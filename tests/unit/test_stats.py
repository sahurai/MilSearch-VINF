# ==============================================================================
# EXTRACTION COVERAGE STATISTICS TESTS — MilSearch
# ==============================================================================
# PURPOSE:
#     Test the field coverage counts and the DokuWiki table.
#
# RESPONSIBILITIES:
#     1. Make sure that empty values are not counted as filled.
#     2. Make sure that rejected implausible values are counted for each field.
#     3. Make sure that the DokuWiki table has one row for each counted field.
#
# DEPENDENCIES:
#     extractor.stats — functions under test.
# ==============================================================================

from __future__ import annotations

from extractor.stats import build_dokuwiki_table, compute_coverage

# ==============================================================================
# CONSTANTS
# ==============================================================================

_DOCUMENTS = [
    {"id": "a", "name": "A", "caliber": "7.62x39mm", "operators": ["Cuba"], "implausible_fields": []},
    {"id": "b", "name": "B", "caliber": None, "operators": [], "implausible_fields": ["weight_kg"]},
]


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def test_coverage_counts_filled_values_and_rejected_values() -> None:
    """
    None and empty lists are not filled; technical fields are not counted.

    Steps:
        1. Compute the coverage of two sample documents.

    Returns:
        None.
    """
    # 1. Compute the coverage of two sample documents
    coverage = compute_coverage(_DOCUMENTS)
    assert coverage["documents_count"] == 2
    assert coverage["filled_count_by_field"] == {"name": 2, "caliber": 1, "operators": 1}
    assert coverage["implausible_count_by_field"] == {"weight_kg": 1}


def test_dokuwiki_table_has_one_row_for_each_field() -> None:
    """
    The table has a heading, a header row and one row for each field.

    Steps:
        1. Build the table of two sample documents.

    Returns:
        None.
    """
    # 1. Build the table of two sample documents
    table = build_dokuwiki_table("smallarms", compute_coverage(_DOCUMENTS))
    assert table.startswith("==== smallarms (2 dokumentov) ====")
    assert "| ''caliber'' | 1 / 2 | 50 % |  |" in table
    assert len([line for line in table.splitlines() if line.startswith("| ''")]) == 3
