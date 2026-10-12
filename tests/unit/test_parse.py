# ==============================================================================
# DETAIL PAGE EXTRACTOR TESTS — MilSearch
# ==============================================================================
# PURPOSE:
#     Test the regex extraction of the title block and of the spec sections.
#
# RESPONSIBILITIES:
#     1. Make sure that the title block gives name, type, country and year.
#     2. Make sure that a variants block with separator divs gives one variant for each line.
#     3. Make sure that values with units in parentheses do not get double parentheses.
#     4. Make sure that ships in class, roles, countries and numeric fields are correct.
#     5. Make sure that an implausible numeric value from the site becomes None.
#
# DEPENDENCIES:
#     extractor.parse — functions under test.
# ==============================================================================

from __future__ import annotations

from extractor.parse import extract_document

# ==============================================================================
# CONSTANTS
# ==============================================================================

_SEPARATOR = "<div style='width:100%; height:1px; background-color:#666;'></div>"

_PAGE = f"""
<h1 class="textJumbo"><span class="textBold">M1 Abrams</span></h1>
<h2 class="textLarge">Main Battle Tank (MBT)</h2>
<h3 class="textLarger textBold">United States | 1980</h3>
<span class="textJumbo textBold">Power &amp; Performance</span>
<div class="specContainerMain specContainerShort specBGcolor1">
    <span class="textBold textLargest">42 mph</span><br /><span class="textLarge">67 kph</span><br />
    <span class="textNormal">Road Speed</span>
</div>
<div class="specContainerMain specContainerShort specBGcolor1">
    <span class="textBold textLargest">4</span><br /><span class="textLarge">(MANNED)</span><br />
    <span class="textNormal">Crew</span>
</div>
<span class="textJumbo textBold">Variants</span>
<div class="specContainerMain specBGcolor1" style="text-align:left;">
    <span class="textWhite textNormal">XM1 - Prototype.<br />{_SEPARATOR}
M1 - Base Production Model.<br />{_SEPARATOR}
M1A2 - Modernized model.</span>
</div>
"""

_SHIP_PAGE = """
<h1 class="textJumbo"><span class="textBold">Type VII U-Boat</span></h1>
<h2 class="textLarge">Ocean-Going Diesel-Electric Attack Submarine</h2>
<h3 class="textLarger textBold">Nazi Germany | 1936</h3>
<span class="textJumbo textBold">Power &amp; Performance</span>
<div class="specContainerMain specContainerShort specBGcolor1">
    <span class="textBold textLargest">5,580.0 kts</span><br /><span class="textLarge">6,421.4 mph</span><br />
    <span class="textNormal">Submerged Speed</span>
</div>
<div class="specContainerMain specContainerShort specBGcolor1">
    <span class="textBold textLargest">Essentially Unlimited</span><br />
    <span class="textNormal">Range</span>
</div>
<div class="specContainerMain specContainerShort specBGcolor1">
    <span class="textBold textLargest">218.1 ft</span><br /><span class="textLarge">66.48 meters</span><br />
    <span class="textNormal">O/A Length</span>
</div>
<span class="textJumbo textBold">Ships-in-Class (3)</span>
<div class="specContainerMain specBGcolor1" style="text-align:left;">
    <span class="textWhite textNormal">U-27; U-28;U-29</span>
</div>
<span class="textJumbo textBold">Operators</span>
<a href="/ships/navy-warships-and-submarines-by-country.php?Nation=Nazi%20Germany">x</a>
<a href="/ships/navy-warships-and-submarines-by-country.php?Nation=Germany">x</a>
<span class="textJumbo textBold">Mission Roles</span>
<div class="roleContainers boxShadow">
    SUBMERGED ATTACK
</div>
"""


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def test_extracts_title_block_and_numeric_speed() -> None:
    """
    The title block and the road speed in km/h come from the page.

    Steps:
        1. Extract the document from the sample armor page.

    Returns:
        None.
    """
    # 1. Extract the document from the sample armor page
    document = extract_document(_PAGE, "armor", "data/raw/armor/armor-1.html")
    assert document["name"] == "M1 Abrams"
    assert document["type"] == "Main Battle Tank (MBT)"
    assert document["country"] == "United States"
    assert document["year"] == 1980
    assert document["max_speed_kmh"] == 67.0


def test_variants_block_with_separator_divs_gives_one_variant_per_line() -> None:
    """
    Regression: separator divs inside the variants block must not stop the block early.

    Steps:
        1. Extract the document and read the variants.

    Returns:
        None.
    """
    # 1. Extract the document and read the variants
    document = extract_document(_PAGE, "armor", "data/raw/armor/armor-1.html")
    assert document["variants"] == ["XM1 - Prototype.", "M1 - Base Production Model.", "M1A2 - Modernized model."]


def test_alt_value_in_parentheses_has_single_parentheses() -> None:
    """
    Regression: an alternative value that is already in parentheses gets no second pair.

    Steps:
        1. Extract the document and read the crew value.

    Returns:
        None.
    """
    # 1. Extract the document and read the crew value
    document = extract_document(_PAGE, "armor", "data/raw/armor/armor-1.html")
    assert document["crew"] == "4 (MANNED)"


def test_page_without_title_block_is_not_a_document() -> None:
    """
    A page without the title block (index or catalog) gives no document.

    Steps:
        1. Extract a page without <h1>.

    Returns:
        None.
    """
    # 1. Extract a page without <h1>
    assert extract_document("<html><body>catalog</body></html>", "armor", "x.html") is None


def test_implausible_numeric_value_becomes_none() -> None:
    """
    Regression: the site gives 5,580 kts as a submerged speed; the value must be rejected.

    Steps:
        1. Extract the sample ship page and read the submerged speed.

    Returns:
        None.
    """
    # 1. Extract the sample ship page and read the submerged speed
    document = extract_document(_SHIP_PAGE, "ships", "data/raw/ships/ships-Type-VII-Uboat.html")
    assert document["submerged_speed"] == "5,580.0 kts (6,421.4 mph)"
    assert document["submerged_speed_kts"] is None
    assert document["implausible_fields"] == ["submerged_speed_kts"]


def test_unlimited_range_has_flag_and_no_number() -> None:
    """
    "Essentially Unlimited" gives no range number and sets the unlimited flag.

    Steps:
        1. Extract the sample ship page and read the range fields.

    Returns:
        None.
    """
    # 1. Extract the sample ship page and read the range fields
    document = extract_document(_SHIP_PAGE, "ships", "data/raw/ships/ships-Type-VII-Uboat.html")
    assert document["range_km"] is None
    assert document["is_range_unlimited"] is True
    assert document["length_m"] == 66.48


def test_ships_in_class_roles_and_countries() -> None:
    """
    The ships of the class, the role tags and the canonical countries come from the page.

    Steps:
        1. Extract the sample ship page and read the list fields and the countries.

    Returns:
        None.
    """
    # 1. Extract the sample ship page and read the list fields and the countries
    document = extract_document(_SHIP_PAGE, "ships", "data/raw/ships/ships-Type-VII-Uboat.html")
    assert document["ships_in_class"] == ["U-27", "U-28", "U-29"]
    assert document["roles"] == ["SUBMERGED ATTACK"]
    assert document["country_original"] == "Nazi Germany"
    assert document["country"] == "Germany"
    assert document["operators"] == ["Germany"]
    assert document["operators_original"] == ["Germany", "Nazi Germany"]
