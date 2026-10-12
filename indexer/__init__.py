# ==============================================================================
# INDEXER PACKAGE — MilSearch
# ==============================================================================
# PURPOSE:
#     Own inverted index and ranked search over the extracted MilitaryFactory documents.
#     Submodules:
#         indexer.tokenizer — terms from text (regex tokens, stop words, S-stemmer).
#         indexer.build     — builds the positional inverted index on disk.
#         indexer.ranking   — idf variants and score functions (tf-idf, probabilistic idf, BM25).
#         indexer.query     — query parser (free text, phrases, field filters).
#         indexer.search    — loads the index and answers queries.
#
# RESPONSIBILITIES:
#     1. Provide the package boundary; consumers import submodules directly.
#     2. Keep the __init__ empty of re-exports.
# ==============================================================================
