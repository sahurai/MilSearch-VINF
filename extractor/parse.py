# ==============================================================================
# DETAIL PAGE EXTRACTOR — MilSearch
# ==============================================================================
# PURPOSE:
#     Extract structured data from the downloaded MilitaryFactory detail pages.
#     Use only regular expressions. Do not use an HTML parser or XPath.
#
# RESPONSIBILITIES:
#     1. Extract the title block: name, type, country of origin, year, summary.
#     2. Extract the spec sections and map the site labels to MilSearch field names.
#     3. Extract armament, variants, ships in class, operators, manufacturer and site categories.
#     4. Normalize the country of origin and the operator countries.
#     5. Compute numeric fields for range queries. Reject values out of the plausible range.
#     6. Write one JSON line for each detail page to data/parsed/<category>.jsonl.
#
# DEPENDENCIES:
#     crawler.config — data paths, categories.
#     crawler.storage — project-relative paths.
#     extractor.countries — canonical country names.
#
# USAGE:
#     python -m extractor.parse
# ==============================================================================

from __future__ import annotations

import html
import json
import logging
import re
from typing import Any
from urllib.parse import unquote

from crawler.config import CATEGORIES, PARSED_DIR, RAW_DIR
from crawler.storage import get_relative_path
from extractor.countries import get_canonical_countries, get_canonical_country

# ==============================================================================
# CONSTANTS
# ==============================================================================

logger = logging.getLogger("extractor")

# ------------------------------------------------------------------------------
# Title block
# ------------------------------------------------------------------------------
_NAME_RE = re.compile(r'<h1 class="textJumbo"><span class="textBold">(.*?)</span></h1>', re.S)
_TYPE_RE = re.compile(r'<h2 class="textLarge">(.*?)</h2>', re.S)
# Example: <h3 class="textLarger textBold">United States | 1983</h3>
_ORIGIN_RE = re.compile(r'<h3 class="textLarger textBold">\s*(.*?)\s*\|\s*([^<]*?)\s*</h3>', re.S)
_QUOTE_RE = re.compile(r'<h4 class="textWhite textItalics textLarge">"?(.*?)"?</h4>', re.S)
_CANONICAL_RE = re.compile(r'<link rel="canonical" href="([^"]+)"', re.I)

# ------------------------------------------------------------------------------
# Body text: <!--BODYTEXT1--> ... <!--BODYTEXT1-->, <!--BODYTEXT2--> ...
# ------------------------------------------------------------------------------
_BODY_RE = re.compile(r"<!--(BODYTEXT\d+)-->(.*?)<!--\1-->", re.S)

# ------------------------------------------------------------------------------
# Sections: "Physical", "Performance", "Armament", "Variants", "Operators", ...
# ------------------------------------------------------------------------------
_SECTION_RE = re.compile(r'<span class="textJumbo textBold">([^<]+)</span>')
_SPEC_BLOCK_RE = re.compile(r'<div class="specContainerMain[^"]*"[^>]*>(.*?)</div>', re.S)
# An empty separator <div ...></div> inside a spec block stops _SPEC_BLOCK_RE too early.
_EMPTY_DIV_RE = re.compile(r"<div[^>]*>\s*</div>")
_SPAN_RE = re.compile(r"<span[^>]*>(.*?)</span>", re.S)
_LABELED_VALUE_RE = re.compile(r'<span class="textBold">([^<:]+):</span>\s*([^<]+)</span>')
_NATION_RE = re.compile(r'by-country\.php\?Nation=([^"&]+)"')
_CONFLICT_BLOCK_RE = re.compile(r"<!--CONFLICTBANNERS-->(.*?)<!--CONFLICTBANNERS-->", re.S)
_TITLE_ATTRIBUTE_RE = re.compile(r'title="([^"]+)"')
# Role tags of the sections "Design Qualities" and "Mission Roles", for example "MAIN BATTLE TANK".
_ROLE_RE = re.compile(r'<div class="roleContainers[^"]*">\s*([^<]+?)\s*</div>')
_SECTION_COUNT_SUFFIX_RE = re.compile(r"\s*\(\d+\)$")  # "Ships-in-Class (2)" -> "Ships-in-Class"

# ------------------------------------------------------------------------------
# Text cleanup
# ------------------------------------------------------------------------------
_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
_NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")

# ------------------------------------------------------------------------------
# Field mapping
# ------------------------------------------------------------------------------
# These sections do not contain data about the object.
_SKIP_SECTIONS: frozenset[str] = frozenset({"Recognition", "Going Further..."})
_ARMAMENT_SECTIONS: tuple[str, ...] = ("Armament", "Armament & Ammunition")

# Label on MilitaryFactory -> field name in MilSearch.
_FIELD_NAMES_BY_LABEL: dict[str, dict[str, str]] = {
    "smallarms": {
        "Caliber(s)": "caliber", "Muzzle Velocity": "muzzle_velocity",
        "Max.Eff.Range": "effective_range", "Weight": "weight", "Action": "action",
        "Rate-of-Fire": "rate_of_fire", "Feed": "feed", "O/A Length": "length",
        "Barrel Length": "barrel_length",
    },
    "armor": {
        "Crew": "crew", "Road Speed": "max_speed", "Range": "range",
        "Installed Power": "engine", "Weight": "weight", "O/A Length": "length",
        "O/A Width": "width", "O/A Height": "height",
    },
    "ships": {
        "Displacement": "displacement", "O/A Length": "length", "Beam": "beam",
        "Draught": "draught", "Surface Speed": "speed", "Submerged Speed": "submerged_speed",
        "Range": "range", "Complement": "crew", "Propulsion": "propulsion",
    },
}

# ------------------------------------------------------------------------------
# Numeric fields
# ------------------------------------------------------------------------------
# (numeric field, text field, unit regex after the number or None for the first number,
#  (minimum, maximum)). A value out of the range is an error on the site and becomes None.
# The ranges are wide: they include the extreme real objects of each category.
_HORSEPOWER_UNIT = r"(?:shaft\s+)?(?:horsepower|hp)\b"
_NUMERIC_FIELDS: dict[str, tuple[tuple[str, str, str | None, tuple[float, float]], ...]] = {
    "smallarms": (
        ("length_mm", "length", r"mm\b", (50, 20_000)),                  # Pistol to heavy mortar.
        ("barrel_length_mm", "barrel_length", r"mm\b", (20, 15_000)),
        ("weight_kg", "weight", r"kg\b", (0.1, 5_000)),                  # Heavy mortars weigh approx. 1.7 t.
        ("effective_range_m", "effective_range", r"m(?![a-z/])", (1, 100_000)),
        ("muzzle_velocity_mps", "muzzle_velocity", r"m/sec", (30, 2_000)),
        ("rate_of_fire_rpm", "rate_of_fire", None, (1, 10_000)),         # Rotary guns fire approx. 6,000 rpm.
    ),
    "armor": (
        ("max_speed_kmh", "max_speed", r"kph\b", (1, 150)),
        ("range_km", "range", r"km\b", (1, 5_000)),
        ("crew_count", "crew", None, (1, 100)),
        ("engine_hp", "engine", _HORSEPOWER_UNIT, (5, 10_000)),
        ("weight_kg", "weight", r"kg\b", (100, 300_000)),                # The heaviest tank (Maus) is 188 t.
        ("length_m", "length", r"meters\b", (0.5, 60)),
        ("width_m", "width", r"meters\b", (0.5, 20)),
        ("height_m", "height", r"meters\b", (0.5, 20)),
    ),
    "ships": (
        ("displacement_tons", "displacement", None, (1, 150_000)),       # Largest carriers approx. 100,000 t.
        ("length_m", "length", r"meters\b", (3, 500)),
        ("beam_m", "beam", r"meters\b", (1, 100)),
        ("draught_m", "draught", r"meters\b", (0.3, 40)),
        ("speed_kts", "speed", r"kts\b", (1, 60)),                        # Fastest warships approx. 45 kts.
        ("submerged_speed_kts", "submerged_speed", r"kts\b", (1, 50)),
        ("range_km", "range", r"km\b", (5, 200_000)),
        ("crew_count", "crew", None, (1, 10_000)),
    ),
}
_UNLIMITED_RE = re.compile(r"unlimited", re.I)
_LIST_SEPARATOR_RE = re.compile(r"\s*;\s*")


# ==============================================================================
# TYPES
# ==============================================================================

Document = dict[str, Any]  # Any: a document holds str, int, float, list and dict values.
SectionItem = dict[str, str | None]


# ==============================================================================
# HELPERS
# ==============================================================================

def _get_plain_text(fragment: str) -> str:
    """
    Remove the HTML tags and entities from a fragment. Keep the line breaks.

    Steps:
        1. Replace <br> with line breaks, remove tags and decode entities.
        2. Collapse whitespace on each line and remove empty lines.

    Returns:
        Plain text; lines are separated by "\\n".
    """
    # 1. Replace <br> with line breaks, remove tags and decode entities
    text = html.unescape(_TAG_RE.sub(" ", _BR_RE.sub("\n", fragment)))

    # 2. Collapse whitespace on each line and remove empty lines
    lines = (_SPACE_RE.sub(" ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def _get_first_match(pattern: re.Pattern[str], page: str) -> str | None:
    """
    Get the plain text of group 1 of the first match.

    Steps:
        1. Search the page and clean group 1.

    Returns:
        Plain text; None when the pattern does not match.
    """
    # 1. Search the page and clean group 1
    match = pattern.search(page)
    return _get_plain_text(match.group(1)) if match else None


def _get_number(value: str | None) -> float | None:
    """
    Get the first number from a text such as "10,000 Units".

    Steps:
        1. Find the first number and remove the thousands separators.

    Returns:
        Number; None when the text has no number.
    """
    # 1. Find the first number and remove the thousands separators
    match = _NUMBER_RE.search(value or "")
    return float(match.group().replace(",", "")) if match else None


def _get_unit_number(value: str | None, unit_pattern: str | None) -> float | None:
    """
    Get the number in front of a unit, for example "42 mph (67 kph)" and r"kph\\b" -> 67.0.

    Steps:
        1. Without a unit pattern, get the first number.
        2. With a unit pattern, find the number that the unit follows.

    Returns:
        Number; None when the text does not contain the number or the unit.
    """
    # 1. Without a unit pattern, get the first number
    if unit_pattern is None:
        return _get_number(value)

    # 2. With a unit pattern, find the number that the unit follows
    match = re.search(rf"(\d[\d.,]*)\s*(?:{unit_pattern})", value or "")
    return _get_number(match.group(1)) if match else None


def _compute_numeric_fields(document: Document, category: str) -> list[str]:
    """
    Add the numeric fields of a category to a document. Reject values out of the plausible range.

    Steps:
        1. Get the number of each numeric field from its text field.
        2. Set a value out of the plausible range to None and remember the field name.
        3. Mark a range that the site gives as "unlimited".

    Returns:
        Names of the numeric fields with a rejected value.
    """
    implausible_fields: list[str] = []
    for field_name, source_field, unit_pattern, (min_value, max_value) in _NUMERIC_FIELDS[category]:
        # 1. Get the number of each numeric field from its text field
        number = _get_unit_number(document.get(source_field), unit_pattern)

        # 2. Set a value out of the plausible range to None and remember the field name
        if number is not None and not min_value <= number <= max_value:
            implausible_fields.append(field_name)
            number = None
        document[field_name] = number

    # 3. Mark a range that the site gives as "unlimited"
    if "range" in document:
        document["is_range_unlimited"] = bool(_UNLIMITED_RE.search(document["range"] or ""))
    return implausible_fields


def _build_section_item(block: str) -> SectionItem | None:
    """
    Build one item from a spec block.

    Steps:
        1. Get the plain text of each <span> in the block.
        2. Build a labeled value from two or more spans, or a list item from one span.

    Returns:
        {"label", "value", "alt"} or {"text"}; None for an empty block.
    """
    # 1. Get the plain text of each <span> in the block
    spans = [text for text in (_get_plain_text(span) for span in _SPAN_RE.findall(block)) if text]

    # 2. Build a labeled value from two or more spans, or a list item from one span
    if len(spans) >= 2:
        # Span order: value | value in other units (optional) | label.
        return {"label": spans[-1], "value": spans[0], "alt": spans[1].strip("()") if len(spans) > 2 else None}
    if spans:
        return {"text": spans[0]}
    return None


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def extract_sections(page: str) -> tuple[dict[str, list[SectionItem]], dict[str, str]]:
    """
    Cut a page at the section headings and extract the spec blocks of each section.

    Steps:
        1. Find all section headings.
        2. Cut the HTML between two headings and remove the empty separator divs.
        3. Build one item from each spec block of the section.

    Returns:
        (items by section title, raw HTML by section title).
    """
    sections: dict[str, list[SectionItem]] = {}
    chunks: dict[str, str] = {}

    # 1. Find all section headings
    headings = list(_SECTION_RE.finditer(page))
    for index, heading in enumerate(headings):
        title = _SECTION_COUNT_SUFFIX_RE.sub("", _get_plain_text(heading.group(1)))
        if title in _SKIP_SECTIONS:
            continue

        # 2. Cut the HTML between two headings and remove the empty separator divs
        end = headings[index + 1].start() if index + 1 < len(headings) else len(page)
        chunks[title] = _EMPTY_DIV_RE.sub("<br />", page[heading.end():end])

        # 3. Build one item from each spec block of the section
        items = [_build_section_item(block) for block in _SPEC_BLOCK_RE.findall(chunks[title])]
        sections[title] = [item for item in items if item]
    return sections, chunks


def extract_document(page: str, category: str, source_file: str) -> Document | None:
    """
    Extract one MilSearch document from a detail page.

    Steps:
        1. Extract the title block.
        2. Extract the spec sections and collect the labeled values.
        3. Map the site labels to MilSearch field names.
        4. Extract armament, variants, ships in class, roles, operators, manufacturer and site categories.
        5. Normalize the country of origin and the operator countries.
        6. Compute the numeric fields and reject implausible values.
        7. Extract the body text.

    Returns:
        Document; None when the page has no title block (it is not a detail page).
    """
    # 1. Extract the title block
    name = _get_first_match(_NAME_RE, page)
    if not name:
        return None
    origin = _ORIGIN_RE.search(page)
    year_text = _get_plain_text(origin.group(2)) if origin else ""
    document: Document = {
        "id": source_file.rsplit("/", 1)[-1].removesuffix(".html"),
        "category": category,
        "url": _get_first_match(_CANONICAL_RE, page),
        "name": name,
        "type": _get_first_match(_TYPE_RE, page),
        "country_original": _get_plain_text(origin.group(1)) if origin else None,
        "year": int(year_text) if year_text.isdigit() else None,
        "summary": _get_first_match(_QUOTE_RE, page),
    }

    # 2. Extract the spec sections and collect the labeled values
    sections, chunks = extract_sections(page)
    specs = {
        item["label"]: item["value"] + (f" ({item['alt']})" if item["alt"] else "")
        for items in sections.values() for item in items if "label" in item
    }

    # 3. Map the site labels to MilSearch field names
    for label, field_name in _FIELD_NAMES_BY_LABEL[category].items():
        document[field_name] = specs.get(label)

    # 4. Extract armament, variants, ships in class, roles, operators, manufacturer and site categories
    document["armament"] = [
        item["text"] for title in _ARMAMENT_SECTIONS for item in sections.get(title, []) if "text" in item
    ]
    # The site puts all variants in one block. Each line is one variant.
    document["variants"] = [
        line for item in sections.get("Variants", []) if "text" in item for line in item["text"].split("\n")
    ]
    if category == "ships":
        # The site puts all ships of the class in one block, separated by ";".
        document["ships_in_class"] = [
            ship for item in sections.get("Ships-in-Class", []) if "text" in item
            for ship in _LIST_SEPARATOR_RE.split(item["text"].replace("\n", ";")) if ship
        ]
    document["roles"] = [html.unescape(role) for role in _ROLE_RE.findall(page)]
    operators_original = sorted({
        html.unescape(unquote(nation)) for nation in _NATION_RE.findall(chunks.get("Operators", ""))
    })
    labeled_values = {label.strip(): _get_plain_text(value) for label, value in _LABELED_VALUE_RE.findall(page)}
    document["manufacturer"] = labeled_values.get("Contractor(s)")
    document["total_production"] = _get_number(labeled_values.get("Total Production"))
    banners = _CONFLICT_BLOCK_RE.search(page)
    document["site_categories"] = _TITLE_ATTRIBUTE_RE.findall(banners.group(1)) if banners else []

    # 5. Normalize the country of origin and the operator countries
    document["country"] = get_canonical_country(document["country_original"])
    document["operators"] = get_canonical_countries(operators_original)
    document["operators_original"] = operators_original

    # 6. Compute the numeric fields and reject implausible values
    document["implausible_fields"] = _compute_numeric_fields(document, category)
    document["specs"] = specs

    # 7. Extract the body text
    document["text"] = "\n\n".join(_get_plain_text(body) for _, body in _BODY_RE.findall(page))
    document["source_file"] = source_file
    return document


# ==============================================================================
# ENTRY POINT
# ==============================================================================

def main() -> None:
    """
    Extract all downloaded detail pages to JSONL files.

    Steps:
        1. Configure the logging and make the output directory.
        2. For each category, extract each detail page and write one JSON line.

    Returns:
        None.
    """
    # 1. Configure the logging and make the output directory
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    PARSED_DIR.mkdir(parents=True, exist_ok=True)

    # 2. For each category, extract each detail page and write one JSON line
    for category in CATEGORIES:
        output_path = PARSED_DIR / f"{category}.jsonl"
        documents_count = 0
        with output_path.open("w", encoding="utf-8") as output_file:
            for path in sorted((RAW_DIR / category).glob("*.html")):
                if path.stem.endswith(("-index", "-catalog")):
                    continue
                document = extract_document(path.read_text(encoding="utf-8"), category, get_relative_path(path))
                if document:
                    output_file.write(json.dumps(document, ensure_ascii=False) + "\n")
                    documents_count += 1
        logger.info("SAVED documents | category=%s documents_count=%d path=%s",
                    category, documents_count, get_relative_path(output_path))


if __name__ == "__main__":
    main()
