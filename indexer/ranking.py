# ==============================================================================
# RANKING — MilSearch
# ==============================================================================
# PURPOSE:
#     Compute the idf of a term and the score of a document for a query.
#     Give three ranking methods that differ in the idf and in the length normalization.
#
# RESPONSIBILITIES:
#     1. Compute three idf variants: classic, probabilistic and BM25.
#     2. Score documents in the vector space model (log tf, idf, cosine normalization).
#     3. Score documents with Okapi BM25.
#
# FORMULAS:
#     idf_classic(t)       = log10(N / df)
#     idf_probabilistic(t) = max(0, log10((N - df) / df))
#     idf_bm25(t)          = ln((N - df + 0.5) / (df + 0.5) + 1)
#
#     Vector model (SMART lnc.ltc):
#         w(t, d) = (1 + log10 tf(t, d)) / norm(d)
#         w(t, q) = (1 + log10 tf(t, q)) * idf(t) / |q|
#         score(q, d) = sum over t of w(t, q) * w(t, d)          (cosine similarity)
#
#     BM25 (k1 = 1.2, b = 0.75):
#         score(q, d) = sum over t of idf_bm25(t) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * |d| / avg|d|))
# ==============================================================================

from __future__ import annotations

import math
from collections.abc import Callable, Iterable

# ==============================================================================
# CONSTANTS
# ==============================================================================

BM25_K1 = 1.2   # Standard value: tf saturates after a few occurrences.
BM25_B = 0.75   # Standard value: partial length normalization.

METHOD_TFIDF = "tfidf"
METHOD_PROBABILISTIC = "probidf"
METHOD_BM25 = "bm25"
METHODS: tuple[str, ...] = (METHOD_TFIDF, METHOD_PROBABILISTIC, METHOD_BM25)


# ==============================================================================
# TYPES
# ==============================================================================

# (doc number, weighted tf) for each document that contains the term.
TermPostings = list[tuple[int, float]]


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def compute_idf_classic(documents_count: int, df: int) -> float:
    """
    Compute the classic inverse document frequency.

    Steps:
        1. Return log10(N / df); a term without documents has idf 0.

    Returns:
        Classic idf, 0 or more.
    """
    # 1. Return log10(N / df); a term without documents has idf 0
    return math.log10(documents_count / df) if df else 0.0


def compute_idf_probabilistic(documents_count: int, df: int) -> float:
    """
    Compute the probabilistic inverse document frequency.

    Steps:
        1. Return max(0, log10((N - df) / df)); a term in half or more of the documents has idf 0.

    Returns:
        Probabilistic idf, 0 or more.
    """
    # 1. Return max(0, log10((N - df) / df)); a term in half or more of the documents has idf 0
    if not df or df >= documents_count:
        return 0.0
    return max(0.0, math.log10((documents_count - df) / df))


def compute_idf_bm25(documents_count: int, df: int) -> float:
    """
    Compute the BM25 inverse document frequency (always positive).

    Steps:
        1. Return ln((N - df + 0.5) / (df + 0.5) + 1).

    Returns:
        BM25 idf, more than 0.
    """
    # 1. Return ln((N - df + 0.5) / (df + 0.5) + 1)
    return math.log((documents_count - df + 0.5) / (df + 0.5) + 1)


def get_idf_function(method: str) -> Callable[[int, int], float]:
    """
    Get the idf function of a ranking method.

    Steps:
        1. Look up the idf function by the method name.

    Returns:
        Function (documents_count, df) -> idf.

    Raises:
        ValueError when the method is not known.
    """
    # 1. Look up the idf function by the method name
    functions = {
        METHOD_TFIDF: compute_idf_classic,
        METHOD_PROBABILISTIC: compute_idf_probabilistic,
        METHOD_BM25: compute_idf_bm25,
    }
    if method not in functions:
        raise ValueError(f"unknown ranking method: {method}")
    return functions[method]


def score_vector_model(
    query_tf: dict[str, int],
    postings_by_term: dict[str, TermPostings],
    norm_by_doc: dict[int, float],
    documents_count: int,
    idf_function: Callable[[int, int], float],
) -> dict[int, float]:
    """
    Score documents with cosine similarity (lnc.ltc) and the given idf.

    Steps:
        1. Compute the query weight of each term and the query vector length.
        2. Add w(t, q) * w(t, d) for each document in the postings of each term.

    Returns:
        Map from doc number to score (cosine similarity, 0 to 1).
    """
    # 1. Compute the query weight of each term and the query vector length
    query_weights = {
        term: (1 + math.log10(count)) * idf_function(documents_count, len(postings_by_term.get(term, [])))
        for term, count in query_tf.items()
    }
    query_length = math.sqrt(sum(weight ** 2 for weight in query_weights.values())) or 1.0

    # 2. Add w(t, q) * w(t, d) for each document in the postings of each term
    scores: dict[int, float] = {}
    for term, query_weight in query_weights.items():
        if query_weight == 0:
            continue
        for doc_number, tf in postings_by_term.get(term, []):
            document_weight = (1 + math.log10(tf)) / norm_by_doc[doc_number]
            scores[doc_number] = scores.get(doc_number, 0.0) + query_weight / query_length * document_weight
    return scores


def score_bm25(
    query_tf: dict[str, int],
    postings_by_term: dict[str, TermPostings],
    length_by_doc: dict[int, float],
    avg_length: float,
    documents_count: int,
) -> dict[int, float]:
    """
    Score documents with Okapi BM25.

    Steps:
        1. Compute the BM25 idf of each query term.
        2. Add the saturated, length-normalized tf times idf for each document.

    Returns:
        Map from doc number to BM25 score.
    """
    scores: dict[int, float] = {}
    for term, count in query_tf.items():
        postings = postings_by_term.get(term, [])

        # 1. Compute the BM25 idf of each query term
        idf = compute_idf_bm25(documents_count, len(postings))

        # 2. Add the saturated, length-normalized tf times idf for each document
        for doc_number, tf in postings:
            length_ratio = length_by_doc[doc_number] / avg_length if avg_length else 1.0
            saturated = tf * (BM25_K1 + 1) / (tf + BM25_K1 * (1 - BM25_B + BM25_B * length_ratio))
            scores[doc_number] = scores.get(doc_number, 0.0) + count * idf * saturated
    return scores


def intersect_postings(doc_lists: Iterable[list[int]]) -> list[int]:
    """
    Intersect sorted doc number lists; start with the shortest list.

    Steps:
        1. Sort the lists by length (the rarest term first).
        2. Merge the result with each next list (linear two-pointer intersection).

    Returns:
        Sorted doc numbers that are in all lists.
    """
    # 1. Sort the lists by length (the rarest term first)
    ordered = sorted(doc_lists, key=len)
    if not ordered:
        return []
    result = ordered[0]

    # 2. Merge the result with each next list (linear two-pointer intersection)
    for other in ordered[1:]:
        merged: list[int] = []
        left_index = right_index = 0
        while left_index < len(result) and right_index < len(other):
            if result[left_index] == other[right_index]:
                merged.append(result[left_index])
                left_index += 1
                right_index += 1
            elif result[left_index] < other[right_index]:
                left_index += 1
            else:
                right_index += 1
        result = merged
        if not result:
            break
    return result
