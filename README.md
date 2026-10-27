# MilSearch

MilSearch is a search engine for military equipment. It has data about three types of equipment:

- Infantry small arms (rifles, pistols, machine guns).
- Land vehicles and artillery (tanks, armored vehicles, howitzers).
- Naval ships and submarines.

The data comes from two sources:

1. HTML pages from [MilitaryFactory.com](https://www.militaryfactory.com). The MilSearch crawler downloads these pages.
2. The Wikipedia dump (EN and SK). The second part of the project uses this dump.

This document is about the first part of the project: the crawler, the regex extractor, the own inverted index with ranked search, and the statistics and evaluation reports.

> Course: Information Retrieval (VINF), FIIT STU Bratislava, 2026.
> The documentation in this repository uses ASD-STE100 Simplified Technical English.

---

## 1. Requirements

| Item | Version |
|------|---------|
| Python | 3.11 or higher |
| requests | 2.31 or higher |
| tiktoken | 0.7 or higher (optional, only for the token statistics) |

The crawler, the extractor and the indexer do not use other libraries. They use these modules from the Python standard library: `re`, `urllib.robotparser`, `json`, `hashlib`, `math`, `argparse`, `logging`.

The project does not use a database. It keeps all data in text files (HTML, JSONL, TSV, plain-text index files).

For the tests, you also need pytest 8.0 or higher (`requirements-dev.txt`).

## 2. Installation

1. Install Python 3.11 or higher.
2. Open a terminal in the project root directory.
3. Make a virtual environment in the project. Do not install the dependencies into the global Python.

   ```bash
   python -m venv .venv
   ```

4. Activate the virtual environment:

   ```bash
   # Windows (PowerShell)
   .venv\Scripts\Activate.ps1
   # Windows (Git Bash)
   source .venv/Scripts/activate
   # Linux / macOS
   source .venv/bin/activate
   ```

5. Install the dependencies (the development file also installs the runtime dependencies):

   ```bash
   python -m pip install -r requirements-dev.txt
   ```

All commands in this document use the activated virtual environment.

## 3. Project structure

```
MilSearch/
├── crawler/
│   ├── config.py      All settings: seed URLs, HTTP headers, timeouts, delays, limits.
│   ├── crawl.py       Main loop and command-line interface.
│   ├── frontier.py    URL queue (BFS, FIFO). The state is in append-only files.
│   ├── fetcher.py     HTTP requests: robots.txt, delay, timeouts, retries, backoff.
│   ├── links.py       Link extraction with regex, URL normalization, URL filter.
│   └── storage.py     Writes HTML pages and the link graph to disk.
├── extractor/
│   ├── parse.py       Regex extractor.
│   ├── countries.py   Canonical country names.
│   └── stats.py       Field coverage statistics (JSON and DokuWiki table).
├── indexer/
│   ├── tokenizer.py   Terms from text: regex tokens, stop words, S-stemmer, compound tokens.
│   ├── build.py       Builds the positional inverted index (dictionary, postings, documents).
│   ├── ranking.py     Three idf variants (classic, smooth, BM25), vector model (cosine) and BM25.
│   ├── query.py       Query parser: free text, phrases, field filters.
│   └── search.py      Search engine and command-line interface.
├── reports/
│   ├── corpus_stats.py    Crawl, disk and token statistics (own tokenizer and tiktoken).
│   ├── extractor_eval.py  Extractor accuracy against the A-Z catalog pages.
│   └── search_eval.py     Comparison of the ranking methods (MAP, P@10, R@100, nDCG@10, idf of query terms).
├── tools/
│   └── build_zip.py       Makes the ZIP file with the source code for the wiki submission.
├── tests/
│   ├── unit/          Tests of pure functions. No network.
│   ├── integration/   Tests with files in a temporary directory (frontier, index and search).
│   └── fixtures/      20 real detail pages and their hand-checked expected values.
├── data/              Output of the crawler, the extractor, the indexer and the reports (see section 8).
├── docs/
│   ├── konzultacia2.txt   Wiki page for consultation 2a (DokuWiki syntax, Slovak).
│   ├── konzultacia2b.txt  Wiki page for consultation 2b (DokuWiki syntax, Slovak).
│   ├── odovzdanie1.txt    Wiki page for the submission of part 1: the full report (DokuWiki, Slovak).
│   └── diagrams/          Architecture diagram: HTML source (rough.js), render script, PNG.
├── dist/              Output of tools/build_zip.py (not in git).
├── pytest.ini
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

## 4. Run the crawler

### 4.1 Start a crawl

1. Open a terminal in the project root directory.
2. Start the crawler:

   ```bash
   python -m crawler.crawl
   ```

The crawler starts from three seed URLs:

- `https://www.militaryfactory.com/smallarms/index.php`
- `https://www.militaryfactory.com/armor/index.php`
- `https://www.militaryfactory.com/ships/index.php`

On each index page, the crawler finds the link to the A-Z catalog (`indexMAIN.php`). The catalog has links to all detail pages. The crawler downloads the detail pages. It also follows the links between detail pages.

### 4.2 Command-line options

| Option | Default | Description |
|--------|---------|-------------|
| `--categories` | all | Categories to crawl: `smallarms`, `armor`, `ships`. |
| `--max-pages N` | no limit | Stop after N downloaded pages. |
| `--max-minutes N` | no limit | Stop after N minutes. |
| `--max-depth N` | 3 | Maximum number of links from a seed URL. |
| `--seed-url URL` | none | Add a URL to the queue. You can use this option more than one time. |
| `--delay S` | 2.0 | Minimum time in seconds between two requests. |
| `--verbose` | off | Log each page (DEBUG level). |

Example: download 50 pages and stop.

```bash
python -m crawler.crawl --max-pages 50
```

### 4.3 Log format

Each log line has an action, a subject and `key=value` pairs:

```
00:29:43 INFO    STARTED crawl | categories=smallarms,armor,ships pending_count=3667
00:29:45 DEBUG   SAVED page | category=smallarms kind=detail depth=2 url=... elapsed_ms=125 links_count=7 new_links_count=0
00:29:56 INFO    STOPPED crawl | reason=max_pages fetched_count=5 pending_count=3662
```

| Level | Content |
|-------|---------|
| `DEBUG` | One line for each page (`SAVED`, `SKIPPED`). Use `--verbose` to see these lines. |
| `INFO` | Start, progress (each 25 pages), stop, pages that robots.txt rejects. |
| `WARNING` | A retry after a temporary error. robots.txt is not available. |
| `ERROR` | A page failed after all retries. The crawler stops after 10 failures in sequence. |

### 4.4 Stop and resume a crawl

1. Push `Ctrl+C` to stop the crawler.
   The crawler completes the current request. Then it stops.
2. Start the crawler again with the same command.
   The crawler reads `data/crawl/discovered.jsonl` and `data/crawl/fetched.jsonl`. It continues from the last URL.

The crawler does not download a page two times. A page with the status `failed` goes back into the queue when the crawler starts again.

## 5. Run the extractor

1. Make sure that the crawler downloaded some pages.
2. Start the extractor:

   ```bash
   python -m extractor.parse
   ```

The extractor reads `data/raw/<category>/*.html`. It writes one JSON object for each detail page to `data/parsed/<category>.jsonl`. The extractor uses only regular expressions. It does not use an HTML parser or XPath.

3. Start the coverage statistics:

   ```bash
   python -m extractor.stats
   ```

   The statistics show how many documents have a value in each field. The output is `data/parsed/coverage.json` and `data/parsed/coverage.dokuwiki.txt` (tables for the wiki).

### 5.1 Extracted fields

| Group | Fields |
|-------|--------|
| All categories | `name`, `type`, `country`, `country_original`, `year`, `summary`, `armament`, `variants`, `roles`, `operators`, `operators_original`, `manufacturer`, `total_production`, `site_categories`, `specs`, `text` |
| Small Arms (numbers) | `length_mm`, `barrel_length_mm`, `weight_kg`, `effective_range_m`, `muzzle_velocity_mps`, `rate_of_fire_rpm` |
| Armor (numbers) | `max_speed_kmh`, `range_km`, `crew_count`, `engine_hp`, `weight_kg`, `length_m`, `width_m`, `height_m` |
| Ships | `ships_in_class`; numbers: `displacement_tons`, `length_m`, `beam_m`, `draught_m`, `speed_kts`, `submerged_speed_kts`, `range_km`, `crew_count` |

### 5.2 Data quality rules

- `country` and `operators` contain canonical names. A historical state with one successor gets the successor name (Nazi Germany becomes Germany). The fields `country_original` and `operators_original` keep the names from the site.
- States with more than one successor keep their names: Soviet Union, Czechoslovakia, Yugoslavia, Austria-Hungary.
- Each numeric field has a plausible range. A value out of the range is an error on the site. The extractor sets it to `None` and writes the field name to `implausible_fields`.
- A range "Essentially Unlimited" (nuclear ships) gives `range_km = None` and `is_range_unlimited = true`.
- Some pages show the form placeholder "Please Select" in place of the country. The extractor sets `country_original = None`. If the page has exactly one operator country, `country` gets that country.
- Ship pages give the manufacturer with the label "Shipbuilder(s)", the other pages with "Contractor(s)". Both go to `manufacturer`.

### 5.3 Extractor evaluation

The A-Z catalog page of each category shows the name, the type, the year and the country flag of each object. The catalog uses a different HTML template than the detail page. Thus it is an independent source for a check of the extractor.

```bash
python -m reports.extractor_eval
```

The output is `data/reports/extractor_eval.json` (all mismatches) and `data/reports/extractor_eval.dokuwiki.txt` (match rate of each field).

## 6. Build the index and search

1. Make sure that `data/parsed/` contains the extracted documents (section 5).
2. Build the index:

   ```bash
   python -m indexer.build
   ```

3. Search:

   ```bash
   python -m indexer.search "barrett m82"
   python -m indexer.search "main battle tank max_speed_kmh>60" --method all --top 5
   ```

### 6.1 Index structure

| File | Content |
|------|---------|
| `data/index/dictionary.tsv` | One line for each term: term, document frequency (df), byte offset and byte length of its postings line. The search engine keeps the dictionary in memory. |
| `data/index/postings.txt` | One line for each term: `doc:tf:pos,pos;doc:tf:pos;...`. The search engine reads one line with `seek` (the postings stay on disk). |
| `data/index/documents.jsonl` | One line for each document: the fields for filters and display, the length and the cosine norm. |
| `data/index/meta.json` | Number of documents, terms and postings, mean document length, zone weights. |

The indexer gives a weight to each zone of a document. The tf of a term is the sum of the zone weights of its occurrences:

| Zone | Weight |
|------|--------|
| `name` | 3 |
| `type`, `roles`, `country`, `caliber`, anchor texts of links from other detail pages | 2 |
| all other fields and the article text | 1 |

### 6.2 Ranking methods

| Method | idf | Score |
|--------|-----|-------|
| `tfidf` | classic: `log10(N / df)` | Cosine similarity, SMART lnc.ltc: `(1 + log10 tf) / norm(d)` for the document, `(1 + log10 tf) * idf` for the query. |
| `tfidf_smooth` | smooth: `log10(N / (1 + df)) + 1` | The same cosine similarity. Only the idf is different from `tfidf`. |
| `bm25` | BM25: `ln((N - df + 0.5) / (df + 0.5) + 1)` | Okapi BM25 with `k1 = 1.2`, `b = 0.75`. |

The project does not use the probabilistic idf `log((N - df) / df)`. The course forbids it.

Behavior of the idf variants:

- The classic idf gives a term in all documents the weight 0.
- The smooth idf gives frequent terms a weight near 1. Thus frequent query words ("world", "war") keep a part of the query weight.
- The BM25 idf is equal to `ln((N + 1) / (df + 0.5))`. For rare terms, it is approximately 2.3 times the classic idf. Thus the terms of a query get the same shares of the weight as with the classic idf. The difference between `tfidf` and `bm25` comes from the tf saturation and the length normalization of BM25, not from the idf.

The index stores tf and df only. The search engine computes the idf at query time, so one index serves all methods.

### 6.3 Query syntax

| Part | Example | Meaning |
|------|---------|---------|
| Free text | `main battle tank` | Ranked search. The query uses the same tokenizer as the index. |
| Phrase | `"aircraft carrier"` | The terms must stand next to each other (positional index). |
| Text filter | `country:germany`, `roles:submerged`, `name:"type vii"` | Case-insensitive substring of a field. `country:` and `operators:` also match the original site names and the canonical names. |
| Numeric filter | `max_speed_kmh>60`, `displacement_tons<=10000` | Comparison on a numeric field. |
| Range filter | `year:1939..1945` | Value in a closed range. |

| Option | Default | Description |
|--------|---------|-------------|
| `--method` | `bm25` | `tfidf`, `tfidf_smooth`, `bm25` or `all` (results of the three methods). |
| `--top` | 10 | Number of results. |
| `--match` | `any` | `any`: documents with one or more query terms. `all`: documents with all query terms (intersection of the postings lists, the shortest list first). |

A query with only filters (for example `category:ships country:japan`) gives all documents that match the filters.

A filter on an unknown field (for example `colour:green`) or a missing index stops the command with a short error message and exit code 2.

## 7. Reports

| Command | Output | Content |
|---------|--------|---------|
| `python -m extractor.stats` | `data/parsed/coverage.*` | Number of documents with a value in each field. |
| `python -m reports.corpus_stats` | `data/reports/corpus_stats.*` | Crawl results, size on disk, number of tokens (own tokenizer, tiktoken `o200k_base` and `cl100k_base`). Use `--skip-raw-tokens` to skip the slow count on the raw HTML. |
| `python -m reports.extractor_eval` | `data/reports/extractor_eval.*` | Extractor accuracy against the catalog pages. |
| `python -m reports.search_eval` | `data/reports/search_eval.*` | MAP, P@10, R@100 and nDCG@10 of the three ranking methods on 10 queries, the top 5 results, and the df and the three idf values of each query term. The relevant documents come from the structured fields, not from the text search. |

Each report has a JSON file and a `.dokuwiki.txt` file with tables for the wiki.

Run the full pipeline after a crawl in this order. Each step reads the output of the step before it:

```bash
python -m extractor.parse          # approx. 4 s
python -m extractor.stats
python -m indexer.build            # approx. 10 s
python -m reports.extractor_eval
python -m reports.search_eval
python -m reports.corpus_stats     # approx. 60 s (tiktoken on the raw HTML)
```

To make the ZIP file for the wiki submission (source code, tests and documentation; without `data/` and `.venv/`):

```bash
python -m tools.build_zip          # output: dist/MilSearch-VINF-part1.zip
```

### 7.1 Current results

These values come from the full crawl of 13 October 2026.

| Item | Value |
|------|-------|
| Downloaded pages | 3,717 (100 % of the discovered URLs; 0 failures; 5 retries) |
| Relevant pages (detail pages) | 3,711: 1,314 small arms, 1,263 armor, 1,134 ships |
| Raw HTML on disk | 287.5 MB |
| Extracted documents | 3,711 (22.9 MB JSONL) |
| Index | 91,837 terms, 1,455,490 postings, 2,474,694 positions, 29.1 MB |
| Tokens of the extracted text | 2,556,743 (own tokenizer), 3,463,465 (tiktoken `o200k_base`) |
| Extractor match with the A-Z catalogs | name, type, year 100 %; country 99.8–99.9 % |

| Method | MAP | P@10 | R@100 | nDCG@10 |
|--------|-----|------|-------|---------|
| `tfidf` | 0.475 | 0.550 | 0.835 | 0.605 |
| `tfidf_smooth` | 0.462 | 0.530 | 0.831 | 0.580 |
| `bm25` | 0.754 | 0.610 | 0.912 | 0.841 |

## 8. Output files

| File | Format | Content |
|------|--------|---------|
| `data/raw/<category>/<doc_id>.html` | HTML | The downloaded page. Example: `smallarms-29.html`, `ships-USS-Nimitz-CVN68.html`. |
| `data/crawl/discovered.jsonl` | JSONL | One line for each URL that the crawler found: `url`, `kind`, `category`, `depth`, `parent_url`. |
| `data/crawl/fetched.jsonl` | JSONL | One line for each download attempt: `status`, `http_status`, `final_url`, `file`, `size_bytes`, `sha1`, `elapsed_ms`, `attempts_count`, `links_count`, `new_links_count`, `fetched_at`. |
| `data/crawl/anchors.tsv` | TSV | Link graph: source URL, target URL, anchor text, title attribute. The indexer uses the anchor texts of links between detail pages. |
| `data/parsed/<category>.jsonl` | JSONL | Structured data from the extractor. |
| `data/parsed/coverage.json`, `coverage.dokuwiki.txt` | JSON, DokuWiki | Field coverage statistics. |
| `data/index/*` | TSV, text, JSONL, JSON | Inverted index (section 6.1). |
| `data/reports/*` | JSON, DokuWiki | Statistics and evaluation reports (section 7). |

Values of `status` in `fetched.jsonl`:

| Status | Meaning |
|--------|---------|
| `done` | The crawler downloaded and kept the page. |
| `duplicate` | The page has the same content as a page that the crawler has. The crawler does not keep the page. |
| `failed` | The request failed after all retries. |
| `blocked` | `robots.txt` does not permit the URL. |

## 9. Crawl rules

### 9.1 HTTP headers

| Header | Value |
|--------|-------|
| `User-Agent` | `MilSearchBot/0.1 (+academic search-engine project; FIIT STU Bratislava, course VINF)` |
| `Accept` | `text/html,application/xhtml+xml;q=0.9,*/*;q=0.5` |
| `Accept-Language` | `en-US,en;q=0.8` |
| `Accept-Encoding` | `gzip, deflate` |
| `Connection` | `keep-alive` |
| `Referer` | The URL of the page that has the link. The seed URLs do not have a referer. |

The crawler identifies itself in the `User-Agent` header. It does not show itself as a web browser.

### 9.2 Timeouts and delays

| Parameter | Value | Description |
|-----------|-------|-------------|
| Connect timeout | 5 s | Maximum time to open the TCP/TLS connection. |
| Read timeout | 20 s | Maximum time between two data packets from the server. |
| Delay | 2 s + random 0 to 1 s | Minimum time between two requests to the server. |
| Retries | 3 | After HTTP 429, HTTP 5xx, timeout or connection error. |
| Backoff | 5 s, 10 s, 20 s | Wait time before each retry. If the server sends `Retry-After`, the crawler uses the larger value. |
| Stop condition | 10 failures | The crawler stops after 10 failures in sequence. |

### 9.3 Politeness and scope

- The crawler reads `robots.txt` before the first request. If `robots.txt` has a `Crawl-delay` value that is larger than the delay, the crawler uses that value.
- The crawler sends one request at a time (one thread).
- The crawler downloads only pages from `www.militaryfactory.com`.
- The crawler downloads only these URL types: `/<category>/index.php`, `/<category>/indexMAIN.php`, `/<category>/detail.php?<category>_id=<id>`.
- The crawler accepts only the MIME type `text/html`. The maximum page size is 8 MB.
- The crawler does not follow URLs that are longer than 300 characters.

### 9.4 URL normalization and duplicates

- The crawler changes relative URLs to absolute URLs.
- The crawler changes `http` to `https` and `militaryfactory.com` to `www.militaryfactory.com`.
- The crawler removes fragments (`#...`).
- The crawler changes old links `detail.asp` to `detail.php`.
- The server sends an unknown ID to a different page (HTTP 302). Thus the crawler compares the final URL and the SHA-1 hash of the content. If the page is a duplicate, the crawler does not keep it.

## 10. Tests

1. Make sure that the virtual environment is active and has the development dependencies (section 2).
2. Run all tests:

   ```bash
   python -m pytest
   ```

3. To run one category only, give its directory:

   ```bash
   python -m pytest tests/unit
   python -m pytest tests/integration
   ```

The project has 73 tests. The tests do not send HTTP requests. Each bug fix has a regression test. The test `tests/unit/test_extractor_pages.py` runs the extractor on 20 real pages (7 small arms, 7 armor, 6 ships) and compares the result with hand-checked values.

## 11. Code conventions

- Each `.py` file starts with a header block: `PURPOSE`, `RESPONSIBILITIES` and optional `DEPENDENCIES`, `USAGE`, `INVARIANTS`, `FORMULAS`.
- The body has these sections in this order: `CONSTANTS`, `TYPES`, `HELPERS`, `MAIN LOGIC`, `ENTRY POINT`.
- Each function has a docstring with `Steps:` and `Returns:`. The inline markers `# 1.`, `# 2.` in the body repeat the steps.
- Each class has a docstring with `Responsibilities:` and optional `Invariants:`.
- Names use unit suffixes (`_seconds`, `_ms`, `_bytes`), counter suffixes (`_count`) and boolean prefixes (`is_`, `has_`, `should_`).
- Function prefixes: `fetch_` (network), `load_` (file read), `save_` (file write), `compute_`, `build_`, `get_` (pure functions).
- Comments and documentation use ASD-STE100 Simplified Technical English.

## 12. Known limitations

- The crawler uses one thread. A full crawl of approximately 3,700 pages takes approximately 2.5 hours.
- The extractor uses the HTML structure of MilitaryFactory.com. If the site changes its HTML, you must change the regular expressions in `extractor/parse.py`.
- The indexer builds the index in memory in one pass. This is correct for approximately 3,700 documents. A much larger collection needs block-based indexing (BSBI or SPIMI with blocks on disk).
- The S-stemmer only removes plural endings. It does not change verb forms.
- Some fields exist only on some pages of the site: `total_production` only on armor pages, the shipbuilder only on 19 % of the ship pages, the muzzle velocity on 56 % of the small arms pages. The extractor cannot get values that the site does not show.
- The cosine methods (`tfidf`, `tfidf_smooth`) put long articles lower. For example, `barrett m82` gives Barrett M82 at rank 5 with `tfidf` and at rank 1 with `bm25`. Thus `bm25` is the default method.
