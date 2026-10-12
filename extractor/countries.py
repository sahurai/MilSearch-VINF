# ==============================================================================
# COUNTRY NORMALIZATION — MilSearch
# ==============================================================================
# PURPOSE:
#     Give one canonical name to each country, so that a search for "Germany"
#     also finds objects from "Nazi Germany" and "Imperial Germany".
#
# RESPONSIBILITIES:
#     1. Merge the historical names of a state that has one clear successor.
#     2. Merge the spelling variants and the typing errors of the source site.
#     3. Keep the names of states with more than one successor without change.
#
# INVARIANTS:
#     The extractor keeps the original name in a separate field. Normalization does not lose data.
#     Soviet Union, Czechoslovakia, Yugoslavia, Austria-Hungary and South Vietnam have more than
#     one successor state. They keep their own names.
# ==============================================================================

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from urllib.parse import unquote

# ==============================================================================
# CONSTANTS
# ==============================================================================

# Lookup key (lowercase letters and digits only) -> canonical name.
_CANONICAL_NAME_BY_KEY: dict[str, str] = {
    # ---- Historical states with one clear successor ----
    "nazigermany": "Germany",
    "imperialgermany": "Germany",
    "germanempire": "Germany",
    "westgermany": "Germany",
    "eastgermany": "Germany",
    "prussia": "Germany",
    "germanynazi": "Germany",            # Flag file name on the site.
    "germanyimperial": "Germany",        # Flag file name on the site.
    "imperialjapan": "Japan",
    "japan1940": "Japan",                # Flag file name on the site.
    "kingdomofitaly": "Italy",
    "imperialrussia": "Russia",
    "colonialamerica": "United States",
    "confederatestates": "United States",
    "zaire": "Democratic Republic of the Congo",
    "rhodesia": "Zimbabwe",
    # ---- Spelling variants ----
    "usa": "United States",
    "unitedstatesofamerica": "United States",
    "britain": "United Kingdom",
    "greatbritain": "United Kingdom",
    "england": "United Kingdom",
    "uk": "United Kingdom",
    "holland": "Netherlands",
    "uae": "United Arab Emirates",
    "czech": "Czech Republic",
    "czechia": "Czech Republic",
    "turkiye": "Turkey",                 # Detail pages use "Turkiye"; catalogs and operators use "Turkey".
    # ---- Typing errors on the site ----
    "beliz": "Belize",
}

_KEY_RE = re.compile(r"[^a-z0-9]")
_SPACE_RE = re.compile(r"\s+")
_EDGE_PUNCTUATION = " \t;,.:"


# ==============================================================================
# HELPERS
# ==============================================================================

def get_country_key(name: str) -> str:
    """
    Make the lookup key of a country name ("Türkiye" -> "turkiye", "Austria-Hungary" -> "austriahungary").

    Steps:
        1. Remove the diacritics.
        2. Use lowercase and remove all characters that are not letters or digits.

    Returns:
        Key such as "nazigermany".
    """
    # 1. Remove the diacritics
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(char for char in decomposed if not unicodedata.combining(char))

    # 2. Use lowercase and remove all characters that are not letters or digits
    return _KEY_RE.sub("", ascii_name.lower())


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def get_canonical_country(name: str | None) -> str | None:
    """
    Get the canonical name of one country.

    Steps:
        1. Decode URL escapes, collapse whitespace and remove edge punctuation.
        2. Replace a known alias with its canonical name.

    Returns:
        Canonical name; None for an empty name.
    """
    # 1. Decode URL escapes, collapse whitespace and remove edge punctuation
    if not name:
        return None
    cleaned = _SPACE_RE.sub(" ", unquote(name)).strip(_EDGE_PUNCTUATION)
    if not cleaned:
        return None

    # 2. Replace a known alias with its canonical name
    return _CANONICAL_NAME_BY_KEY.get(get_country_key(cleaned), cleaned)


def get_canonical_countries(names: Iterable[str]) -> list[str]:
    """
    Get the sorted canonical names of a list of countries without duplicates.

    Steps:
        1. Normalize each name and remove empty names and duplicates.

    Returns:
        Sorted list of canonical names.
    """
    # 1. Normalize each name and remove empty names and duplicates
    return sorted({canonical for canonical in map(get_canonical_country, names) if canonical})
