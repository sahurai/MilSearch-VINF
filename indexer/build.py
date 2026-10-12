# ==============================================================================
# INDEX BUILDER — MilSearch
# ==============================================================================
# PURPOSE:
#     Build a positional inverted index from the extracted documents. Keep the
#     dictionary small enough for memory and the postings on disk.
#
# RESPONSIBILITIES:
#     1. Load the extracted documents and the anchor texts of the links between detail pages.
#     2. Make the weighted fields (zones) of each document and change them into terms.
#     3. Collect a postings list for each term in memory (single pass, SPIMI style).
#     4. Write the dictionary, the postings, the document table and the index metadata.
#
# DEPENDENCIES:
#     crawler.config — data paths, categories.
#     indexer.tokenizer — terms from text.
#
# USAGE:
#     python -m indexer.build
#
# FORMULAS:
#     tf(t, d)    = sum over zones z of weight(z) * count(t in z)
#     length(d)   = sum over t of tf(t, d)
#     norm(d)     = sqrt(sum over t of (1 + log10 tf(t, d)) ** 2)
#
# INVARIANTS:
#     Doc numbers in a postings list are in ascending order.
#     The positions of two zones have a gap, so that a phrase cannot cross two zones.
#     The index files are plain text. The index does not use a database.
# ==============================================================================

from __future__ import annotations

import json
import logging
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from crawler.config import ANCHORS_PATH, CATEGORIES, DATA_DIR, PARSED_DIR
from indexer.tokenizer import get_term_groups

# ==============================================================================
# CONSTANTS
# ==============================================================================

logger = logging.getLogger("indexer.build")

INDEX_DIR: Path = DATA_DIR / "index"
DICTIONARY_PATH: Path = INDEX_DIR / "dictionary.tsv"
POSTINGS_PATH: Path = INDEX_DIR / "postings.txt"
DOCUMENTS_PATH: Path = INDEX_DIR / "documents.jsonl"
META_PATH: Path = INDEX_DIR / "meta.json"

# Zone weights. The name is the strongest signal; type, roles, country and caliber describe
# the object; the long article text has the base weight.
ZONE_WEIGHTS: dict[str, float] = {
    "name": 3.0,
    "type": 2.0,
    "roles": 2.0,
    "country": 2.0,
    "caliber": 2.0,
    "anchors": 2.0,
    "manufacturer": 1.0,
    "operators": 1.0,
    "site_categories": 1.0,
    "summary": 1.0,
    "armament": 1.0,
    "variants": 1.0,
    "ships_in_class": 1.0,
    "specs": 1.0,
    "text": 1.0,
}
_ZONE_POSITION_GAP = 50       # Larger than any phrase query, so no phrase crosses two zones.
_TEXT_FIELDS_NOT_STORED: frozenset[str] = frozenset({"text", "specs"})  # Large; not needed for display.
_DETAIL_SOURCE_MARKER = "/detail.php?"  # Anchor texts from catalogs are template text ("71 1982 ...").


# ==============================================================================
# TYPES
# ==============================================================================

Document = dict[str, Any]  # Any: a document holds str, int, float, list and dict values.
# term -> list of (doc number, weighted tf, positions)
Postings = dict[str, list[tuple[int, float, list[int]]]]


# ==============================================================================
# HELPERS
# ==============================================================================

def _load_documents(categories: tuple[str, ...]) -> list[Document]:
    """
    Read the extracted documents of all categories.

    Steps:
        1. Read each category file line by line in category order.

    Returns:
        Documents; the list index is the doc number.
    """
    documents: list[Document] = []

    # 1. Read each category file line by line in category order
    for category in categories:
        path = PARSED_DIR / f"{category}.jsonl"
        if not path.exists():
            logger.warning("SKIPPED category | reason=no_parsed_file path=%s", path)
            continue
        with path.open(encoding="utf-8") as file_handle:
            documents.extend(json.loads(line) for line in file_handle if line.strip())
    return documents


def _load_anchor_texts(anchors_path: Path) -> dict[str, list[str]]:
    """
    Collect the anchor texts that other detail pages use for each target URL.

    Steps:
        1. Read anchors.tsv and keep only links from detail pages.
        2. Keep each different anchor text one time for each target.

    Returns:
        Map from target URL to its anchor texts.
    """
    texts_by_url: dict[str, list[str]] = defaultdict(list)
    if not anchors_path.exists():
        return {}

    # 1. Read anchors.tsv and keep only links from detail pages
    with anchors_path.open(encoding="utf-8") as file_handle:
        for line in file_handle:
            cells = line.rstrip("\n").split("\t")
            if len(cells) < 3 or _DETAIL_SOURCE_MARKER not in cells[0] or cells[0] == cells[1]:
                continue

            # 2. Keep each different anchor text one time for each target
            target_url, anchor_text = cells[1], cells[2].strip()
            if anchor_text and anchor_text not in texts_by_url[target_url]:
                texts_by_url[target_url].append(anchor_text)
    return dict(texts_by_url)


def _get_zone_text(document: Document, zone: str, anchor_texts: list[str]) -> str:
    """
    Get the text of one zone of a document.

    Steps:
        1. Use the anchor texts for the anchor zone.
        2. Join list values and spec values; keep strings as they are.

    Returns:
        Zone text; an empty string when the document has no value.
    """
    # 1. Use the anchor texts for the anchor zone
    if zone == "anchors":
        return "\n".join(anchor_texts)
    if zone == "country":
        return " ".join(value for value in (document.get("country"), document.get("country_original")) if value)

    # 2. Join list values and spec values; keep strings as they are
    value = document.get(zone)
    if isinstance(value, dict):
        return "\n".join(f"{label} {text}" for label, text in value.items())
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return str(value) if value else ""


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def index_document(document: Document, anchor_texts: list[str]) -> dict[str, tuple[float, list[int]]]:
    """
    Change one document into weighted term frequencies and term positions.

    Steps:
        1. Go through the zones in a fixed order.
        2. Add the zone weight to the tf of each term and keep the term position.
        3. Leave a position gap after each zone.

    Returns:
        Map from term to (weighted tf, positions).
    """
    tf_by_term: dict[str, float] = defaultdict(float)
    positions_by_term: dict[str, list[int]] = defaultdict(list)
    position = 0

    # 1. Go through the zones in a fixed order
    for zone, weight in ZONE_WEIGHTS.items():
        groups = get_term_groups(_get_zone_text(document, zone, anchor_texts))

        # 2. Add the zone weight to the tf of each term and keep the term position
        for group in groups:
            for term in group:
                tf_by_term[term] += weight
                positions_by_term[term].append(position)
            position += 1

        # 3. Leave a position gap after each zone
        position += _ZONE_POSITION_GAP
    return {term: (tf_by_term[term], positions_by_term[term]) for term in tf_by_term}


def build_index(documents: list[Document], anchor_texts_by_url: dict[str, list[str]]) -> tuple[Postings, list[Document]]:
    """
    Build the postings and the document table in memory.

    Steps:
        1. Index each document and add its terms to the postings lists.
        2. Compute the length and the cosine norm of each document.
        3. Keep the document fields for filters and display.

    Returns:
        (postings by term, document table rows).
    """
    postings: Postings = defaultdict(list)
    rows: list[Document] = []
    for doc_number, document in enumerate(documents):
        # 1. Index each document and add its terms to the postings lists
        terms = index_document(document, anchor_texts_by_url.get(document.get("url") or "", []))
        for term, (tf, positions) in terms.items():
            postings[term].append((doc_number, tf, positions))

        # 2. Compute the length and the cosine norm of each document
        length = sum(tf for tf, _ in terms.values())
        norm = math.sqrt(sum((1 + math.log10(tf)) ** 2 for tf, _ in terms.values())) or 1.0

        # 3. Keep the document fields for filters and display
        row = {key: value for key, value in document.items() if key not in _TEXT_FIELDS_NOT_STORED}
        row.update(doc_number=doc_number, length=length, norm=norm, terms_count=len(terms))
        rows.append(row)
    return dict(postings), rows


def save_index(postings: Postings, rows: list[Document], meta: dict[str, Any], index_dir: Path) -> None:
    """
    Write the index files.

    Steps:
        1. Write the postings file and remember the byte offset and length of each term line.
        2. Write the dictionary (term, df, offset, length) sorted by term.
        3. Write the document table and the metadata.

    Returns:
        None.
    """
    index_dir.mkdir(parents=True, exist_ok=True)
    dictionary_rows: list[str] = []

    # 1. Write the postings file and remember the byte offset and length of each term line
    with (index_dir / POSTINGS_PATH.name).open("wb") as postings_file:
        for term in sorted(postings):
            line = ";".join(
                f"{doc_number}:{tf:g}:{','.join(map(str, positions))}"
                for doc_number, tf, positions in postings[term]
            ).encode("utf-8") + b"\n"
            offset = postings_file.tell()
            postings_file.write(line)

            # 2. Write the dictionary (term, df, offset, length) sorted by term
            dictionary_rows.append(f"{term}\t{len(postings[term])}\t{offset}\t{len(line)}\n")
    (index_dir / DICTIONARY_PATH.name).write_bytes("".join(dictionary_rows).encode("utf-8"))

    # 3. Write the document table and the metadata
    with (index_dir / DOCUMENTS_PATH.name).open("wb") as documents_file:
        for row in rows:
            documents_file.write((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
    (index_dir / META_PATH.name).write_bytes(json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8"))


# ==============================================================================
# ENTRY POINT
# ==============================================================================

def main() -> None:
    """
    Build the index from data/parsed and write it to data/index.

    Steps:
        1. Load the documents and the anchor texts.
        2. Build the postings and the document table.
        3. Compute the metadata and write the index files.

    Returns:
        None.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    started_at = time.monotonic()

    # 1. Load the documents and the anchor texts
    documents = _load_documents(CATEGORIES)
    anchor_texts_by_url = _load_anchor_texts(ANCHORS_PATH)
    logger.info("STARTED index | documents_count=%d anchor_targets_count=%d",
                len(documents), len(anchor_texts_by_url))

    # 2. Build the postings and the document table
    postings, rows = build_index(documents, anchor_texts_by_url)

    # 3. Compute the metadata and write the index files
    postings_count = sum(len(postings_list) for postings_list in postings.values())
    meta = {
        "documents_count": len(rows),
        "terms_count": len(postings),
        "postings_count": postings_count,
        "positions_count": sum(len(positions) for postings_list in postings.values() for *_, positions in postings_list),
        "avg_length": sum(row["length"] for row in rows) / len(rows) if rows else 0.0,
        "zone_weights": ZONE_WEIGHTS,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    save_index(postings, rows, meta, INDEX_DIR)
    logger.info("SAVED index | documents_count=%d terms_count=%d postings_count=%d elapsed_seconds=%.1f",
                meta["documents_count"], meta["terms_count"], postings_count, time.monotonic() - started_at)


if __name__ == "__main__":
    main()
