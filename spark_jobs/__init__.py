# ==============================================================================
# SPARK JOBS PACKAGE — MilSearch
# ==============================================================================
# PURPOSE:
#     Apache Spark jobs of part 2 (distributed processing). Run them with spark-submit:
#         spark_jobs.extract_pages — regex extractor of part 1 on the MilitaryFactory pages, in Spark.
#         spark_jobs.wiki_pages    — Wikipedia XML dump -> page table (Parquet), regex fields.
#         spark_jobs.wiki_anchors  — anchor texts and inlinks of the military pages (all EN links).
#         spark_jobs.wiki_join     — join of the MilitaryFactory documents with EN and SK Wikipedia.
#
# RESPONSIBILITIES:
#     1. Provide the package boundary; spark-submit runs the job files directly.
#
# INVARIANTS:
#     Python 3.6 syntax and Spark 2.4 API (image iisas/hadoop-spark-pig-hive:2.9.2 of the course).
# ==============================================================================
