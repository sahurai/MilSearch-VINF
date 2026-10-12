# ==============================================================================
# LINK EXTRACTOR TESTS — MilSearch
# ==============================================================================
# PURPOSE:
#     Test URL normalization, URL scope and link extraction.
#
# RESPONSIBILITIES:
#     1. Make sure that canonical URLs use https, the www host and .php.
#     2. Make sure that only index, catalog and detail URLs are in the crawl scope.
#     3. Make sure that link extraction keeps anchor texts and drops out-of-scope links.
#
# DEPENDENCIES:
#     crawler.links — functions under test.
# ==============================================================================

from __future__ import annotations

from crawler.links import build_canonical_url, build_document_id, extract_links, get_url_scope

# ==============================================================================
# CONSTANTS
# ==============================================================================

_BASE = "https://www.militaryfactory.com/smallarms/indexMAIN.php"


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def test_canonical_url_maps_old_asp_link_to_php() -> None:
    """
    An old detail.asp link becomes the canonical detail.php URL.

    Steps:
        1. Normalize an http detail.asp link with a fragment.

    Returns:
        None.
    """
    # 1. Normalize an http detail.asp link with a fragment
    url = build_canonical_url("http://militaryfactory.com/smallarms/detail.asp?smallarms_id=38#top", _BASE)
    assert url == "https://www.militaryfactory.com/smallarms/detail.php?smallarms_id=38"


def test_canonical_url_rejects_other_host() -> None:
    """
    A link to a different site is out of scope.

    Steps:
        1. Normalize a link to a different host.

    Returns:
        None.
    """
    # 1. Normalize a link to a different host
    assert build_canonical_url("https://www.globalfirepower.com/", _BASE) is None


def test_url_scope_accepts_crawl_pages_and_rejects_others() -> None:
    """
    Only index, catalog and detail pages of the three categories are in scope.

    Steps:
        1. Classify URLs in the scope.
        2. Classify URLs out of the scope.

    Returns:
        None.
    """
    # 1. Classify URLs in the scope
    assert get_url_scope("https://www.militaryfactory.com/armor/index.php") == ("index", "armor")
    assert get_url_scope("https://www.militaryfactory.com/ships/indexMAIN.php") == ("catalog", "ships")
    assert get_url_scope(
        "https://www.militaryfactory.com/ships/detail.php?ship_id=USS-Nimitz-CVN68"
    ) == ("detail", "ships")

    # 2. Classify URLs out of the scope
    assert get_url_scope("https://www.militaryfactory.com/aircraft/detail.php?aircraft_id=1") is None
    assert get_url_scope("https://www.militaryfactory.com/smallarms/by-country.php?Nation=Chile") is None


def test_extract_links_keeps_anchor_text_and_title() -> None:
    """
    Link extraction keeps in-scope links with anchor text and title, and drops other links.

    Steps:
        1. Extract links from a page with one detail link and one external link.

    Returns:
        None.
    """
    # 1. Extract links from a page with one detail link and one external link
    page = (
        '<a href="/smallarms/detail.php?smallarms_id=29" title="Picture of the Barrett M82">'
        "<span>Barrett M82</span></a> <a href='https://example.com/'>other</a>"
    )
    links = extract_links(page, _BASE)
    assert len(links) == 1
    assert links[0].url == "https://www.militaryfactory.com/smallarms/detail.php?smallarms_id=29"
    assert links[0].anchor_text == "Barrett M82"
    assert links[0].title == "Picture of the Barrett M82"


def test_document_id_uses_detail_id_or_page_kind() -> None:
    """
    The document id comes from the detail id or from the page kind.

    Steps:
        1. Build the ids of a detail page and of a catalog page.

    Returns:
        None.
    """
    # 1. Build the ids of a detail page and of a catalog page
    assert build_document_id("https://www.militaryfactory.com/armor/detail.php?armor_id=1") == "armor-1"
    assert build_document_id("https://www.militaryfactory.com/ships/indexMAIN.php") == "ships-catalog"
