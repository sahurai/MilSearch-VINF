# ==============================================================================
# SPARK JOB: EXTRACT PAGES — MilSearch
# ==============================================================================
# PURPOSE:
#     Run the regex extractor of part 1 (extractor/parse.py) on all downloaded MilitaryFactory
#     pages in Spark. The same function runs in parallel tasks on the Spark executors.
#
# RESPONSIBILITIES:
#     1. Read the HTML pages (one record per file) and extract one document from each detail page.
#     2. Write the documents as JSON lines to data/spark/parsed/.
#     3. Compute the statistics in Spark: documents and field coverage per category.
#     4. Compare the result with the sequential extractor (data/parsed/*.jsonl), when it exists.
#
# USAGE:
#     python -m tools.spark extract_pages
#     spark-submit --py-files milsearch-spark.zip spark_jobs/extract_pages.py --data-dir hdfs:///user/root/milsearch
#
# INVARIANTS:
#     Python 3.6 syntax and Spark 2.4 API (image of the course).
# ==============================================================================

import json
import time
from typing import Dict, Iterator, List, Tuple

from extractor.parse import extract_document
from spark_jobs.common import build_dokuwiki_table, get_argument_parser, get_data_uri, get_spark, log, save_report

# ==============================================================================
# CONSTANTS
# ==============================================================================

CATEGORIES = ("smallarms", "armor", "ships")
# Fields of the coverage table (fields of all categories and the most important numeric fields).
COVERAGE_FIELDS = ("name", "type", "country", "year", "manufacturer", "operators", "variants", "roles",
                   "caliber", "weight_kg", "max_speed_kmh", "engine_hp", "displacement_tons", "speed_kts")
MIN_PARTITIONS = 64


# ==============================================================================
# HELPERS
# ==============================================================================

def extract_partition(files: Iterator[Tuple[str, str]]) -> Iterator[str]:
    """
    Extract the documents of one partition of (file URI, HTML) pairs.

    Steps:
        1. Get the category and the project-relative path from the file URI.
        2. Skip the navigation pages (index and A-Z catalog).
        3. Run the extractor of part 1 and write the document as one JSON line.

    Returns:
        JSON lines of the documents.
    """
    for uri, page in files:
        # 1. Get the category and the project-relative path from the file URI
        parts = uri.replace("\\", "/").split("/")
        category, file_name = parts[-2], parts[-1]

        # 2. Skip the navigation pages (index and A-Z catalog)
        if category not in CATEGORIES or file_name.endswith(("-index.html", "-catalog.html")):
            continue

        # 3. Run the extractor of part 1 and write the document as one JSON line
        document = extract_document(page, category, "data/raw/{}/{}".format(category, file_name))
        if document:
            yield json.dumps(document, ensure_ascii=False)


def get_coverage_pairs(line: str) -> List[Tuple[Tuple[str, str], int]]:
    """
    Get the (category, field) pairs that have a value in one document, plus a document counter.

    Steps:
        1. Parse the JSON line.
        2. Emit one pair for the document and one pair for each coverage field with a value.

    Returns:
        List of ((category, field), 1).
    """
    # 1. Parse the JSON line
    document = json.loads(line)
    category = document["category"]

    # 2. Emit one pair for the document and one pair for each coverage field with a value
    pairs = [((category, "_documents"), 1)]
    for field in COVERAGE_FIELDS:
        if document.get(field) not in (None, "", []):
            pairs.append(((category, field), 1))
    return pairs


def get_canonical_json(line: str) -> Tuple[str, str]:
    """
    Get the document ID and a canonical JSON text (sorted keys) for the comparison.

    Steps:
        1. Parse the line and dump it again with sorted keys.

    Returns:
        (document ID, canonical JSON).
    """
    # 1. Parse the line and dump it again with sorted keys
    document = json.loads(line)
    return document["id"], json.dumps(document, sort_keys=True, ensure_ascii=False)


# ==============================================================================
# ENTRY POINT
# ==============================================================================

def main() -> None:
    """
    Extract all pages in Spark and write the documents and the report.

    Steps:
        1. Read the HTML files: one (URI, content) record per file.
        2. Extract the documents in parallel and write them as JSON lines.
        3. Count the documents and the field coverage per category (reduceByKey).
        4. Compare with the sequential extractor: full outer join on the document ID.
        5. Write the report.

    Returns:
        None.
    """
    arguments = get_argument_parser("Run the regex extractor of part 1 in Spark.").parse_args()
    spark = get_spark("extract_pages")
    started = time.time()

    # 1. Read the HTML files: one (URI, content) record per file
    pages = spark.sparkContext.wholeTextFiles(get_data_uri(arguments.data_dir, "raw", "*", "*.html"),
                                              minPartitions=MIN_PARTITIONS)

    # 2. Extract the documents in parallel and write them as JSON lines
    documents = pages.mapPartitions(extract_partition).cache()
    output_uri = get_data_uri(arguments.data_dir, "spark", "parsed")
    spark.createDataFrame(documents.map(lambda line: (line,)), ["value"]).write.mode("overwrite").text(output_uri)
    log("SAVED", "documents", path=output_uri, partitions_count=documents.getNumPartitions())

    # 3. Count the documents and the field coverage per category (reduceByKey)
    counts: Dict[Tuple[str, str], int] = dict(
        documents.flatMap(get_coverage_pairs).reduceByKey(lambda left, right: left + right).collect()
    )
    documents_by_category = {category: counts.get((category, "_documents"), 0) for category in CATEGORIES}
    log("COUNTED", "documents", **documents_by_category)

    # 4. Compare with the sequential extractor: full outer join on the document ID
    comparison: Dict[str, int] = {}
    try:
        sequential = spark.sparkContext.textFile(get_data_uri(arguments.data_dir, "parsed", "*.jsonl"))
        joined = documents.map(get_canonical_json).fullOuterJoin(sequential.map(get_canonical_json))
        comparison = dict(joined.map(lambda pair: (
            "same" if pair[1][0] == pair[1][1] else
            "only_spark" if pair[1][1] is None else
            "only_sequential" if pair[1][0] is None else "different", 1)
        ).reduceByKey(lambda left, right: left + right).collect())
        log("COMPARED", "sequential extractor", **comparison)
    except Exception as error:  # The sequential output is optional (for example on the cluster).
        log("SKIPPED", "comparison", reason=type(error).__name__)

    # 5. Write the report
    elapsed_seconds = round(time.time() - started, 1)
    coverage = {category: {field: counts.get((category, field), 0) for field in COVERAGE_FIELDS}
                for category in CATEGORIES}
    report = {"documents": documents_by_category, "documents_count": sum(documents_by_category.values()),
              "input_files_count": pages.count(), "coverage": coverage, "comparison_with_sequential": comparison,
              "elapsed_seconds": elapsed_seconds, "spark_version": spark.version,
              "master": spark.sparkContext.master, "default_parallelism": spark.sparkContext.defaultParallelism}
    coverage_rows = [[field] + ["{} ({:.0%})".format(coverage[category][field],
                                                     coverage[category][field] / max(1, documents_by_category[category]))
                                for category in CATEGORIES] for field in COVERAGE_FIELDS]
    tables = [
        build_dokuwiki_table(["Kategória", "Dokumenty (Spark)"],
                             [[category, documents_by_category[category]] for category in CATEGORIES]
                             + [["spolu", report["documents_count"]]]),
        build_dokuwiki_table(["Pole"] + list(CATEGORIES), coverage_rows),
        build_dokuwiki_table(["Porovnanie so sekvenčným extraktorom", "Dokumenty"],
                             sorted([[key, value] for key, value in comparison.items()])),
        build_dokuwiki_table(["Spark", "Master", "Paralelizmus", "Čas"],
                             [[spark.version, spark.sparkContext.master, spark.sparkContext.defaultParallelism,
                               "{} s".format(elapsed_seconds)]]),
    ]
    save_report("spark_extract", report, tables)
    spark.stop()


if __name__ == "__main__":
    main()
