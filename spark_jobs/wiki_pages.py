# ==============================================================================
# SPARK JOB: WIKI PAGES — MilSearch
# ==============================================================================
# PURPOSE:
#     Change the Wikipedia XML dump (bz2) into a page table in Parquet. The record fields come
#     from regular expressions that run in the JVM (regexp_extract), not from an XML parser.
#
# RESPONSIBILITIES:
#     1. Read the dump with the record separator "</page>" (one record = one page; bz2 splits are parallel).
#     2. Extract the title, namespace, page ID, redirect target and text with regular expressions.
#     3. Keep the articles (namespace 0); decode the XML entities.
#     4. Mark the military equipment pages (weapon, vehicle or ship infobox or category).
#     5. Write the table partitioned by is_military, so that later jobs read only the military pages.
#
# USAGE:
#     python -m tools.spark wiki_pages --lang en
#     python -m tools.spark wiki_pages --lang sk
#
# OUTPUT:
#     data/wiki/<lang>/pages/is_military=true|false/*.parquet
#         id, title, redirect (null for an article), text_bytes, text (null for a redirect)
#     data/reports/wiki_pages_<lang>.json, .dokuwiki.txt
#
# INVARIANTS:
#     Python 3.6 syntax and Spark 2.4 API (image of the course). The dump files are never changed.
# ==============================================================================

import time

from pyspark.sql import Column
from pyspark.sql import functions as F

from spark_jobs.common import build_dokuwiki_table, get_argument_parser, get_data_uri, get_spark, log, save_report
from wiki.wikitext import (MILITARY_CATEGORY_PATTERN, MILITARY_INFOBOX_PATTERN, NAMESPACE_PATTERN, PAGE_ID_PATTERN,
                           REDIRECT_PATTERN, TEXT_PATTERN, TITLE_PATTERN)

# ==============================================================================
# CONSTANTS
# ==============================================================================

RECORD_SEPARATOR = "</page>"
DUMP_PATTERNS = {"en": "enwiki-*-pages-articles*.xml-p*.bz2", "sk": "skwiki-*-pages-articles.xml.bz2"}
ARTICLE_NAMESPACE = 0
# One dump file = one partition. Hadoop's LineRecordReader with a multi-byte separator reads a page
# twice at the border of two splits of a compressed file (test: 27,152 records for 27,151 pages).
# The open cost equal to the maximum split size closes a partition after each file.
ONE_FILE_PER_PARTITION_BYTES = str(1 << 40)
# The XML dump escapes only these characters. "&amp;" is the last one, so "&amp;lt;" becomes "&lt;".
XML_ENTITIES = (("&lt;", "<"), ("&gt;", ">"), ("&quot;", "\""), ("&#039;", "'"), ("&amp;", "&"))


# ==============================================================================
# HELPERS
# ==============================================================================

def decode_xml(column: Column) -> Column:
    """
    Decode the XML entities of a string column in the JVM.

    Steps:
        1. Replace each entity with its character; "&amp;" last.

    Returns:
        Column with decoded text.
    """
    # 1. Replace each entity with its character; "&amp;" last
    for entity, character in XML_ENTITIES:
        column = F.regexp_replace(column, entity, character)
    return column


# ==============================================================================
# ENTRY POINT
# ==============================================================================

def main() -> None:
    """
    Convert one Wikipedia dump into the page table.

    Steps:
        1. Read the dump files; one record per page.
        2. Extract the record fields with regular expressions; keep the articles.
        3. Decode the entities; mark redirects and military pages.
        4. Write the table partitioned by is_military.
        5. Count the pages from the written table and write the report.

    Returns:
        None.
    """
    parser = get_argument_parser("Convert a Wikipedia XML dump into a Parquet page table.")
    parser.add_argument("--lang", choices=sorted(DUMP_PATTERNS), default="en")
    parser.add_argument("--dump-glob", help="Other file pattern in the dump directory (for a test on some files).")
    arguments = parser.parse_args()
    spark = get_spark("wiki_pages " + arguments.lang)
    started = time.time()

    # 1. Read the dump files; one record per page
    input_uri = get_data_uri(arguments.data_dir, "wiki", "dump", arguments.lang,
                             arguments.dump_glob or DUMP_PATTERNS[arguments.lang])
    spark.conf.set("spark.sql.files.maxPartitionBytes", ONE_FILE_PER_PARTITION_BYTES)
    spark.conf.set("spark.sql.files.openCostInBytes", ONE_FILE_PER_PARTITION_BYTES)
    records = spark.read.text(input_uri, lineSep=RECORD_SEPARATOR)
    log("STARTED", "wiki_pages", lang=arguments.lang, input=input_uri, partitions_count=records.rdd.getNumPartitions())

    # 2. Extract the record fields with regular expressions; keep the articles
    pages = records.select(
        F.regexp_extract("value", TITLE_PATTERN, 1).alias("title"),
        F.regexp_extract("value", NAMESPACE_PATTERN, 1).alias("ns"),
        F.regexp_extract("value", PAGE_ID_PATTERN, 1).cast("long").alias("id"),
        F.regexp_extract("value", REDIRECT_PATTERN, 1).alias("redirect"),
        F.regexp_extract("value", TEXT_PATTERN, 1).alias("raw_text"),
    ).where((F.col("title") != "") & (F.col("ns") == str(ARTICLE_NAMESPACE)))

    # 3. Decode the entities; mark redirects and military pages
    is_redirect = F.col("redirect") != ""
    text = decode_xml(F.col("raw_text"))
    pages = pages.select(
        "id",
        decode_xml(F.col("title")).alias("title"),
        F.when(is_redirect, decode_xml(F.col("redirect"))).alias("redirect"),
        F.length("raw_text").alias("text_bytes"),
        F.when(~is_redirect, text).alias("text"),
        (~is_redirect & (F.col("raw_text").rlike(MILITARY_INFOBOX_PATTERN)
                         | F.col("raw_text").rlike(MILITARY_CATEGORY_PATTERN))).alias("is_military"),
    )

    # 4. Write the table partitioned by is_military
    output_uri = get_data_uri(arguments.data_dir, "wiki", arguments.lang, "pages")
    pages.write.mode("overwrite").partitionBy("is_military").parquet(output_uri)
    write_seconds = round(time.time() - started, 1)
    log("SAVED", "pages", path=output_uri, elapsed_s=write_seconds)

    # 5. Count the pages from the written table and write the report
    # Spark 2.4 reads a partition column "true"/"false" as a string.
    table = spark.read.parquet(output_uri).withColumn("is_military", F.col("is_military").cast("boolean"))
    row = table.agg(
        F.count(F.lit(1)).alias("pages"),
        F.sum(F.col("redirect").isNotNull().cast("int")).alias("redirects"),
        F.sum((F.col("redirect").isNull() & F.col("is_military")).cast("int")).alias("military"),
        F.sum("text_bytes").alias("text_bytes"),
        F.sum(F.when(F.col("is_military"), F.col("text_bytes"))).alias("military_text_bytes"),
    ).collect()[0]
    input_path = spark._jvm.org.apache.hadoop.fs.Path(input_uri)
    input_bytes = sum(status.getLen() for status in input_path.getFileSystem(
        spark._jsc.hadoopConfiguration()).globStatus(input_path))
    report = {
        "lang": arguments.lang, "input_files": input_uri, "input_bytes_compressed": input_bytes,
        "pages_count": row["pages"], "redirects_count": row["redirects"], "articles_count": row["pages"] - row["redirects"],
        "military_pages_count": row["military"], "text_bytes": row["text_bytes"],
        "military_text_bytes": row["military_text_bytes"], "write_seconds": write_seconds,
        "elapsed_seconds": round(time.time() - started, 1), "partitions_count": records.rdd.getNumPartitions(),
        "spark_version": spark.version, "master": spark.sparkContext.master,
    }
    log("COUNTED", "pages", **{key: value for key, value in report.items() if key.endswith(("count", "bytes"))})
    tables = [build_dokuwiki_table(
        ["Wikipédia", "Dump (bz2)", "Text (rozbalený)", "Stránky (ns 0)", "Presmerovania", "Články",
         "Vojenská technika", "Čas"],
        [[arguments.lang.upper(), "{:,.2f} GB".format(input_bytes / 1e9), "{:,.2f} GB".format(row["text_bytes"] / 1e9),
          "{:,}".format(row["pages"]), "{:,}".format(row["redirects"]), "{:,}".format(report["articles_count"]),
          "{:,}".format(row["military"]), "{:.1f} min".format(report["elapsed_seconds"] / 60)]],
    )]
    save_report("wiki_pages_" + arguments.lang, report, tables)
    spark.stop()


if __name__ == "__main__":
    main()
