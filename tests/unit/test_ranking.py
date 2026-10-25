# ==============================================================================
# RANKING TESTS — MilSearch
# ==============================================================================
# PURPOSE:
#     Test the idf variants, the score functions and the postings intersection.
#
# RESPONSIBILITIES:
#     1. Make sure that the three idf formulas give the expected values.
#     2. Make sure that the methods do not use the probabilistic idf (the course forbids it).
#     3. Make sure that the vector model and BM25 rank the better document first.
#     4. Make sure that the postings intersection is correct for sorted lists.
#
# DEPENDENCIES:
#     indexer.ranking — functions under test.
# ==============================================================================

from __future__ import annotations

import math

import pytest

from indexer.ranking import (
    METHOD_BM25,
    METHOD_TFIDF,
    METHOD_TFIDF_SMOOTH,
    METHODS,
    compute_idf_bm25,
    compute_idf_classic,
    compute_idf_smooth,
    get_idf_function,
    intersect_postings,
    score_bm25,
    score_vector_model,
)

# ==============================================================================
# CONSTANTS
# ==============================================================================

# Two documents: doc 0 has "tank" 3 times, doc 1 one time; doc 1 also has "ship".
_POSTINGS = {"tank": [(0, 3.0), (1, 1.0)], "ship": [(1, 2.0)]}


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def test_classic_idf_matches_lecture_example() -> None:
    """
    N = 10000 and df = 50 give idf = log10(200) = 2.30103 (lecture example).

    Steps:
        1. Compute the classic idf for the lecture values.

    Returns:
        None.
    """
    # 1. Compute the classic idf for the lecture values
    assert compute_idf_classic(10_000, 50) == pytest.approx(2.30103, abs=1e-5)
    assert compute_idf_classic(10_000, 0) == 0.0


def test_methods_use_only_classic_and_bm25_idf() -> None:
    """
    The course forbids the probabilistic idf. Each method uses the classic, smooth or BM25 idf.

    Steps:
        1. Get the idf function of each method.

    Returns:
        None.
    """
    # 1. Get the idf function of each method
    assert METHODS == (METHOD_TFIDF, METHOD_TFIDF_SMOOTH, METHOD_BM25)
    assert get_idf_function(METHOD_TFIDF) is compute_idf_classic
    assert get_idf_function(METHOD_TFIDF_SMOOTH) is compute_idf_smooth
    assert get_idf_function(METHOD_BM25) is compute_idf_bm25
    with pytest.raises(ValueError):
        get_idf_function("probidf")


def test_classic_idf_is_zero_and_bm25_idf_positive_for_a_term_in_all_documents() -> None:
    """
    A term in all documents has classic idf 0, but a small positive BM25 idf.

    Steps:
        1. Compute both idf variants for df = N.

    Returns:
        None.
    """
    # 1. Compute both idf variants for df = N
    assert compute_idf_classic(100, 100) == 0.0
    assert 0 < compute_idf_bm25(100, 100) < 0.01


def test_smooth_idf_keeps_frequent_terms_near_one() -> None:
    """
    The smooth idf is log10(N / (1 + df)) + 1: about 1 for a term in all documents.

    Steps:
        1. Compute the smooth idf for a rare term, a term in all documents and an unknown term.

    Returns:
        None.
    """
    # 1. Compute the smooth idf for a rare term, a term in all documents and an unknown term
    assert compute_idf_smooth(10_000, 49) == pytest.approx(math.log10(200) + 1)
    assert compute_idf_smooth(100, 100) == pytest.approx(1.0, abs=0.01)
    assert compute_idf_smooth(100, 0) == 0.0


def test_bm25_idf_is_almost_proportional_to_classic_idf() -> None:
    """
    idf_bm25 = ln((N + 1) / (df + 0.5)): for a rare term it is about ln(10) times the classic idf.

    Steps:
        1. Compare the ratio of the two idf values for rare terms.

    Returns:
        None.
    """
    # 1. Compare the ratio of the two idf values for rare terms
    for df in (10, 50, 200):
        ratio = compute_idf_bm25(100_000, df) / compute_idf_classic(100_000, df)
        assert ratio == pytest.approx(math.log(10), rel=0.02)


def test_bm25_idf_is_positive_for_all_terms() -> None:
    """
    The BM25 idf stays more than 0, also for a term in all documents.

    Steps:
        1. Compute the BM25 idf for a rare term and for a term in all documents.

    Returns:
        None.
    """
    # 1. Compute the BM25 idf for a rare term and for a term in all documents
    assert compute_idf_bm25(10, 1) == pytest.approx(math.log(9.5 / 1.5 + 1))
    assert compute_idf_bm25(10, 10) > 0


def test_vector_model_ranks_higher_tf_first() -> None:
    """
    With equal norms, the document with the higher tf of the query term has the higher score.

    Steps:
        1. Score the query "tank" with the classic idf.

    Returns:
        None.
    """
    # 1. Score the query "tank" with the classic idf
    scores = score_vector_model({"tank": 1}, _POSTINGS, {0: 1.0, 1: 1.0}, 4, compute_idf_classic)
    assert scores[0] > scores[1] > 0


def test_bm25_ranks_higher_tf_first_and_rewards_rare_terms() -> None:
    """
    BM25 ranks doc 0 first for "tank" and doc 1 first for "tank ship".

    Steps:
        1. Score the query "tank".
        2. Score the query "tank ship".

    Returns:
        None.
    """
    # 1. Score the query "tank"
    lengths = {0: 3.0, 1: 3.0}
    scores = score_bm25({"tank": 1}, _POSTINGS, lengths, 3.0, 4)
    assert scores[0] > scores[1]

    # 2. Score the query "tank ship"
    scores = score_bm25({"tank": 1, "ship": 1}, _POSTINGS, lengths, 3.0, 4)
    assert scores[1] > scores[0]


def test_intersect_postings_keeps_common_docs() -> None:
    """
    The intersection of sorted lists keeps only the common doc numbers.

    Steps:
        1. Intersect three lists and an empty list.

    Returns:
        None.
    """
    # 1. Intersect three lists and an empty list
    assert intersect_postings([[1, 2, 4, 8, 16], [2, 3, 8, 16], [8, 16, 20]]) == [8, 16]
    assert intersect_postings([[1, 2], []]) == []
