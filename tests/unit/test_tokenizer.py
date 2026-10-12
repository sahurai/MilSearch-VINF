# ==============================================================================
# TOKENIZER TESTS — MilSearch
# ==============================================================================
# PURPOSE:
#     Test the change of text into index terms.
#
# RESPONSIBILITIES:
#     1. Make sure that stop words are removed and plural forms are stemmed.
#     2. Make sure that compound tokens keep the full form and add the joined form and the parts.
#     3. Make sure that calibers also have the form without "mm" and diacritics are removed.
#
# DEPENDENCIES:
#     indexer.tokenizer — functions under test.
# ==============================================================================

from __future__ import annotations

from indexer.tokenizer import get_term_groups, get_terms, stem

# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def test_removes_stop_words_and_stems_plurals() -> None:
    """
    "the" and "of" are removed; "tanks", "batteries" and "carriers" become singular.

    Steps:
        1. Get the terms of a sentence.

    Returns:
        None.
    """
    # 1. Get the terms of a sentence
    assert get_terms("The tanks of the batteries and carriers") == ["tank", "battery", "carrier"]


def test_stemmer_keeps_short_words_and_ss_us_endings() -> None:
    """
    Short words and the endings "ss" and "us" do not change.

    Steps:
        1. Stem words that the S-stemmer must not change.

    Returns:
        None.
    """
    # 1. Stem words that the S-stemmer must not change
    assert [stem(word) for word in ("gas", "class", "corpus", "m1a2")] == ["gas", "class", "corpus", "m1a2"]


def test_compound_token_has_full_joined_and_part_forms() -> None:
    """
    "AK-47" gives "ak-47", "ak47", "ak" and "47" at one position.

    Steps:
        1. Get the term groups of the token.

    Returns:
        None.
    """
    # 1. Get the term groups of the token
    assert get_term_groups("AK-47") == [["ak-47", "ak47", "ak", "47"]]


def test_caliber_has_form_without_mm_and_keeps_decimal_point() -> None:
    """
    "7.62x39mm" is one token and also gives "7.62x39".

    Steps:
        1. Get the term groups of the caliber.

    Returns:
        None.
    """
    # 1. Get the term groups of the caliber
    assert get_term_groups("7.62x39mm") == [["7.62x39mm", "7.62x39"]]


def test_removes_diacritics_and_single_letter_parts() -> None:
    """
    "Škoda" becomes "skoda"; "U-boat" has no single-letter part "u".

    Steps:
        1. Get the term groups of the text.

    Returns:
        None.
    """
    # 1. Get the term groups of the text
    assert get_term_groups("Škoda U-boat") == [["skoda"], ["u-boat", "uboat", "boat"]]
