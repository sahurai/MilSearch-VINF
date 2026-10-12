# ==============================================================================
# SEARCH EVALUATION — MilSearch
# ==============================================================================
# PURPOSE:
#     Compare the three ranking methods on a fixed set of free-text queries.
#     The relevant documents of each query come from the structured fields of the site,
#     not from the ranked text search, so the judgment does not depend on the ranking.
#
# RESPONSIBILITIES:
#     1. Define the evaluation queries and their relevance rules.
#     2. Find the relevant documents of each query in the document table.
#     3. Rank the free-text query with each method and compute P@10, R@100, AP and nDCG@10.
#     4. Write data/reports/search_eval.json and data/reports/search_eval.dokuwiki.txt.
#
# DEPENDENCIES:
#     indexer.ranking — method names.
#     indexer.search — search engine.
#     reports.corpus_stats — report paths.
#
# USAGE:
#     python -m indexer.build && python -m reports.search_eval
#
# FORMULAS:
#     P@k     = relevant documents in the top k / k
#     R@k     = relevant documents in the top k / all relevant documents
#     AP      = (1 / |R|) * sum over relevant documents d in the ranking of P@rank(d)
#     nDCG@k  = DCG@k / ideal DCG@k,  DCG@k = sum over i <= k of rel(i) / log2(i + 1)
#
# INVARIANTS:
#     The free-text query has no field filters; only the relevance rule uses the fields.
# ==============================================================================

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from indexer.ranking import METHODS
from indexer.search import SearchEngine
from reports.corpus_stats import REPORTS_DIR

# ==============================================================================
# CONSTANTS
# ==============================================================================

logger = logging.getLogger("reports.search_eval")

SEARCH_EVAL_JSON_PATH: Path = REPORTS_DIR / "search_eval.json"
SEARCH_EVAL_WIKI_PATH: Path = REPORTS_DIR / "search_eval.dokuwiki.txt"

_PRECISION_DEPTH = 10
_RECALL_DEPTH = 100
_RANKING_DEPTH = 1000  # Deep enough to hold all relevant documents of each query for AP.


# ==============================================================================
# TYPES
# ==============================================================================

Row = dict[str, Any]  # Any: a document row holds str, int, float and list values.


@dataclass(frozen=True)
class EvaluationQuery:
    """
    One evaluation query.

    Responsibilities:
        1. Carry the label, the free-text query and the rule that marks a row as relevant.
    """

    label: str
    text: str
    is_relevant: Callable[[Row], bool]


# ==============================================================================
# HELPERS
# ==============================================================================

def _has(row: Row, field_name: str, *needles: str) -> bool:
    """
    Test if a text or list field contains all needles (case-insensitive).

    Steps:
        1. Join a list field, then test each needle as a substring.

    Returns:
        True when all needles are in the field.
    """
    # 1. Join a list field, then test each needle as a substring
    value = row.get(field_name)
    text = " ".join(map(str, value)) if isinstance(value, list) else str(value or "")
    return all(needle.casefold() in text.casefold() for needle in needles)


def _is_in_years(row: Row, first_year: int, last_year: int) -> bool:
    """
    Test if the year of a row is in a range.

    Steps:
        1. Compare the year with the bounds; a row without a year is out of the range.

    Returns:
        True when first_year <= year <= last_year.
    """
    # 1. Compare the year with the bounds; a row without a year is out of the range
    return isinstance(row.get("year"), int) and first_year <= row["year"] <= last_year


EVALUATION_QUERIES: tuple[EvaluationQuery, ...] = (
    EvaluationQuery("Q1", "7.62x39 assault rifle",
                    lambda row: row["category"] == "smallarms" and _has(row, "caliber", "7.62x39")
                    and _has(row, "type", "assault rifle")),
    EvaluationQuery("Q2", "main battle tank",
                    lambda row: row["category"] == "armor" and _has(row, "type", "main battle tank")),
    EvaluationQuery("Q3", "aircraft carrier",
                    lambda row: row["category"] == "ships" and _has(row, "type", "aircraft carrier")),
    EvaluationQuery("Q4", "german submarine world war 2",
                    lambda row: row["category"] == "ships" and row.get("country") == "Germany"
                    and _has(row, "type", "submarine") and _is_in_years(row, 1935, 1945)),
    EvaluationQuery("Q5", "barrett m82",
                    lambda row: _has(row, "name", "barrett m82")),
    EvaluationQuery("Q6", "soviet self-propelled howitzer",
                    lambda row: row["category"] == "armor" and row.get("country") == "Soviet Union"
                    and _has(row, "type", "self-propelled") and _has(row, "type", "howitzer")),
    EvaluationQuery("Q7", "japanese battleship",
                    lambda row: row["category"] == "ships" and row.get("country") == "Japan"
                    and _has(row, "type", "battleship")),
    EvaluationQuery("Q8", "british submachine gun",
                    lambda row: row["category"] == "smallarms" and row.get("country") == "United Kingdom"
                    and _has(row, "type", "submachine gun")),
    EvaluationQuery("Q9", ".50 bmg sniper rifle",
                    lambda row: row["category"] == "smallarms" and _has(row, "type", "rifle")
                    and (_has(row, "caliber", ".50 bmg") or _has(row, "caliber", "12.7x99"))),
    EvaluationQuery("Q10", "american destroyer",
                    lambda row: row["category"] == "ships" and row.get("country") == "United States"
                    and _has(row, "type", "destroyer")),
)


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def compute_metrics(ranked_doc_ids: list[str], relevant_ids: set[str]) -> dict[str, float]:
    """
    Compute P@10, R@100, AP and nDCG@10 of one ranking.

    Steps:
        1. Compute the precision and the recall at the fixed depths.
        2. Compute the average precision over the full ranking.
        3. Compute the nDCG at depth 10 with binary relevance.

    Returns:
        Map from metric name to value.
    """
    if not relevant_ids:
        return {"p_at_10": 0.0, "r_at_100": 0.0, "ap": 0.0, "ndcg_at_10": 0.0}

    # 1. Compute the precision and the recall at the fixed depths
    p_at_10 = sum(doc in relevant_ids for doc in ranked_doc_ids[:_PRECISION_DEPTH]) / _PRECISION_DEPTH
    r_at_100 = sum(doc in relevant_ids for doc in ranked_doc_ids[:_RECALL_DEPTH]) / len(relevant_ids)

    # 2. Compute the average precision over the full ranking
    hits = 0
    precision_sum = 0.0
    for rank, doc in enumerate(ranked_doc_ids, start=1):
        if doc in relevant_ids:
            hits += 1
            precision_sum += hits / rank
    ap = precision_sum / len(relevant_ids)

    # 3. Compute the nDCG at depth 10 with binary relevance
    dcg = sum(1 / math.log2(rank + 1) for rank, doc in enumerate(ranked_doc_ids[:_PRECISION_DEPTH], start=1)
              if doc in relevant_ids)
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(len(relevant_ids), _PRECISION_DEPTH) + 1))
    return {"p_at_10": p_at_10, "r_at_100": r_at_100, "ap": ap, "ndcg_at_10": dcg / ideal}


def evaluate(engine: SearchEngine) -> dict[str, Any]:  # Any: nested metrics and top results.
    """
    Evaluate all queries with all methods.

    Steps:
        1. Find the relevant documents of each query from the structured fields.
        2. Rank each query with each method and compute the metrics; keep the top 5 names.
        3. Compute the mean of each metric for each method.

    Returns:
        Per-query results and mean metrics by method.
    """
    queries: list[dict[str, Any]] = []  # Any: labels, counts, metrics and names.
    for query in EVALUATION_QUERIES:
        # 1. Find the relevant documents of each query from the structured fields
        relevant_ids = {row["id"] for row in engine.rows if query.is_relevant(row)}
        result: dict[str, Any] = {"label": query.label, "text": query.text,  # Any: mixed values.
                                  "relevant_count": len(relevant_ids), "methods": {}}

        # 2. Rank each query with each method and compute the metrics; keep the top 5 names
        for method in METHODS:
            hits = engine.search(query.text, method=method, top=_RANKING_DEPTH)
            ranked_ids = [hit.row["id"] for hit in hits]
            result["methods"][method] = {
                **compute_metrics(ranked_ids, relevant_ids),
                "top5": [f"{hit.row['name']}{' ✓' if hit.row['id'] in relevant_ids else ''}" for hit in hits[:5]],
            }
        queries.append(result)

    # 3. Compute the mean of each metric for each method
    judged = [query for query in queries if query["relevant_count"]]
    means = {
        method: {
            metric: sum(query["methods"][method][metric] for query in judged) / len(judged) if judged else 0.0
            for metric in ("p_at_10", "r_at_100", "ap", "ndcg_at_10")
        }
        for method in METHODS
    }
    return {"queries": queries, "means": means, "documents_count": len(engine.rows)}


def build_dokuwiki_report(report: dict[str, Any]) -> str:  # Any: nested metrics and top results.
    """
    Build the DokuWiki tables of the search evaluation.

    Steps:
        1. Write the table of mean metrics by method.
        2. Write the table of AP and P@10 for each query and method.
        3. Write the top 5 results of each query for each method.

    Returns:
        DokuWiki text.
    """
    # 1. Write the table of mean metrics by method
    lines = ["^ Metóda ^ MAP ^ priemer P@10 ^ priemer R@100 ^ priemer nDCG@10 ^"]
    for method, means in report["means"].items():
        lines.append(f"| ''{method}'' | {means['ap']:.3f} | {means['p_at_10']:.3f} | {means['r_at_100']:.3f} "
                     f"| {means['ndcg_at_10']:.3f} |")

    # 2. Write the table of AP and P@10 for each query and method
    lines += ["", "^ Dopyt ^ Text ^ Relevantné ^ " + " ^ ".join(f"AP / P@10 ''{m}''" for m in METHODS) + " ^"]
    for query in report["queries"]:
        cells = [f"{query['methods'][m]['ap']:.2f} / {query['methods'][m]['p_at_10']:.1f}" for m in METHODS]
        lines.append(f"| {query['label']} | ''{query['text']}'' | {query['relevant_count']} | " + " | ".join(cells) + " |")

    # 3. Write the top 5 results of each query for each method
    lines += ["", "^ Dopyt ^ " + " ^ ".join(f"Top 5 ''{m}''" for m in METHODS) + " ^"]
    for query in report["queries"]:
        cells = ["\\\\ ".join(query["methods"][m]["top5"]) for m in METHODS]
        lines.append(f"| {query['label']} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


# ==============================================================================
# ENTRY POINT
# ==============================================================================

def main() -> None:
    """
    Evaluate the search and write the JSON and DokuWiki reports.

    Steps:
        1. Configure the logging and load the index.
        2. Evaluate and log the mean metrics.
        3. Write the reports.

    Returns:
        None.
    """
    # 1. Configure the logging and load the index
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    engine = SearchEngine()

    # 2. Evaluate and log the mean metrics
    report = evaluate(engine)
    for method, means in report["means"].items():
        logger.info("EVALUATED search | method=%s map=%.3f p_at_10=%.3f r_at_100=%.3f ndcg_at_10=%.3f",
                    method, means["ap"], means["p_at_10"], means["r_at_100"], means["ndcg_at_10"])

    # 3. Write the reports
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    SEARCH_EVAL_JSON_PATH.write_bytes(json.dumps(report, ensure_ascii=False, indent=1).encode("utf-8"))
    SEARCH_EVAL_WIKI_PATH.write_bytes(build_dokuwiki_report(report).encode("utf-8"))
    logger.info("SAVED search_eval | json=%s wiki=%s", SEARCH_EVAL_JSON_PATH.name, SEARCH_EVAL_WIKI_PATH.name)


if __name__ == "__main__":
    main()
