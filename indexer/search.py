# ==============================================================================
# SEARCH — MilSearch
# ==============================================================================
# PURPOSE:
#     Answer a query from the inverted index: find the candidate documents,
#     apply phrases and field filters, and rank the result with a selected method.
#
# RESPONSIBILITIES:
#     1. Load the dictionary and the document table into memory.
#     2. Read the postings list of a term from disk (seek by byte offset).
#     3. Find candidates: union of the term postings (any) or their intersection (all).
#     4. Keep only documents that contain each phrase (positional check) and match the filters.
#     5. Rank the candidates with tf-idf (classic idf), tf-idf with smooth idf, or BM25.
#     6. Show the results on the command line.
#
# DEPENDENCIES:
#     indexer.build — index file paths.
#     indexer.query — query parser and filters.
#     indexer.ranking — idf variants and score functions.
#
# USAGE:
#     python -m indexer.search "main battle tank max_speed_kmh>60"
#     python -m indexer.search "barrett m82" --method tfidf --top 5
#     python -m indexer.search "submarine country:germany year:1939..1945" --method all
#     python -m indexer.search "assault rifle" --match all
# ==============================================================================

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from indexer.build import DICTIONARY_PATH, DOCUMENTS_PATH, INDEX_DIR, META_PATH, POSTINGS_PATH
from indexer.query import ParsedQuery, matches_filters, parse_query
from indexer.ranking import (
    METHOD_BM25,
    METHODS,
    get_idf_function,
    intersect_postings,
    score_bm25,
    score_vector_model,
)

# ==============================================================================
# CONSTANTS
# ==============================================================================

logger = logging.getLogger("indexer.search")

MATCH_ANY = "any"
MATCH_ALL = "all"

# Fields that the result line shows for each category.
_DISPLAY_FIELDS: dict[str, tuple[str, ...]] = {
    "smallarms": ("caliber", "effective_range_m", "muzzle_velocity_mps", "weight_kg"),
    "armor": ("max_speed_kmh", "weight_kg", "engine_hp", "crew_count"),
    "ships": ("displacement_tons", "speed_kts", "crew_count", "length_m"),
}


# ==============================================================================
# TYPES
# ==============================================================================

Row = dict[str, Any]  # Any: a document row holds str, int, float and list values.
Posting = tuple[int, float, list[int]]


@dataclass(frozen=True)
class SearchHit:
    """
    One ranked result.

    Responsibilities:
        1. Carry the rank, the score and the document row.
    """

    rank: int
    score: float
    row: Row


# ==============================================================================
# HELPERS
# ==============================================================================

def _parse_postings_line(line: str) -> list[Posting]:
    """
    Parse one postings line "doc:tf:pos,pos;doc:tf:pos".

    Steps:
        1. Split the line into postings and each posting into doc, tf and positions.

    Returns:
        Postings in doc number order.
    """
    # 1. Split the line into postings and each posting into doc, tf and positions
    postings: list[Posting] = []
    for item in line.strip().split(";"):
        doc_number, tf, positions = item.split(":")
        postings.append((int(doc_number), float(tf), [int(position) for position in positions.split(",") if position]))
    return postings


def _has_phrase(position_lists: list[list[int]]) -> bool:
    """
    Test if the terms of a phrase stand next to each other in one document.

    Steps:
        1. For each position of the first term, test that term i is at position + i.

    Returns:
        True when the phrase is in the document.
    """
    # 1. For each position of the first term, test that term i is at position + i
    later_sets = [set(positions) for positions in position_lists[1:]]
    return any(
        all(start + offset in positions for offset, positions in enumerate(later_sets, start=1))
        for start in position_lists[0]
    )


def _format_hit(hit: SearchHit) -> str:
    """
    Make the result line of one hit.

    Steps:
        1. Join the rank, the score, the name, the category, the country, the year and the key fields.

    Returns:
        One result line and the URL line.
    """
    # 1. Join the rank, the score, the name, the category, the country, the year and the key fields
    row = hit.row
    details = " ".join(
        f"{name}={row[name]:g}" if isinstance(row.get(name), float) else f"{name}={row.get(name)}"
        for name in _DISPLAY_FIELDS.get(row["category"], ()) if row.get(name) is not None
    )
    return (
        f"{hit.rank:>3}. {hit.score:7.4f}  {row['name']}  [{row['category']} | {row.get('country')} | "
        f"{row.get('year')}]  {details}\n       {row.get('url')}"
    )


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

class SearchEngine:
    """
    Read-only search over the files of one index.

    Responsibilities:
        1. Keep the dictionary, the document table and the metadata in memory.
        2. Read postings lists from disk on demand and keep them in a cache.
        3. Find, filter and rank documents for a query.

    Invariants:
        The engine does not change the index files.
    """

    def __init__(self, index_dir: Path = INDEX_DIR) -> None:
        """
        Load the dictionary, the document table and the metadata.

        Steps:
            1. Load the dictionary: term -> (df, byte offset, byte length).
            2. Load the document table and the metadata.

        Returns:
            None.
        """
        # 1. Load the dictionary: term -> (df, byte offset, byte length)
        self.postings_path = index_dir / POSTINGS_PATH.name
        self.dictionary: dict[str, tuple[int, int, int]] = {}
        with (index_dir / DICTIONARY_PATH.name).open(encoding="utf-8") as file_handle:
            for line in file_handle:
                term, df, offset, length = line.rstrip("\n").split("\t")
                self.dictionary[term] = (int(df), int(offset), int(length))

        # 2. Load the document table and the metadata
        with (index_dir / DOCUMENTS_PATH.name).open(encoding="utf-8") as file_handle:
            self.rows: list[Row] = [json.loads(line) for line in file_handle if line.strip()]
        self.meta = json.loads((index_dir / META_PATH.name).read_text(encoding="utf-8"))
        self.known_fields = {name for row in self.rows for name in row}
        self.length_by_doc = {doc: row["length"] for doc, row in enumerate(self.rows)}
        self.norm_by_doc = {doc: row["norm"] for doc, row in enumerate(self.rows)}
        self._postings_cache: dict[str, list[Posting]] = {}

    def load_postings(self, term: str) -> list[Posting]:
        """
        Read the postings list of a term from disk.

        Steps:
            1. Return the cached list or an empty list for an unknown term.
            2. Seek to the byte offset of the term and parse its line.

        Returns:
            Postings (doc number, weighted tf, positions) in doc number order.
        """
        # 1. Return the cached list or an empty list for an unknown term
        if term in self._postings_cache:
            return self._postings_cache[term]
        if term not in self.dictionary:
            return []

        # 2. Seek to the byte offset of the term and parse its line
        _, offset, length = self.dictionary[term]
        with self.postings_path.open("rb") as postings_file:
            postings_file.seek(offset)
            postings = _parse_postings_line(postings_file.read(length).decode("utf-8"))
        self._postings_cache[term] = postings
        return postings

    def _find_candidates(self, parsed: ParsedQuery, match_mode: str) -> list[int]:
        """
        Find the candidate documents of a parsed query.

        Steps:
            1. Without terms and phrases, use all documents (a filter-only query).
            2. In "all" mode, intersect the postings of all terms; otherwise, join them.
            3. Keep only documents that contain each phrase.
            4. Keep only documents that match all field filters.

        Returns:
            Candidate doc numbers.
        """
        all_terms = list(dict.fromkeys(parsed.terms + [term for phrase in parsed.phrases for term in phrase]))

        # 1. Without terms and phrases, use all documents (a filter-only query)
        if not all_terms:
            candidates = list(range(len(self.rows)))

        # 2. In "all" mode, intersect the postings of all terms; otherwise, join them
        elif match_mode == MATCH_ALL or not parsed.terms:
            candidates = intersect_postings([[doc for doc, *_ in self.load_postings(term)] for term in all_terms])
        else:
            candidates = sorted({doc for term in all_terms for doc, *_ in self.load_postings(term)})

        # 3. Keep only documents that contain each phrase
        for phrase in parsed.phrases:
            positions_by_term = [{doc: positions for doc, _, positions in self.load_postings(term)} for term in phrase]
            candidates = [
                doc for doc in candidates
                if all(doc in positions for positions in positions_by_term)
                and _has_phrase([positions[doc] for positions in positions_by_term])
            ]

        # 4. Keep only documents that match all field filters
        return [doc for doc in candidates if matches_filters(self.rows[doc], parsed.filters)]

    def search(self, query: str, method: str = METHOD_BM25, top: int = 10, match_mode: str = MATCH_ANY) -> list[SearchHit]:
        """
        Find and rank the documents for a query.

        Steps:
            1. Parse the query and reject filters on unknown fields.
            2. Find the candidate documents.
            3. Score the candidates with the selected method.
            4. Sort by score (then by name) and keep the top results.

        Returns:
            Ranked hits, at most `top`.

        Raises:
            ValueError when a filter uses an unknown field or the method is unknown.
        """
        # 1. Parse the query and reject filters on unknown fields
        parsed = parse_query(query)
        unknown = [item.field_name for item in parsed.filters if item.field_name not in self.known_fields]
        if unknown:
            raise ValueError(f"unknown filter field(s): {', '.join(unknown)}")

        # 2. Find the candidate documents
        candidates = set(self._find_candidates(parsed, match_mode))

        # 3. Score the candidates with the selected method
        query_tf: dict[str, int] = {}
        for term in parsed.terms + [term for phrase in parsed.phrases for term in phrase]:
            query_tf[term] = query_tf.get(term, 0) + 1
        # The full postings give the df of the whole collection; the candidates only limit the result.
        postings_by_term = {term: [(doc, tf) for doc, tf, _ in self.load_postings(term)] for term in query_tf}
        documents_count = self.meta["documents_count"]
        if method == METHOD_BM25:
            scores = score_bm25(query_tf, postings_by_term, self.length_by_doc, self.meta["avg_length"], documents_count)
        else:
            scores = score_vector_model(query_tf, postings_by_term, self.norm_by_doc, documents_count,
                                        get_idf_function(method))
        scores = {doc: score for doc, score in scores.items() if doc in candidates}

        # 4. Sort by score (then by name) and keep the top results
        ordered = sorted(candidates, key=lambda doc: (-scores.get(doc, 0.0), self.rows[doc]["name"]))
        return [SearchHit(rank, scores.get(doc, 0.0), self.rows[doc]) for rank, doc in enumerate(ordered[:top], start=1)]


# ==============================================================================
# ENTRY POINT
# ==============================================================================

def main() -> None:
    """
    Run one query from the command line and show the ranked results.

    Steps:
        1. Read the options and configure the logging (plain output lines).
        2. Load the index.
        3. Run the query with each selected method and show the results.

    Returns:
        None.
    """
    # 1. Read the options and configure the logging (plain output lines)
    parser = argparse.ArgumentParser(description="MilSearch – search the MilitaryFactory index")
    parser.add_argument("query")
    parser.add_argument("--method", choices=(*METHODS, "all"), default=METHOD_BM25)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--match", choices=(MATCH_ANY, MATCH_ALL), default=MATCH_ANY)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # 2. Load the index
    engine = SearchEngine()

    # 3. Run the query with each selected method and show the results
    methods = METHODS if arguments.method == "all" else (arguments.method,)
    for method in methods:
        hits = engine.search(arguments.query, method=method, top=arguments.top, match_mode=arguments.match)
        logger.info("\n=== %s | query=%r | match=%s | results shown=%d", method, arguments.query, arguments.match, len(hits))
        for hit in hits:
            logger.info(_format_hit(hit))


if __name__ == "__main__":
    main()
