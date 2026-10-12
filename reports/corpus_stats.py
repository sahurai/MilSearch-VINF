# ==============================================================================
# CORPUS STATISTICS — MilSearch
# ==============================================================================
# PURPOSE:
#     Measure the downloaded corpus: crawl results, size on disk and number of tokens.
#     Give a JSON report and DokuWiki tables for the consultation wiki.
#
# RESPONSIBILITIES:
#     1. Count the crawl results by status and category, and the crawl duration.
#     2. Measure the size on disk of the raw pages, the parsed data and the index.
#     3. Count tokens with the own tokenizer and with tiktoken (raw HTML and extracted text).
#     4. Write data/reports/corpus_stats.json and data/reports/corpus_stats.dokuwiki.txt.
#
# DEPENDENCIES:
#     crawler.config — data paths, categories.
#     indexer.build — index paths.
#     indexer.tokenizer — own tokenizer.
#
# USAGE:
#     python -m reports.corpus_stats
#     python -m reports.corpus_stats --skip-raw-tokens
#
# INVARIANTS:
#     tiktoken is optional. Without it, the report has no tiktoken columns.
# ==============================================================================

from __future__ import annotations

import argparse
import json
import logging
import statistics
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from crawler.config import CATEGORIES, CRAWL_DIR, DATA_DIR, FETCHED_PATH, PARSED_DIR, RAW_DIR
from indexer.build import INDEX_DIR, META_PATH
from indexer.tokenizer import get_terms, get_tokens

# ==============================================================================
# CONSTANTS
# ==============================================================================

logger = logging.getLogger("reports.corpus_stats")

REPORTS_DIR: Path = DATA_DIR / "reports"
STATS_JSON_PATH: Path = REPORTS_DIR / "corpus_stats.json"
STATS_WIKI_PATH: Path = REPORTS_DIR / "corpus_stats.dokuwiki.txt"

TIKTOKEN_ENCODINGS: tuple[str, ...] = ("o200k_base", "cl100k_base")  # GPT-4o and GPT-4 encodings.
_TEXT_FIELDS: tuple[str, ...] = ("name", "type", "summary", "text")  # Extracted plain text of a document.
_BYTES_IN_MB = 1024 * 1024


# ==============================================================================
# TYPES
# ==============================================================================

Report = dict[str, Any]  # Any: nested counts, sizes and labels.


# ==============================================================================
# HELPERS
# ==============================================================================

def _load_jsonl(path: Path) -> list[dict[str, Any]]:  # Any: JSON values have mixed types.
    """
    Read a JSONL file.

    Steps:
        1. Return no records when the file does not exist; otherwise parse each line.

    Returns:
        Records in file order.
    """
    # 1. Return no records when the file does not exist; otherwise parse each line
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as file_handle:
        return [json.loads(line) for line in file_handle if line.strip()]


def _get_directory_bytes(directory: Path, pattern: str = "*") -> tuple[int, int]:
    """
    Measure the files in a directory tree.

    Steps:
        1. Add the sizes of all files that match the pattern.

    Returns:
        (files count, total bytes).
    """
    # 1. Add the sizes of all files that match the pattern
    files = [path for path in directory.rglob(pattern) if path.is_file()] if directory.exists() else []
    return len(files), sum(path.stat().st_size for path in files)


def _get_mb(size_bytes: float) -> float:
    """
    Change bytes to megabytes.

    Steps:
        1. Divide by 1024 * 1024 and round to two decimals.

    Returns:
        Size in MB.
    """
    # 1. Divide by 1024 * 1024 and round to two decimals
    return round(size_bytes / _BYTES_IN_MB, 2)


def _load_encoders() -> dict[str, Any]:  # Any: tiktoken.Encoding, optional dependency.
    """
    Load the tiktoken encoders if tiktoken is installed.

    Steps:
        1. Import tiktoken; return no encoders when it is not installed.
        2. Load each encoding.

    Returns:
        Map from encoding name to encoder.
    """
    # 1. Import tiktoken; return no encoders when it is not installed
    try:
        import tiktoken
    except ImportError:
        logger.warning("SKIPPED tiktoken | reason=not_installed")
        return {}

    # 2. Load each encoding
    return {name: tiktoken.get_encoding(name) for name in TIKTOKEN_ENCODINGS}


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def compute_crawl_stats(records: list[dict[str, Any]]) -> Report:  # Any: JSON values have mixed types.
    """
    Count the crawl results.

    Steps:
        1. Count the attempts by status and the saved pages by category and kind.
        2. Compute the crawl duration, the mean request time and the retried requests.

    Returns:
        Crawl statistics.
    """
    # 1. Count the attempts by status and the saved pages by category and kind
    done = [record for record in records if record["status"] == "done"]
    report: Report = {
        "attempts_count": len(records),
        "status_counts": dict(Counter(record["status"] for record in records)),
        "done_by_category_kind": {
            f"{category}/{kind}": count
            for (category, kind), count in sorted(Counter((r["category"], r["kind"]) for r in done).items())
        },
    }

    # 2. Compute the crawl duration, the mean request time and the retried requests
    times = sorted(datetime.fromisoformat(record["fetched_at"]) for record in records if record.get("fetched_at"))
    report["first_fetched_at"] = times[0].isoformat() if times else None
    report["last_fetched_at"] = times[-1].isoformat() if times else None
    elapsed = [record["elapsed_ms"] for record in done if record.get("elapsed_ms") is not None]
    report["mean_elapsed_ms"] = round(statistics.mean(elapsed), 1) if elapsed else None
    report["retried_count"] = sum(1 for record in records if (record.get("attempts_count") or 1) > 1)
    return report


def compute_disk_stats() -> Report:
    """
    Measure the size on disk of the raw pages, the parsed data, the crawl state and the index.

    Steps:
        1. Measure the raw HTML pages of each category, and the detail-page sizes.
        2. Measure the parsed data, the crawl state and the index directories.

    Returns:
        Disk statistics in bytes and MB.
    """
    report: Report = {"raw_by_category": {}}

    # 1. Measure the raw HTML pages of each category, and the detail-page sizes
    for category in CATEGORIES:
        files = [path for path in (RAW_DIR / category).glob("*.html")]
        detail_sizes = [path.stat().st_size for path in files if not path.stem.endswith(("-index", "-catalog"))]
        total_bytes = sum(path.stat().st_size for path in files)
        report["raw_by_category"][category] = {
            "files_count": len(files),
            "detail_pages_count": len(detail_sizes),
            "bytes": total_bytes,
            "mb": _get_mb(total_bytes),
            "detail_mean_kb": round(statistics.mean(detail_sizes) / 1024, 1) if detail_sizes else None,
            "detail_min_kb": round(min(detail_sizes) / 1024, 1) if detail_sizes else None,
            "detail_max_kb": round(max(detail_sizes) / 1024, 1) if detail_sizes else None,
        }
    raw_files, raw_bytes = _get_directory_bytes(RAW_DIR, "*.html")
    report["raw_total"] = {"files_count": raw_files, "bytes": raw_bytes, "mb": _get_mb(raw_bytes)}

    # 2. Measure the parsed data, the crawl state and the index directories
    for name, directory in (("parsed", PARSED_DIR), ("crawl_state", CRAWL_DIR), ("index", INDEX_DIR)):
        files_count, size_bytes = _get_directory_bytes(directory)
        report[name] = {"files_count": files_count, "bytes": size_bytes, "mb": _get_mb(size_bytes)}
    return report


def compute_token_stats(encoders: dict[str, Any], should_count_raw: bool) -> Report:  # Any: tiktoken encoders.
    """
    Count tokens of the extracted text and of the raw HTML.

    Steps:
        1. For each category, count own tokens, own index terms and tiktoken tokens of the extracted text.
        2. Count tiktoken tokens and own tokens of the raw HTML pages (optional, slow).

    Returns:
        Token statistics by category and in total.
    """
    report: Report = {"by_category": {}, "encodings": list(encoders)}
    for category in CATEGORIES:
        # 1. For each category, count own tokens, own index terms and tiktoken tokens of the extracted text
        documents = _load_jsonl(PARSED_DIR / f"{category}.jsonl")
        texts = ["\n".join(str(document.get(name) or "") for name in _TEXT_FIELDS) for document in documents]
        counts: Report = {
            "documents_count": len(documents),
            "text_characters": sum(len(text) for text in texts),
            "text_own_tokens": sum(len(get_tokens(text)) for text in texts),
            "text_own_terms": sum(len(get_terms(text)) for text in texts),
        }
        for name, encoder in encoders.items():
            counts[f"text_tiktoken_{name}"] = sum(len(encoder.encode_ordinary(text)) for text in texts)

        # 2. Count tiktoken tokens and own tokens of the raw HTML pages (optional, slow)
        if should_count_raw:
            for path in sorted((RAW_DIR / category).glob("*.html")):
                html_text = path.read_text(encoding="utf-8")
                counts["raw_own_tokens"] = counts.get("raw_own_tokens", 0) + len(get_tokens(html_text))
                for name, encoder in encoders.items():
                    key = f"raw_tiktoken_{name}"
                    counts[key] = counts.get(key, 0) + len(encoder.encode_ordinary(html_text))
        report["by_category"][category] = counts

    totals: Counter[str] = Counter()
    for counts in report["by_category"].values():
        totals.update({key: value for key, value in counts.items() if isinstance(value, int)})
    report["total"] = dict(totals)
    return report


def build_dokuwiki_report(report: Report) -> str:
    """
    Build the DokuWiki tables of the corpus report.

    Steps:
        1. Write the crawl table.
        2. Write the disk table.
        3. Write the token table.

    Returns:
        DokuWiki text.
    """
    crawl, disk, tokens = report["crawl"], report["disk"], report["tokens"]

    # 1. Write the crawl table
    lines = ["==== Crawl ====", "", "^ Ukazovateľ ^ Hodnota ^"]
    lines.append(f"| Pokusy o stiahnutie | {crawl['attempts_count']} |")
    lines.extend(f"| Status ''{status}'' | {count} |" for status, count in sorted(crawl["status_counts"].items()))
    lines.extend(f"| Uložené ''{key}'' | {count} |" for key, count in crawl["done_by_category_kind"].items())
    lines.append(f"| Začiatok – koniec | {crawl['first_fetched_at']} – {crawl['last_fetched_at']} |")
    lines.append(f"| Priemerná odozva servera | {crawl['mean_elapsed_ms']} ms |")
    lines.append(f"| Požiadavky s opakovaním (retry) | {crawl['retried_count']} |")

    # 2. Write the disk table
    lines += ["", "==== Veľkosť na disku ====", "",
              "^ Časť ^ Súbory ^ Detailné stránky ^ MB ^ Detail priemer / min / max (KB) ^"]
    for category, values in disk["raw_by_category"].items():
        lines.append(
            f"| raw ''{category}'' | {values['files_count']} | {values['detail_pages_count']} | {values['mb']} "
            f"| {values['detail_mean_kb']} / {values['detail_min_kb']} / {values['detail_max_kb']} |"
        )
    lines.append(f"| **raw spolu** | {disk['raw_total']['files_count']} | | **{disk['raw_total']['mb']}** | |")
    for name in ("parsed", "crawl_state", "index"):
        lines.append(f"| ''{name}'' | {disk[name]['files_count']} | | {disk[name]['mb']} | |")

    # 3. Write the token table
    encodings = tokens["encodings"]
    header = ["Kategória", "Dokumenty", "Znaky textu", "Tokeny (vlastný)", "Termy (index)"]
    header += [f"tiktoken {name} (text)" for name in encodings]
    has_raw = any("raw_own_tokens" in counts for counts in tokens["by_category"].values())
    if has_raw:
        header += ["Tokeny HTML (vlastný)"] + [f"tiktoken {name} (HTML)" for name in encodings]
    lines += ["", "==== Tokeny ====", "", "^ " + " ^ ".join(header) + " ^"]
    for label, counts in [*tokens["by_category"].items(), ("**spolu**", tokens["total"])]:
        cells = [label, counts.get("documents_count", 0), counts.get("text_characters", 0),
                 counts.get("text_own_tokens", 0), counts.get("text_own_terms", 0)]
        cells += [counts.get(f"text_tiktoken_{name}", 0) for name in encodings]
        if has_raw:
            cells += [counts.get("raw_own_tokens", 0)] + [counts.get(f"raw_tiktoken_{name}", 0) for name in encodings]
        lines.append("| " + " | ".join(f"{cell:,}".replace(",", " ") if isinstance(cell, int) else str(cell)
                                        for cell in cells) + " |")
    return "\n".join(lines) + "\n"


# ==============================================================================
# ENTRY POINT
# ==============================================================================

def main() -> None:
    """
    Compute the corpus statistics and write the JSON and DokuWiki reports.

    Steps:
        1. Read the options and configure the logging.
        2. Compute the crawl, disk and token statistics.
        3. Write the reports.

    Returns:
        None.
    """
    # 1. Read the options and configure the logging
    parser = argparse.ArgumentParser(description="MilSearch corpus statistics")
    parser.add_argument("--skip-raw-tokens", action="store_true", help="do not tokenize the raw HTML (faster)")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    started_at = time.monotonic()

    # 2. Compute the crawl, disk and token statistics
    report: Report = {
        "crawl": compute_crawl_stats(_load_jsonl(FETCHED_PATH)),
        "disk": compute_disk_stats(),
        "tokens": compute_token_stats(_load_encoders(), should_count_raw=not arguments.skip_raw_tokens),
        "index": json.loads(META_PATH.read_text(encoding="utf-8")) if META_PATH.exists() else None,
        "computed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    # 3. Write the reports
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    STATS_JSON_PATH.write_bytes(json.dumps(report, ensure_ascii=False, indent=1).encode("utf-8"))
    STATS_WIKI_PATH.write_bytes(build_dokuwiki_report(report).encode("utf-8"))
    logger.info("SAVED corpus_stats | raw_mb=%s documents_count=%d elapsed_seconds=%.1f",
                report["disk"]["raw_total"]["mb"], report["tokens"]["total"].get("documents_count", 0),
                time.monotonic() - started_at)


if __name__ == "__main__":
    main()
