# ==============================================================================
# EXTRACTION COVERAGE STATISTICS — MilSearch
# ==============================================================================
# PURPOSE:
#     Measure how many documents have a value in each extracted field.
#     Give a JSON report and a DokuWiki table for the consultation wiki.
#
# RESPONSIBILITIES:
#     1. Load the extracted documents of each category.
#     2. Count the filled values of each field and the rejected implausible values.
#     3. Build a DokuWiki table for each category.
#     4. Write data/parsed/coverage.json and data/parsed/coverage.dokuwiki.txt.
#
# DEPENDENCIES:
#     crawler.config — data paths, categories.
#     crawler.storage — project-relative paths.
#
# USAGE:
#     python -m extractor.parse && python -m extractor.stats
#
# INVARIANTS:
#     A field is filled when its value is not None, not an empty string and not an empty list.
#     Boolean flags and technical fields are not counted.
# ==============================================================================

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

from crawler.config import CATEGORIES, PARSED_DIR
from crawler.storage import get_relative_path

# ==============================================================================
# CONSTANTS
# ==============================================================================

logger = logging.getLogger("extractor.stats")

_SKIPPED_FIELDS: frozenset[str] = frozenset({
    "id", "category", "source_file", "specs", "implausible_fields", "is_range_unlimited",
})
_EMPTY_VALUES: tuple[Any, ...] = (None, "", [], {})  # Any: JSON values have mixed types.
COVERAGE_JSON_PATH: Path = PARSED_DIR / "coverage.json"
COVERAGE_WIKI_PATH: Path = PARSED_DIR / "coverage.dokuwiki.txt"


# ==============================================================================
# TYPES
# ==============================================================================

Document = dict[str, Any]  # Any: a document holds str, int, float, list and dict values.
CategoryCoverage = dict[str, Any]  # Any: counts (int) and per-field maps.


# ==============================================================================
# HELPERS
# ==============================================================================

def _load_documents(path: Path) -> list[Document]:
    """
    Read the extracted documents of one category.

    Steps:
        1. Return no documents when the file does not exist.
        2. Parse each line that is not empty.

    Returns:
        Documents in file order.
    """
    # 1. Return no documents when the file does not exist
    if not path.exists():
        return []

    # 2. Parse each line that is not empty
    with path.open(encoding="utf-8") as file_handle:
        return [json.loads(line) for line in file_handle if line.strip()]


def _get_percent_text(filled_count: int, documents_count: int) -> str:
    """
    Make the percent text of a filled count.

    Steps:
        1. Divide and round to a whole percent.

    Returns:
        Text such as "94 %"; "–" when there are no documents.
    """
    # 1. Divide and round to a whole percent
    if documents_count == 0:
        return "–"
    return f"{round(100 * filled_count / documents_count)} %"


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def compute_coverage(documents: list[Document]) -> CategoryCoverage:
    """
    Count the filled values of each field and the rejected implausible values.

    Steps:
        1. Collect the field names in the order of first appearance.
        2. Count the documents with a filled value for each field.
        3. Count the rejected values for each numeric field.

    Returns:
        {"documents_count", "filled_count_by_field", "implausible_count_by_field"}.
    """
    # 1. Collect the field names in the order of first appearance
    field_names: list[str] = []
    for document in documents:
        for field_name in document:
            if field_name not in _SKIPPED_FIELDS and field_name not in field_names:
                field_names.append(field_name)

    # 2. Count the documents with a filled value for each field
    filled_count_by_field = {
        field_name: sum(1 for document in documents if document.get(field_name) not in _EMPTY_VALUES)
        for field_name in field_names
    }

    # 3. Count the rejected values for each numeric field
    implausible_count_by_field = Counter(
        field_name for document in documents for field_name in document.get("implausible_fields", [])
    )
    return {
        "documents_count": len(documents),
        "filled_count_by_field": filled_count_by_field,
        "implausible_count_by_field": dict(implausible_count_by_field),
    }


def build_dokuwiki_table(category: str, coverage: CategoryCoverage) -> str:
    """
    Build the DokuWiki coverage table of one category.

    Steps:
        1. Write the heading with the number of documents.
        2. Write one row for each field: filled count, percent and rejected values.

    Returns:
        DokuWiki text of the table.
    """
    documents_count = coverage["documents_count"]

    # 1. Write the heading with the number of documents
    rows = [
        f"==== {category} ({documents_count} dokumentov) ====",
        "",
        "^ Pole ^ Vyplnené ^ % ^ Odmietnuté (mimo rozsahu) ^",
    ]

    # 2. Write one row for each field: filled count, percent and rejected values
    for field_name, filled_count in coverage["filled_count_by_field"].items():
        rejected_count = coverage["implausible_count_by_field"].get(field_name, 0)
        rows.append(
            f"| ''{field_name}'' | {filled_count} / {documents_count} "
            f"| {_get_percent_text(filled_count, documents_count)} | {rejected_count or ''} |"
        )
    return "\n".join(rows) + "\n"


# ==============================================================================
# ENTRY POINT
# ==============================================================================

def main() -> None:
    """
    Compute the coverage of all categories and write the JSON and DokuWiki reports.

    Steps:
        1. Configure the logging.
        2. Compute the coverage of each category and log the summary.
        3. Write the JSON report and the DokuWiki tables.

    Returns:
        None.
    """
    # 1. Configure the logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    # 2. Compute the coverage of each category and log the summary
    coverage_by_category: dict[str, CategoryCoverage] = {}
    for category in CATEGORIES:
        coverage = compute_coverage(_load_documents(PARSED_DIR / f"{category}.jsonl"))
        coverage_by_category[category] = coverage
        logger.info(
            "COMPUTED coverage | category=%s documents_count=%d fields_count=%d implausible_total=%d",
            category, coverage["documents_count"], len(coverage["filled_count_by_field"]),
            sum(coverage["implausible_count_by_field"].values()),
        )

    # 3. Write the JSON report and the DokuWiki tables
    COVERAGE_JSON_PATH.write_text(json.dumps(coverage_by_category, ensure_ascii=False, indent=1), encoding="utf-8")
    COVERAGE_WIKI_PATH.write_text(
        "\n".join(build_dokuwiki_table(category, coverage_by_category[category]) for category in CATEGORIES),
        encoding="utf-8",
    )
    logger.info("SAVED coverage | json=%s wiki=%s",
                get_relative_path(COVERAGE_JSON_PATH), get_relative_path(COVERAGE_WIKI_PATH))


if __name__ == "__main__":
    main()
