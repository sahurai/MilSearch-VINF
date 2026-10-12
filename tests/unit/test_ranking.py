# ==============================================================================
# RANKING TESTS — MilSearch
# ==============================================================================
# PURPOSE:
#     Test the idf variants, the score functions and the postings intersection.
#
# RESPONSIBILITIES:
#     1. Make sure that the three idf formulas give the values from the lecture example.
#     2. Make sure that the vector model and BM25 rank the better document first.
#     3. Make sure that the postings intersection is correct for sorted lists.
#
# DEPENDENCIES:
#     indexer.ranking — functions under test.
# ==============================================================================

from __future__ import annotations

import math

import pytest

from indexer.ranking import (
    compute_idf_bm25,
    compute_idf_classic,
    compute_idf_probabilistic,
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


def test_probabilistic_idf_is_zero_for_frequent_terms() -> None:
    """
    The probabilistic idf is log10((N - df) / df) and 0 for a term in half or more of the documents.

    Steps:
        1. Compute the probabilistic idf for a rare term and for frequent terms.

    Returns:
        None.
    """
    # 1. Compute the probabilistic idf for a rare term and for frequent terms
    assert compute_idf_probabilistic(10_000, 50) == pytest.approx(math.log10(9_950 / 50))
    assert compute_idf_probabilistic(10, 5) == 0.0
    assert compute_idf_probabilistic(10, 8) == 0.0


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
