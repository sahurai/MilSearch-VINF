# ==============================================================================
# LUCENE INDEX — MilSearch (part 2, consultation 4)
# ==============================================================================
# PURPOSE:
#     Second index of the application, built with PyLucene (Apache Lucene 10).
#
# MODULES:
#     schema   — fields of the Lucene document, numeric filters of a query (pure Python).
#     analysis — JVM start, analyzers and similarity (needs PyLucene).
#     build    — build the Lucene index from the same documents as the own index.
#     search   — search engine and command line (Lucene query syntax).
#     compare  — compare the own index and the Lucene index on the same queries.
#
# USAGE:
#     PyLucene runs in Docker (image coady/pylucene:10); tools/lucene.py starts the container.
# ==============================================================================
