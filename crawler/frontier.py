# ==============================================================================
# URL FRONTIER — MilSearch
# ==============================================================================
# PURPOSE:
#     Keep the queue of URLs that the crawler must download. Keep the state in
#     append-only text files, so that a stopped crawl can continue at a later time.
#
# RESPONSIBILITIES:
#     1. Load the crawl state from discovered.jsonl and fetched.jsonl at start.
#     2. Add new URLs to the queue and to discovered.jsonl.
#     3. Give the next URL: breadth-first (FIFO) for each category, round-robin across categories.
#     4. Save the result of each download attempt to fetched.jsonl.
#     5. Find duplicates by final URL and by content hash.
#
# INVARIANTS:
#     The frontier only appends lines to its files. It does not change or delete data.
#     A URL with a final status ("done", "duplicate", "blocked") does not go back into the queue.
#     A URL with the status "failed" goes back into the queue at the next start.
# ==============================================================================

from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

# ==============================================================================
# CONSTANTS
# ==============================================================================

FINAL_STATUSES: frozenset[str] = frozenset({"done", "duplicate", "blocked"})


# ==============================================================================
# TYPES
# ==============================================================================

CrawlRecord = dict[str, Any]  # Any: JSON lines hold str, int and None values.


# ==============================================================================
# HELPERS
# ==============================================================================

def _load_jsonl(path: Path) -> Iterator[CrawlRecord]:
    """
    Read a JSONL file line by line.

    Steps:
        1. Return no records when the file does not exist.
        2. Parse each line that is not empty.

    Returns:
        Iterator over the records in file order.
    """
    # 1. Return no records when the file does not exist
    if not path.exists():
        return

    # 2. Parse each line that is not empty
    with path.open(encoding="utf-8") as file_handle:
        for line in file_handle:
            if line.strip():
                yield json.loads(line)


def _append_jsonl(path: Path, record: CrawlRecord) -> None:
    """
    Append one record to a JSONL file.

    Steps:
        1. Write the record as one JSON line.

    Returns:
        None.
    """
    # 1. Write the record as one JSON line
    with path.open("a", encoding="utf-8") as file_handle:
        file_handle.write(json.dumps(record, ensure_ascii=False) + "\n")


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

class Frontier:
    """
    URL queue of the crawler with a file-based state.

    Responsibilities:
        1. Keep one FIFO queue for each category and give URLs round-robin.
        2. Keep the sets of known URLs and finished URLs.
        3. Keep the map from content hash to URL for duplicate detection.

    Invariants:
        Each URL is added to discovered.jsonl only one time.
    """

    def __init__(self, discovered_path: Path, fetched_path: Path, categories: Iterable[str]) -> None:
        """
        Load the crawl state and make the queues again.

        Steps:
            1. Make the empty queues and sets.
            2. Load the finished URLs and the content hashes from fetched.jsonl.
            3. Load the known URLs from discovered.jsonl and queue the URLs that are not finished.

        Returns:
            None.
        """
        # 1. Make the empty queues and sets
        self.discovered_path = discovered_path
        self.fetched_path = fetched_path
        discovered_path.parent.mkdir(parents=True, exist_ok=True)
        self.category_order: list[str] = list(categories)
        self.queues: dict[str, deque[CrawlRecord]] = {category: deque() for category in self.category_order}
        self.turn_index = 0
        self.known_urls: set[str] = set()
        self.finished_urls: set[str] = set()
        self.url_by_sha1: dict[str, str] = {}

        # 2. Load the finished URLs and the content hashes from fetched.jsonl
        for record in _load_jsonl(fetched_path):
            self._remember_result(record)

        # 3. Load the known URLs from discovered.jsonl and queue the URLs that are not finished
        for record in _load_jsonl(discovered_path):
            self.known_urls.add(record["url"])
            if record["url"] not in self.finished_urls and record["category"] in self.queues:
                self.queues[record["category"]].append(record)

    def _remember_result(self, record: CrawlRecord) -> None:
        """
        Update the finished URLs and the content hashes from one download result.

        Steps:
            1. Mark a URL with a final status as finished.
            2. For a saved page, also mark the final URL as finished and keep the hash.

        Returns:
            None.
        """
        # 1. Mark a URL with a final status as finished
        if record["status"] in FINAL_STATUSES:
            self.finished_urls.add(record["url"])

        # 2. For a saved page, also mark the final URL as finished and keep the hash
        if record["status"] == "done":
            self.finished_urls.add(record.get("final_url", record["url"]))
            self.url_by_sha1[record["sha1"]] = record["url"]

    def add_url(self, url: str, kind: str, category: str, depth: int, parent_url: str | None) -> bool:
        """
        Add a URL to the queue if the frontier does not know it.

        Steps:
            1. Ignore a known URL.
            2. Queue the URL and append it to discovered.jsonl.

        Returns:
            True when the URL is new; False when the frontier already knows it.
        """
        # 1. Ignore a known URL
        if url in self.known_urls:
            return False

        # 2. Queue the URL and append it to discovered.jsonl
        record = {"url": url, "kind": kind, "category": category, "depth": depth, "parent_url": parent_url}
        self.known_urls.add(url)
        if category in self.queues:
            self.queues[category].append(record)
        _append_jsonl(self.discovered_path, record)
        return True

    def pop_next_record(self) -> CrawlRecord | None:
        """
        Take the next URL from the queues.

        Steps:
            1. Go to the next category queue (round-robin).
            2. Skip a URL that a redirect from a different URL already finished.

        Returns:
            Discovered record of the next URL; None when all queues are empty.
        """
        while self.get_pending_count():
            # 1. Go to the next category queue (round-robin)
            queue = self.queues[self.category_order[self.turn_index]]
            self.turn_index = (self.turn_index + 1) % len(self.category_order)
            if not queue:
                continue

            # 2. Skip a URL that a redirect from a different URL already finished
            record = queue.popleft()
            if record["url"] not in self.finished_urls:
                return record
        return None

    def save_result(self, record: CrawlRecord) -> None:
        """
        Save the result of one download attempt.

        Steps:
            1. Update the finished URLs and the content hashes.
            2. Append the record to fetched.jsonl.

        Returns:
            None.
        """
        # 1. Update the finished URLs and the content hashes
        self._remember_result(record)

        # 2. Append the record to fetched.jsonl
        _append_jsonl(self.fetched_path, record)

    def get_duplicate_url(self, url: str, final_url: str, sha1: str) -> str | None:
        """
        Find a saved page that is the same as a new page.

        Steps:
            1. Compare the final URL after redirects with the finished URLs.
            2. Compare the content hash with the hashes of the saved pages.

        Returns:
            URL of the saved page; None when the page is new.
        """
        # 1. Compare the final URL after redirects with the finished URLs
        if final_url != url and final_url in self.finished_urls:
            return final_url

        # 2. Compare the content hash with the hashes of the saved pages
        return self.url_by_sha1.get(sha1)

    def get_pending_count(self) -> int:
        """
        Count the URLs in all queues.

        Steps:
            1. Add the lengths of the category queues.

        Returns:
            Number of queued URLs.
        """
        # 1. Add the lengths of the category queues
        return sum(len(queue) for queue in self.queues.values())
