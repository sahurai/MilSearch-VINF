# ==============================================================================
# WIKIPEDIA DUMP DOWNLOAD — MilSearch
# ==============================================================================
# PURPOSE:
#     Download the Wikipedia dump files for the Spark jobs: the English articles
#     (split files), the Slovak articles and the Slovak interlanguage links.
#
# RESPONSIBILITIES:
#     1. Get the list of files and their MD5 sums from the dump directory.
#     2. Download each file with resume (HTTP Range). Use 2 connections at a time.
#     3. Check the MD5 sum of each file. Download a file with a wrong sum again.
#
# USAGE:
#     python -m tools.download_wiki                  # EN split files + SK files
#     python -m tools.download_wiki --only sk        # SK files only
#     python -m tools.download_wiki --dump-date 20261101
#
# INVARIANTS:
#     Wikimedia permits at most 2-3 connections from one address (HTTP 429 after that).
#     A downloaded file is never deleted. A file with a wrong MD5 sum gets a ".bad" suffix.
# ==============================================================================

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from crawler.config import DATA_DIR, USER_AGENT

# ==============================================================================
# CONSTANTS
# ==============================================================================

logger = logging.getLogger("download_wiki")

DUMP_ROOT_URL = "https://dumps.wikimedia.org"
WIKI_DUMP_DIR: Path = DATA_DIR / "wiki" / "dump"
DEFAULT_DUMP_DATE = "20261101"
WORKERS_COUNT = 2
CHUNK_BYTES = 1024 * 1024
MAX_ATTEMPT_COUNT = 8
BACKOFF_BASE_SECONDS = 10.0

# EN: the split article files ("pages-articles12.xml-p7054860p8554859.bz2"). Each file is one Spark input split group.
_EN_FILE_RE = re.compile(r"^([0-9a-f]{32})\s+(enwiki-\d+-pages-articles\d+\.xml-p\d+p\d+\.bz2)$")
# SK: the full article file and the interlanguage links (SQL dump, parsed with regex in Spark).
_SK_FILE_RE = re.compile(r"^([0-9a-f]{32})\s+(skwiki-\d+-(?:pages-articles\.xml\.bz2|langlinks\.sql\.gz))$")


# ==============================================================================
# HELPERS
# ==============================================================================

def compute_md5(path: Path) -> str:
    """
    Compute the MD5 sum of a file.

    Steps:
        1. Read the file in chunks and update the hash.

    Returns:
        Hexadecimal MD5 digest.
    """
    # 1. Read the file in chunks and update the hash
    digest = hashlib.md5()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_file_list(session: requests.Session, wiki: str, dump_date: str) -> list[tuple[str, str]]:
    """
    Get the dump files of one wiki and their MD5 sums.

    Steps:
        1. Download the md5sums file of the dump.
        2. Keep the lines that match the file pattern of the wiki.

    Returns:
        List of (md5, file name).
    """
    # 1. Download the md5sums file of the dump
    url = f"{DUMP_ROOT_URL}/{wiki}/{dump_date}/{wiki}-{dump_date}-md5sums.txt"
    response = session.get(url, timeout=(5, 60))
    response.raise_for_status()

    # 2. Keep the lines that match the file pattern of the wiki
    pattern = _EN_FILE_RE if wiki == "enwiki" else _SK_FILE_RE
    matches = (pattern.match(line.strip()) for line in response.text.splitlines())
    return [(match.group(1), match.group(2)) for match in matches if match]


def fetch_file(session: requests.Session, url: str, path: Path) -> None:
    """
    Download one file. Continue a partial file with an HTTP Range request.

    Steps:
        1. Ask only for the bytes after the end of the partial file.
        2. Append the response to the file. Retry with backoff after HTTP 429, 5xx or a network error.

    Returns:
        None.
    """
    for attempt in range(1, MAX_ATTEMPT_COUNT + 1):
        # 1. Ask only for the bytes after the end of the partial file
        offset_bytes = path.stat().st_size if path.exists() else 0
        headers = {"Range": f"bytes={offset_bytes}-"} if offset_bytes else {}
        try:
            with session.get(url, headers=headers, stream=True, timeout=(5, 60)) as response:
                if response.status_code == 416:
                    return  # The file is complete.
                response.raise_for_status()
                if offset_bytes and response.status_code != 206:
                    offset_bytes = 0  # The server ignored the Range header.

                # 2. Append the response to the file
                with path.open("ab" if offset_bytes else "wb") as file_handle:
                    for chunk in response.iter_content(CHUNK_BYTES):
                        file_handle.write(chunk)
            return
        except requests.RequestException as error:
            wait_seconds = BACKOFF_BASE_SECONDS * attempt
            logger.warning("RETRY download | file=%s attempt=%d wait_s=%.0f error=%s",
                           path.name, attempt, wait_seconds, error)
            time.sleep(wait_seconds)
    raise RuntimeError(f"download failed after {MAX_ATTEMPT_COUNT} attempts: {url}")


def download_checked(session: requests.Session, wiki: str, dump_date: str, md5: str, name: str) -> None:
    """
    Download one file and check its MD5 sum.

    Steps:
        1. Skip a file that exists and has the correct MD5 sum.
        2. Download the file; on a wrong MD5 sum rename it to ".bad" and download it again once.

    Returns:
        None.
    """
    path = WIKI_DUMP_DIR / wiki[:2] / name
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"{DUMP_ROOT_URL}/{wiki}/{dump_date}/{name}"
    for _ in range(2):
        # 1. Skip a file that exists and has the correct MD5 sum
        if path.exists() and compute_md5(path) == md5:
            logger.info("CHECKED file | file=%s size_mb=%.1f", name, path.stat().st_size / 1e6)
            return

        # 2. Download the file; on a wrong MD5 sum rename it and download it again once
        if path.exists() and path.stat().st_size > 0:
            fetch_file(session, url, path)
            if compute_md5(path) == md5:
                logger.info("SAVED file | file=%s size_mb=%.1f", name, path.stat().st_size / 1e6)
                return
            path.rename(path.with_name(path.name + ".bad"))
        else:
            fetch_file(session, url, path)
    if compute_md5(path) != md5:
        raise RuntimeError(f"MD5 mismatch: {name}")
    logger.info("SAVED file | file=%s size_mb=%.1f", name, path.stat().st_size / 1e6)


# ==============================================================================
# ENTRY POINT
# ==============================================================================

def main() -> None:
    """
    Download and check the dump files.

    Steps:
        1. Parse the arguments and open an HTTP session with the MilSearch User-Agent.
        2. Get the file lists of the selected wikis.
        3. Download the files with 2 workers.

    Returns:
        None.
    """
    # 1. Parse the arguments and open an HTTP session with the MilSearch User-Agent
    parser = argparse.ArgumentParser(description="Download the Wikipedia dump files for the Spark jobs.")
    parser.add_argument("--dump-date", default=DEFAULT_DUMP_DATE, help="Dump date, for example 20261101.")
    parser.add_argument("--only", choices=("en", "sk"), help="Download only one wiki.")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    # 2. Get the file lists of the selected wikis
    wikis = [wiki for wiki in ("skwiki", "enwiki") if arguments.only in (None, wiki[:2])]
    jobs = [(wiki, md5, name) for wiki in wikis for md5, name in fetch_file_list(session, wiki, arguments.dump_date)]
    logger.info("STARTED download | files_count=%d", len(jobs))

    # 3. Download the files with 2 workers
    with ThreadPoolExecutor(max_workers=WORKERS_COUNT) as executor:
        futures = [executor.submit(download_checked, session, wiki, arguments.dump_date, md5, name)
                   for wiki, md5, name in jobs]
        for future in futures:
            future.result()
    logger.info("FINISHED download | files_count=%d", len(jobs))


if __name__ == "__main__":
    main()
