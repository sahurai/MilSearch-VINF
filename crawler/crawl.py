# ==============================================================================
# CRAWLER MAIN LOOP — MilSearch
# ==============================================================================
# PURPOSE:
#     Crawl the Small Arms, Armor and Ships catalogs of MilitaryFactory.com.
#     Download each page in the crawl scope one time and keep the crawl state on disk.
#
# RESPONSIBILITIES:
#     1. Read the command-line options.
#     2. Add the seed URLs to the frontier.
#     3. Take URLs from the frontier until it is empty or a limit is reached.
#     4. For each URL: download, find duplicates, save the page and the links, queue new URLs.
#     5. Stop cleanly on Ctrl+C. The next start continues from the saved state.
#
# DEPENDENCIES:
#     crawler.config — paths, seeds, limits.
#     crawler.fetcher — polite HTTP download.
#     crawler.frontier — URL queue with file-based state.
#     crawler.links — link extraction and URL scope.
#     crawler.storage — page and link-graph files.
#
# USAGE:
#     python -m crawler.crawl
#     python -m crawler.crawl --max-pages 40
#     python -m crawler.crawl --max-minutes 30 --categories ships
#     python -m crawler.crawl --seed-url URL [--seed-url URL ...]
#     python -m crawler.crawl --verbose
# ==============================================================================

from __future__ import annotations

import argparse
import logging
import signal
import threading
import time
from dataclasses import dataclass, field

from crawler import config
from crawler.fetcher import FetchError, Fetcher
from crawler.frontier import CrawlRecord, Frontier
from crawler.links import build_document_id, extract_links, get_url_scope
from crawler.storage import compute_sha1, get_relative_path, save_anchor_rows, save_page

# ==============================================================================
# CONSTANTS
# ==============================================================================

logger = logging.getLogger("crawler")


# ==============================================================================
# TYPES
# ==============================================================================

@dataclass(frozen=True)
class CrawlOptions:
    """
    Options of one crawl run.

    Responsibilities:
        1. Carry the categories, the limits, the extra seed URLs and the delay.
    """

    categories: tuple[str, ...] = config.CATEGORIES
    max_pages: int | None = None
    max_minutes: float | None = None
    max_depth: int = config.MAX_DEPTH
    seed_urls: tuple[str, ...] = field(default_factory=tuple)
    delay_seconds: float = config.REQUEST_DELAY_SECONDS


@dataclass(frozen=True)
class CrawlSummary:
    """
    Result of one crawl run.

    Responsibilities:
        1. Carry the number of downloaded pages, the number of queued URLs and the stop reason.
    """

    fetched_count: int
    pending_count: int
    stop_reason: str


# ==============================================================================
# HELPERS
# ==============================================================================

def _add_seed_urls(frontier: Frontier, options: CrawlOptions) -> None:
    """
    Add the category seed URLs and the extra seed URLs to the frontier.

    Steps:
        1. Add the index page of each selected category.
        2. Add each extra seed URL after a scope check.

    Returns:
        None.

    Raises:
        SystemExit when an extra seed URL is not in the crawl scope.
    """
    # 1. Add the index page of each selected category
    for category in options.categories:
        frontier.add_url(config.SEED_URLS[category], "index", category, depth=0, parent_url=None)

    # 2. Add each extra seed URL after a scope check
    for url in options.seed_urls:
        scope = get_url_scope(url)
        if scope is None:
            raise SystemExit(f"The seed URL is not in the crawl scope: {url}")
        frontier.add_url(url, scope[0], scope[1], depth=0, parent_url=None)


def _crawl_one(frontier: Frontier, fetcher: Fetcher, item: CrawlRecord, options: CrawlOptions) -> str:
    """
    Process one URL from the frontier.

    Steps:
        1. Reject a URL that robots.txt does not permit.
        2. Download the page; save a failed attempt.
        3. Skip a page that is a duplicate by final URL or by content hash.
        4. Save the page and its links with anchor texts.
        5. Queue the new links that are not too deep.
        6. Save the result record.

    Returns:
        Status of the URL: "done", "duplicate", "failed" or "blocked".
    """
    url, category, depth = item["url"], item["category"], item["depth"]
    record: CrawlRecord = {
        "url": url, "category": category, "kind": item["kind"], "depth": depth,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    # 1. Reject a URL that robots.txt does not permit
    if not fetcher.is_allowed(url):
        frontier.save_result({**record, "status": "blocked", "error": "robots.txt"})
        logger.info("REJECTED page | reason=robots url=%s", url)
        return "blocked"

    # 2. Download the page; save a failed attempt
    try:
        result = fetcher.fetch_page(url, referer=item["parent_url"])
    except FetchError as exc:
        frontier.save_result({**record, "status": "failed", "http_status": exc.http_status,
                              "attempts_count": exc.attempts_count, "error": str(exc)})
        logger.error("FAILED page | url=%s http_status=%s error=%s", url, exc.http_status, exc)
        return "failed"
    record.update(http_status=result.http_status, final_url=result.final_url,
                  elapsed_ms=result.elapsed_ms, attempts_count=result.attempts_count,
                  size_bytes=len(result.body.encode("utf-8")))

    # 3. Skip a page that is a duplicate by final URL or by content hash
    sha1 = compute_sha1(result.body)
    duplicate_of_url = frontier.get_duplicate_url(url, result.final_url, sha1)
    if duplicate_of_url:
        frontier.save_result({**record, "status": "duplicate", "sha1": sha1,
                              "duplicate_of_url": duplicate_of_url})
        logger.debug("SKIPPED page | reason=duplicate url=%s duplicate_of_url=%s", url, duplicate_of_url)
        return "duplicate"

    # 4. Save the page and its links with anchor texts
    # After a redirect, the final URL names the file, because it is the real page.
    name_url = result.final_url if get_url_scope(result.final_url) else url
    path = save_page(category, build_document_id(name_url), result.body)
    links = extract_links(result.body, result.final_url)
    save_anchor_rows(url, links)

    # 5. Queue the new links that are not too deep
    new_links_count = 0
    if depth < options.max_depth:
        for link in links:
            is_new = link.category in options.categories and frontier.add_url(
                link.url, link.kind, link.category, depth + 1, url,
            )
            new_links_count += int(is_new)

    # 6. Save the result record
    frontier.save_result({**record, "status": "done", "sha1": sha1, "file": get_relative_path(path),
                          "links_count": len(links), "new_links_count": new_links_count})
    logger.debug(
        "SAVED page | category=%s kind=%s depth=%d url=%s elapsed_ms=%d links_count=%d new_links_count=%d",
        category, item["kind"], depth, url, result.elapsed_ms, len(links), new_links_count,
    )
    return "done"


def _parse_options() -> tuple[CrawlOptions, bool]:
    """
    Read the command-line options.

    Steps:
        1. Define the options and parse the command line.
        2. Build the CrawlOptions.

    Returns:
        (CrawlOptions, is_verbose).
    """
    # 1. Define the options and parse the command line
    parser = argparse.ArgumentParser(description="MilSearch crawler for MilitaryFactory.com")
    parser.add_argument("--categories", nargs="+", choices=config.CATEGORIES, default=list(config.CATEGORIES))
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--max-minutes", type=float, default=None)
    parser.add_argument("--max-depth", type=int, default=config.MAX_DEPTH)
    parser.add_argument("--seed-url", action="append", default=[])
    parser.add_argument("--delay", type=float, default=config.REQUEST_DELAY_SECONDS)
    parser.add_argument("--verbose", action="store_true", help="log each page (DEBUG level)")
    arguments = parser.parse_args()

    # 2. Build the CrawlOptions
    options = CrawlOptions(
        categories=tuple(arguments.categories),
        max_pages=arguments.max_pages,
        max_minutes=arguments.max_minutes,
        max_depth=arguments.max_depth,
        seed_urls=tuple(arguments.seed_url),
        delay_seconds=arguments.delay,
    )
    return options, arguments.verbose


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def run_crawl(options: CrawlOptions, stop_event: threading.Event) -> CrawlSummary:
    """
    Crawl until the frontier is empty, a limit is reached or stop_event is set.

    Steps:
        1. Load the frontier, open the fetcher and add the seed URLs.
        2. Check the stop event, the page limit and the time limit.
        3. Process the next URL and count the downloads and the failures.
        4. Log the progress at a fixed interval.

    Returns:
        CrawlSummary with the number of downloaded pages and the stop reason.
    """
    # 1. Load the frontier, open the fetcher and add the seed URLs
    frontier = Frontier(config.DISCOVERED_PATH, config.FETCHED_PATH, options.categories)
    fetcher = Fetcher(delay_seconds=options.delay_seconds)
    _add_seed_urls(frontier, options)
    logger.info("STARTED crawl | categories=%s pending_count=%d", ",".join(options.categories),
                frontier.get_pending_count())

    deadline_at = time.monotonic() + options.max_minutes * 60 if options.max_minutes else None
    fetched_count = 0
    consecutive_failures_count = 0
    stop_reason = "frontier_empty"
    while True:
        # 2. Check the stop event, the page limit and the time limit
        if stop_event.is_set():
            stop_reason = "interrupted"
            break
        if options.max_pages is not None and fetched_count >= options.max_pages:
            stop_reason = "max_pages"
            break
        if deadline_at is not None and time.monotonic() > deadline_at:
            stop_reason = "max_minutes"
            break
        item = frontier.pop_next_record()
        if item is None:
            break

        # 3. Process the next URL and count the downloads and the failures
        status = _crawl_one(frontier, fetcher, item, options)
        if status == "failed":
            consecutive_failures_count += 1
            if consecutive_failures_count >= config.MAX_CONSECUTIVE_FAILURE_COUNT:
                logger.error("STOPPED crawl | reason=consecutive_failures consecutive_failures_count=%d",
                             consecutive_failures_count)
                stop_reason = "consecutive_failures"
                break
            continue
        consecutive_failures_count = 0
        if status in ("done", "duplicate"):
            fetched_count += 1

        # 4. Log the progress at a fixed interval
        if fetched_count and fetched_count % config.PROGRESS_LOG_INTERVAL == 0 and status == "done":
            logger.info("PROGRESS crawl | fetched_count=%d pending_count=%d",
                        fetched_count, frontier.get_pending_count())

    return CrawlSummary(fetched_count, frontier.get_pending_count(), stop_reason)


# ==============================================================================
# ENTRY POINT
# ==============================================================================

def main() -> None:
    """
    Run the crawler from the command line.

    Steps:
        1. Read the options and configure the logging.
        2. Connect Ctrl+C to the stop event.
        3. Run the crawl and log the summary.

    Returns:
        None.
    """
    # 1. Read the options and configure the logging
    options, is_verbose = _parse_options()
    logging.basicConfig(
        level=logging.DEBUG if is_verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    # 2. Connect Ctrl+C to the stop event
    stop_event = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop_event.set())

    # 3. Run the crawl and log the summary
    summary = run_crawl(options, stop_event)
    logger.info("STOPPED crawl | reason=%s fetched_count=%d pending_count=%d",
                summary.stop_reason, summary.fetched_count, summary.pending_count)


if __name__ == "__main__":
    main()
