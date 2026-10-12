# ==============================================================================
# COUNTRY NORMALIZATION TESTS — MilSearch
# ==============================================================================
# PURPOSE:
#     Test the canonical country names.
#
# RESPONSIBILITIES:
#     1. Make sure that historical names with one successor become the successor name.
#     2. Make sure that spelling variants, typing errors and URL escapes are merged.
#     3. Make sure that states with more than one successor keep their names.
#
# DEPENDENCIES:
#     extractor.countries — functions under test.
# ==============================================================================

from __future__ import annotations

from extractor.countries import get_canonical_countries, get_canonical_country

# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def test_historical_states_with_one_successor_are_merged() -> None:
    """
    Nazi Germany, Imperial Japan and Kingdom of Italy become Germany, Japan and Italy.

    Steps:
        1. Normalize the historical names.

    Returns:
        None.
    """
    # 1. Normalize the historical names
    assert get_canonical_country("Nazi Germany") == "Germany"
    assert get_canonical_country("Imperial Germany") == "Germany"
    assert get_canonical_country("Imperial Japan") == "Japan"
    assert get_canonical_country("Kingdom of Italy") == "Italy"


def test_spelling_variants_typing_errors_and_escapes_are_merged() -> None:
    """
    Britain, Holland, "Beliz", "Niger;" and URL escapes get the canonical names.

    Steps:
        1. Normalize the variant names.

    Returns:
        None.
    """
    # 1. Normalize the variant names
    assert get_canonical_country("Britain") == "United Kingdom"
    assert get_canonical_country("Holland") == "Netherlands"
    assert get_canonical_country("Beliz") == "Belize"
    assert get_canonical_country("Niger;") == "Niger"
    assert get_canonical_country("Bosnia%20and%20Herzegovina") == "Bosnia and Herzegovina"


def test_states_with_more_than_one_successor_keep_their_names() -> None:
    """
    Soviet Union, Czechoslovakia, Yugoslavia and Austria-Hungary do not change.

    Steps:
        1. Normalize the names of the states with more than one successor.

    Returns:
        None.
    """
    # 1. Normalize the names of the states with more than one successor
    for name in ("Soviet Union", "Czechoslovakia", "Yugoslavia", "Austria-Hungary"):
        assert get_canonical_country(name) == name


def test_country_list_is_sorted_without_duplicates() -> None:
    """
    A list with Germany and Nazi Germany gives Germany one time.

    Steps:
        1. Normalize a list with duplicates after normalization and an empty name.

    Returns:
        None.
    """
    # 1. Normalize a list with duplicates after normalization and an empty name
    assert get_canonical_countries(["Nazi Germany", "France", "Germany", ""]) == ["France", "Germany"]


def test_turkiye_and_diacritics_become_turkey() -> None:
    """
    Regression: detail pages use "Turkiye", catalogs and operators use "Turkey".

    Steps:
        1. Normalize the spellings with and without diacritics.

    Returns:
        None.
    """
    # 1. Normalize the spellings with and without diacritics
    assert get_canonical_country("Turkiye") == "Turkey"
    assert get_canonical_country("Türkiye") == "Turkey"
