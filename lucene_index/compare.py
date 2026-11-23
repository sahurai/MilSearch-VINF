# ==============================================================================
# INDEX COMPARISON — MilSearch
# ==============================================================================
# PURPOSE:
#     Compare the own index (indexer/, data/index) and the Lucene index (lucene_index/,
#     data/lucene/index) on the same documents and the same queries.
#
# RESPONSIBILITIES:
#     1. Compare the indexes: documents, terms, size, build time.
#     2. Rank the 10 evaluation queries of reports.search_eval with each variant and compute
#        P@10, R@100, AP and nDCG@10; count the common documents in the top 10.
#     3. Run one example of each query type (Boolean, phrase, proximity, fuzzy, wildcard,
#        regexp, range, field, boost, grouping) on both indexes.
#     4. Measure the mean query time of both indexes (warm, in one process).
#     5. Write data/reports/lucene_compare.json and data/reports/lucene_compare.dokuwiki.txt.
#
# DEPENDENCIES:
#     PyLucene — runs in Docker (python -m tools.lucene compare).
#     indexer.build, indexer.search — own index (built with --wiki: the same data).
#     reports.search_eval — evaluation queries, relevance rules and metrics.
#
# VARIANTS:
#     own_bm25        own index, BM25 over the zone-weighted tf (k1 = 1.2, b = 0.75)
#     own_tfidf       own index, tf-idf with the classic idf
#     lucene_bm25     Lucene, MultiFieldQueryParser: BM25 of each field x boost, summed
#     lucene_bm25f    Lucene, CombinedFieldQuery: BM25F over the weighted fields (the model of own_bm25)
#     lucene_classic  Lucene, ClassicSimilarity (tf-idf) with the same multi-field query
#
# INVARIANTS:
#     The relevance of a document comes from the structured fields (reports.search_eval),
#     not from either ranking.
# ==============================================================================

from __future__ import annotations

import json
import logging
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from indexer.build import INDEX_DIR, build_index, load_index_documents, save_index
from indexer.ranking import METHOD_BM25, METHOD_TFIDF
from indexer.search import MATCH_ALL, MATCH_ANY, SearchEngine
from lucene_index.analysis import SIMILARITY_BM25, SIMILARITY_CLASSIC
from lucene_index.build import LUCENE_INDEX_DIR, LUCENE_META_PATH
from lucene_index.search import SCORING_BM25F, SCORING_FIELDS, LuceneSearchEngine
from reports.corpus_stats import REPORTS_DIR
from reports.search_eval import EVALUATION_QUERIES, compute_metrics

# ==============================================================================
# CONSTANTS
# ==============================================================================

logger = logging.getLogger("lucene_index.compare")

COMPARE_JSON_PATH: Path = REPORTS_DIR / "lucene_compare.json"
COMPARE_WIKI_PATH: Path = REPORTS_DIR / "lucene_compare.dokuwiki.txt"

_RANKING_DEPTH = 1000      # As in reports.search_eval: deep enough for AP.
_TOP_DEPTH = 10
_TIMING_ROUNDS = 20        # Runs of each evaluation query for the mean query time.
_ALL_HITS = 100_000
_METRICS: tuple[str, ...] = ("ap", "p_at_10", "r_at_100", "ndcg_at_10")
_LINE_BREAK = "\\\\ "      # DokuWiki line break inside a table cell.


# ==============================================================================
# TYPES
# ==============================================================================

Ranker = Callable[[str, int], list[str]]   # (query, depth) -> ranked document IDs


@dataclass(frozen=True)
class QueryTypeExample:
    """
    One example of a query type.

    Responsibilities:
        1. Carry the type, the Lucene query, the query for the own index (None = no equivalent),
           the match mode of the own query and a note.
    """

    query_type: str
    lucene_query: str
    own_query: str | None
    own_match: str = MATCH_ANY
    note: str = ""


QUERY_TYPE_EXAMPLES: tuple[QueryTypeExample, ...] = (
    QueryTypeExample("Term", "abrams", "abrams"),
    QueryTypeExample("Boolean AND", "tiger AND tank", "tiger tank", MATCH_ALL, "own: --match all for the whole query"),
    QueryTypeExample("Boolean OR", "tiger OR panther", "tiger OR panther", note="own: \"or\" is a stop word, any-match"),
    QueryTypeExample("Boolean NOT", "submarine NOT country:germany", "submarine NOT country:germany",
                     note="own: \"not\" is a stop word and country:germany is a required filter"),
    QueryTypeExample("Phrase", "\"main battle tank\"", "\"main battle tank\""),
    QueryTypeExample("Proximity", "\"gas operated\"~3", "\"gas operated\"", note="own: exact phrase only"),
    QueryTypeExample("Fuzzy", "kalashnikof~1", "kalashnikof", note="own: no fuzzy search"),
    QueryTypeExample("Wildcard", "panz*", "panz*", note="own: no wildcard search"),
    QueryTypeExample("Regexp", "name:/t-[0-9]{2}/", None, note="tokens t-34, t-55, t-72, ..."),
    QueryTypeExample("Range (number)", "category:armor weight_kg:[60000 TO *]", "category:armor weight_kg>=60000",
                     note="own: filter on the document table"),
    QueryTypeExample("Range + phrase", "\"main battle tank\" year>=1990", "\"main battle tank\" year>=1990"),
    QueryTypeExample("Range (Wikipedia)", "rifle wiki_built>=1000000", None, note="number built from the join"),
    QueryTypeExample("Field", "wiki_designer:kalashnikov", "wiki_designer:kalashnikov",
                     note="own: filter only, all scores 0"),
    QueryTypeExample("Boost", "panther^3 tank", "panther tank", note="own: no boost"),
    QueryTypeExample("Grouping", "(tiger OR panther) AND category:armor -country:germany", None),
)


# ==============================================================================
# HELPERS
# ==============================================================================

def _cell(text: object) -> str:
    """
    Make a DokuWiki table cell text: no "|" or "^" that would start a new cell.

    Steps:
        1. Replace "|" and "^" in the text.

    Returns:
        Safe cell text.
    """
    # 1. Replace "|" and "^" in the text
    return str(text).replace("|", "/").replace("^", "ˆ")


def _code(text: str) -> str:
    """
    Make inline code of a query for DokuWiki (no formatting inside).

    Steps:
        1. Wrap the text in '' and %% %%.

    Returns:
        DokuWiki inline code.
    """
    # 1. Wrap the text in '' and %% %%
    return f"''%%{text}%%''"


def _measure_own_build() -> dict[str, Any]:  # Any: counts and floats.
    """
    Build the own index again into a temporary directory and measure it.

    Steps:
        1. Load the documents with the Wikipedia data; build and save the index; measure the time.
        2. Count the terms and the size of the files.

    Returns:
        Statistics of the own index.
    """
    # 1. Load the documents with the Wikipedia data; build and save the index; measure the time
    started_at = time.monotonic()
    documents, anchor_texts_by_url, _ = load_index_documents(True)
    postings, rows = build_index(documents, anchor_texts_by_url)
    with tempfile.TemporaryDirectory() as directory:
        save_index(postings, rows, {}, Path(directory))
        build_seconds = time.monotonic() - started_at

        # 2. Count the terms and the size of the files
        size = sum(path.stat().st_size for path in Path(directory).iterdir())
    return {"documents_count": len(rows), "terms_count": len(postings), "index_bytes": size,
            "build_seconds": round(build_seconds, 2)}


def _get_own_ids(engine: SearchEngine, query: str, depth: int, method: str, match_mode: str = MATCH_ANY) -> list[str]:
    """
    Rank a query with the own index.

    Steps:
        1. Search and keep the document IDs.

    Returns:
        Ranked document IDs.
    """
    # 1. Search and keep the document IDs
    return [hit.row["id"] for hit in engine.search(query, method=method, top=depth, match_mode=match_mode)]


def _get_lucene_ids(engine: LuceneSearchEngine, query: str, depth: int, scoring: str) -> list[str]:
    """
    Rank a query with the Lucene index.

    Steps:
        1. Search and keep the document IDs.

    Returns:
        Ranked document IDs.
    """
    # 1. Search and keep the document IDs
    return [hit.row["id"] for hit in engine.search(query, top=depth, scoring=scoring).hits]


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def evaluate_variants(variants: dict[str, Ranker], own: SearchEngine) -> dict[str, Any]:  # Any: nested results.
    """
    Rank the evaluation queries with each variant and compute the metrics.

    Steps:
        1. Find the relevant documents of each query from the structured fields.
        2. Rank the query with each variant; compute the metrics; keep the top 5 names and the top 10 IDs.
        3. Count the common documents of own_bm25 and the Lucene variants in the top 10.
        4. Compute the mean of each metric for each variant.

    Returns:
        Per-query results and mean metrics by variant.
    """
    names_by_id = {row["id"]: row["name"] for row in own.rows}
    queries: list[dict[str, Any]] = []  # Any: labels, counts, metrics and names.
    for query in EVALUATION_QUERIES:
        # 1. Find the relevant documents of each query from the structured fields
        relevant_ids = {row["id"] for row in own.rows if query.is_relevant(row)}
        result: dict[str, Any] = {"label": query.label, "text": query.text, "relevant_count": len(relevant_ids),
                                  "variants": {}}

        # 2. Rank the query with each variant; compute the metrics; keep the top 5 names and the top 10 IDs
        top_ids: dict[str, list[str]] = {}
        for name, ranker in variants.items():
            ranked = ranker(query.text, _RANKING_DEPTH)
            top_ids[name] = ranked[:_TOP_DEPTH]
            result["variants"][name] = {
                **compute_metrics(ranked, relevant_ids), "hits_count": len(ranked),
                "top5": [f"{names_by_id[doc]}{' ✓' if doc in relevant_ids else ''}" for doc in ranked[:5]],
            }

        # 3. Count the common documents of own_bm25 and the Lucene variants in the top 10
        result["top10_common_with_own_bm25"] = {
            name: len(set(top_ids["own_bm25"]) & set(ids)) for name, ids in top_ids.items() if name.startswith("lucene")
        }
        queries.append(result)

    # 4. Compute the mean of each metric for each variant
    judged = [query for query in queries if query["relevant_count"]]
    means = {name: {metric: sum(query["variants"][name][metric] for query in judged) / len(judged)
                    for metric in _METRICS} for name in variants}
    return {"queries": queries, "means": means}


def run_query_types(own: SearchEngine, lucene_engine: LuceneSearchEngine) -> list[dict[str, Any]]:  # Any: results.
    """
    Run one example of each query type on both indexes.

    Steps:
        1. Run the Lucene query: total hits, top 3 names and the parsed Lucene query.
        2. Run the query of the own index when it has one: total hits and top 3 names.

    Returns:
        One row for each example.
    """
    rows: list[dict[str, Any]] = []  # Any: strings, counts, lists.
    for example in QUERY_TYPE_EXAMPLES:
        # 1. Run the Lucene query: total hits, top 3 names and the parsed Lucene query
        result = lucene_engine.search(example.lucene_query, top=3)
        row: dict[str, Any] = {  # Any: mixed values.
            "type": example.query_type, "lucene_query": example.lucene_query, "note": example.note,
            "lucene_hits": result.total_hits, "lucene_top3": [hit.row["name"] for hit in result.hits],
            "lucene_parsed": result.lucene_query[:400],
            "own_query": example.own_query, "own_match": example.own_match, "own_hits": None, "own_top3": [],
        }

        # 2. Run the query of the own index when it has one: total hits and top 3 names
        if example.own_query is not None:
            hits = own.search(example.own_query, method=METHOD_BM25, top=_ALL_HITS, match_mode=example.own_match)
            row["own_hits"] = len(hits)
            row["own_top3"] = [hit.row["name"] for hit in hits[:3]]
        rows.append(row)
    return rows


def measure_query_times(rankers: dict[str, Ranker]) -> dict[str, float]:
    """
    Measure the mean time of one evaluation query (top 10) for each ranker.

    Steps:
        1. Run each query once (warm-up), then _TIMING_ROUNDS times; divide the time by the run count.

    Returns:
        Map from ranker name to milliseconds per query.
    """
    times: dict[str, float] = {}
    for name, ranker in rankers.items():
        # 1. Run each query once (warm-up), then _TIMING_ROUNDS times; divide the time by the run count
        for query in EVALUATION_QUERIES:
            ranker(query.text, _TOP_DEPTH)
        started_at = time.perf_counter()
        for _ in range(_TIMING_ROUNDS):
            for query in EVALUATION_QUERIES:
                ranker(query.text, _TOP_DEPTH)
        times[name] = round((time.perf_counter() - started_at) * 1000 / (_TIMING_ROUNDS * len(EVALUATION_QUERIES)), 2)
    return times


def build_dokuwiki_report(report: dict[str, Any]) -> str:  # Any: nested results.
    """
    Build the DokuWiki tables of the comparison.

    Steps:
        1. Index table: documents, terms, size, build time, query time.
        2. Mean metrics of each variant; AP of each query and variant; common top 10.
        3. Top 5 of own_bm25, lucene_bm25 and lucene_bm25f for each query.
        4. Query types: Lucene query, hits, top 3; own query, hits, top 3.

    Returns:
        DokuWiki text.
    """
    own, lucene_stats, times = report["own_index"], report["lucene_index"], report["query_ms"]
    variants = list(report["evaluation"]["means"])

    # 1. Index table: documents, terms, size, build time, query time
    lines = ["^ Index ^ Dokumenty ^ Termy ^ Veľkosť ^ Čas stavby ^ Čas dopytu (top 10) ^",
             f"| vlastný (''data/index'') | {own['documents_count']} | {own['terms_count']:,} | "
             f"{own['index_bytes'] / 1e6:.1f} MB | {own['build_seconds']:.1f} s | {times['own_bm25']:.1f} ms |",
             f"| Lucene {lucene_stats['lucene_version']} (''data/lucene/index'') | {lucene_stats['documents_count']} | "
             f"{lucene_stats['terms_count']:,} (súčet za polia) | {lucene_stats['index_bytes'] / 1e6:.1f} MB | "
             f"{lucene_stats['build_seconds']:.1f} s | {times['lucene_bm25']:.1f} ms |", ""]

    # 2. Mean metrics of each variant; AP of each query and variant; common top 10
    lines.append("^ Variant ^ MAP ^ priemer P@10 ^ priemer R@100 ^ priemer nDCG@10 ^")
    for name in variants:
        means = report["evaluation"]["means"][name]
        lines.append(f"| ''{name}'' | {means['ap']:.3f} | {means['p_at_10']:.3f} | {means['r_at_100']:.3f} "
                     f"| {means['ndcg_at_10']:.3f} |")
    lucene_variants = [name for name in variants if name.startswith("lucene")]
    lines += ["", "^ Dopyt ^ Text ^ Relevantné ^ " + " ^ ".join(f"AP ''{name}''" for name in variants)
              + " ^ " + " ^ ".join(f"spoločné v top 10 ''own_bm25'' a ''{name}''" for name in lucene_variants) + " ^"]
    for query in report["evaluation"]["queries"]:
        cells = [f"{query['variants'][name]['ap']:.2f}" for name in variants]
        common = [str(query["top10_common_with_own_bm25"][name]) for name in lucene_variants]
        lines.append(f"| {query['label']} | {_code(query['text'])} | {query['relevant_count']} | "
                     + " | ".join(cells) + " | " + " | ".join(common) + " |")

    # 3. Top 5 of own_bm25, lucene_bm25 and lucene_bm25f for each query
    shown = [name for name in ("own_bm25", "lucene_bm25", "lucene_bm25f") if name in variants]
    lines += ["", "^ Dopyt ^ " + " ^ ".join(f"Top 5 ''{name}''" for name in shown) + " ^"]
    for query in report["evaluation"]["queries"]:
        cells = [_LINE_BREAK.join(_cell(name) for name in query["variants"][variant]["top5"]) for variant in shown]
        lines.append(f"| {query['label']} | " + " | ".join(cells) + " |")

    # 4. Query types: Lucene query, hits, top 3; own query, hits, top 3
    lines += ["", "^ Typ dopytu ^ Dopyt Lucene ^ Výsledky ^ Top 3 Lucene ^ Dopyt vo vlastnom indexe ^ Výsledky ^ Top 3 vlastný ^"]
    for row in report["query_types"]:
        own_query = "–" if row["own_query"] is None else _code(row["own_query"]) + (
            " (''--match all'')" if row["own_match"] == MATCH_ALL else "")
        own_hits = "–" if row["own_hits"] is None else str(row["own_hits"])
        lucene_top = _LINE_BREAK.join(_cell(name) for name in row["lucene_top3"]) or "–"
        own_top = _LINE_BREAK.join(_cell(name) for name in row["own_top3"]) or "–"
        lines.append(f"| {row['type']} | {_code(row['lucene_query'])} | {row['lucene_hits']} | {lucene_top} | "
                     f"{own_query} | {own_hits} | {own_top} |")
    return "\n".join(lines) + "\n"


# ==============================================================================
# ENTRY POINT
# ==============================================================================

def main() -> None:
    """
    Compare the own index and the Lucene index; write the reports.

    Steps:
        1. Open both indexes; check that the own index has the Wikipedia data.
        2. Measure the own index build; read the Lucene index statistics.
        3. Evaluate the variants, run the query types, measure the query times.
        4. Write the JSON and the DokuWiki reports.

    Returns:
        None.
    """
    # 1. Open both indexes; check that the own index has the Wikipedia data
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    own = SearchEngine(INDEX_DIR)
    if not own.meta.get("wiki_documents_count"):
        logger.warning("WARNING own index | reason=no_wiki_data hint='python -m indexer.build --wiki'")
    lucene_bm25 = LuceneSearchEngine(LUCENE_INDEX_DIR, SIMILARITY_BM25)
    lucene_classic = LuceneSearchEngine(LUCENE_INDEX_DIR, SIMILARITY_CLASSIC)

    # 2. Measure the own index build; read the Lucene index statistics
    own_stats = _measure_own_build()
    lucene_stats = json.loads(LUCENE_META_PATH.read_text(encoding="utf-8"))

    # 3. Evaluate the variants, run the query types, measure the query times
    variants: dict[str, Ranker] = {
        "own_bm25": lambda text, depth: _get_own_ids(own, text, depth, METHOD_BM25),
        "own_tfidf": lambda text, depth: _get_own_ids(own, text, depth, METHOD_TFIDF),
        "lucene_bm25": lambda text, depth: _get_lucene_ids(lucene_bm25, text, depth, SCORING_FIELDS),
        "lucene_bm25f": lambda text, depth: _get_lucene_ids(lucene_bm25, text, depth, SCORING_BM25F),
        "lucene_classic": lambda text, depth: _get_lucene_ids(lucene_classic, text, depth, SCORING_FIELDS),
    }
    evaluation = evaluate_variants(variants, own)
    for name, means in evaluation["means"].items():
        logger.info("EVALUATED variant | name=%s map=%.3f p_at_10=%.3f r_at_100=%.3f ndcg_at_10=%.3f",
                    name, means["ap"], means["p_at_10"], means["r_at_100"], means["ndcg_at_10"])
    query_types = run_query_types(own, lucene_bm25)
    query_ms = measure_query_times({name: variants[name] for name in ("own_bm25", "lucene_bm25", "lucene_bm25f")})
    logger.info("MEASURED query time | %s", " ".join(f"{name}={ms}ms" for name, ms in query_ms.items()))

    # 4. Write the JSON and the DokuWiki reports
    report = {"own_index": own_stats, "lucene_index": {key: value for key, value in lucene_stats.items() if key != "fields"},
              "query_ms": query_ms, "evaluation": evaluation, "query_types": query_types}
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    COMPARE_JSON_PATH.write_bytes(json.dumps(report, ensure_ascii=False, indent=1).encode("utf-8"))
    COMPARE_WIKI_PATH.write_bytes(build_dokuwiki_report(report).encode("utf-8"))
    lucene_bm25.close()
    lucene_classic.close()
    logger.info("SAVED lucene_compare | json=%s wiki=%s", COMPARE_JSON_PATH.name, COMPARE_WIKI_PATH.name)


if __name__ == "__main__":
    main()
