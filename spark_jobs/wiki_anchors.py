# ==============================================================================
# SPARK JOB: WIKI ANCHORS — MilSearch
# ==============================================================================
# PURPOSE:
#     Collect the anchor texts and the number of inlinks of each military page from all links
#     of the English Wikipedia. Anchor texts are other names of an object ("Kalashnikov" -> AK-47).
#
# RESPONSIBILITIES:
#     1. Split the text of each article at "[[" and get the link target and the anchor text (regex, JVM).
#     2. Resolve the redirects: a link to "AK47" counts for "AK-47".
#     3. Keep the links to military pages (broadcast join with the name table).
#     4. Count the distinct source pages (inlinks) and the most frequent anchor texts of each page.
#
# USAGE:
#     python -m tools.spark wiki_anchors          (after wiki_pages --lang en)
#
# OUTPUT:
#     data/wiki/en/anchors/*.parquet: title, inlinks_count, links_count, anchors (array of {text, count})
#     data/reports/wiki_anchors.json, .dokuwiki.txt
#
# INVARIANTS:
#     Python 3.6 syntax and Spark 2.4 API (image of the course).
# ==============================================================================

import time

from pyspark.sql import Window
from pyspark.sql import functions as F

from spark_jobs.common import build_dokuwiki_table, get_argument_parser, get_data_uri, get_spark, log, save_report

# ==============================================================================
# CONSTANTS
# ==============================================================================

# One piece after split("[["): "Target#Section|anchor]] rest of the text".
LINK_TARGET_PATTERN = r"^([^\[\]|#]*)(?:#[^\[\]|]*)?(?:\|[^\[\]]*)?\]\]"
LINK_ANCHOR_PATTERN = r"^[^\[\]|]*\|([^\[\]]*)\]\]"
NAMESPACE_PREFIX_PATTERN = r"^:?[A-Za-z ]+:"
MAX_ANCHORS_COUNT = 15
MAX_ANCHOR_LENGTH = 80


# ==============================================================================
# ENTRY POINT
# ==============================================================================

def main() -> None:
    """
    Compute the anchor texts and the inlinks of the military pages.

    Steps:
        1. Read the page table; build the name table: military title -> itself, redirect -> military title.
        2. Explode the links of all articles and get the target and the anchor text.
        3. Normalize the target (spaces, first letter uppercase) and join it with the name table.
        4. Count the inlinks and the anchor texts of each page; keep the most frequent anchors.
        5. Write the table and the report.

    Returns:
        None.
    """
    arguments = get_argument_parser("Collect anchor texts and inlinks of the military pages.").parse_args()
    spark = get_spark("wiki_anchors")
    started = time.time()

    # 1. Read the page table; build the name table
    pages_uri = get_data_uri(arguments.data_dir, "wiki", "en", "pages")
    pages = spark.read.parquet(pages_uri)
    military = spark.read.option("basePath", pages_uri).parquet(pages_uri + "/is_military=true") \
        .where(F.col("redirect").isNull()).select(F.col("title").alias("page"))
    redirects = pages.where(F.col("redirect").isNotNull()).select(F.col("title").alias("name"),
                                                                    F.col("redirect").alias("page"))
    names = military.select(F.col("page").alias("name"), "page").union(
        redirects.join(military, "page").select("name", "page")).cache()
    log("COUNTED", "names", names_count=names.count())

    # 2. Explode the links of all articles and get the target and the anchor text
    articles = pages.where(F.col("redirect").isNull() & F.col("text").isNotNull()).select("id", "title", "text")
    pieces = articles.select(F.col("id").alias("source_id"), F.col("title").alias("source"),
                             F.explode(F.split("text", r"\[\[")).alias("piece"))
    links = pieces.select(
        "source_id", "source",
        F.trim(F.regexp_replace(F.regexp_extract("piece", LINK_TARGET_PATTERN, 1), "_", " ")).alias("target"),
        F.trim(F.regexp_extract("piece", LINK_ANCHOR_PATTERN, 1)).alias("anchor"),
    ).where((F.col("target") != "") & ~F.col("target").rlike(NAMESPACE_PREFIX_PATTERN))

    # 3. Normalize the target and join it with the name table
    links = links.withColumn("name", F.concat(F.upper(F.substring("target", 1, 1)), F.expr("substring(target, 2)")))
    links = links.join(F.broadcast(names), "name").where(F.col("source") != F.col("page"))
    links = links.withColumn("anchor_text", F.when(F.col("anchor") != "", F.col("anchor")).otherwise(F.col("target")))
    links = links.where(F.length("anchor_text") <= MAX_ANCHOR_LENGTH).cache()

    # 4. Count the inlinks and the anchor texts of each page; keep the most frequent anchors
    inlinks = links.groupBy("page").agg(F.countDistinct("source_id").alias("inlinks_count"),
                                        F.count(F.lit(1)).alias("links_count"))
    anchor_counts = links.groupBy("page", F.lower(F.col("anchor_text")).alias("anchor_key")).agg(
        F.first("anchor_text").alias("text"), F.count(F.lit(1)).alias("count"))
    rank = F.row_number().over(Window.partitionBy("page").orderBy(F.desc("count"), F.asc("anchor_key")))
    top_anchors = anchor_counts.withColumn("rank", rank).where(F.col("rank") <= MAX_ANCHORS_COUNT) \
        .groupBy("page").agg(F.collect_list(F.struct("text", "count")).alias("anchors"))
    result = inlinks.join(top_anchors, "page", "left").withColumnRenamed("page", "title")

    # 5. Write the table and the report
    output_uri = get_data_uri(arguments.data_dir, "wiki", "en", "anchors")
    result.write.mode("overwrite").parquet(output_uri)
    written = spark.read.parquet(output_uri)
    totals = written.agg(F.count(F.lit(1)).alias("pages"), F.sum("inlinks_count").alias("inlinks"),
                         F.sum("links_count").alias("links")).collect()[0]
    top_pages = written.orderBy(F.desc("inlinks_count")).limit(10).collect()
    report = {"military_pages_with_inlinks": totals["pages"], "inlinks_sum": totals["inlinks"],
              "links_to_military_pages": totals["links"], "names_count": names.count(),
              "top_pages": [{"title": row["title"], "inlinks_count": row["inlinks_count"],
                             "anchors": [anchor["text"] for anchor in (row["anchors"] or [])[:8]]} for row in top_pages],
              "elapsed_seconds": round(time.time() - started, 1)}
    log("COUNTED", "anchors", pages_count=totals["pages"], links_count=totals["links"])
    tables = [
        build_dokuwiki_table(["Vojenské stránky s odkazmi", "Odkazy na ne", "Súčet inlinkov", "Čas"],
                             [["{:,}".format(totals["pages"]), "{:,}".format(totals["links"]),
                               "{:,}".format(totals["inlinks"]), "{:.1f} min".format(report["elapsed_seconds"] / 60)]]),
        build_dokuwiki_table(["Stránka", "Inlinky", "Najčastejšie anchor texty"],
                             [[page["title"], "{:,}".format(page["inlinks_count"]), ", ".join(page["anchors"])]
                              for page in report["top_pages"]]),
    ]
    save_report("wiki_anchors", report, tables)
    spark.stop()


if __name__ == "__main__":
    main()
