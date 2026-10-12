# ==============================================================================
# CRAWLER PACKAGE — MilSearch
# ==============================================================================
# PURPOSE:
#     Own crawler for the Small Arms, Armor and Ships catalogs of MilitaryFactory.com.
#     Submodules:
#         crawler.config   — all settings (paths, scope, HTTP, politeness, limits).
#         crawler.crawl    — main loop and command-line interface.
#         crawler.frontier — URL queue with a file-based state.
#         crawler.fetcher  — polite HTTP download.
#         crawler.links    — regex link extraction and URL scope.
#         crawler.storage  — page and link-graph files.
#
# RESPONSIBILITIES:
#     1. Provide the package boundary; consumers import submodules directly.
#     2. Keep the __init__ empty of re-exports.
# ==============================================================================
