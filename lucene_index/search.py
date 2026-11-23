# ==============================================================================
# LUCENE SEARCH — MilSearch
# ==============================================================================
# PURPOSE:
#     Answer a query from the Lucene index. The query uses the Lucene query syntax
#     (Boolean, phrase, proximity, fuzzy, wildcard, range, field, boost, grouping);
#     numeric conditions become point range filters.
#
# RESPONSIBILITIES:
#     1. Open the index; set the similarity (BM25 or classic tf-idf).
#     2. Change a query string into a Lucene query:
#        a) numeric conditions -> IntPoint / DoublePoint range queries (FILTER or MUST_NOT),
#        b) the text -> MultiFieldQueryParser over the default fields with the zone boosts,
#           or -> BM25F (CombinedFieldQuery) over the same fields and weights.
#     3. Run the query; return the ranked hits, the total hit count and the Lucene query.
#     4. Show the results on the command line; an interactive mode keeps one JVM.
#
# DEPENDENCIES:
#     PyLucene — runs in Docker (python -m tools.lucene search ...).
#     lucene_index.analysis, lucene_index.schema — analyzers, similarity, fields.
#     indexer.search — hit type and the result line (same output as the own index).
#
# USAGE:
#     python -m tools.lucene search "tiger OR panther"
#     python -m tools.lucene search "\"main battle tank\" AND country:germany year>=1990"
#     python -m tools.lucene search "kalashnikof~1" --top 5
#     python -m tools.lucene search "category:armor weight_kg:[60000 TO *]" --sort weight_kg:desc
#     python -m tools.lucene search "german submarine" --scoring bm25f
#     python -m indexer.search "tiger OR panther" --engine lucene      # the same from the application
#
# INVARIANTS:
#     The engine does not change the index.
# ==============================================================================

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import lucene
from java.io import StringReader
from java.lang import Float
from java.nio.file import Paths
from java.util import HashMap
from org.apache.lucene.analysis.tokenattributes import CharTermAttribute, OffsetAttribute
from org.apache.lucene.document import DoublePoint, IntPoint
from org.apache.lucene.index import DirectoryReader
from org.apache.lucene.queryparser.classic import MultiFieldQueryParser, QueryParser, QueryParserBase
from org.apache.lucene.sandbox.search import CombinedFieldQuery
from org.apache.lucene.search import (
    BooleanClause,
    BooleanQuery,
    IndexSearcher,
    MatchAllDocsQuery,
    Query,
    Sort,
    SortField,
)
from org.apache.lucene.store import FSDirectory
from org.apache.lucene.util import BytesRef

from indexer.search import MATCH_ALL, MATCH_ANY, SearchHit, format_hit
from lucene_index.analysis import SIMILARITIES, SIMILARITY_BM25, build_analyzer, get_similarity, init_vm
from lucene_index.build import LUCENE_INDEX_DIR
from lucene_index.schema import (
    DEFAULT_FIELD_BOOSTS,
    FIELDS_BY_NAME,
    KIND_INT,
    NUMERIC_FIELDS,
    NumericFilter,
    extract_numeric_filters,
)

# ==============================================================================
# CONSTANTS
# ==============================================================================

logger = logging.getLogger("lucene_index.search")

SCORING_FIELDS = "fields"   # MultiFieldQueryParser: BM25 of each field, sum of the boosted field scores.
SCORING_BM25F = "bm25f"     # CombinedFieldQuery: one BM25 over the weighted sum of the field tf (as the own index).
SCORING_AUTO = "auto"       # bm25f for plain words, fields for a query with the Lucene syntax.
SCORINGS: tuple[str, ...] = (SCORING_AUTO, SCORING_FIELDS, SCORING_BM25F)

_INT_MIN = -(2 ** 31)
_INT_MAX = 2 ** 31 - 1
_FIELD_REFERENCE_RE = re.compile(r"(?<![\w.\\])([A-Za-z_]\w*):")
_QUOTED_RE = re.compile(r'"[^"]*"')
_INNER_SLASH_RE = re.compile(r"(?<=\w)/(?=\w)")   # "f/a-18": not a regular expression query.
_QUERY_SYNTAX_RE = re.compile(r'["():^~*?\[\]{}\\]|\b(?:AND|OR|NOT)\b|&&|\|\||(?:^|\s)[+\-!/]')
_PROMPT = "milsearch> "
_MAX_SHOWN_QUERY_LENGTH = 300   # The full multi-field query has one clause for each field.


# ==============================================================================
# TYPES
# ==============================================================================

@dataclass(frozen=True)
class LuceneResult:
    """
    Result of one Lucene query.

    Responsibilities:
        1. Carry the ranked hits, the total number of matching documents, the Lucene query text
           and the scoring that was used.
    """

    hits: list[SearchHit] = field(default_factory=list)
    total_hits: int = 0
    lucene_query: str = ""
    scoring: str = SCORING_FIELDS


# ==============================================================================
# HELPERS
# ==============================================================================

def _make_point_query(numeric_filter: NumericFilter) -> Query:
    """
    Make the point range query of one numeric filter.

    Steps:
        1. Int field: move an exclusive bound by one; use the int limits for open bounds.
        2. Float field: move an exclusive bound to the next float; use infinity for open bounds.

    Returns:
        IntPoint or DoublePoint range query.
    """
    low, high = numeric_filter.low, numeric_filter.high

    # 1. Int field: move an exclusive bound by one; use the int limits for open bounds
    if NUMERIC_FIELDS[numeric_filter.field_name] == KIND_INT:
        int_low = _INT_MIN if low is None else math.ceil(low) + (0 if numeric_filter.include_low or low != math.ceil(low) else 1)
        int_high = _INT_MAX if high is None else math.floor(high) - (0 if numeric_filter.include_high or high != math.floor(high) else 1)
        return IntPoint.newRangeQuery(numeric_filter.field_name, int(int_low), int(int_high))

    # 2. Float field: move an exclusive bound to the next float; use infinity for open bounds
    float_low = -math.inf if low is None else (low if numeric_filter.include_low else math.nextafter(low, math.inf))
    float_high = math.inf if high is None else (high if numeric_filter.include_high else math.nextafter(high, -math.inf))
    return DoublePoint.newRangeQuery(numeric_filter.field_name, float_low, float_high)


def _is_only_prohibited(query: Query) -> bool:
    """
    Test if a parsed query has only prohibited clauses ("-country:germany").

    Steps:
        1. Test a Boolean query: every clause is MUST_NOT.

    Returns:
        True for a query that alone matches no document.
    """
    # 1. Test a Boolean query: every clause is MUST_NOT
    if not BooleanQuery.instance_(query):
        return False
    clauses = list(BooleanQuery.cast_(query).clauses())
    return bool(clauses) and all(clause.occur() == BooleanClause.Occur.MUST_NOT for clause in clauses)


def _get_total_hits(total_hits: object) -> int:
    """
    Read the hit count of a TotalHits object (a record in Lucene 10: value()).

    Steps:
        1. Call value() when it is a method; otherwise read the field.

    Returns:
        Hit count.
    """
    # 1. Call value() when it is a method; otherwise read the field
    value = total_hits.value
    return int(value() if callable(value) else value)


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

class LuceneSearchEngine:
    """
    Read-only search over the Lucene index.

    Responsibilities:
        1. Keep the index reader, the searcher and the query analyzer.
        2. Change query strings into Lucene queries and run them.

    Invariants:
        The engine does not change the index.
    """

    def __init__(self, index_dir: Path = LUCENE_INDEX_DIR, similarity: str = SIMILARITY_BM25) -> None:
        """
        Open the index and the searcher.

        Steps:
            1. Start the JVM; open the index directory.
            2. Make the searcher with the similarity and the query analyzer.

        Returns:
            None.
        """
        # 1. Start the JVM; open the index directory
        init_vm()
        self.reader = DirectoryReader.open(FSDirectory.open(Paths.get(str(index_dir))))

        # 2. Make the searcher with the similarity and the query analyzer
        self.similarity = similarity
        self.searcher = IndexSearcher(self.reader)
        self.searcher.setSimilarity(get_similarity(similarity))
        self.analyzer = build_analyzer(for_index=False)
        self.stored_fields = self.reader.storedFields()
        boosts = HashMap()
        for name, boost in DEFAULT_FIELD_BOOSTS.items():
            boosts.put(name, Float(boost))
        self._boosts = boosts

    def close(self) -> None:
        """
        Close the index reader.

        Steps:
            1. Close the reader.

        Returns:
            None.
        """
        # 1. Close the reader
        self.reader.close()

    def analyze(self, text: str, main_only: bool = False, field_name: str = "text") -> list[str]:
        """
        Change a text into the terms of a field with the query analyzer.

        With main_only, keep one term for each source token: the first (full) form, as the
        query parser of the own index does ("ak-47", not also "ak47", "ak" and "47").

        Steps:
            1. Read the tokens and their character offsets from the analyzer token stream.
            2. With main_only, skip a token inside the text span of the last kept token.

        Returns:
            Terms in text order.
        """
        # 1. Read the tokens and their character offsets from the analyzer token stream
        stream = self.analyzer.tokenStream(field_name, StringReader(text))
        term = stream.addAttribute(CharTermAttribute.class_)
        offset = stream.addAttribute(OffsetAttribute.class_)
        terms: list[str] = []
        kept_end = -1
        try:
            stream.reset()
            while stream.incrementToken():
                # 2. With main_only, skip a token inside the text span of the last kept token
                if main_only and offset.startOffset() < kept_end:
                    continue
                terms.append(term.toString())
                kept_end = offset.endOffset()
            stream.end()
        finally:
            stream.close()
        return terms

    def _parse_fields(self, text: str, match_mode: str) -> Query:
        """
        Parse the text with the classic multi-field query parser.

        Steps:
            1. Make the parser over the default fields with the zone boosts; set the default operator.
            2. Escape a "/" inside a word; parse the text.

        Returns:
            Lucene query.

        Raises:
            ValueError for a syntax error.
        """
        # 1. Make the parser over the default fields with the zone boosts; set the default operator
        parser = MultiFieldQueryParser(list(DEFAULT_FIELD_BOOSTS), self.analyzer, self._boosts)
        parser.setDefaultOperator(QueryParser.Operator.AND if match_mode == MATCH_ALL else QueryParser.Operator.OR)

        # 2. Escape a "/" inside a word; parse the text
        try:
            return QueryParserBase.parse(parser, _INNER_SLASH_RE.sub(r"\\/", text))
        except lucene.JavaError as error:  # A Java ParseException comes as lucene.JavaError.
            message = str(error.getJavaException().getMessage() or "").split("\n")[0]
            raise ValueError(f"query syntax error: {message[:200]}") from error

    def _parse_bm25f(self, text: str, match_mode: str) -> Query:
        """
        Make a BM25F query: one CombinedFieldQuery for each query term over the weighted default fields.

        Steps:
            1. Reject the Lucene query syntax (BM25F takes plain words).
            2. Analyze the text; add one combined field query for each different term.

        Returns:
            Boolean query of the combined field queries.

        Raises:
            ValueError when the text uses the query syntax or has no terms.
        """
        # 1. Reject the Lucene query syntax (BM25F takes plain words)
        if _QUERY_SYNTAX_RE.search(text):
            raise ValueError("bm25f scoring takes plain words; use --scoring fields for the query syntax")

        # 2. Analyze the text; add one combined field query for each different term
        builder = BooleanQuery.Builder()
        occur = BooleanClause.Occur.MUST if match_mode == MATCH_ALL else BooleanClause.Occur.SHOULD
        terms = list(dict.fromkeys(self.analyze(text, main_only=True)))
        if not terms:
            raise ValueError("the query has no terms")
        for term in terms:
            combined = CombinedFieldQuery.Builder()
            for name, boost in DEFAULT_FIELD_BOOSTS.items():
                combined.addField(name, float(boost))
            combined.addTerm(BytesRef(term))
            builder.add(combined.build(), occur)
        return builder.build()

    def parse(self, query: str, match_mode: str = MATCH_ANY, scoring: str = SCORING_AUTO) -> tuple[Query, str]:
        """
        Change a query string into a Lucene query.

        Steps:
            1. Split the numeric conditions off the query.
            2. Reject references to unknown fields.
            3. Choose the scoring (auto: BM25F for plain words, the multi-field parser for the query syntax);
               parse the text; an empty text matches all documents.
            4. Combine: the text query is required, numeric conditions are filters (or prohibited).

        Returns:
            (Lucene query, used scoring).

        Raises:
            ValueError for an unknown field or a syntax error.
        """
        # 1. Split the numeric conditions off the query
        text, numeric_filters = extract_numeric_filters(query)

        # 2. Reject references to unknown fields
        unknown = sorted({name for name in _FIELD_REFERENCE_RE.findall(_QUOTED_RE.sub(" ", text))
                          if name not in FIELDS_BY_NAME})
        if unknown:
            raise ValueError(f"unknown field(s): {', '.join(unknown)}")

        # 3. Choose the scoring; parse the text; an empty text matches all documents
        if scoring == SCORING_AUTO:
            scoring = SCORING_FIELDS if _QUERY_SYNTAX_RE.search(text) else SCORING_BM25F
        if not text:
            text_query: Query = MatchAllDocsQuery()
        elif scoring == SCORING_BM25F:
            text_query = self._parse_bm25f(text, match_mode)
        else:
            text_query = self._parse_fields(text, match_mode)

        # 4. Combine: the text query is required, numeric conditions are filters (or prohibited)
        builder = BooleanQuery.Builder()
        if _is_only_prohibited(text_query):
            builder.add(MatchAllDocsQuery(), BooleanClause.Occur.MUST)
            for clause in BooleanQuery.cast_(text_query).clauses():
                builder.add(clause)
        else:
            builder.add(text_query, BooleanClause.Occur.MUST)
        for numeric_filter in numeric_filters:
            occur = BooleanClause.Occur.MUST_NOT if numeric_filter.negated else BooleanClause.Occur.FILTER
            builder.add(_make_point_query(numeric_filter), occur)
        return builder.build(), scoring

    def _make_sort(self, sort: str) -> Sort:
        """
        Make the sort of a "field" or "field:asc|desc" option (doc values of a numeric field).

        Steps:
            1. Check the field; use the doc values type of its kind; then sort by score.

        Returns:
            Lucene sort.

        Raises:
            ValueError for a field that is not numeric.
        """
        # 1. Check the field; use the doc values type of its kind; then sort by score
        name, _, direction = sort.partition(":")
        if name not in NUMERIC_FIELDS:
            raise ValueError(f"sort field must be numeric: {', '.join(NUMERIC_FIELDS)}")
        sort_type = SortField.Type.INT if NUMERIC_FIELDS[name] == KIND_INT else SortField.Type.DOUBLE
        return Sort([SortField(name, sort_type, direction == "desc"), SortField.FIELD_SCORE])

    def search(self, query: str, top: int = 10, match_mode: str = MATCH_ANY, scoring: str = SCORING_AUTO,
               sort: str | None = None) -> LuceneResult:
        """
        Find and rank the documents for a query.

        Steps:
            1. Parse the query.
            2. Run it (by score, or by a numeric field) and count all matching documents.
            3. Read the display fields of each hit.

        Returns:
            LuceneResult.

        Raises:
            ValueError for an unknown field, a syntax error or a wrong sort field.
        """
        # 1. Parse the query
        lucene_query, used_scoring = self.parse(query, match_mode, scoring)

        # 2. Run it (by score, or by a numeric field) and count all matching documents
        if sort:
            top_docs = self.searcher.search(lucene_query, top, self._make_sort(sort), True)
        else:
            top_docs = self.searcher.search(lucene_query, top)
        total_hits = self.searcher.count(lucene_query)

        # 3. Read the display fields of each hit
        hits = []
        for rank, score_doc in enumerate(top_docs.scoreDocs, start=1):
            row = json.loads(self.stored_fields.document(score_doc.doc).get("display"))
            score = float(score_doc.score)
            hits.append(SearchHit(rank, 0.0 if math.isnan(score) else score, row))
        return LuceneResult(hits, total_hits, lucene_query.toString(), used_scoring)

    def explain(self, query: str, doc_id: str, match_mode: str = MATCH_ANY, scoring: str = SCORING_AUTO) -> str:
        """
        Explain the score of one document for a query (Lucene Explanation).

        Steps:
            1. Find the Lucene doc number of the document ID.
            2. Return the explanation text of the parsed query for that document.

        Returns:
            Explanation text; an empty string for an unknown ID.
        """
        # 1. Find the Lucene doc number of the document ID
        found = self.searcher.search(self.parse(f"id:{doc_id.lower()}")[0], 1).scoreDocs
        if not found:
            return ""

        # 2. Return the explanation text of the parsed query for that document
        return self.searcher.explain(self.parse(query, match_mode, scoring)[0], found[0].doc).toString()


# ==============================================================================
# ENTRY POINT
# ==============================================================================

def _run_query(engine: LuceneSearchEngine, query: str, arguments: argparse.Namespace) -> None:
    """
    Run one query and show the results.

    Steps:
        1. Search; show a short message for a wrong query.
        2. Show the header, the Lucene query and the result lines; with --explain, the score of the first hit.

    Returns:
        None.
    """
    # 1. Search; show a short message for a wrong query
    try:
        result = engine.search(query, arguments.top, arguments.match, arguments.scoring, arguments.sort)
    except ValueError as error:
        logger.info("error: %s", error)
        return

    # 2. Show the header, the Lucene query and the result lines; with --explain, the score of the first hit
    logger.info("\n=== lucene %s (%s) | query=%r | match=%s | total hits=%d | results shown=%d",
                engine.similarity, result.scoring, query, arguments.match, result.total_hits, len(result.hits))
    shown_query = result.lucene_query
    if len(shown_query) > _MAX_SHOWN_QUERY_LENGTH and not arguments.explain:
        shown_query = shown_query[:_MAX_SHOWN_QUERY_LENGTH] + " …"
    logger.info("    lucene query: %s", shown_query)
    for hit in result.hits:
        logger.info(format_hit(hit))
    if arguments.explain and result.hits:
        logger.info("\n--- explanation of hit 1 (%s):\n%s", result.hits[0].row["name"],
                    engine.explain(query, result.hits[0].row["id"], arguments.match, arguments.scoring))


def main(argv: list[str] | None = None) -> None:
    """
    Run one query, or an interactive session, against the Lucene index.

    Steps:
        1. Read the options and configure the logging (plain output lines).
        2. Open the index; stop with a short message when the index is missing.
        3. Run the query; without a query, read queries from the input until an empty line.

    Returns:
        None.
    """
    # 1. Read the options and configure the logging (plain output lines)
    parser = argparse.ArgumentParser(description="MilSearch – search the Lucene index (Lucene query syntax)")
    parser.add_argument("query", nargs="?", help="Query; without it, an interactive session starts.")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--match", choices=(MATCH_ANY, MATCH_ALL), default=MATCH_ANY,
                        help="Default operator between words: any = OR, all = AND.")
    parser.add_argument("--similarity", choices=SIMILARITIES, default=SIMILARITY_BM25)
    parser.add_argument("--scoring", choices=SCORINGS, default=SCORING_AUTO,
                        help="auto: BM25F for plain words, multi-field parser for the query syntax.")
    parser.add_argument("--sort", help="Sort by a numeric field: year, year:desc, weight_kg:desc, ...")
    parser.add_argument("--explain", action="store_true", help="Show the score explanation of the first hit.")
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # 2. Open the index; stop with a short message when the index is missing
    if not any(LUCENE_INDEX_DIR.glob("segments_*")):
        parser.error(f"no Lucene index in {LUCENE_INDEX_DIR}; run 'python -m tools.lucene build' first")
    engine = LuceneSearchEngine(similarity=arguments.similarity)

    # 3. Run the query; without a query, read queries from the input until an empty line
    try:
        if arguments.query:
            _run_query(engine, arguments.query, arguments)
            return
        logger.info("MilSearch Lucene search. Empty line or Ctrl+D ends the session.")
        while True:
            sys.stdout.write(_PROMPT)
            sys.stdout.flush()
            line = sys.stdin.readline()
            if not line.strip():
                break
            _run_query(engine, line.strip(), arguments)
    finally:
        engine.close()


if __name__ == "__main__":
    main()
