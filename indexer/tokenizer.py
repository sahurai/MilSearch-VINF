# ==============================================================================
# TOKENIZER — MilSearch
# ==============================================================================
# PURPOSE:
#     Change a text into index terms. The indexer and the query parser use the same
#     function, so that query terms and document terms match.
#
# RESPONSIBILITIES:
#     1. Split a text into tokens with a regular expression.
#     2. Keep compound tokens (calibers, model names) and also add their parts.
#     3. Remove stop words.
#     4. Reduce plural forms with the S-stemmer.
#
# FORMULAS:
#     S-stemmer (Harman, 1991), first matching rule only:
#         "-ies" -> "-y"   (not after "e" or "a")
#         "-es"  -> "-e"   (not after "a", "e" or "o")
#         "-s"   -> ""     (not after "u" or "s")
#
# INVARIANTS:
#     A term is lowercase and has at least one letter or digit.
#     The same input text always gives the same terms in the same order.
# ==============================================================================

from __future__ import annotations

import re
import unicodedata

# ==============================================================================
# CONSTANTS
# ==============================================================================

# A token is a run of letters/digits, optionally joined by ".", "-", "/" or "x"
# inside the token: "7.62x39mm", "t-80", "f/a-18", ".50" (the leading dot is removed).
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.\-/][a-z0-9]+)*")
_PART_SPLIT_RE = re.compile(r"[\-/]")  # "." is a decimal point ("7.62"), not a separator.
# Caliber such as "7.62x39mm" or "12.7x99mm": also index the form without "mm".
_CALIBER_MM_RE = re.compile(r"^(\d+(?:\.\d+)?x\d+)mm$")

_MIN_STEM_LENGTH = 4  # Do not stem short words such as "gas", "bus", "us".

# Common English function words. The list is short on purpose: words such as "light",
# "heavy", "main" or "battle" are important in this domain and stay in the index.
STOP_WORDS: frozenset[str] = frozenset("""
a about above after again against all also am an and any are as at be because been before
being below between both but by can could did do does doing down during each few for from
further had has have having he her here hers herself him himself his how i if in into is it
its itself just me more most my myself no nor not now of off on once only or other our ours
ourselves out over own same she should so some such than that the their theirs them
themselves then there these they this those through to too under until up very was we were
what when where which while who whom why will with would you your yours yourself yourselves
""".split())


# ==============================================================================
# HELPERS
# ==============================================================================

def _get_ascii_lowercase(text: str) -> str:
    """
    Make a lowercase ASCII form of a text ("Škoda" -> "skoda").

    Steps:
        1. Decompose accented characters and drop the combining marks.
        2. Use lowercase.

    Returns:
        Lowercase text without diacritics.
    """
    # 1. Decompose accented characters and drop the combining marks
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))

    # 2. Use lowercase
    return stripped.lower()


def stem(word: str) -> str:
    """
    Reduce a plural form to its singular form with the S-stemmer.

    Steps:
        1. Keep short words and words with digits without change.
        2. Apply the first matching S-stemmer rule.

    Returns:
        Stemmed word.
    """
    # 1. Keep short words and words with digits without change
    if len(word) < _MIN_STEM_LENGTH or any(char.isdigit() for char in word):
        return word

    # 2. Apply the first matching S-stemmer rule
    if word.endswith("ies") and not word.endswith(("eies", "aies")):
        return word[:-3] + "y"
    if word.endswith("es") and not word.endswith(("aes", "ees", "oes")):
        return word[:-1]
    if word.endswith("s") and not word.endswith(("us", "ss")):
        return word[:-1]
    return word


def _get_variants(token: str) -> list[str]:
    """
    Get the token and its extra index forms.

    Steps:
        1. Start with the full token.
        2. Add the caliber form without "mm".
        3. Add the joined form and the parts (except single letters) of a compound token.

    Returns:
        Token variants in a fixed order, without duplicates.
    """
    # 1. Start with the full token
    variants = [token]

    # 2. Add the caliber form without "mm"
    caliber = _CALIBER_MM_RE.match(token)
    if caliber:
        variants.append(caliber.group(1))

    # 3. Add the joined form and the parts (except single letters) of a compound token
    parts = [part for part in _PART_SPLIT_RE.split(token) if part]
    if len(parts) > 1:
        variants.append("".join(parts))
        variants.extend(part for part in parts if not (len(part) == 1 and part.isalpha()))
    return list(dict.fromkeys(variants))


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def get_tokens(text: str) -> list[str]:
    """
    Split a text into lowercase surface tokens (no stop-word removal, no stemming).

    Steps:
        1. Normalize the text to lowercase ASCII.
        2. Find the tokens with the token pattern.

    Returns:
        Tokens in text order.
    """
    # 1. Normalize the text to lowercase ASCII
    normalized = _get_ascii_lowercase(text)

    # 2. Find the tokens with the token pattern
    return _TOKEN_RE.findall(normalized)


def get_term_groups(text: str) -> list[list[str]]:
    """
    Change a text into term groups: one group for each token position.

    A group holds the main term and the extra forms of one token (for example
    "ak-47", "ak47", "ak", "47"). All terms of a group
    share one position in the positional index.

    Steps:
        1. Split the text into tokens.
        2. Drop stop words.
        3. Stem each variant and drop stop words and duplicates inside the group.

    Returns:
        List of term groups in text order.
    """
    groups: list[list[str]] = []

    # 1. Split the text into tokens
    for token in get_tokens(text):
        # 2. Drop stop words
        if token in STOP_WORDS:
            continue

        # 3. Stem each variant and drop stop words and duplicates inside the group
        terms = [stem(variant) for variant in _get_variants(token) if variant not in STOP_WORDS]
        groups.append(list(dict.fromkeys(terms)))
    return groups


def get_terms(text: str) -> list[str]:
    """
    Change a text into a flat list of index terms.

    Steps:
        1. Flatten the term groups of the text.

    Returns:
        Terms in text order; a compound token gives more than one term.
    """
    # 1. Flatten the term groups of the text
    return [term for group in get_term_groups(text) for term in group]
