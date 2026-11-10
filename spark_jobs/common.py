# ==============================================================================
# SPARK COMMON — MilSearch
# ==============================================================================
# PURPOSE:
#     Shared code of the Spark jobs: Spark session, data paths, log lines and report files.
#
# RESPONSIBILITIES:
#     1. Make the Spark session with the job name.
#     2. Make the data URIs: "file://" for a local directory, the given URI for HDFS ("hdfs:///...").
#     3. Write the log lines in the format of the crawler ("ACTION subject | key=value").
#     4. Write a report as JSON and as DokuWiki tables (on the driver, local disk).
#
# INVARIANTS:
#     Python 3.6 syntax and Spark 2.4 API (image of the course).
# ==============================================================================

import argparse
import json
import os
import time
from typing import Dict, List, Sequence

from pyspark.sql import SparkSession

# ==============================================================================
# CONSTANTS
# ==============================================================================

DEFAULT_DATA_DIR = "data"
REPORTS_DIR = os.path.join("data", "reports")


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def get_spark(app_name: str) -> SparkSession:
    """
    Make the Spark session of a job.

    Steps:
        1. Build the session with the job name; spark-submit gives the master and the memory.
        2. Show only warnings in the Spark log.

    Returns:
        Spark session.
    """
    # 1. Build the session with the job name
    spark = SparkSession.builder.appName("MilSearch " + app_name).getOrCreate()

    # 2. Show only warnings in the Spark log
    spark.sparkContext.setLogLevel("WARN")
    return spark


def get_argument_parser(description: str) -> argparse.ArgumentParser:
    """
    Make the argument parser with the shared --data-dir option.

    Steps:
        1. Add --data-dir: a local directory or an HDFS URI.

    Returns:
        Argument parser.
    """
    # 1. Add --data-dir: a local directory or an HDFS URI
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                        help="Data directory: local path (default: data) or URI, for example hdfs:///user/root/milsearch.")
    return parser


def get_data_uri(data_dir: str, *parts: str) -> str:
    """
    Make the URI of a path in the data directory.

    Steps:
        1. Keep a URI with a scheme (hdfs://, file://); join the parts with "/".
        2. Change a local path to an absolute "file://" URI (the default file system of the image is HDFS).

    Returns:
        URI such as "file:///app/data/wiki/en/pages".
    """
    # 1. Keep a URI with a scheme; join the parts with "/"
    if "://" in data_dir:
        return "/".join([data_dir.rstrip("/")] + list(parts))

    # 2. Change a local path to an absolute "file://" URI
    return "file://" + os.path.abspath(os.path.join(data_dir, *parts)).replace("\\", "/")


def log(action: str, subject: str, **fields: object) -> None:
    """
    Print one log line: "HH:MM:SS INFO    ACTION subject | key=value ...".

    Steps:
        1. Format the fields as key=value pairs and print the line.

    Returns:
        None.
    """
    # 1. Format the fields as key=value pairs and print the line
    pairs = " ".join("{}={}".format(key, value) for key, value in fields.items())
    print("{} INFO    {} {} | {}".format(time.strftime("%H:%M:%S"), action, subject, pairs), flush=True)


def build_dokuwiki_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    """
    Make a DokuWiki table.

    Steps:
        1. Write the header row with "^" and the data rows with "|".

    Returns:
        Table text.
    """
    # 1. Write the header row with "^" and the data rows with "|"
    lines = ["^ " + " ^ ".join(headers) + " ^"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def save_report(name: str, report: Dict[str, object], tables: List[str]) -> None:
    """
    Write a report to data/reports/<name>.json and data/reports/<name>.dokuwiki.txt.

    Steps:
        1. Make the report directory on the driver.
        2. Write the JSON and the DokuWiki tables.

    Returns:
        None.
    """
    # 1. Make the report directory on the driver
    os.makedirs(REPORTS_DIR, exist_ok=True)

    # 2. Write the JSON and the DokuWiki tables
    with open(os.path.join(REPORTS_DIR, name + ".json"), "w", encoding="utf-8") as file_handle:
        json.dump(report, file_handle, ensure_ascii=False, indent=2)
    with open(os.path.join(REPORTS_DIR, name + ".dokuwiki.txt"), "w", encoding="utf-8") as file_handle:
        file_handle.write("\n\n".join(tables) + "\n")
    log("SAVED", "report", path=os.path.join(REPORTS_DIR, name + ".json"))
