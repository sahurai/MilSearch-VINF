# MilSearch

MilSearch is a search engine for military equipment. It has data about three types of equipment:

- Infantry small arms (rifles, pistols, machine guns).
- Land vehicles and artillery (tanks, armored vehicles, howitzers).
- Naval ships and submarines.

The data comes from two sources:

1. HTML pages from [MilitaryFactory.com](https://www.militaryfactory.com). The MilSearch crawler downloads these pages.
2. The Wikipedia dump (EN and SK). The second part of the project uses this dump.

Part 1 of the project: the crawler, the regex extractor, the own inverted index with ranked search, and the statistics and evaluation reports (sections 4–12).

Part 2 of the project: distributed processing in Apache Spark, the Wikipedia dump (EN and SK) and the join of the MilitaryFactory documents with Wikipedia (section 13).

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

For the Spark jobs (part 2), you also need Docker and the image of the course `iisas/hadoop-spark-pig-hive:2.9.2` (Spark 2.4.3, Python 3.6.9, Java 8). The Spark jobs and the modules that they use (`extractor/parse.py`, `extractor/countries.py`, `wiki/`, `spark_jobs/`) use Python 3.6 syntax, so that they run in this image and on the cluster of the course.

```bash
docker pull iisas/hadoop-spark-pig-hive:2.9.2
```

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
├── wiki/                  Pure regex functions for Wikipedia (Python 3.6, no pyspark).
│   ├── wikitext.py    Record fields of the XML dump, military filter, templates, infobox, abstract, links, langlinks.
│   ├── keys.py        Join keys: name variants of the documents, alias keys of the wiki titles and redirects.
│   └── match.py       Wiki attributes of interest and the score of a join candidate.
├── spark_jobs/            Spark jobs (spark-submit; Spark 2.4, Python 3.6).
│   ├── common.py      Spark session, data URIs (file:// or hdfs://), log lines, reports.
│   ├── extract_pages.py   The regex extractor of part 1 on all pages, in Spark.
│   ├── wiki_pages.py  Wikipedia XML dump (bz2) -> Parquet page table; regex fields in the JVM.
│   ├── wiki_anchors.py    Anchor texts and inlinks of the military pages from all EN links.
│   └── wiki_join.py   Join of the documents with EN and SK Wikipedia, statistics, evaluation.
├── reports/
│   ├── corpus_stats.py    Crawl, disk and token statistics (own tokenizer and tiktoken).
│   ├── extractor_eval.py  Extractor accuracy against the A-Z catalog pages.
│   └── search_eval.py     Comparison of the ranking methods (MAP, P@10, R@100, nDCG@10, idf of query terms).
├── tools/
│   ├── build_zip.py       Makes the ZIP file with the source code for the wiki submission.
│   ├── download_wiki.py   Downloads the Wikipedia dump files (resume, MD5 check).
│   └── spark.py           Runs a Spark job with spark-submit in the Docker image of the course.
├── tests/
│   ├── unit/          Tests of pure functions. No network.
│   ├── integration/   Tests with files in a temporary directory (frontier, index and search).
│   └── fixtures/      20 real detail pages and their hand-checked expected values.
├── data/              Output of the crawler, the extractor, the indexer and the reports (see section 8).
├── docs/
│   ├── konzultacia2.txt   Wiki page for consultation 2a (DokuWiki syntax, Slovak).
│   ├── konzultacia2b.txt  Wiki page for consultation 2b (DokuWiki syntax, Slovak).
│   ├── odovzdanie1.txt    Wiki page for the submission of part 1: the full report (DokuWiki, Slovak).
│   ├── konzultacia3.txt   Wiki page for consultation 3: Spark, Wikipedia dump, join (DokuWiki, Slovak).
│   └── diagrams/          Diagrams (crawler architecture, Spark pipeline): HTML source (rough.js), render script, PNG.
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

The project has 111 tests (73 for part 1, 38 for part 2). The tests do not send HTTP requests and do not need Spark. Each bug fix has a regression test. The test `tests/unit/test_extractor_pages.py` runs the extractor on 20 real pages (7 small arms, 7 armor, 6 ships) and compares the result with hand-checked values.

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

## 13. Part 2: Spark and Wikipedia

### 13.1 Pipeline

| Step | Command | Input | Output |
|------|---------|-------|--------|
| 1 | `python -m tools.download_wiki` | dumps.wikimedia.org (dump 20261101) | `data/wiki/dump/en/*.bz2` (71 files), `data/wiki/dump/sk/*` |
| 2 | `python -m tools.spark extract_pages` | `data/raw/*/*.html` | `data/spark/parsed/` (JSON lines) |
| 3 | `python -m tools.spark wiki_pages --lang en` | EN dump | `data/wiki/en/pages/is_military=true\|false/` (Parquet) |
| 4 | `python -m tools.spark wiki_pages --lang sk` | SK dump | `data/wiki/sk/pages/` (Parquet) |
| 5 | `python -m tools.spark wiki_anchors` | EN page table | `data/wiki/en/anchors/` (Parquet) |
| 6 | `python -m tools.spark wiki_join` | steps 2–5, SK langlinks dump | `data/wiki/joined.jsonl`, `data/wiki/joined/` |
| 7 | `python -m indexer.build --wiki` | `data/parsed/`, `data/wiki/joined.jsonl` | `data/index/` with the Wikipedia zones |

Each Spark job writes a report to `data/reports/<job>.json` and `data/reports/<job>.dokuwiki.txt`.

`tools/spark.py` packs `extractor/`, `wiki/` and `spark_jobs/` into `dist/milsearch-spark.zip` and runs:

```bash
docker run --rm -v <project>:/app -w /app -e PYSPARK_PYTHON=python3 --entrypoint spark-submit \
    iisas/hadoop-spark-pig-hive:2.9.2 --master local[16] --driver-memory 20g \
    --py-files dist/milsearch-spark.zip spark_jobs/<job>.py [job options]
```

Launcher options: `--cores N` (default: all CPU cores), `--memory 20g` (driver memory; in local mode the driver is also the executor). Other options go to the job.

On a cluster, copy the data to HDFS and give the HDFS directory to each job:

```bash
hadoop fs -put data/raw data/wiki/dump /user/root/milsearch/...
spark-submit --master yarn --py-files milsearch-spark.zip spark_jobs/wiki_pages.py \
    --data-dir hdfs:///user/root/milsearch --lang en
```

### 13.2 What each job does

- `extract_pages`: `wholeTextFiles` gives one record per HTML file. Each task runs `extractor.parse.extract_document` (the same regex extractor as part 1). The job counts the documents and the field coverage with `reduceByKey`, and compares the result with `data/parsed/*.jsonl` (full outer join on the document ID).
- `wiki_pages`: `spark.read.text(..., lineSep="</page>")` gives one record per page. `regexp_extract` (JVM) gets the title, namespace, page ID, redirect target and text. The job keeps namespace 0, decodes the XML entities and marks the military pages (weapon, vehicle or ship infobox or category). One dump file is one partition: Hadoop reads a page two times at the border of two splits of a bz2 file with a multi-byte separator.
- `wiki_anchors`: splits the text of each article at `[[`, gets the link target and the anchor text with `regexp_extract`, resolves the redirects and keeps the links to military pages (broadcast join). It counts the inlinks and the most frequent anchor texts of each military page.
- `wiki_join`: see 13.3.

### 13.3 Join of the documents with Wikipedia

1. Document keys: the name in lowercase letters and digits only (`T-72B3` → `t72b3`), in several variants with a rank: full name (0), name without the text in parentheses or without the ship prefix (1), alternative name in parentheses (2), word suffix with a number and a name word (`Panzer IV`) or name after the type words (3), word prefix or last name word (4).
2. Wiki keys: for each military page, the title and all redirects to it (0) and parts of the title (1): without the text in parentheses (`Comet (tank)`), the ship name (`German battleship Bismarck` → `Bismarck`), the class (`Nanuchka-class corvette` → `Nanuchka class`), without a type word (`T-60 tank`).
3. Equi-join on the key → candidates (document, page).
4. Each candidate page is parsed with regex (infobox, categories, abstract). The score: `10 − 2 × (document rank + alias rank)`; +3 for a page of the expected kind (ship for ships; small arm or weapon for smallarms; heavy equipment or weapon for armor), −6 for another kind; +3 for a year within 3 years (+1 within 10 years, −5 more than 30 years away); −3 for a weak name match (rank sum 2 or more) without a year within 10 years; +2 when the page names the country; −10 for an index page or a type article (`Medium tank`) without an exact name match; −6 for an infobox of another thing (company, event, person); −10 for another hull number in the title; −4 for another ship type or weapon type (mortar vs. carbine). The best page with a score of 8 or more is the result.
5. The result gets the redirects, the anchor texts and the Slovak page (interlanguage links from `skwiki-*-langlinks.sql.gz`, parsed with regex).
6. Evaluation: 51 hand-checked pairs in `tests/fixtures/wiki_gold.tsv` (precision, recall) and a random sample of 40 pairs in the report.

### 13.4 Wikipedia attributes

| Attribute | Infobox parameters | Why |
|-----------|-------------------|-----|
| `designer`, `design_date` | `designer`, `design_date` | MilitaryFactory does not give the designer. |
| `manufacturer` | `manufacturer`, `builder(s)` | Complete list; MilitaryFactory has the shipbuilder on 19 % of the ship pages only. |
| `unit_cost` | `unit_cost`, `cost` | Not on MilitaryFactory. |
| `number_built` | `number`, `total_ships_completed` | MilitaryFactory has the production only on armor pages. |
| `service`, `production_date` | `service`, `in_service_range`, `production_date`, `built_range` | Time ranges (MilitaryFactory has one year). |
| `wars` | `wars`, `battles` | Conflicts (filter `wiki_wars:vietnam`). |
| `laid_down`, `launched`, `commissioned`, `decommissioned`, `fate`, `status` | `ship_*` / career section | Career of a ship. |
| names | title, redirects, anchor texts, Slovak title | Search by other names (`Katyusha`, `Tommy gun`). |
| abstract, categories | lead section, `[[Category:...]]` | Short description in English and Slovak. |

`python -m indexer.build --wiki` adds the zones `wiki_names` (weight 2), `wiki_abstract` and `wiki_wars` (weight 1), and the fields `wiki_title`, `sk_title`, `wiki_inlinks_count` and `wiki_<attribute>` for the filters.

### 13.5 Current results

These values come from the dump of 1 November 2026 (`python -m tools.spark ...` with `--cores 16 --memory 20g`).

| Job | Result | Time |
|-----|--------|------|
| `extract_pages` | 3,711 documents, the same as the sequential extractor (3,711 of 3,711 equal JSON) | 76 s |
| `wiki_pages --lang en` | 25.9 GB bz2 (71 files) → 19,357,214 pages: 12,097,295 redirects, 7,259,919 articles, 67,818 military pages | 27 min |
| `wiki_pages --lang sk` | 0.36 GB bz2 → 349,051 pages (sequential check with `bzcat \| grep`: 349,051) | 3.5 min |
| `wiki_anchors` | 2,001,417 links to 62,047 military pages | 7.5 min |
| `wiki_join` | 2,756 of 3,711 documents (74.3 %) → 2,575 unique EN pages, 292 unique SK pages | 73 s |

| Check | Result |
|-------|--------|
| Hand-checked pairs (`tests/fixtures/wiki_gold.tsv`) | 51 of 51 correct (the rules were tuned on these pairs) |
| Random samples of the result (40 + 50 pairs) | 90 of 90 correct |
| Search evaluation, `bm25`, without → with `--wiki` | MAP 0.754 → 0.808, P@10 0.61 → 0.68, R@100 0.912 → 0.934, nDCG@10 0.841 → 0.876 |

The reports are in `data/reports/spark_extract.*`, `wiki_pages_en.*`, `wiki_pages_sk.*`, `wiki_anchors.*`, `wiki_join.*` and `search_eval_wiki.*` (search evaluation with the Wikipedia zones; `search_eval.*` stays the part 1 evaluation).

### 13.6 Known limitations of part 2

- The local run uses one machine (`local[16]`). One bz2 file is one partition, so the largest file sets the minimum time of `wiki_pages`.
- The military pre-filter (infobox or category) is broad: it also marks some pages about people (categories such as "... Artillery Branch personnel"). The score of the join removes them.
- 955 documents have no join result: the object has no English article, Wikipedia uses a very different name (`Carro Armato L3` → `L3/35`), or no year confirms a weak name match. The rules prefer precision to recall.
- The Slovak Wikipedia has few military articles: 292 of the 2,575 joined English pages have a Slovak version.
