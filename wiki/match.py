# ==============================================================================
# JOIN MATCH — MilSearch
# ==============================================================================
# PURPOSE:
#     Decide which Wikipedia page belongs to a MilitaryFactory document, and select the
#     wiki attributes that MilitaryFactory does not have.
#
# RESPONSIBILITIES:
#     1. Map the infobox parameters of weapon, vehicle and ship pages to MilSearch attribute names.
#     2. Get the years and the countries of a wiki page for the check of a join candidate.
#     3. Compute the score of a join candidate from the name match, the page kind, the year and the country.
#
# INVARIANTS:
#     Python 3.6 syntax (Spark image of the course).
#     A candidate with a score below MIN_MATCH_SCORE is not a join result.
# ==============================================================================

import re
from typing import Dict, Iterable, List, Optional

# ==============================================================================
# CONSTANTS
# ==============================================================================

# MilSearch attribute <- infobox parameters (the first parameter with a value wins).
# Ship infoboxes of old pages use the prefix "ship_" ("ship_laid_down"); get_wiki_attributes removes it.
WIKI_ATTRIBUTE_PARAMETERS = (
    ("origin", ("origin", "place_of_origin", "country", "nationality")),
    ("designer", ("designer", "designers", "design")),
    ("design_date", ("design_date", "designed")),
    ("manufacturer", ("manufacturer", "manufacturers", "builder", "builders")),
    ("unit_cost", ("unit_cost", "cost")),
    ("production_date", ("production_date", "built_range", "produced")),
    ("number_built", ("number", "number_built", "total_ships_completed", "built", "no_built")),
    ("service", ("service", "in_service", "in_service_range", "in_commission_range")),
    ("used_by", ("used_by", "operators", "operator", "users")),
    ("wars", ("wars", "battles", "conflicts")),
    ("variants", ("variants",)),
    ("ordered", ("ordered",)),
    ("laid_down", ("laid_down",)),
    ("launched", ("launched",)),
    ("commissioned", ("commissioned", "in_service_date", "acquired")),
    ("decommissioned", ("decommissioned", "out_of_service", "struck")),
    ("fate", ("fate", "disposal")),
    ("status", ("status",)),
    ("ship_class", ("class", "class_and_type")),
    ("cartridge", ("cartridge", "caliber", "calibre")),
    ("action", ("action",)),
    ("weight", ("weight", "mass")),
    ("length", ("length",)),
    ("crew", ("crew", "complement")),
    ("armour", ("armour", "armor")),
    ("engine", ("engine", "propulsion", "power")),
    ("speed", ("speed", "velocity", "max_speed")),
    ("range", ("range", "vehicle_range", "effective_range")),
    ("displacement", ("displacement",)),
    ("armament", ("primary_armament", "armament", "secondary_armament")),
)
# Parameters with dates: the years in these values describe the life of the object.
_DATE_PARAMETERS = ("design_date", "production_date", "service", "laid_down", "launched", "commissioned",
                    "ordered", "decommissioned")
_YEAR_RE = re.compile(r"\b(1[5-9]\d\d|20[0-4]\d)\b")
_SHIP_PREFIX_PARAMETER_RE = re.compile(r"^ship_")

# Page kinds: infobox kinds and category words of ships and of weapons and vehicles.
_SHIP_KIND_RE = re.compile(r"ship|submarine|boat", re.I)
_WEAPON_KIND_RE = re.compile(r"weapon|firearm|gun|artillery|vehicle|afv|tank|grenade|mortar|rocket|torpedo", re.I)
_SHIP_CATEGORY_RE = re.compile(
    r"\b(ships|submarines|battleships|battlecruisers|cruisers|destroyers|frigates|corvettes|aircraft carriers"
    r"|warships|ship classes|minesweepers|gunboats|patrol vessels|boats)\b", re.I)
# Weapon pages: small arms (MilitaryFactory "smallarms") and heavy equipment (part of "armor").
# Mortars, artillery guns and launchers match neither; they stay "weapon".
_SMALL_ARM_TYPE_RE = re.compile(
    r"\b(rifles?|pistols?|carbines?|submachine guns?|revolvers?|shotguns?|machine guns?|personal defen[cs]e weapons?)\b",
    re.I)
_HEAVY_TYPE_RE = re.compile(
    r"(?<!anti-)\btanks?\b|\barmou?red\b|\bvehicles?\b|self-propelled|\bhowitzers?\b|\bfield guns?\b|\btrucks?\b"
    r"|multiple rocket", re.I)
# Ship index pages and disambiguation pages list several objects with one name ("USS Arizona").
_INDEX_CATEGORY_RE = re.compile(r"set index|disambiguation|^lists? of", re.I)
# Articles about a type of equipment ("Medium tank", "Dreadnought"), not about one object.
_TYPE_CATEGORY_RE = re.compile(r"\bby type\b|^\w+ types$|^types of\b", re.I)
# Infoboxes of things that are not equipment: a company ("Mauser"), an event, a person, an occupation.
_OTHER_KIND_RE = re.compile(r"company|organi[sz]ation|person|officeholder|military unit|conflict|event|occupation"
                            r"|settlement|country|manufacturer|civil conflict", re.I)
# Ship types in a MilitaryFactory type and in a wiki title ("JS Soryu" is a submarine, not a carrier).
_SHIP_TYPE_RE = re.compile(r"\b(carrier|battleship|battlecruiser|cruiser|destroyer|frigate|corvette|submarine|u-boat"
                           r"|minesweeper|gunboat|monitor|sloop|tender|tanker|oiler|cutter)\b", re.I)
_SHIP_TYPE_SYNONYMS = {"u-boat": "submarine"}
# Weapon types in a MilitaryFactory type and in the infobox type ("Denel M4 Commando" is a mortar, not a carbine).
_WEAPON_TYPE_RE = re.compile(r"\b(mortar|pistol|revolver|rifle|carbine|shotgun|machine gun|submachine gun|grenade"
                             r"|launcher|howitzer|tank|flamethrower|cannon|missile|torpedo)s?\b", re.I)
_WEAPON_TYPE_SYNONYMS = {"carbine": "rifle", "revolver": "pistol"}
# Hull number or year in parentheses of a title: "USS Montgomery (DD-121)", "HMS Audacious (1912)".
_PARENTHESES_WITH_DIGIT_RE = re.compile(r"\(([^()]*\d[^()]*)\)")
_NON_KEY_RE = re.compile(r"[^a-z0-9]+")
_WEAPON_CATEGORY_RE = re.compile(
    r"\b(rifles|pistols|revolvers|carbines|shotguns|machine guns|submachine guns|grenades|mortars|firearms"
    r"|rocket launchers|grenade launchers|tanks|armoured|armored|artillery|howitzers|military vehicles"
    r"|infantry fighting vehicles|military trucks|weapons)\b", re.I)

# Country name -> words that a wiki page uses for it (title, origin, operators, categories).
_COUNTRY_WORDS = {
    "United States": ("United States", "American", "U.S.", "USA", "US Navy", "U.S. Navy"),
    "United Kingdom": ("United Kingdom", "British", "Royal Navy", "England", "English"),
    "Germany": ("Germany", "German", "Kriegsmarine", "Wehrmacht", "Bundeswehr", "Prussia"),
    "Soviet Union": ("Soviet", "USSR", "Russia", "Russian"),
    "Russia": ("Russia", "Russian", "Soviet"),
    "Japan": ("Japan", "Japanese"),
    "France": ("France", "French"),
    "Italy": ("Italy", "Italian", "Regia Marina"),
    "China": ("China", "Chinese", "People's Liberation Army"),
    "Spain": ("Spain", "Spanish"),
    "Sweden": ("Sweden", "Swedish"),
    "Norway": ("Norway", "Norwegian"),
    "Finland": ("Finland", "Finnish"),
    "Netherlands": ("Netherlands", "Dutch"),
    "Belgium": ("Belgium", "Belgian"),
    "Austria": ("Austria", "Austrian"),
    "Austria-Hungary": ("Austria-Hungary", "Austro-Hungarian", "Austrian", "Hungarian"),
    "Switzerland": ("Switzerland", "Swiss"),
    "Czechoslovakia": ("Czechoslovakia", "Czechoslovak", "Czech"),
    "Czech Republic": ("Czech",),
    "Yugoslavia": ("Yugoslavia", "Yugoslav", "Serbia", "Serbian"),
    "Poland": ("Poland", "Polish"),
    "Israel": ("Israel", "Israeli"),
    "Turkey": ("Turkey", "Turkish", "Ottoman"),
    "India": ("India", "Indian"),
    "South Korea": ("South Korea", "Korean", "Republic of Korea"),
    "North Korea": ("North Korea", "Korean"),
    "Australia": ("Australia", "Australian"),
    "Canada": ("Canada", "Canadian"),
    "Brazil": ("Brazil", "Brazilian"),
    "Argentina": ("Argentina", "Argentine", "Argentinian"),
    "Ukraine": ("Ukraine", "Ukrainian"),
    "South Africa": ("South Africa", "South African"),
    "Iran": ("Iran", "Iranian"),
    "Greece": ("Greece", "Greek", "Hellenic"),
    "Denmark": ("Denmark", "Danish"),
    "Hungary": ("Hungary", "Hungarian"),
    "Romania": ("Romania", "Romanian"),
    "Portugal": ("Portugal", "Portuguese"),
    "Singapore": ("Singapore", "Singaporean"),
    "Taiwan": ("Taiwan", "Taiwanese", "Republic of China"),
    "Croatia": ("Croatia", "Croatian"),
    "Egypt": ("Egypt", "Egyptian"),
    "Chile": ("Chile", "Chilean"),
    "Pakistan": ("Pakistan", "Pakistani"),
    "Indonesia": ("Indonesia", "Indonesian"),
    "Thailand": ("Thailand", "Thai"),
    "Vietnam": ("Vietnam", "Vietnamese"),
    "Mexico": ("Mexico", "Mexican"),
}

MIN_MATCH_SCORE = 8
_BASE_SCORE = 10
_RANK_PENALTY = 2


# ==============================================================================
# HELPERS
# ==============================================================================

def get_country_words(country: Optional[str]) -> List[str]:
    """
    Get the words that a wiki page uses for a country.

    Steps:
        1. Use the word list of the country; without a list, use the country name.

    Returns:
        List of words; empty without a country.
    """
    # 1. Use the word list of the country; without a list, use the country name
    if not country:
        return []
    return list(_COUNTRY_WORDS.get(country, (country,)))


def get_wiki_years(fields: Dict[str, str]) -> List[int]:
    """
    Get the years from the date parameters of an infobox.

    Steps:
        1. Find all years in the date parameters (with and without the "ship_" prefix).

    Returns:
        Sorted list of years.
    """
    # 1. Find all years in the date parameters
    years = set()
    for key, value in fields.items():
        name = _SHIP_PREFIX_PARAMETER_RE.sub("", key)
        if name in _DATE_PARAMETERS or name.endswith(("_date", "_range")):
            years.update(int(year) for year in _YEAR_RE.findall(value))
    return sorted(years)


def get_weapon_kind(type_text: str, categories: Iterable[str]) -> str:
    """
    Get the kind of a weapon page from the infobox type and the categories.

    Steps:
        1. Search the small arms words and the heavy equipment words.
        2. Return the kind when only one group matches.

    Returns:
        "small_arm", "heavy" or "weapon" (both or none).
    """
    # 1. Search the small arms words and the heavy equipment words
    text = " | ".join([type_text] + list(categories))
    is_small_arm, is_heavy = bool(_SMALL_ARM_TYPE_RE.search(text)), bool(_HEAVY_TYPE_RE.search(text))

    # 2. Return the kind when only one group matches
    if is_small_arm and not is_heavy:
        return "small_arm"
    if is_heavy and not is_small_arm:
        return "heavy"
    return "weapon"


def get_page_kind(infobox_kinds: Iterable[str], categories: Iterable[str], type_text: str = "",
                  title: str = "") -> Optional[str]:
    """
    Get the kind of a wiki page: "index", "type", "other", "ship", "small_arm", "heavy", "weapon" or None.

    Steps:
        1. Mark ship index pages and disambiguation pages.
        2. Use the infobox kinds; split weapons into small arms and heavy equipment.
        3. Mark infoboxes of other things (company, event, person).
        4. Without an infobox, mark articles about a type of equipment: a "by type" category or the
           page is the main article of its own category ("Grenade" in "Grenades").
        5. Use the categories.

    Returns:
        "index", "type", "other", "ship", "small_arm", "heavy", "weapon" or None.
    """
    # 1. Mark ship index pages and disambiguation pages
    categories, infobox_kinds = list(categories), list(infobox_kinds)
    if "index" in infobox_kinds or any(_INDEX_CATEGORY_RE.search(category) for category in categories):
        return "index"

    # 2. Use the infobox kinds; split weapons into small arms and heavy equipment
    kinds_text = " ".join(infobox_kinds)
    if _SHIP_KIND_RE.search(kinds_text):
        return "ship"
    if _WEAPON_KIND_RE.search(kinds_text):
        return get_weapon_kind(type_text, categories)

    # 3. Mark infoboxes of other things (company, event, person)
    if _OTHER_KIND_RE.search(kinds_text):
        return "other"

    # 4. Without an infobox, mark articles about a type of equipment
    title_key = _NON_KEY_RE.sub("", re.sub(r"\s*\([^()]*\)", "", title).lower())
    category_keys = {_NON_KEY_RE.sub("", category.lower()) for category in categories}
    if any(_TYPE_CATEGORY_RE.search(category) for category in categories) or \
            (title_key and {title_key + "s", title_key + "es"} & category_keys):
        return "type"

    # 5. Use the categories
    categories_text = " | ".join(categories)
    if _SHIP_CATEGORY_RE.search(categories_text):
        return "ship"
    if _WEAPON_CATEGORY_RE.search(categories_text):
        return get_weapon_kind(type_text, categories)
    return None


def get_ship_types(text: str) -> List[str]:
    """
    Get the ship type words of a text ("Diesel-Electric Attack Submarine" -> ["submarine"]).

    Steps:
        1. Find the ship type words; change synonyms to one word.

    Returns:
        Sorted list of type words without duplicates.
    """
    # 1. Find the ship type words; change synonyms to one word
    words = (word.lower() for word in _SHIP_TYPE_RE.findall(text or ""))
    return sorted({_SHIP_TYPE_SYNONYMS.get(word, word) for word in words})


def get_weapon_types(text: str) -> List[str]:
    """
    Get the weapon type words of a text ("Light Machine Gun (LMG)" -> ["machine gun"]).

    Steps:
        1. Find the weapon type words; change synonyms to one word.

    Returns:
        Sorted list of type words without duplicates.
    """
    # 1. Find the weapon type words; change synonyms to one word
    words = (word.lower() for word in _WEAPON_TYPE_RE.findall(text or ""))
    return sorted({_WEAPON_TYPE_SYNONYMS.get(word, word) for word in words})


def get_parenthesis_keys(text: str) -> List[str]:
    """
    Get the keys of the texts with digits in parentheses ("USS Nimitz (CVN-68)" -> ["cvn68"]).

    Steps:
        1. Find the texts in parentheses with a digit; make lowercase keys of letters and digits.

    Returns:
        List of keys.
    """
    # 1. Find the texts in parentheses with a digit; make lowercase keys of letters and digits
    return [_NON_KEY_RE.sub("", group.lower()) for group in _PARENTHESES_WITH_DIGIT_RE.findall(text or "")]


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def get_wiki_attributes(fields: Dict[str, str]) -> Dict[str, str]:
    """
    Map the infobox parameters of a page to the MilSearch attribute names.

    Steps:
        1. Remove the "ship_" prefix of old ship infoboxes.
        2. For each attribute, take the first parameter with a value.

    Returns:
        Map attribute -> plain value.
    """
    # 1. Remove the "ship_" prefix of old ship infoboxes
    plain_fields: Dict[str, str] = {}
    for key, value in fields.items():
        plain_fields.setdefault(_SHIP_PREFIX_PARAMETER_RE.sub("", key), value)

    # 2. For each attribute, take the first parameter with a value
    attributes: Dict[str, str] = {}
    for attribute, parameters in WIKI_ATTRIBUTE_PARAMETERS:
        for parameter in parameters:
            value = plain_fields.get(parameter)
            if value and not value.lower().startswith("see "):
                attributes[attribute] = value
                break
    return attributes


def compute_match_score(document_rank: int, alias_rank: int, category: str, year: Optional[int],
                        country: Optional[str], page_kind: Optional[str], wiki_years: List[int],
                        country_text: str, document_name: str = "", document_type: str = "",
                        page_title: str = "", page_type: str = "") -> int:
    """
    Compute the score of a join candidate (document, wiki page).

    Steps:
        1. Start from the base score; subtract a penalty for weak name variants on both sides.
        2. Add points for a page of the same kind (ship page for a ship, no heavy equipment for a small arm);
           subtract points for another kind.
        3. Add points for a close year; subtract points for a year that is far away.
           A weak name match (rank sum 2 or more) without a year within 10 years loses points.
        4. Add points when the page names the country of the document.
        5. Subtract points for another hull number or year in the title, and for another ship type
           (page title) or weapon type (infobox type).

    Returns:
        Score; a candidate is accepted with MIN_MATCH_SCORE or more.
    """
    # 1. Start from the base score; subtract a penalty for weak name variants on both sides
    score = _BASE_SCORE - _RANK_PENALTY * (document_rank + alias_rank)

    # 2. Add points for a page of the same kind; subtract points for another kind
    # Small arms are not heavy equipment, and the equipment of "armor" is not a small arm.
    expected_kinds = {"ships": ("ship",), "smallarms": ("small_arm", "weapon"), "armor": ("heavy", "weapon")}
    is_exact_name = document_rank == 0 and alias_rank == 0
    if page_kind in expected_kinds.get(category, ("small_arm", "heavy", "weapon")):
        score += 3
    elif page_kind == "type" and is_exact_name:
        pass  # A document about a type ("Carrack") belongs to the type article.
    elif page_kind in ("index", "type"):
        score -= 10
    elif page_kind is not None:
        score -= 6

    # 3. Add points for a close year; subtract points for a year that is far away.
    #    A weak name match (rank sum 2 or more) needs a year within 10 years.
    distance = min(abs(wiki_year - year) for wiki_year in wiki_years) if year and wiki_years else None
    if distance is not None and distance <= 3:
        score += 3
    elif distance is not None and distance <= 10:
        score += 1
    elif distance is not None and distance > 30:
        score -= 5
    if document_rank + alias_rank >= 2 and (distance is None or distance > 10):
        score -= 3

    # 4. Add points when the page names the country of the document
    words = get_country_words(country)
    if words and any(re.search(r"\b" + re.escape(word) + r"\b", country_text) for word in words):
        score += 2

    # 5. Subtract points for another hull number or year in the title, and for another ship type
    page_parentheses = get_parenthesis_keys(page_title)
    if page_parentheses and not set(page_parentheses) & set(get_parenthesis_keys(document_name)):
        score -= 10
    if category == "ships":
        document_types, page_types = get_ship_types(document_type), get_ship_types(page_title)
    else:
        document_types, page_types = get_weapon_types(document_type), get_weapon_types(page_type)
    if document_types and page_types and not set(document_types) & set(page_types):
        score -= 4
    return score
