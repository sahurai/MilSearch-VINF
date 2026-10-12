# ==============================================================================
# PAGE FETCHER — MilSearch
# ==============================================================================
# PURPOSE:
#     Download one HTML page with the politeness rules of the crawler:
#     robots.txt, a minimum delay between requests, timeouts, retries and backoff.
#
# RESPONSIBILITIES:
#     1. Read robots.txt at start and answer if a URL is permitted.
#     2. Keep the minimum delay (plus random jitter) between two requests.
#     3. Retry temporary errors (network error, HTTP 429, HTTP 5xx) with exponential backoff.
#     4. Reject responses that are not HTML, are too large or are blocked.
#
# DEPENDENCIES:
#     crawler.config — HTTP headers, timeouts, delays, retry limits.
#
# FORMULAS:
#     backoff_seconds = max(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), Retry-After)
# ==============================================================================

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from urllib import robotparser

import requests

from crawler import config

# ==============================================================================
# CONSTANTS
# ==============================================================================

logger = logging.getLogger(__name__)

_HTTP_OK = 200
_HTTP_FORBIDDEN = 403
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR_MIN = 500
_CLOUDFLARE_CHALLENGE_MARKER = "cf-chl"


# ==============================================================================
# TYPES
# ==============================================================================

@dataclass(frozen=True)
class FetchResult:
    """
    Successful download of one HTML page.

    Responsibilities:
        1. Carry the requested URL, the final URL after redirects and the page body.
        2. Carry the request metrics for the crawl records.
    """

    url: str
    final_url: str
    http_status: int
    content_type: str
    body: str
    elapsed_ms: int
    attempts_count: int


class FetchError(Exception):
    """
    Download failed after all retries, or the response is not permitted.

    Responsibilities:
        1. Carry the HTTP status (None for a network error) and the number of attempts.
    """

    def __init__(self, message: str, http_status: int | None = None, attempts_count: int = 0) -> None:
        """
        Keep the error message, the HTTP status and the number of attempts.

        Steps:
            1. Initialize the exception and keep the extra fields.

        Returns:
            None.
        """
        # 1. Initialize the exception and keep the extra fields
        super().__init__(message)
        self.http_status = http_status
        self.attempts_count = attempts_count


# ==============================================================================
# HELPERS
# ==============================================================================

def _is_retryable_status(http_status: int) -> bool:
    """
    Tell if an HTTP status is a temporary server condition.

    Steps:
        1. Accept HTTP 429 and all HTTP 5xx statuses.

    Returns:
        True for HTTP 429 and HTTP 5xx.
    """
    # 1. Accept HTTP 429 and all HTTP 5xx statuses
    return http_status == _HTTP_TOO_MANY_REQUESTS or http_status >= _HTTP_SERVER_ERROR_MIN


def _sleep_backoff(attempt: int, retry_after: str | None, reason: str) -> None:
    """
    Wait before the next attempt (exponential backoff).

    Steps:
        1. Compute the wait time from the attempt number and Retry-After.
        2. Log the retry and sleep.

    Returns:
        None.
    """
    # 1. Compute the wait time from the attempt number and Retry-After
    wait_seconds = config.BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)
    if retry_after and retry_after.isdigit():
        wait_seconds = max(wait_seconds, float(retry_after))

    # 2. Log the retry and sleep
    logger.warning(
        "RETRY request | reason=%s attempt=%d max_retry_count=%d wait_seconds=%.0f",
        reason, attempt, config.MAX_RETRY_COUNT, wait_seconds,
    )
    time.sleep(wait_seconds)


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

class Fetcher:
    """
    Polite HTTP client for one site.

    Responsibilities:
        1. Keep one HTTP session (keep-alive connection, default headers).
        2. Keep the robots.txt rules and the time of the last request.
        3. Download pages with delay, timeouts, retries and backoff.

    Invariants:
        The fetcher sends one request at a time.
        The time between two requests is at least delay_seconds.
    """

    def __init__(
        self,
        delay_seconds: float = config.REQUEST_DELAY_SECONDS,
        jitter_seconds: float = config.DELAY_JITTER_SECONDS,
    ) -> None:
        """
        Open the HTTP session and read robots.txt.

        Steps:
            1. Open the session with the default headers.
            2. Read robots.txt.

        Returns:
            None.
        """
        # 1. Open the session with the default headers
        self.delay_seconds = delay_seconds
        self.jitter_seconds = jitter_seconds
        self.session = requests.Session()
        self.session.headers.update(config.DEFAULT_HEADERS)
        self.last_request_at = 0.0

        # 2. Read robots.txt
        self.robots = robotparser.RobotFileParser(f"{config.BASE_URL}/robots.txt")
        self._fetch_robots()

    def _fetch_robots(self) -> None:
        """
        Read robots.txt and apply its Crawl-delay.

        Steps:
            1. Download robots.txt; permit all URLs when it is not available.
            2. Use Crawl-delay when it is larger than the configured delay.

        Returns:
            None.
        """
        # 1. Download robots.txt; permit all URLs when it is not available
        try:
            response = self.session.get(
                self.robots.url,
                timeout=(config.CONNECT_TIMEOUT_SECONDS, config.READ_TIMEOUT_SECONDS),
            )
            self.last_request_at = time.monotonic()
            self.robots.parse(response.text.splitlines() if response.ok else [])
        except requests.RequestException as exc:
            logger.warning("SKIPPED robots.txt | reason=unavailable error=%r", exc)
            self.robots.parse([])

        # 2. Use Crawl-delay when it is larger than the configured delay
        crawl_delay = self.robots.crawl_delay(config.USER_AGENT)
        if crawl_delay and float(crawl_delay) > self.delay_seconds:
            logger.info("OVERRIDE delay | crawl_delay_seconds=%s", crawl_delay)
            self.delay_seconds = float(crawl_delay)

    def is_allowed(self, url: str) -> bool:
        """
        Tell if robots.txt permits a URL.

        Steps:
            1. Ask the robots.txt parser for the crawler user agent.

        Returns:
            True when the crawler can download the URL.
        """
        # 1. Ask the robots.txt parser for the crawler user agent
        return self.robots.can_fetch(config.USER_AGENT, url)

    def _wait_for_turn(self) -> None:
        """
        Wait until the minimum delay since the last request is over.

        Steps:
            1. Compute the gap: delay plus random jitter.
            2. Sleep for the remaining part of the gap.

        Returns:
            None.
        """
        # 1. Compute the gap: delay plus random jitter
        gap_seconds = self.delay_seconds + random.uniform(0, self.jitter_seconds)

        # 2. Sleep for the remaining part of the gap
        remaining_seconds = gap_seconds - (time.monotonic() - self.last_request_at)
        if remaining_seconds > 0:
            time.sleep(remaining_seconds)

    def fetch_page(self, url: str, referer: str | None = None) -> FetchResult:
        """
        Download one HTML page.

        Steps:
            1. Wait for the politeness delay and send the GET request.
            2. Retry with backoff after a network error, HTTP 429 or HTTP 5xx.
            3. Reject a blocked, non-200, non-HTML or too large response.
            4. Decode the body and build the result.

        Returns:
            FetchResult of the downloaded page.

        Raises:
            FetchError when the page cannot be downloaded or is not permitted.
        """
        headers = {"Referer": referer} if referer else {}
        for attempt in range(1, config.MAX_RETRY_COUNT + 2):
            # 1. Wait for the politeness delay and send the GET request
            self._wait_for_turn()
            started_at = time.monotonic()
            self.last_request_at = started_at
            try:
                response = self.session.get(
                    url,
                    headers=headers,
                    timeout=(config.CONNECT_TIMEOUT_SECONDS, config.READ_TIMEOUT_SECONDS),
                    allow_redirects=True,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                # 2. Retry with backoff after a network error, HTTP 429 or HTTP 5xx
                if attempt > config.MAX_RETRY_COUNT:
                    raise FetchError(f"network error: {exc!r}", None, attempt) from exc
                _sleep_backoff(attempt, None, type(exc).__name__)
                continue
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            if _is_retryable_status(response.status_code):
                if attempt > config.MAX_RETRY_COUNT:
                    raise FetchError(f"HTTP {response.status_code}", response.status_code, attempt)
                _sleep_backoff(attempt, response.headers.get("Retry-After"), f"http_{response.status_code}")
                continue

            # 3. Reject a blocked, non-200, non-HTML or too large response
            if response.status_code == _HTTP_FORBIDDEN and _CLOUDFLARE_CHALLENGE_MARKER in response.text:
                raise FetchError("Cloudflare challenge blocked the request", response.status_code, attempt)
            if response.status_code != _HTTP_OK:
                raise FetchError(f"HTTP {response.status_code}", response.status_code, attempt)
            content_type = response.headers.get("Content-Type", "")
            if config.ALLOWED_MIME_TYPE not in content_type:
                raise FetchError(f"MIME type is not permitted: {content_type!r}", response.status_code, attempt)
            if len(response.content) > config.MAX_CONTENT_BYTES:
                raise FetchError(f"page is too large: {len(response.content)} B", response.status_code, attempt)

            # 4. Decode the body and build the result
            # Without a charset, requests decodes as ISO-8859-1. The site sends UTF-8.
            if "charset" not in content_type.lower():
                response.encoding = "utf-8"
            return FetchResult(
                url=url,
                final_url=response.url,
                http_status=response.status_code,
                content_type=content_type,
                body=response.text,
                elapsed_ms=elapsed_ms,
                attempts_count=attempt,
            )
        raise AssertionError("retry loop exits only through return or raise")
