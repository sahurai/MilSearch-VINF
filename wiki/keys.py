# ==============================================================================
# JOIN KEYS — MilSearch
# ==============================================================================
# PURPOSE:
#     Make the join keys of the MilitaryFactory documents and of the Wikipedia pages.
#     A key is the name in lowercase ASCII letters and digits only ("T-72B3" -> "t72b3").
#
# RESPONSIBILITIES:
#     1. Normalize a name to a key: no diacritics, no case, no spaces, hyphens or punctuation.
#     2. Make the name variants of a MilitaryFactory document with a rank (0 = the full name).
#     3. Make the alias keys of a Wikipedia page from its title with a rank (0 = the title).
#
# INVARIANTS:
#     Python 3.6 syntax (Spark image of the course). Regular expressions only.
#     The same get_name_key() function makes the keys on both sides of the join.
# ==============================================================================

import re
import unicodedata
from typing import Dict, List, Tuple

# ==============================================================================
# CONSTANTS
# ==============================================================================

# Letters without a Unicode decomposition ("Krag–Jørgensen" -> "kragjorgensen").
_TRANSLITERATION = str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "ß": "ss", "đ": "d", "Đ": "D",
                                  "ł": "l", "Ł": "L", "þ": "th", "œ": "oe", "ı": "i"})
_NON_KEY_RE = re.compile(r"[^a-z0-9]+")
_PARENTHESES_RE = re.compile(r"\s*\(([^()]*)\)")
_SPACE_RE = re.compile(r"\s+")
MIN_KEY_LENGTH = 3

# Ship prefixes of MilitaryFactory and Wikipedia ("KMS Bismarck", "USS Nimitz", "IJN Yamato").
_SHIP_PREFIX_RE = re.compile(
    r"^(USS|USNS|HMS|HMAS|HMCS|HMNZS|HMNLMS|HNLMS|HNoMS|HSwMS|HDMS|KMS|SMS|IJN|JDS|JS|INS|FS|CNS|PLANS|ROKS|RSS"
    r"|TCG|BRP|ARA|BAE|NRP|ORP|RFS|ITS|SNS|KRI|KD|BNS|PNS|HS|ENS|IRIS|HMSAS|SAS|HMT|USCGC|ROCS|NMS|RV|SS|RN|MV)\s+"
)
# Wikipedia ship article titles: "<nationality> <ship type> <name>" ("German battleship Bismarck").
_SHIP_TITLE_RE = re.compile(
    r"^[A-Z][a-z]+(?:[- ][A-Z][a-z]+)?\s+(?:[a-z-]+\s+){0,3}?"
    r"(?:battleship|battlecruiser|cruiser|destroyer|frigate|corvette|submarine|carrier|tender|sloop|gunboat"
    r"|monitor|minesweeper|minelayer|ship|vessel|boat|escort|aviso|ironclad|icebreaker|cutter|tanker|tug"
    r"|transport|oiler|dock|craft|yacht|hovercraft|hydrofoil|torpedo boat|u-boat)\s+(.+)$"
)
# Wikipedia class titles: "Nanuchka-class corvette" -> "Nanuchka class".
_CLASS_TITLE_RE = re.compile(r"^(.+?)[- ]class\b")
# Wikipedia titles with a generic type word at the end: "T-60 tank" -> "T-60", "M16 rifle" -> "M16".
_TYPE_SUFFIX_RE = re.compile(
    r"\s+(tank|rifle|pistol|revolver|carbine|shotgun|machine gun|submachine gun|light machine gun|heavy machine gun"
    r"|general-purpose machine gun|sniper rifle|assault rifle|battle rifle|anti-materiel rifle|grenade|mortar"
    r"|howitzer|gun|cannon|rocket launcher|grenade launcher|armoured car|armored car|armoured personnel carrier"
    r"|armored personnel carrier|infantry fighting vehicle|tank destroyer|self-propelled gun|self-propelled howitzer"
    r"|vehicle|truck|family|series|weapon system|rocket system|multiple rocket launcher|air defense system"
    r"|air defence system|missile system|submarine|destroyer|frigate|corvette)$",
    re.I,
)
# Parenthetical texts of MilitaryFactory names that are not names ("(class)", "(Series)", "(1944)").
_GENERIC_ALIAS_RE = re.compile(r"^(class|series|family|model|prototype|variants?|\d{4}|[ivx]+|[a-z])$", re.I)
# Words of type descriptions in names ("Cruiser Tank Mk VI Crusader"). A name part of these words only is not a name.
_GENERIC_WORD_RE = re.compile(
    r"^(cruiser|infantry|light|medium|heavy|main|battle|tank|tanks|rifle|gun|machine|sub|submachine|pistol|carbine"
    r"|mk|mark|model|type|no|nr|series|self|propelled|armored|armoured|car|vehicle|assault|sniper|anti|special"
    r"|purpose|general|automatic|semi|service|combat|fighting|personnel|carrier|uboat|boat|submarine|warship|ship"
    r"|destroyer|frigate|corvette|battleship|[ivx]+|[a-z])$", re.I)
# Roman number in a name ("Panzer IV", "Tiger II"): a number for the suffix rule.
_ROMAN_NUMBER_RE = re.compile(r"^[IVX]{1,4}$")

# Document name variants, best first.
RANK_FULL_NAME = 0
RANK_MAIN_NAME = 1
RANK_ALTERNATIVE_NAME = 2
RANK_NAME_SUFFIX = 3
RANK_NAME_PREFIX = 4

# Wikipedia alias kinds, best first.
ALIAS_TITLE = 0
ALIAS_REDIRECT = 0
ALIAS_TITLE_PART = 1
ALIAS_INFOBOX_NAME = 2


# ==============================================================================
# HELPERS
# ==============================================================================

def get_name_key(name: str) -> str:
    """
    Make the join key of a name ("Krag–Jørgensen M1894" -> "kragjorgensenm1894").

    Steps:
        1. Change letters without a decomposition and remove the diacritics.
        2. Use lowercase, change "&" to "and" and remove all characters that are not letters or digits.

    Returns:
        Key; "" for a name without letters and digits.
    """
    # 1. Change letters without a decomposition and remove the diacritics
    decomposed = unicodedata.normalize("NFKD", name.translate(_TRANSLITERATION))
    ascii_name = "".join(char for char in decomposed if not unicodedata.combining(char))

    # 2. Use lowercase, change "&" to "and" and remove all characters that are not letters or digits
    return _NON_KEY_RE.sub("", ascii_name.lower().replace("&", "and"))


def _add_key(keys: Dict[str, int], name: str, rank: int) -> None:
    """
    Add the key of a name with a rank. Keep the best (lowest) rank of a key.

    Steps:
        1. Make the key; skip a key that is too short or only digits.
        2. Keep the lowest rank.

    Returns:
        None.
    """
    # 1. Make the key; skip a key that is too short or only digits
    key = get_name_key(name)
    if len(key) < MIN_KEY_LENGTH or key.isdigit():
        return

    # 2. Keep the lowest rank
    if rank < keys.get(key, 99):
        keys[key] = rank


def split_name(name: str) -> Tuple[str, List[str]]:
    """
    Split a MilitaryFactory name into the main name and the texts in parentheses.

    Steps:
        1. Collect the texts in parentheses and split them at "/".
        2. Remove the parentheses from the name and split it at " / " (take the first part).

    Returns:
        (main name, alternative names).
    """
    # 1. Collect the texts in parentheses and split them at "/"
    alternatives = [part.strip() for group in _PARENTHESES_RE.findall(name) for part in group.split("/")]

    # 2. Remove the parentheses from the name and split it at " / " (take the first part)
    main = _SPACE_RE.sub(" ", _PARENTHESES_RE.sub(" ", name)).strip(" /")
    parts = [part.strip() for part in main.split(" / ") if part.strip()]
    return (parts[0] if parts else main), [part for part in alternatives + parts[1:] if part]


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def build_document_keys(name: str, category: str) -> Dict[str, int]:
    """
    Make the join keys of a MilitaryFactory document from its name.

    Steps:
        1. Add the full name and the main name (without the texts in parentheses).
        2. For ships, add the name without the ship prefix ("KMS Bismarck" -> "Bismarck").
        3. For "X (class)", add "X class".
        4. Add the alternative names in parentheses that are names, not generic words.
           Hull numbers ("CVN-68") are not added alone; they are in the full name.
        5. Add the word suffixes with a number and a name word ("Fabrique Nationale FN M2HB" -> "FN M2HB",
           "M2HB"; "SdKfz 161 Panzer IV" -> "Panzer IV"), the name after the leading type words
           ("Cruiser Tank Comet" -> "Comet") and the last name word ("Merkava Namer" -> "Namer", weak).
        6. Add the word prefixes that are not only type words ("Krag-Jorgensen Model 1894" -> "Krag-Jorgensen").

    Returns:
        Map key -> rank (0 = full name; a higher rank is a weaker variant).
    """
    keys: Dict[str, int] = {}
    main, alternatives = split_name(name)

    # 1. Add the full name and the main name
    _add_key(keys, name, RANK_FULL_NAME)
    _add_key(keys, main, RANK_MAIN_NAME)

    # 2. For ships, add the name without the ship prefix
    if category == "ships":
        without_prefix = _SHIP_PREFIX_RE.sub("", main)
        if without_prefix != main:
            _add_key(keys, without_prefix, RANK_MAIN_NAME)
            hull = [alternative for alternative in alternatives if re.search(r"\d", alternative)]
            if hull:
                _add_key(keys, "{} ({})".format(main, hull[0]), RANK_FULL_NAME)

    # 3. For "X (class)", add "X class"
    if any(alternative.lower() == "class" for alternative in alternatives):
        _add_key(keys, main + " class", RANK_FULL_NAME)

    # 4. Add the alternative names in parentheses that are names, not generic words
    for alternative in alternatives:
        is_hull = category == "ships" and bool(re.match(r"^[A-Z]{0,5}-?\d+$", alternative))
        if not is_hull and not _GENERIC_ALIAS_RE.match(alternative) and len(get_name_key(alternative)) >= 4:
            _add_key(keys, alternative, RANK_ALTERNATIVE_NAME)
            _add_key(keys, main + " " + alternative, RANK_ALTERNATIVE_NAME)

    # 5. Add the word suffixes with a number and a name word, and the name after the leading type words
    words = main.split()
    is_generic = [bool(_GENERIC_WORD_RE.match(get_name_key(word))) for word in words]
    for start in range(1, len(words)):
        suffix_words = words[start:]
        has_number = any(re.search(r"\d", word) or _ROMAN_NUMBER_RE.match(word) for word in suffix_words)
        has_name = any(not is_generic[start + index] and not get_name_key(word).isdigit()
                       for index, word in enumerate(suffix_words))
        if has_number and has_name:
            # "FN M2HB", "Panzer IV"; not "Model 1894" (type word and number only).
            _add_key(keys, " ".join(suffix_words), RANK_NAME_SUFFIX)
    if is_generic and is_generic[0] and not all(is_generic):
        # "Cruiser Tank Mk VI Crusader" -> "Crusader".
        first_name_word = is_generic.index(False)
        _add_key(keys, " ".join(words[first_name_word:]), RANK_NAME_SUFFIX)
    last_word = words[-1] if words else ""
    if len(words) > 1 and re.match(r"^[A-Za-z]{5,}$", last_word) and not is_generic[-1]:
        # "Merkava Namer" -> "Namer" (weak variant; the score must confirm it).
        _add_key(keys, last_word, RANK_NAME_PREFIX)

    # 6. Add the word prefixes that are not only type words
    for end in range(len(words) - 1, 0, -1):
        prefix = " ".join(words[:end])
        if len(get_name_key(prefix)) >= 5 and not all(is_generic[:end]):
            _add_key(keys, prefix, RANK_NAME_PREFIX)
    return keys


def build_title_aliases(title: str, is_redirect: bool) -> Dict[str, int]:
    """
    Make the alias keys of a Wikipedia title (a page title or a redirect title).

    Steps:
        1. Add the title itself.
        2. Add the title without the text in parentheses ("Comet (tank)" -> "Comet").
        3. Add the name part of a ship title ("German battleship Bismarck" -> "Bismarck").
        4. Add "X class" for a class title ("Nanuchka-class corvette").
        5. Add the title without a generic type word at the end ("T-60 tank" -> "T-60").

    Returns:
        Map key -> alias rank (0 = title or redirect).
    """
    keys: Dict[str, int] = {}
    # 1. Add the title itself
    _add_key(keys, title, ALIAS_REDIRECT if is_redirect else ALIAS_TITLE)

    # 2. Add the title without the text in parentheses
    base = _SPACE_RE.sub(" ", _PARENTHESES_RE.sub(" ", title)).strip()
    _add_key(keys, base, ALIAS_TITLE_PART)

    # 3. Add the name part of a ship title
    ship = _SHIP_TITLE_RE.match(base)
    if ship:
        _add_key(keys, ship.group(1), ALIAS_TITLE_PART)

    # 4. Add "X class" for a class title
    ship_class = _CLASS_TITLE_RE.match(base)
    if ship_class:
        _add_key(keys, ship_class.group(1) + " class", ALIAS_TITLE_PART)

    # 5. Add the title without a generic type word at the end
    without_type = _TYPE_SUFFIX_RE.sub("", base)
    if without_type != base:
        _add_key(keys, without_type, ALIAS_TITLE_PART)
    return keys
