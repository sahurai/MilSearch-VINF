# ==============================================================================
# WIKITEXT — MilSearch
# ==============================================================================
# PURPOSE:
#     Get structured data from one Wikipedia page of the XML dump with regular expressions:
#     the record fields, the infobox, the categories, the abstract, the plain text and the links.
#
# RESPONSIBILITIES:
#     1. Define the record patterns. Spark uses the same pattern strings in the JVM (regexp_extract).
#     2. Define the patterns that mark a military equipment page (infobox type or category).
#     3. Resolve the templates from the innermost one outward. Keep the parameters of each infobox.
#     4. Make plain text: links -> anchor text, no references, no tables, no markup.
#     5. Get the interlanguage links from the SQL dump of the langlinks table.
#
# INVARIANTS:
#     Python 3.6 syntax (Spark image of the course). Regular expressions only.
#     The pattern strings in the CONSTANTS section are valid in Python "re" and in Java regex.
# ==============================================================================

import html
import re
from typing import Dict, List, Optional, Tuple

# ==============================================================================
# CONSTANTS
# ==============================================================================

# ------------------------------------------------------------------------------
# Record fields. One record is the text up to "</page>" (Spark text source with lineSep="</page>").
# ------------------------------------------------------------------------------
TITLE_PATTERN = r"<title>([^<]*)</title>"
NAMESPACE_PATTERN = r"<ns>(\d+)</ns>"
PAGE_ID_PATTERN = r"<id>(\d+)</id>"                      # The first <id> is the page ID (before <revision>).
REDIRECT_PATTERN = r"<redirect title=\"([^\"]*)\""
TEXT_PATTERN = r"(?s)<text[^>]*>(.*)</text>"            # An empty page has "<text ... />" and gives "".

# ------------------------------------------------------------------------------
# Military equipment pages: an infobox of a weapon, vehicle or ship, or a category of equipment.
# ------------------------------------------------------------------------------
MILITARY_INFOBOX_PATTERN = (
    r"(?i)\{\{\s*infobox[ _]+(weapon|firearm|gun\b|artillery|military vehicle|afv|tank|ship|submarine"
    r"|warship|torpedo|grenade|mortar|rocket)"
)
MILITARY_CATEGORY_PATTERN = (
    r"(?i)\[\[\s*category\s*:[^\]]*\b(rifles|pistols|revolvers|carbines|shotguns|machine guns|submachine guns"
    r"|grenades|mortars|rocket launchers|grenade launchers|firearms|infantry weapons|tanks|armoured fighting vehicles"
    r"|armored fighting vehicles|armoured cars|armored cars|armoured personnel carriers|armored personnel carriers"
    r"|infantry fighting vehicles|self-propelled artillery|artillery|howitzers|military vehicles|military trucks"
    r"|battleships|battlecruisers|cruisers|destroyers|frigates|corvettes|submarines|aircraft carriers|warships"
    r"|ships|ship classes|minesweepers|patrol vessels|gunboats|monitors|ironclads)\b"
)

# Interlanguage link in the SQL dump: (page_id,'en','Title'). The title has backslash escapes.
_LANGLINK_RE = re.compile(r"\((\d+),'([a-z-]+)','((?:[^'\\]|\\.)*)'\)")
_SQL_ESCAPE_RE = re.compile(r"\\(.)")

# ------------------------------------------------------------------------------
# Wikitext cleanup
# ------------------------------------------------------------------------------
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_REF_RE = re.compile(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", re.S | re.I)
_NOWIKI_BLOCK_RE = re.compile(r"<(gallery|math|syntaxhighlight|score|timeline|imagemap)[^>]*>.*?</\1>", re.S | re.I)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
# [[File:...|caption with [[link]]]]: one level of nested links inside the caption.
_FILE_LINK_RE = re.compile(r"\[\[\s*(?:file|image|súbor|obrázok)\s*:(?:[^\[\]]|\[\[[^\[\]]*\]\])*\]\]", re.I)
_CATEGORY_RE = re.compile(r"\[\[\s*(?:category|kategória)\s*:\s*([^\]|]+?)\s*(?:\|[^\]]*)?\]\]", re.I)
_INTERWIKI_RE = re.compile(r"\[\[[a-z]{2,3}(?:-[a-z]+)?:[^\]]*\]\]")
# [[Target]], [[Target|anchor]], [[Target#Section|anchor]]
_LINK_RE = re.compile(r"\[\[([^\[\]|]*)(?:\|([^\[\]]*))?\]\]")
_EXTERNAL_LINK_RE = re.compile(r"\[(?:https?:)?//[^\s\]]+\s*([^\]]*)\]")
_TEMPLATE_RE = re.compile(r"\{\{([^{}]*)\}\}")
_TABLE_RE = re.compile(r"\{\|[^{}]*?\|\}", re.S)
_HEADING_RE = re.compile(r"^\s*(=+)\s*(.*?)\s*=+\s*$", re.M)
_QUOTES_RE = re.compile(r"'{2,}")
_LIST_BULLET_RE = re.compile(r"^(?:[ \t]*[*#:;])+[ \t]*", re.M)   # "* * item" -> "item"; keeps the line break.
_SPACE_RE = re.compile(r"[ \t ]+")
_BLANK_LINES_RE = re.compile(r"\n\s*\n+")
_NAMED_ARGUMENT_RE = re.compile(r"^\s*([A-Za-z0-9_ -]+?)\s*=(.*)$", re.S)
_VALUE_SEPARATOR_RE = re.compile(r"\s*(?:\n\s*[*#]*\s*|\s*;\s*)+")
_MAX_TEMPLATE_PASSES = 40

# Templates that show their first argument (flags, formatting).
_FIRST_ARGUMENT_TEMPLATES = frozenset({
    "flag", "flagcountry", "flagu", "flag country", "naval", "navy", "army", "air force", "nowrap", "nobr",
    "small", "big", "abbr", "smaller", "larger", "center", "lang-en", "nihongo", "transl", "sic", "vanchor",
    "ill", "interlanguage link", "proper name", "tooltip", "visible anchor", "nobold", "noitalic",
    "flagdeco", "country data", "linktext", "ship name",
})
# Templates that show a list of their arguments.
_LIST_TEMPLATES = frozenset({
    "ubl", "unbulleted list", "plainlist", "plain list", "flatlist", "flat list", "hlist", "bulleted list",
    "ublist", "collapsible list", "bulletedlist", "unbulletedlist", "enum", "ordered list", "indented plainlist",
    "ubli", "ubil", "ubt", "unbulleted indent list", "cslist", "comma separated entries", "unordered list",
    "flat list", "startflatlist", "plainlist/start", "bulleted list/start", "endflatlist",
})
_DATE_TEMPLATES = frozenset({
    "start date", "start date and age", "end date", "end date and age", "date", "dts", "birth date",
    "death date", "film date", "start-date", "end-date", "start and end dates",
})
# Ship name templates: {{USS|Nimitz|CVN-68}} -> "USS Nimitz (CVN-68)".
_SHIP_PREFIX_TEMPLATES = frozenset({
    "uss", "hms", "hmas", "hmcs", "hmnzs", "sms", "ss", "usns", "usat", "usrc", "uscgc", "hmnls", "hnlms",
    "hnoms", "hswms", "hdms", "ins", "jds", "js", "ijn", "rms", "ors", "ns", "hs", "fs", "ars", "bns", "kd",
    "kri", "roks", "rss", "tcg", "brp", "ara", "bae", "cns", "nrp", "orp", "rfs", "hmt", "sms", "sas", "mv",
})
# Templates of index pages: the page lists several objects with one name. The kind "index" marks them.
_INDEX_TEMPLATES = frozenset({
    "shipindex", "ship index", "disambiguation", "disambig", "dab", "disamb", "set index article", "sia",
    "hndis", "geodis", "army unit index", "weapon index", "letter-number combination disambiguation",
})
_SYMBOL_TEMPLATES = {"snd": " – ", "spaced ndash": " – ", "ndash": "–", "mdash": "—", "nbsp": " ", "!": " ",
                     "=": "=", "-": " ", "•": " • ", "middot": " · ", "spaces": " ", "sp": " ", "zwsp": ""}
_RANGE_WORDS = frozenset({"-", "–", "to", "and", "or", "x", "×", "by", "+"})


# ==============================================================================
# HELPERS
# ==============================================================================

def unescape_xml(text: str) -> str:
    """
    Decode the XML entities of the dump (&lt; &gt; &quot; &amp; and numeric entities).

    Steps:
        1. Decode all entities with html.unescape.

    Returns:
        Text with the entities decoded.
    """
    # 1. Decode all entities with html.unescape
    return html.unescape(text) if "&" in text else text


def _split_arguments(body: str) -> Tuple[str, List[str], Dict[str, str]]:
    """
    Split the body of a template into the name, the positional arguments and the named arguments.

    Steps:
        1. Split at "|"; the first part is the template name.
        2. Put "key = value" parts into the named arguments, the other parts into the positional arguments.

    Returns:
        (lowercase name, positional arguments, named arguments).
    """
    # 1. Split at "|"; the first part is the template name
    parts = body.split("|")
    name = _SPACE_RE.sub(" ", parts[0].replace("_", " ")).strip().lower()

    # 2. Put "key = value" parts into the named arguments, the other parts into the positional arguments
    positional: List[str] = []
    named: Dict[str, str] = {}
    for part in parts[1:]:
        match = _NAMED_ARGUMENT_RE.match(part)
        if match:
            named[match.group(1).strip().lower().replace(" ", "_")] = match.group(2).strip()
        else:
            positional.append(part.strip())
    return name, positional, named


def _render_convert(positional: List[str]) -> str:
    """
    Show a {{convert}} template as value and unit: {{convert|3.47|kg|lb}} -> "3.47 kg".

    Steps:
        1. Join a range ("{{convert|5|to|10|km}}") or take one value.
        2. Add the unit (the first argument after the values).

    Returns:
        Text such as "3.47 kg" or "5–10 km"; "" without arguments.
    """
    if not positional:
        return ""
    # 1. Join a range or take one value
    values = [positional[0]]
    index = 1
    while index + 1 < len(positional) and positional[index].lower() in _RANGE_WORDS:
        values.append(positional[index + 1])
        index += 2

    # 2. Add the unit (the first argument after the values)
    unit = positional[index] if index < len(positional) else ""
    return "–".join(values) + (" " + unit if unit else "")


def _render_date(positional: List[str]) -> str:
    """
    Show a date template as ISO text: {{start date|1968|6|22}} -> "1968-06-22".

    Steps:
        1. Keep the numeric arguments (year, month, day).
        2. Join them with "-" and add zeros to month and day.

    Returns:
        Date text; "" without a numeric argument.
    """
    # 1. Keep the numeric arguments (year, month, day)
    numbers = [value for value in positional[:3] if value.isdigit()]
    if not numbers:
        return positional[0] if positional else ""

    # 2. Join them with "-" and add zeros to month and day
    return "-".join([numbers[0]] + [value.zfill(2) for value in numbers[1:]])


def _render_template(body: str, infoboxes: List[Tuple[str, Dict[str, str]]]) -> str:
    """
    Replace one innermost template with its text. Keep the parameters of an infobox.

    Steps:
        1. Split the template into the name and the arguments.
        2. Keep an infobox in the infobox list and remove it from the text.
        3. Show the templates for numbers, dates, lists, flags and ship names.
        4. Remove all other templates (citations, notes, maintenance tags).

    Returns:
        Replacement text.
    """
    # 1. Split the template into the name and the arguments
    name, positional, named = _split_arguments(body)

    # 2. Keep an infobox in the infobox list and remove it from the text
    if name.startswith("infobox"):
        kind = name[len("infobox"):].strip(" _").split("/")[0].strip() or "generic"
        infoboxes.append((kind, named))
        return ""
    if name in _INDEX_TEMPLATES:
        infoboxes.append(("index", {}))
        return ""

    # 3. Show the templates for numbers, dates, lists, flags and ship names
    if name in _SYMBOL_TEMPLATES:
        return _SYMBOL_TEMPLATES[name]
    if name in ("convert", "cvt"):
        return _render_convert(positional)
    if name in _DATE_TEMPLATES:
        return _render_date(positional)
    if name in _LIST_TEMPLATES:
        return "\n".join("* " + value for value in positional if value)
    if name in _FIRST_ARGUMENT_TEMPLATES or name.startswith(("lang-", "flag")):
        if name == "flagicon":
            return ""
        return positional[0] if positional else named.get("1", "")
    if name == "lang" and len(positional) >= 2:
        return positional[1]
    if name in _SHIP_PREFIX_TEMPLATES and positional:
        hull = " ({})".format(positional[1]) if len(positional) > 1 and positional[1] else ""
        return "{} {}{}".format(name.upper(), positional[0], hull)
    if name in ("ship", "warship", "sclass-", "ship-") and len(positional) >= 2:
        hull = " ({})".format(positional[2]) if len(positional) > 2 and positional[2] else ""
        return "{} {}{}".format(positional[0], positional[1], hull)
    if name in ("sclass", "sclass2", "sclass3") and len(positional) >= 2:
        return "{}-class {}".format(positional[0], positional[1])
    if name in ("circa", "c.") and positional:
        return "c. " + positional[0]
    if name in ("us$", "usd", "currency") and positional:
        return "US$" + positional[0]
    if name in ("£", "gbp") and positional:
        return "£" + positional[0]

    # 4. Remove all other templates (citations, notes, maintenance tags)
    return ""


def resolve_templates(text: str) -> Tuple[str, List[Tuple[str, Dict[str, str]]]]:
    """
    Resolve all templates and simple tables from the innermost one outward.

    Steps:
        1. Replace the innermost templates ({{...}} without braces inside) with their text.
        2. Remove the innermost tables ({| ... |} without braces inside).
        3. Repeat until nothing changes (at most _MAX_TEMPLATE_PASSES times).

    Returns:
        (text without templates, list of (infobox kind, infobox parameters) in page order).
    """
    infoboxes: List[Tuple[str, Dict[str, str]]] = []
    for _ in range(_MAX_TEMPLATE_PASSES):
        # 1. Replace the innermost templates with their text
        new_text = _TEMPLATE_RE.sub(lambda match: _render_template(match.group(1), infoboxes), text)

        # 2. Remove the innermost tables
        new_text = _TABLE_RE.sub("", new_text)

        # 3. Repeat until nothing changes
        if new_text == text:
            break
        text = new_text
    # The innermost infobox is found first. Page order puts the outer (main) infobox first.
    infoboxes.reverse()
    return text, infoboxes


def _replace_link(match: "re.Match") -> str:
    """
    Show a wiki link as its anchor text.

    Steps:
        1. Use the anchor text; without it, use the target without the section part.

    Returns:
        Visible text of the link.
    """
    # 1. Use the anchor text; without it, use the target without the section part
    target, anchor = match.group(1), match.group(2)
    if anchor is not None and anchor.strip():
        return anchor
    return target.split("#")[0].lstrip(":")


def get_plain_value(value: str) -> str:
    """
    Make plain text from a wikitext fragment that has no templates (an infobox value or a paragraph).

    Steps:
        1. Remove the HTML tags, the bold and italic quotes and the list bullets.
        2. Collapse the whitespace on each line and remove empty lines.

    Returns:
        Plain text; lines are separated by "\\n".
    """
    # 1. Remove the HTML tags, the bold and italic quotes and the list bullets
    # Wikitext can contain HTML entities ("&nbsp;", "&ndash;") after the XML decoding.
    text = _QUOTES_RE.sub("", _TAG_RE.sub("", unescape_xml(value)))
    text = _LIST_BULLET_RE.sub("", text)

    # 2. Collapse the whitespace on each line and remove empty lines
    lines = (_SPACE_RE.sub(" ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def get_page_record(record: str) -> Optional[Dict[str, object]]:
    """
    Get the fields of one <page> record of the XML dump (Python version of the Spark record parser).

    Steps:
        1. Find the title, the namespace, the page ID, the redirect target and the text.
        2. Decode the XML entities.

    Returns:
        {"id", "title", "ns", "redirect", "text"}; None when the record has no title.
    """
    # 1. Find the title, the namespace, the page ID, the redirect target and the text
    title = re.search(TITLE_PATTERN, record)
    if not title:
        return None
    namespace = re.search(NAMESPACE_PATTERN, record)
    page_id = re.search(PAGE_ID_PATTERN, record)
    redirect = re.search(REDIRECT_PATTERN, record)
    text = re.search(TEXT_PATTERN, record)

    # 2. Decode the XML entities
    return {
        "id": int(page_id.group(1)) if page_id else None,
        "title": unescape_xml(title.group(1)),
        "ns": int(namespace.group(1)) if namespace else None,
        "redirect": unescape_xml(redirect.group(1)) if redirect else None,
        "text": unescape_xml(text.group(1)) if text else "",
    }


def is_military_page(text: str) -> bool:
    """
    Check if the wikitext of a page describes military equipment (Python version of the Spark filter).

    Steps:
        1. Search for a military infobox or a military equipment category.

    Returns:
        True for a weapon, vehicle or ship page.
    """
    # 1. Search for a military infobox or a military equipment category
    return bool(re.search(MILITARY_INFOBOX_PATTERN, text) or re.search(MILITARY_CATEGORY_PATTERN, text))


def get_categories(text: str) -> List[str]:
    """
    Get the categories of a page in page order without duplicates.

    Steps:
        1. Find all [[Category:...]] links.

    Returns:
        Category names.
    """
    # 1. Find all [[Category:...]] links
    seen: Dict[str, None] = {}
    for name in _CATEGORY_RE.findall(text):
        seen.setdefault(name.strip(), None)
    return list(seen)


def get_links(text: str) -> List[Tuple[str, str]]:
    """
    Get the article links of a page with their anchor texts.

    Steps:
        1. Find all [[Target|anchor]] links outside File and Category links.
        2. Normalize the target: no section part, spaces for "_", first letter uppercase.

    Returns:
        List of (target title, anchor text).
    """
    links: List[Tuple[str, str]] = []
    # 1. Find all [[Target|anchor]] links outside File and Category links
    for target, anchor in _LINK_RE.findall(_FILE_LINK_RE.sub("", text)):
        if ":" in target.lstrip(":")[:15]:
            continue  # Namespace link (Category:, File:, Wikipedia:, interwiki).

        # 2. Normalize the target
        title = _SPACE_RE.sub(" ", target.split("#")[0].replace("_", " ")).strip()
        if title:
            links.append((title[0].upper() + title[1:], (anchor or target).strip()))
    return links


def clean_wikitext(text: str) -> Tuple[str, List[Tuple[str, Dict[str, str]]], List[str]]:
    """
    Make plain text from wikitext. Keep the infoboxes and the categories.

    Steps:
        1. Remove comments, references and special blocks; change <br> to line breaks.
        2. Get the categories; remove File, Category and interwiki links.
        3. Replace article links with their anchor texts, external links with their labels.
        4. Resolve the templates; keep the infoboxes.
        5. Remove headings markup, tags and quotes.

    Returns:
        (plain text, infoboxes, categories).
    """
    # 1. Remove comments, references and special blocks; change <br> to line breaks
    text = _COMMENT_RE.sub("", text)
    text = _REF_RE.sub("", text)
    text = _NOWIKI_BLOCK_RE.sub("", text)
    text = _BR_RE.sub("\n", text)

    # 2. Get the categories; remove File, Category and interwiki links
    categories = get_categories(text)
    text = _FILE_LINK_RE.sub("", text)
    text = _CATEGORY_RE.sub("", text)
    text = _INTERWIKI_RE.sub("", text)

    # 3. Replace article links with their anchor texts, external links with their labels
    text = _LINK_RE.sub(_replace_link, text)
    text = _EXTERNAL_LINK_RE.sub(r"\1", text)

    # 4. Resolve the templates; keep the infoboxes
    text, infoboxes = resolve_templates(text)

    # 5. Remove headings markup, tags and quotes
    text = _HEADING_RE.sub(lambda match: "\n== " + match.group(2) + " ==\n", text)
    return get_plain_value(text), infoboxes, categories


def get_infobox_fields(infoboxes: List[Tuple[str, Dict[str, str]]]) -> Tuple[List[str], Dict[str, str]]:
    """
    Merge the infoboxes of a page into one map of plain values.

    Steps:
        1. Collect the infobox kinds in page order.
        2. Clean each value; the first non-empty value of a parameter wins.

    Returns:
        (infobox kinds, parameter -> plain value with "; " between list items).
    """
    kinds: List[str] = []
    fields: Dict[str, str] = {}
    for kind, parameters in infoboxes:
        # 1. Collect the infobox kinds in page order
        if kind not in kinds:
            kinds.append(kind)

        # 2. Clean each value; the first non-empty value of a parameter wins
        for key, value in parameters.items():
            plain = _VALUE_SEPARATOR_RE.sub("; ", get_plain_value(value)).strip("; ")
            if plain and key not in fields:
                fields[key] = plain
    return kinds, fields


def get_abstract(plain_text: str, max_length: int = 1200) -> str:
    """
    Get the abstract: the lead section (text before the first heading).

    Steps:
        1. Cut the text at the first heading.
        2. Join the paragraphs and cut the text at a sentence end before max_length.

    Returns:
        Abstract text.
    """
    # 1. Cut the text at the first heading
    lead = plain_text.split("\n== ", 1)[0]

    # 2. Join the paragraphs and cut the text at a sentence end before max_length
    abstract = _SPACE_RE.sub(" ", lead.replace("\n", " ")).strip()
    if len(abstract) <= max_length:
        return abstract
    cut = abstract.rfind(". ", 0, max_length)
    return abstract[:cut + 1] if cut > 0 else abstract[:max_length]


def parse_page(text: str) -> Dict[str, object]:
    """
    Get the structured data of one article.

    Steps:
        1. Clean the wikitext; keep the infoboxes and the categories.
        2. Merge the infobox parameters and get the abstract.

    Returns:
        {"infobox_kinds", "infobox", "categories", "abstract", "text"}.
    """
    # 1. Clean the wikitext; keep the infoboxes and the categories
    plain_text, infoboxes, categories = clean_wikitext(text)

    # 2. Merge the infobox parameters and get the abstract
    kinds, fields = get_infobox_fields(infoboxes)
    return {
        "infobox_kinds": kinds,
        "infobox": fields,
        "categories": categories,
        "abstract": get_abstract(plain_text),
        "text": plain_text,
    }


def get_langlinks(sql_line: str, language: str) -> List[Tuple[int, str]]:
    """
    Get the interlanguage links of one language from a line of the langlinks SQL dump.

    Steps:
        1. Find all (page_id,'lang','Title') tuples.
        2. Keep the tuples of the language and decode the SQL backslash escapes.

    Returns:
        List of (page ID in this wiki, title in the other language).
    """
    links: List[Tuple[int, str]] = []
    # 1. Find all (page_id,'lang','Title') tuples
    for page_id, link_language, title in _LANGLINK_RE.findall(sql_line):
        # 2. Keep the tuples of the language and decode the SQL backslash escapes
        if link_language == language and title:
            links.append((int(page_id), _SQL_ESCAPE_RE.sub(r"\1", title)))
    return links
