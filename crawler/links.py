# ==============================================================================
# LINK EXTRACTOR — MilSearch
# ==============================================================================
# PURPOSE:
#     Find the links on a downloaded page, make canonical URLs from them and
#     keep only the URLs that are in the crawl scope. Use only regular expressions.
#
# RESPONSIBILITIES:
#     1. Extract <a href> links with their anchor texts and title attributes.
#     2. Make a canonical absolute URL from each link.
#     3. Classify a URL as index, catalog or detail page, or reject it.
#     4. Make a stable document id from a URL.
#
# DEPENDENCIES:
#     crawler.config — host, categories, URL length limit.
#
# INVARIANTS:
#     A canonical URL uses https, the host www.militaryfactory.com and no fragment.
# ==============================================================================

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

from crawler.config import ALLOWED_HOST, CATEGORIES, MAX_URL_LENGTH

# ==============================================================================
# CONSTANTS
# ==============================================================================

# Full <a ...>...</a> element. Group 1 is the attributes, group 2 is the inner HTML.
_ANCHOR_RE = re.compile(r"<a\s([^>]*)>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"'#>]+)""", re.IGNORECASE)
_TITLE_RE = re.compile(r"""title\s*=\s*["']([^"']*)""", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")

_CATEGORY_GROUP = "|".join(CATEGORIES)
# The crawler downloads only these three page types.
_INDEX_RE = re.compile(rf"^/({_CATEGORY_GROUP})/index\.php$")
_CATALOG_RE = re.compile(rf"^/({_CATEGORY_GROUP})/indexMAIN\.php$")
_DETAIL_RE = re.compile(
    rf"^/({_CATEGORY_GROUP})/detail\.php\?(smallarms_id|armor_id|ship_id)=([A-Za-z0-9._%-]+)$"
)
_PAGE_KIND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("index", _INDEX_RE),
    ("catalog", _CATALOG_RE),
    ("detail", _DETAIL_RE),
)

_MAX_ANCHOR_TEXT_LENGTH = 200  # Catalog anchors have approx. 80 characters. Longer text is page noise.


# ==============================================================================
# TYPES
# ==============================================================================

@dataclass(frozen=True)
class Link:
    """
    One link from a page that is in the crawl scope.

    Responsibilities:
        1. Carry the canonical target URL and its scope (kind, category).
        2. Carry the anchor text and the title attribute for the indexer.
    """

    url: str
    kind: str          # index | catalog | detail
    category: str      # smallarms | armor | ships
    anchor_text: str
    title: str


# ==============================================================================
# HELPERS
# ==============================================================================

def _get_target(url: str) -> str:
    """
    Get the path and the query string of a URL.

    Steps:
        1. Split the URL and join the path with the query string.

    Returns:
        String such as "/armor/detail.php?armor_id=1".
    """
    # 1. Split the URL and join the path with the query string
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


def _get_plain_text(fragment: str) -> str:
    """
    Remove the HTML tags and entities from a fragment.

    Steps:
        1. Replace tags with spaces, decode entities and collapse whitespace.

    Returns:
        Plain text on one line.
    """
    # 1. Replace tags with spaces, decode entities and collapse whitespace
    return _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", fragment))).strip()


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def build_canonical_url(href: str, base_url: str) -> str | None:
    """
    Make a canonical absolute URL from a link.

    Steps:
        1. Resolve the link against the page URL.
        2. Reject a scheme other than http/https and a different host.
        3. Change the path suffix .asp to .php and remove the fragment.

    Returns:
        Canonical URL; None when the link goes out of the site.
    """
    # 1. Resolve the link against the page URL
    parts = urlsplit(urljoin(base_url, html.unescape(href.strip())))

    # 2. Reject a scheme other than http/https and a different host
    if parts.scheme not in ("http", "https"):
        return None
    host = parts.netloc.lower()
    if host == "militaryfactory.com":
        host = ALLOWED_HOST
    if host != ALLOWED_HOST:
        return None

    # 3. Change the path suffix .asp to .php and remove the fragment
    # The server answers detail.asp with HTTP 301 to detail.php.
    path = re.sub(r"\.asp$", ".php", parts.path)
    return urlunsplit(("https", host, path, parts.query, ""))


def get_url_scope(url: str) -> tuple[str, str] | None:
    """
    Classify a canonical URL as a page type of the crawl scope.

    Steps:
        1. Reject a URL that is longer than the limit.
        2. Match the path against the index, catalog and detail patterns.

    Returns:
        (kind, category); None when the URL is not in the crawl scope.
    """
    # 1. Reject a URL that is longer than the limit
    if len(url) > MAX_URL_LENGTH:
        return None

    # 2. Match the path against the index, catalog and detail patterns
    target = _get_target(url)
    for kind, pattern in _PAGE_KIND_PATTERNS:
        match = pattern.match(target)
        if match:
            return kind, match.group(1)
    return None


def extract_links(page_html: str, base_url: str) -> list[Link]:
    """
    Find all links of a page that are in the crawl scope.

    Steps:
        1. Find each <a> element and its href attribute.
        2. Make the canonical URL and keep only URLs in the crawl scope.
        3. Build a Link with the anchor text and the title attribute.

    Returns:
        Links in page order; the list can contain the same URL more than one time.
    """
    links: list[Link] = []

    # 1. Find each <a> element and its href attribute
    for attributes, inner_html in _ANCHOR_RE.findall(page_html):
        href = _HREF_RE.search(attributes)
        if not href:
            continue

        # 2. Make the canonical URL and keep only URLs in the crawl scope
        url = build_canonical_url(href.group(1), base_url)
        scope = get_url_scope(url) if url else None
        if not scope:
            continue

        # 3. Build a Link with the anchor text and the title attribute
        title = _TITLE_RE.search(attributes)
        links.append(Link(
            url=url,
            kind=scope[0],
            category=scope[1],
            anchor_text=_get_plain_text(inner_html)[:_MAX_ANCHOR_TEXT_LENGTH],
            title=html.unescape(title.group(1)) if title else "",
        ))
    return links


def build_document_id(url: str) -> str:
    """
    Make a stable, file-system safe document id from a URL in the crawl scope.

    Steps:
        1. Get the kind and the category of the URL.
        2. For a detail page, use the id value from the query string.
        3. For an index or a catalog page, use the page kind.

    Returns:
        Id such as "smallarms-29", "ships-Admiral-Kuznetsov" or "armor-catalog".

    Raises:
        ValueError when the URL is not in the crawl scope.
    """
    # 1. Get the kind and the category of the URL
    scope = get_url_scope(url)
    if scope is None:
        raise ValueError(f"URL is not in the crawl scope: {url}")
    kind, category = scope

    # 2. For a detail page, use the id value from the query string
    if kind == "detail":
        value = _DETAIL_RE.match(_get_target(url)).group(3)
        return f"{category}-{re.sub(r'[^A-Za-z0-9._-]', '_', value)}"

    # 3. For an index or a catalog page, use the page kind
    return f"{category}-{kind}"
