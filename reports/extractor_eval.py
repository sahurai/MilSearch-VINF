# ==============================================================================
# EXTRACTOR EVALUATION — MilSearch
# ==============================================================================
# PURPOSE:
#     Measure the accuracy of the detail-page extractor against an independent source:
#     the entry of each object in the A-Z catalog page (name, type, year, country flag).
#
# RESPONSIBILITIES:
#     1. Extract the catalog entries of each category with regular expressions.
#     2. Match each extracted document to its catalog entry by the detail id.
#     3. Compare name, type, year and country; count matches and keep the mismatches.
#     4. Write data/reports/extractor_eval.json and data/reports/extractor_eval.dokuwiki.txt.
#
# DEPENDENCIES:
#     crawler.config — data paths, categories.
#     extractor.countries — canonical country names.
#     reports.corpus_stats — report paths.
#
# USAGE:
#     python -m reports.extractor_eval
#
# INVARIANTS:
#     The catalog and the detail page are two different HTML templates of the site.
#     A match on both is strong evidence that the extractor reads the correct values.
# ==============================================================================

from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path
from typing import Any

from crawler.config import CATEGORIES, PARSED_DIR, RAW_DIR
from extractor.countries import get_canonical_country, get_country_key
from reports.corpus_stats import REPORTS_DIR

# ==============================================================================
# CONSTANTS
# ==============================================================================

logger = logging.getLogger("reports.extractor_eval")

EVAL_JSON_PATH: Path = REPORTS_DIR / "extractor_eval.json"
EVAL_WIKI_PATH: Path = REPORTS_DIR / "extractor_eval.dokuwiki.txt"

_ENTRY_RE = re.compile(r"<!--ENTRY-->(.*?)<!--ENTRY-->", re.S)
_ENTRY_ID_RE = re.compile(r'detail\.php\?(?:smallarms_id|armor_id|ship_id)=([^"]+)"')
_ENTRY_FLAG_RE = re.compile(r"/imgs/flags/spheres/([a-z0-9_\-]+)\.png")
_ENTRY_SPAN_RE = re.compile(r"<span[^>]*>(.*?)</span>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")

# Flag file names that stand for more than one state, or that differ from the country name.
_COUNTRIES_BY_FLAG: dict[str, frozenset[str]] = {
    "czech": frozenset({"Czechoslovakia", "Czech Republic"}),
    "north_ossetia": frozenset({"North Ossetia", "Russia"}),
}
_COMPARED_FIELDS: tuple[str, ...] = ("name", "type", "year", "country")
_MAX_MISMATCH_EXAMPLES = 15  # Enough examples for the wiki; the JSON report has all of them.


# ==============================================================================
# TYPES
# ==============================================================================

CatalogEntry = dict[str, Any]  # Any: str and int values.
Report = dict[str, Any]  # Any: nested counts and lists.


# ==============================================================================
# HELPERS
# ==============================================================================

def _get_plain_text(fragment: str) -> str:
    """
    Remove tags and entities and collapse whitespace.

    Steps:
        1. Replace tags with spaces, decode entities and collapse whitespace.

    Returns:
        Plain text on one line.
    """
    # 1. Replace tags with spaces, decode entities and collapse whitespace
    return _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", fragment))).strip()


def _get_flag_countries(flag: str) -> frozenset[str]:
    """
    Get the canonical countries that a flag file name stands for.

    Steps:
        1. Use the special table, or the canonical name of the flag name with spaces.

    Returns:
        Set of canonical country names.
    """
    # 1. Use the special table, or the canonical name of the flag name with spaces
    if flag in _COUNTRIES_BY_FLAG:
        return _COUNTRIES_BY_FLAG[flag]
    return frozenset({get_canonical_country(re.sub(r"[_\-]", " ", flag)) or ""})


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def extract_catalog_entries(catalog_html: str) -> dict[str, CatalogEntry]:
    """
    Extract the entries of an A-Z catalog page.

    Steps:
        1. Cut the page into entries.
        2. Get the detail id, the flag and the span texts (number, year, name, type) of each entry.

    Returns:
        Map from detail id to {"name", "type", "year", "flag"}.
    """
    entries: dict[str, CatalogEntry] = {}

    # 1. Cut the page into entries
    for block in _ENTRY_RE.findall(catalog_html):
        detail_id = _ENTRY_ID_RE.search(block)
        flag = _ENTRY_FLAG_RE.search(block)
        spans = [_get_plain_text(span) for span in _ENTRY_SPAN_RE.findall(block)]

        # 2. Get the detail id, the flag and the span texts (number, year, name, type) of each entry
        if not detail_id or len(spans) < 4:
            continue
        entries[html.unescape(detail_id.group(1))] = {
            "year": int(spans[1]) if spans[1].isdigit() else None,
            "name": spans[2],
            "type": spans[3],
            "flag": flag.group(1) if flag else None,
        }
    return entries


def compare_document(document: dict[str, Any], entry: CatalogEntry) -> dict[str, bool]:  # Any: JSON document.
    """
    Compare one extracted document with its catalog entry.

    Steps:
        1. Compare name, type and year exactly (after whitespace cleanup).
        2. Compare the country with the countries of the flag.

    Returns:
        Map from compared field to True (match) or False.
    """
    # 1. Compare name, type and year exactly (after whitespace cleanup)
    result = {
        "name": _SPACE_RE.sub(" ", document.get("name") or "") == entry["name"],
        "type": _SPACE_RE.sub(" ", document.get("type") or "") == entry["type"],
        "year": document.get("year") == entry["year"],
    }

    # 2. Compare the country with the countries of the flag (by lookup key: case and punctuation ignored)
    flag_keys = {get_country_key(country) for country in _get_flag_countries(entry["flag"])} if entry["flag"] else set()
    result["country"] = get_country_key(document.get("country") or "") in flag_keys
    return result


def evaluate_category(category: str) -> Report:
    """
    Evaluate the extracted documents of one category against the catalog.

    Steps:
        1. Load the catalog entries and the extracted documents.
        2. Compare each document that has a catalog entry; keep the mismatches.
        3. Count the catalog entries without a document and the documents without an entry.

    Returns:
        Counts of matches for each field, coverage counts and the mismatches.
    """
    # 1. Load the catalog entries and the extracted documents
    catalog_path = RAW_DIR / category / f"{category}-catalog.html"
    entries = extract_catalog_entries(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else {}
    parsed_path = PARSED_DIR / f"{category}.jsonl"
    documents = [json.loads(line) for line in parsed_path.open(encoding="utf-8")] if parsed_path.exists() else []

    # 2. Compare each document that has a catalog entry; keep the mismatches
    match_counts = dict.fromkeys(_COMPARED_FIELDS, 0)
    mismatches: list[dict[str, Any]] = []  # Any: str values and the entry dict.
    compared_ids: set[str] = set()
    for document in documents:
        detail_id = (document.get("url") or "").split("=", 1)[-1]
        if detail_id not in entries:
            continue
        compared_ids.add(detail_id)
        result = compare_document(document, entries[detail_id])
        for field_name, is_match in result.items():
            match_counts[field_name] += int(is_match)
            if not is_match:
                mismatches.append({
                    "id": document["id"], "field": field_name,
                    "extracted": document.get(field_name), "catalog": entries[detail_id],
                })

    # 3. Count the catalog entries without a document and the documents without an entry
    return {
        "catalog_entries_count": len(entries),
        "documents_count": len(documents),
        "compared_count": len(compared_ids),
        "documents_without_entry": len(documents) - len(compared_ids),
        "entries_without_document": len(set(entries) - compared_ids),
        "match_counts": match_counts,
        "mismatches": mismatches,
    }


def build_dokuwiki_report(report: dict[str, Report]) -> str:
    """
    Build the DokuWiki tables of the evaluation.

    Steps:
        1. Write one row for each category with the match rate of each field.
        2. Write a table with mismatch examples.

    Returns:
        DokuWiki text.
    """
    # 1. Write one row for each category with the match rate of each field
    lines = ["^ Kategória ^ Porovnané ^ " + " ^ ".join(_COMPARED_FIELDS) + " ^ Bez dokumentu ^"]
    for category, values in report.items():
        compared = values["compared_count"] or 1
        cells = [f"{values['match_counts'][name]} ({100 * values['match_counts'][name] / compared:.1f} %)"
                 for name in _COMPARED_FIELDS]
        lines.append(f"| {category} | {values['compared_count']} | " + " | ".join(cells)
                     + f" | {values['entries_without_document']} |")

    # 2. Write a table with mismatch examples
    examples = [item for values in report.values() for item in values["mismatches"]][:_MAX_MISMATCH_EXAMPLES]
    if examples:
        lines += ["", "^ Dokument ^ Pole ^ Extraktor ^ Katalóg ^"]
        for item in examples:
            catalog_value = item["catalog"]["flag"] if item["field"] == "country" else item["catalog"][item["field"]]
            lines.append(f"| ''{item['id']}'' | {item['field']} | {item['extracted']} | {catalog_value} |")
    return "\n".join(lines) + "\n"


# ==============================================================================
# ENTRY POINT
# ==============================================================================

def main() -> None:
    """
    Evaluate all categories and write the JSON and DokuWiki reports.

    Steps:
        1. Configure the logging.
        2. Evaluate each category and log the match rates.
        3. Write the reports.

    Returns:
        None.
    """
    # 1. Configure the logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    # 2. Evaluate each category and log the match rates
    report = {category: evaluate_category(category) for category in CATEGORIES}
    for category, values in report.items():
        logger.info("EVALUATED extractor | category=%s compared_count=%d match_counts=%s mismatches_count=%d",
                    category, values["compared_count"], values["match_counts"], len(values["mismatches"]))

    # 3. Write the reports
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    EVAL_JSON_PATH.write_bytes(json.dumps(report, ensure_ascii=False, indent=1).encode("utf-8"))
    EVAL_WIKI_PATH.write_bytes(build_dokuwiki_report(report).encode("utf-8"))
    logger.info("SAVED extractor_eval | json=%s wiki=%s", EVAL_JSON_PATH.name, EVAL_WIKI_PATH.name)


if __name__ == "__main__":
    main()
