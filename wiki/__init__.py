# ==============================================================================
# WIKI PACKAGE — MilSearch
# ==============================================================================
# PURPOSE:
#     Pure regex functions for the Wikipedia dump. The Spark jobs (spark_jobs/) call them.
#     Submodules:
#         wiki.wikitext — page record fields, wikitext cleanup, infobox, categories, abstract, links.
#         wiki.keys     — name keys for the join of MilitaryFactory documents with Wikipedia pages.
#         wiki.match    — wiki attributes of interest and the score of a join candidate.
#
# RESPONSIBILITIES:
#     1. Provide the package boundary; consumers import submodules directly.
#     2. Keep the functions free of pyspark, so that pytest can test them without Spark.
#
# INVARIANTS:
#     Python 3.6 syntax. The Spark image of the course (iisas/hadoop-spark-pig-hive:2.9.2) has Python 3.6.
#     Regular expressions only. No XML parser, no wikitext parser library.
# ==============================================================================
