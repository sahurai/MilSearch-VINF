# ==============================================================================
# PAGE STORAGE — MilSearch
# ==============================================================================
# PURPOSE:
#     Write the downloaded pages and the link graph to disk.
#
# RESPONSIBILITIES:
#     1. Compute the SHA-1 hash of page content.
#     2. Save one HTML file for each document.
#     3. Append the links of a page to the anchor file.
#     4. Make project-relative paths for the crawl records.
#
# DEPENDENCIES:
#     crawler.config — data paths.
#     crawler.links — Link type.
# ==============================================================================

from __future__ import annotations

import hashlib
from pathlib import Path

from crawler.config import ANCHORS_PATH, PROJECT_ROOT, RAW_DIR
from crawler.links import Link

# ==============================================================================
# HELPERS
# ==============================================================================

def _get_tsv_cell(value: str) -> str:
    """
    Make a value safe for one TSV cell.

    Steps:
        1. Replace tabs and line breaks with spaces.

    Returns:
        Value without tabs and line breaks.
    """
    # 1. Replace tabs and line breaks with spaces
    return value.replace("\t", " ").replace("\r", " ").replace("\n", " ")


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def compute_sha1(text: str) -> str:
    """
    Compute the SHA-1 hash of a text.

    Steps:
        1. Encode the text as UTF-8 and hash it.

    Returns:
        Hexadecimal SHA-1 digest.
    """
    # 1. Encode the text as UTF-8 and hash it
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def save_page(category: str, document_id: str, body: str) -> Path:
    """
    Write an HTML page to data/raw/<category>/<document_id>.html.

    Steps:
        1. Make the category directory.
        2. Write the page as UTF-8.

    Returns:
        Path of the written file.
    """
    # 1. Make the category directory
    path = RAW_DIR / category / f"{document_id}.html"
    path.parent.mkdir(parents=True, exist_ok=True)

    # 2. Write the page as UTF-8
    path.write_text(body, encoding="utf-8")
    return path


def save_anchor_rows(source_url: str, links: list[Link]) -> None:
    """
    Append the links of one page to anchors.tsv.

    Steps:
        1. Make the crawl directory.
        2. Append one row for each link: source URL, target URL, anchor text, title.

    Returns:
        None.
    """
    # 1. Make the crawl directory
    ANCHORS_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 2. Append one row for each link: source URL, target URL, anchor text, title
    with ANCHORS_PATH.open("a", encoding="utf-8") as file_handle:
        for link in links:
            cells = (source_url, link.url, link.anchor_text, link.title)
            file_handle.write("\t".join(_get_tsv_cell(cell) for cell in cells) + "\n")


def get_relative_path(path: Path) -> str:
    """
    Make a path relative to the project root.

    Steps:
        1. Remove the project root and use forward slashes.

    Returns:
        Path such as "data/raw/armor/armor-1.html".
    """
    # 1. Remove the project root and use forward slashes
    return path.relative_to(PROJECT_ROOT).as_posix()
