# ==============================================================================
# CRAWLER CONFIG — MilSearch
# ==============================================================================
# PURPOSE:
#     Keep all settings of the MilitaryFactory.com crawler in one module:
#     file paths, crawl scope, HTTP headers, timeouts, politeness and limits.
#
# RESPONSIBILITIES:
#     1. Define the layout of the data directory.
#     2. Define the crawl scope: host, categories and seed URLs.
#     3. Define the HTTP request settings and the politeness policy.
#     4. Define the limits that protect the crawler against crawler traps.
#
# INVARIANTS:
#     The crawler keeps all state in plain text files (HTML, JSONL, TSV).
#     The crawler does not use a database.
# ==============================================================================

from __future__ import annotations

from pathlib import Path

# ==============================================================================
# CONSTANTS
# ==============================================================================

# ------------------------------------------------------------------------------
# Paths
# ------------------------------------------------------------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = PROJECT_ROOT / "data"
RAW_DIR: Path = DATA_DIR / "raw"                          # Downloaded HTML pages.
CRAWL_DIR: Path = DATA_DIR / "crawl"                      # Crawl state.
DISCOVERED_PATH: Path = CRAWL_DIR / "discovered.jsonl"    # One line for each new URL.
FETCHED_PATH: Path = CRAWL_DIR / "fetched.jsonl"          # One line for each download attempt.
ANCHORS_PATH: Path = CRAWL_DIR / "anchors.tsv"            # Link graph with anchor texts.
PARSED_DIR: Path = DATA_DIR / "parsed"                    # Output of the regex extractor.

# ------------------------------------------------------------------------------
# Crawl scope
# ------------------------------------------------------------------------------
BASE_URL: str = "https://www.militaryfactory.com"
ALLOWED_HOST: str = "www.militaryfactory.com"
CATEGORIES: tuple[str, ...] = ("smallarms", "armor", "ships")

# The crawler goes from index.php to indexMAIN.php (A-Z catalog) and then to the detail pages.
SEED_URLS: dict[str, str] = {category: f"{BASE_URL}/{category}/index.php" for category in CATEGORIES}

# ------------------------------------------------------------------------------
# HTTP
# ------------------------------------------------------------------------------
# The crawler identifies itself. It does not show itself as a web browser.
USER_AGENT: str = (
    "MilSearchBot/0.1 (+academic search-engine project; FIIT STU Bratislava, course VINF)"
)
DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
    "Accept-Language": "en-US,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}
CONNECT_TIMEOUT_SECONDS: float = 5.0   # A TCP/TLS handshake to the CDN takes less than 1 s.
READ_TIMEOUT_SECONDS: float = 20.0     # The largest page (2 MB) arrives in less than 1 s.
MAX_CONTENT_BYTES: int = 8 * 1024 * 1024  # 4 times the largest page (A-Z catalog, 2 MB).
ALLOWED_MIME_TYPE: str = "text/html"

# ------------------------------------------------------------------------------
# Politeness
# ------------------------------------------------------------------------------
REQUEST_DELAY_SECONDS: float = 2.0     # Minimum time between two requests to the site.
DELAY_JITTER_SECONDS: float = 1.0      # Random 0 to 1 s added to the delay. No fixed rhythm.
MAX_RETRY_COUNT: int = 3               # Retries after HTTP 429, HTTP 5xx, timeout or connection error.
BACKOFF_BASE_SECONDS: float = 5.0      # Wait time 5, 10, 20 s. Longer than a usual rate-limit window.
MAX_CONSECUTIVE_FAILURE_COUNT: int = 10  # 10 failures in sequence means a block or a server outage.

# ------------------------------------------------------------------------------
# Limits (protection against crawler traps)
# ------------------------------------------------------------------------------
MAX_DEPTH: int = 3                     # index -> catalog -> detail -> linked detail.
MAX_URL_LENGTH: int = 300              # The longest valid detail URL has approx. 130 characters.
PROGRESS_LOG_INTERVAL: int = 25        # Pages between two progress log lines (approx. 1 min).
