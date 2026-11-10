# ==============================================================================
# SPARK JOB: WIKI JOIN — MilSearch
# ==============================================================================
# PURPOSE:
#     Join the MilitaryFactory documents (part 1) with the English and the Slovak Wikipedia.
#     MilitaryFactory has the technical data. Wikipedia adds the attributes that the site does not
#     have (designer, unit cost, number built, wars, service dates, ship career), other names
#     (redirects, anchor texts), the abstract, the categories and the Slovak name.
#
# RESPONSIBILITIES:
#     1. Make the name keys of the documents (several variants with a rank) and of the military pages
#        (title, redirects, title parts with a rank).
#     2. Join the documents with the pages on the key (equi-join in Spark) -> join candidates.
#     3. Parse the candidate pages (infobox, categories, abstract) with regular expressions.
#     4. Score each candidate (name match, page kind, year, country); keep the best page of each document.
#     5. Join the result with the redirects, the anchor texts and the Slovak pages (langlinks SQL dump).
#     6. Write the joined documents and the join statistics.
#
# USAGE:
#     python -m tools.spark wiki_join             (after extract_pages, wiki_pages en/sk, wiki_anchors)
#
# OUTPUT:
#     data/wiki/joined/ (JSON parts) and data/wiki/joined.jsonl: one line for each joined document.
#     data/reports/wiki_join.json, .dokuwiki.txt
#
# INVARIANTS:
#     Python 3.6 syntax and Spark 2.4 API (image of the course).
# ==============================================================================

import html
import json
import re
import os
import random
import time
from collections import Counter
from typing import Dict, Iterator, List, Optional, Tuple

from pyspark.sql import Row
from pyspark.sql import functions as F

from spark_jobs.common import build_dokuwiki_table, get_argument_parser, get_data_uri, get_spark, log, save_report
from wiki.keys import build_document_keys, build_title_aliases
from wiki.match import (MIN_MATCH_SCORE, WIKI_ATTRIBUTE_PARAMETERS, compute_match_score, get_page_kind,
                        get_wiki_attributes, get_wiki_years)
from wiki.wikitext import get_langlinks, parse_page

# ==============================================================================
# CONSTANTS
# ==============================================================================

CATEGORIES = ("smallarms", "armor", "ships")
ATTRIBUTES = tuple(attribute for attribute, _ in WIKI_ATTRIBUTE_PARAMETERS)
LANGLINKS_PATTERN = "skwiki-*-langlinks.sql.gz"
WIKI_URL = "https://{}.wikipedia.org/wiki/{}"
MAX_REDIRECTS_COUNT = 40
JOINED_JSONL_PATH = os.path.join("data", "wiki", "joined.jsonl")
_MARKUP_RE = re.compile(r"[{}<>\[\]|]")
DOCUMENT_FIELDS = ("id", "name", "category", "type", "country", "year", "url")
# Explicit schemas: Spark 2.4 cannot infer the type of an empty list, and Row(**kwargs) sorts the fields.
DOCUMENT_KEY_SCHEMA = "key string, doc_id string, doc_rank int"
ALIAS_SCHEMA = "key string, page string, alias_rank int"
PAGE_INFO_SCHEMA = ("page string, text_bytes int, kind string, kinds array<string>, years array<int>, "
                    "country_text string, attributes string, categories array<string>, abstract string, "
                    "plain_text string, type_text string")
LANGLINK_SCHEMA = "sk_id long, en_title string"
SLOVAK_SCHEMA = "en_title string, sk_title string, sk_abstract string, sk_categories array<string>"


# ==============================================================================
# HELPERS
# ==============================================================================

def get_document_meta(line: str) -> Dict[str, object]:
    """
    Get the fields of a MilitaryFactory document that the join needs.

    Steps:
        1. Parse the JSON line and keep the join fields.

    Returns:
        Map with id, name, category, type, country, year, url.
    """
    # 1. Parse the JSON line and keep the join fields
    document = json.loads(line)
    return {field: document.get(field) for field in DOCUMENT_FIELDS}


def get_clean_anchors(anchors: List[Row]) -> List[str]:
    """
    Clean the anchor texts of a page for the output.

    Steps:
        1. Skip anchor texts with wikitext markup ("{{convert|...}}", tags).
        2. Decode HTML entities ("&nbsp;") and remove duplicates; keep the frequency order.

    Returns:
        Anchor texts.
    """
    texts: List[str] = []
    for anchor in anchors:
        # 1. Skip anchor texts with wikitext markup
        if _MARKUP_RE.search(anchor["text"]):
            continue

        # 2. Decode HTML entities and remove duplicates; keep the frequency order
        text = " ".join(html.unescape(anchor["text"]).split())
        if text and text not in texts:
            texts.append(text)
    return texts


def get_page_url(language: str, title: str) -> str:
    """
    Make the URL of a Wikipedia page.

    Steps:
        1. Replace spaces with "_" in the title.

    Returns:
        URL such as https://en.wikipedia.org/wiki/Leopard_2.
    """
    # 1. Replace spaces with "_" in the title
    return WIKI_URL.format(language, title.replace(" ", "_"))


def parse_page_partition(rows: Iterator[Row]) -> Iterator[Tuple]:
    """
    Parse the candidate pages of one partition: infobox, kind, years, countries, abstract.

    Steps:
        1. Parse the wikitext with regular expressions.
        2. Get the page kind, the infobox years, the attributes and the text for the country check.

    Returns:
        One tuple for each page in the order of PAGE_INFO_SCHEMA.
    """
    for row in rows:
        # 1. Parse the wikitext with regular expressions
        try:
            page = parse_page(row["text"] or "")
        except RecursionError:  # Defensive: a broken page must not stop the job.
            page = {"infobox_kinds": [], "infobox": {}, "categories": [], "abstract": "", "text": ""}

        # 2. Get the page kind, the infobox years, the attributes and the text for the country check
        fields = page["infobox"]
        attributes = get_wiki_attributes(fields)
        country_text = " | ".join([row["page"], attributes.get("origin", ""), attributes.get("used_by", ""),
                                   attributes.get("manufacturer", ""), fields.get("ship_country", ""),
                                   fields.get("ship_flag", ""), fields.get("operators", "")] + page["categories"])
        yield (row["page"], row["text_bytes"],
               get_page_kind(page["infobox_kinds"], page["categories"], fields.get("type", ""), row["page"]),
               page["infobox_kinds"], get_wiki_years(fields), country_text,
               json.dumps(attributes, ensure_ascii=False), page["categories"], page["abstract"], page["text"],
               fields.get("type", ""))


def score_candidate(row: Row, documents: Dict[str, Dict[str, object]]) -> Tuple[str, Tuple]:
    """
    Score one (document, page) candidate.

    Steps:
        1. Get the document fields from the broadcast map.
        2. Compute the score from the name ranks, the page kind, the years and the countries.

    Returns:
        (document ID, (score, -name penalty, page size, page, key, document rank, alias rank)).
    """
    # 1. Get the document fields from the broadcast map
    document = documents[row["doc_id"]]

    # 2. Compute the score from the name ranks, the page kind, the years and the countries
    score = compute_match_score(row["doc_rank"], row["alias_rank"], document["category"], document["year"],
                                document["country"], row["kind"], row["years"] or [], row["country_text"] or "",
                                document["name"] or "", document["type"] or "", row["page"], row["type_text"] or "")
    return row["doc_id"], (score, -(row["doc_rank"] + row["alias_rank"]), row["text_bytes"] or 0, row["page"],
                           row["key"], row["doc_rank"], row["alias_rank"])


def build_joined_record(document: Dict[str, object], match: Tuple, candidates_count: int,
                        page: Row, redirects: Optional[List[str]], anchors: Optional[Row],
                        slovak: Optional[Row]) -> Dict[str, object]:
    """
    Build one output record: the document fields, the match details and the wiki data.

    Steps:
        1. Copy the document fields and the match details.
        2. Add the English page data: attributes, abstract, categories, redirects, anchors, text.
        3. Add the Slovak page data, when the page has a Slovak version.

    Returns:
        Record for data/wiki/joined.jsonl.
    """
    score, _, _, title, key, document_rank, alias_rank = match
    # 1. Copy the document fields and the match details
    record = dict(document)
    record["match"] = {"score": score, "key": key, "document_rank": document_rank, "alias_rank": alias_rank,
                       "accepted_candidates_count": candidates_count}

    # 2. Add the English page data
    record.update({
        "wiki_title": title, "wiki_url": get_page_url("en", title), "wiki_kinds": page["kinds"],
        "wiki_attributes": json.loads(page["attributes"]), "wiki_abstract": page["abstract"],
        "wiki_categories": page["categories"], "wiki_redirects": redirects or [],
        "wiki_inlinks_count": anchors["inlinks_count"] if anchors else 0,
        "wiki_anchors": get_clean_anchors(anchors["anchors"] or []) if anchors else [],
        "wiki_text": page["plain_text"],
    })

    # 3. Add the Slovak page data
    if slovak:
        record.update({"sk_title": slovak["sk_title"], "sk_url": get_page_url("sk", slovak["sk_title"]),
                       "sk_abstract": slovak["sk_abstract"], "sk_categories": slovak["sk_categories"]})
    return record


def parse_slovak_partition(rows: Iterator[Row]) -> Iterator[Tuple]:
    """
    Parse the Slovak pages of one partition: abstract and categories.

    Steps:
        1. Parse the wikitext and keep the abstract and the categories.

    Returns:
        One tuple for each Slovak page in the order of SLOVAK_SCHEMA.
    """
    for row in rows:
        # 1. Parse the wikitext and keep the abstract and the categories
        page = parse_page(row["text"] or "")
        yield row["en_title"], row["sk_title"], page["abstract"], page["categories"]


def load_gold_pairs(path: str) -> List[Tuple[str, str]]:
    """
    Load the hand-checked pairs (MilitaryFactory name, English Wikipedia title).

    Steps:
        1. Read the TSV file on the driver; skip the header and empty lines.

    Returns:
        List of (document name, expected title); empty when the file does not exist.
    """
    # 1. Read the TSV file on the driver; skip the header and empty lines
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as file_handle:
        rows = [line.rstrip("\n").split("\t") for line in file_handle.readlines()[1:]]
    return [(row[0], row[1]) for row in rows if len(row) == 2]


def evaluate_gold(gold_pairs: List[Tuple[str, str]], records: List[Dict[str, object]],
                  canonical_titles: Dict[str, str]) -> Dict[str, object]:
    """
    Compare the join result with the hand-checked pairs.

    Steps:
        1. Resolve each expected title through the redirects to the page title.
        2. Count correct, wrong and missing joins; compute precision and recall.

    Returns:
        Evaluation with the counts, precision, recall and the rows of each pair.
    """
    joined_by_name = {record["name"]: record["wiki_title"] for record in records}
    rows = []
    for name, expected in gold_pairs:
        # 1. Resolve each expected title through the redirects to the page title
        expected_page = canonical_titles.get(expected, expected)

        # 2. Count correct, wrong and missing joins
        actual = joined_by_name.get(name)
        status = "missing" if actual is None else "correct" if actual == expected_page else "wrong"
        rows.append({"name": name, "expected": expected_page, "actual": actual, "status": status})
    counts = Counter(row["status"] for row in rows)
    joined_count = counts["correct"] + counts["wrong"]
    return {"pairs_count": len(rows), "correct": counts["correct"], "wrong": counts["wrong"],
            "missing": counts["missing"], "precision": round(counts["correct"] / max(1, joined_count), 3),
            "recall": round(counts["correct"] / max(1, len(rows)), 3), "rows": rows}


def build_tables(report: Dict[str, object]) -> List[str]:
    """
    Make the DokuWiki tables of the join report.

    Steps:
        1. Table of documents, joined documents and unique pages per category.
        2. Tables of the name variants, the attribute coverage and the examples.

    Returns:
        List of table texts.
    """
    # 1. Table of documents, joined documents and unique pages per category
    by_category = report["by_category"]
    rows = [[category, by_category[category]["documents"],
             "{} ({:.1%})".format(by_category[category]["joined"],
                                  by_category[category]["joined"] / max(1, by_category[category]["documents"])),
             by_category[category]["unique_en_pages"], by_category[category]["unique_sk_pages"]]
            for category in CATEGORIES]
    rows.append(["**spolu**", report["documents_count"],
                 "**{} ({:.1%})**".format(report["joined_count"], report["joined_count"] / max(1, report["documents_count"])),
                 "**{}**".format(report["unique_en_pages"]), "**{}**".format(report["unique_sk_pages"])])
    tables = [build_dokuwiki_table(["Kategória", "Dokumenty", "Pripojené dokumenty", "Unikátne EN stránky",
                                    "Unikátne SK stránky"], rows)]

    # 2. Tables of the name variants, the attribute coverage and the examples
    tables.append(build_dokuwiki_table(["Variant názvu (MilitaryFactory)", "Dokumenty"],
                                       [[key, value] for key, value in report["document_ranks"].items()]))
    tables.append(build_dokuwiki_table(["Alias (Wikipédia)", "Dokumenty"],
                                       [[key, value] for key, value in report["alias_ranks"].items()]))
    tables.append(build_dokuwiki_table(["Atribút z Wikipédie", "Pripojené dokumenty s hodnotou"],
                                       [[attribute, "{} ({:.0%})".format(count, count / max(1, report["joined_count"]))]
                                        for attribute, count in report["attribute_coverage"].items()]))
    tables.append(build_dokuwiki_table(["Dokument", "Wikipédia EN", "Wikipédia SK", "Kľúč", "Skóre"],
                                       [[example["name"], example["wiki_title"], example.get("sk_title") or "–",
                                         example["key"], example["score"]] for example in report["examples"]]))
    gold = report["gold"]
    if gold["pairs_count"]:
        tables.append(build_dokuwiki_table(
            ["Ručne overené páry", "Správne", "Nesprávne", "Nepripojené", "Precision", "Recall"],
            [[gold["pairs_count"], gold["correct"], gold["wrong"], gold["missing"], gold["precision"], gold["recall"]]]))
        tables.append(build_dokuwiki_table(["Dokument", "Očakávaná stránka", "Výsledok join", "Stav"],
                                           [[row["name"], row["expected"], row["actual"] or "–", row["status"]]
                                            for row in gold["rows"] if row["status"] != "correct"]))
    tables.append(build_dokuwiki_table(["Náhodná vzorka: dokument", "Wikipédia EN", "Skóre"],
                                       [[row["name"], row["wiki_title"], row["score"]] for row in report["sample"]]))
    return tables


# ==============================================================================
# ENTRY POINT
# ==============================================================================

def main() -> None:
    """
    Join the documents with Wikipedia and write the result and the report.

    Steps:
        1. Read the documents; make the document keys.
        2. Read the page table; make the alias keys of the military pages and their redirects.
        3. Join the keys -> candidates; keep the best name variant of each (document, page) pair.
        4. Parse the candidate pages; score the candidates; keep the best page of each document.
        5. Join the best pages with the redirects, the anchor texts and the Slovak pages.
        6. Write the joined documents and the report.

    Returns:
        None.
    """
    parser = get_argument_parser("Join the MilitaryFactory documents with Wikipedia.")
    parser.add_argument("--gold", default=os.path.join("tests", "fixtures", "wiki_gold.tsv"),
                        help="Hand-checked pairs for the evaluation (local file on the driver).")
    arguments = parser.parse_args()
    spark = get_spark("wiki_join")
    context = spark.sparkContext
    started = time.time()

    # 1. Read the documents; make the document keys
    documents_rdd = context.textFile(get_data_uri(arguments.data_dir, "spark", "parsed")).map(get_document_meta)
    documents = {document["id"]: document for document in documents_rdd.collect()}
    documents_broadcast = context.broadcast(documents)
    document_keys = spark.createDataFrame(documents_rdd.flatMap(lambda document: [
        (key, document["id"], rank)
        for key, rank in build_document_keys(document["name"], document["category"]).items()]), DOCUMENT_KEY_SCHEMA)
    log("COUNTED", "documents", documents_count=len(documents), keys_count=document_keys.count())

    # 2. Read the page table; make the alias keys of the military pages and their redirects
    pages_uri = get_data_uri(arguments.data_dir, "wiki", "en", "pages")
    pages = spark.read.parquet(pages_uri)
    military_all = spark.read.option("basePath", pages_uri).parquet(pages_uri + "/is_military=true") \
        .where(F.col("redirect").isNull())
    military = military_all.select(F.col("title").alias("page"), "text_bytes")
    redirects = pages.where(F.col("redirect").isNotNull()) \
        .select(F.col("title").alias("name"), F.col("redirect").alias("page")).join(military.select("page"), "page")
    title_aliases = military.rdd.flatMap(lambda row: [
        (key, row["page"], rank) for key, rank in build_title_aliases(row["page"], False).items()])
    redirect_aliases = redirects.rdd.flatMap(lambda row: [
        (key, row["page"], rank) for key, rank in build_title_aliases(row["name"], True).items()])
    aliases = spark.createDataFrame(title_aliases.union(redirect_aliases), ALIAS_SCHEMA) \
        .groupBy("key", "page").agg(F.min("alias_rank").alias("alias_rank"))

    # 3. Join the keys -> candidates; keep the best name variant of each (document, page) pair
    candidates = document_keys.join(aliases, "key") \
        .withColumn("penalty", F.col("doc_rank") + F.col("alias_rank")) \
        .groupBy("doc_id", "page").agg(F.min(F.struct("penalty", "doc_rank", "alias_rank", "key")).alias("best")) \
        .select("doc_id", "page", "best.doc_rank", "best.alias_rank", "best.key").cache()
    candidates_count = candidates.count()
    log("JOINED", "keys", candidates_count=candidates_count,
        documents_with_candidates_count=candidates.select("doc_id").distinct().count())

    # 4. Parse the candidate pages; score the candidates; keep the best page of each document
    candidate_pages = military_all.select(F.col("title").alias("page"), "text_bytes", "text") \
        .join(candidates.select("page").distinct(), "page")
    page_info = spark.createDataFrame(candidate_pages.rdd.mapPartitions(parse_page_partition), PAGE_INFO_SCHEMA).cache()
    log("PARSED", "candidate pages", pages_count=page_info.count())
    scored = candidates.join(page_info.select("page", "text_bytes", "kind", "years", "country_text", "type_text"), "page") \
        .rdd.map(lambda row: score_candidate(row, documents_broadcast.value)).cache()
    accepted = scored.filter(lambda pair: pair[1][0] >= MIN_MATCH_SCORE)
    accepted_counts = dict(accepted.map(lambda pair: (pair[0], 1)).reduceByKey(lambda left, right: left + right).collect())
    best = dict(accepted.reduceByKey(max).collect())
    log("SCORED", "candidates", accepted_count=sum(accepted_counts.values()), joined_documents_count=len(best))

    # 5. Join the best pages with the redirects, the anchor texts and the Slovak pages
    best_titles = spark.createDataFrame(sorted({(match[3],) for match in best.values()}), "page string").cache()
    pages_by_title = {row["page"]: row for row in page_info.join(best_titles, "page").collect()}
    redirects_by_title = {row["page"]: row["names"] for row in redirects.join(best_titles, "page").groupBy("page")
                          .agg(F.slice(F.sort_array(F.collect_set("name")), 1, MAX_REDIRECTS_COUNT).alias("names"))
                          .collect()}
    anchors_by_title: Dict[str, Row] = {}
    try:
        anchors = spark.read.parquet(get_data_uri(arguments.data_dir, "wiki", "en", "anchors"))
        anchors_by_title = {row["title"]: row for row in
                            anchors.join(best_titles.withColumnRenamed("page", "title"), "title").collect()}
    except Exception as error:  # The anchor table is optional (wiki_anchors job).
        log("SKIPPED", "anchors", reason=type(error).__name__)
    slovak_by_title: Dict[str, Row] = {}
    try:
        langlinks = spark.createDataFrame(context.textFile(get_data_uri(
            arguments.data_dir, "wiki", "dump", "sk", LANGLINKS_PATTERN)).flatMap(
            lambda line: get_langlinks(line, "en")), LANGLINK_SCHEMA)
        slovak_pages = spark.read.parquet(get_data_uri(arguments.data_dir, "wiki", "sk", "pages")) \
            .where(F.col("redirect").isNull()).select(F.col("id").alias("sk_id"), F.col("title").alias("sk_title"), "text")
        slovak = langlinks.join(best_titles.withColumnRenamed("page", "en_title"), "en_title").join(slovak_pages, "sk_id")
        slovak_rows = spark.createDataFrame(slovak.rdd.mapPartitions(parse_slovak_partition), SLOVAK_SCHEMA).collect()
        slovak_by_title = {row["en_title"]: row for row in slovak_rows}
        log("JOINED", "slovak pages", langlinks_count=langlinks.count(), sk_pages_count=len(slovak_by_title))
    except Exception as error:  # The Slovak data is optional.
        log("SKIPPED", "slovak", reason="{}: {}".format(type(error).__name__, str(error)[:200]))

    # 6. Write the joined documents and the report
    records = [build_joined_record(documents[document_id], match, accepted_counts.get(document_id, 1),
                                   pages_by_title[match[3]], redirects_by_title.get(match[3]),
                                   anchors_by_title.get(match[3]), slovak_by_title.get(match[3]))
               for document_id, match in sorted(best.items())]
    os.makedirs(os.path.dirname(JOINED_JSONL_PATH), exist_ok=True)
    with open(JOINED_JSONL_PATH, "w", encoding="utf-8") as file_handle:
        for record in records:
            file_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    spark.createDataFrame([(json.dumps(record, ensure_ascii=False),) for record in records], "value string") \
        .write.mode("overwrite").text(get_data_uri(arguments.data_dir, "wiki", "joined"))
    log("SAVED", "joined documents", records_count=len(records), path=JOINED_JSONL_PATH)

    by_category = {}
    for category in CATEGORIES:
        joined = [record for record in records if record["category"] == category]
        by_category[category] = {
            "documents": sum(1 for document in documents.values() if document["category"] == category),
            "joined": len(joined), "unique_en_pages": len({record["wiki_title"] for record in joined}),
            "unique_sk_pages": len({record["sk_title"] for record in joined if record.get("sk_title")}),
        }
    rank_names = {0: "0 – celý názov", 1: "1 – hlavný názov / bez prefixu lode", 2: "2 – alternatívny názov v zátvorke",
                  3: "3 – prípona s číslom (bez výrobcu)", 4: "4 – predpona"}
    alias_names = {0: "0 – názov stránky alebo presmerovanie", 1: "1 – časť názvu (bez zátvorky, typu, národnosti)"}
    document_ranks = Counter(rank_names[record["match"]["document_rank"]] for record in records)
    alias_ranks = Counter(alias_names[record["match"]["alias_rank"]] for record in records)
    attribute_coverage = {attribute: sum(1 for record in records if attribute in record["wiki_attributes"])
                          for attribute in ATTRIBUTES}
    examples = [record for record in records if record["name"] in (
        "Barrett M82", "Leopard 2", "USS Nimitz (CVN-68)", "Type VII U-Boat", "Colt M16A2", "KMS Bismarck",
        "MP40 (Maschinenpistole 40)", "T-14 Armata", "Nanuchka (class) / (Project 1234 Ovod)", "Lahti L-35")]
    report = {
        "documents_count": len(documents), "candidates_count": candidates_count,
        "joined_count": len(records), "unique_en_pages": len({record["wiki_title"] for record in records}),
        "unique_sk_pages": len({record["sk_title"] for record in records if record.get("sk_title")}),
        "documents_with_sk": sum(1 for record in records if record.get("sk_title")),
        "ambiguous_documents": sum(1 for record in records if record["match"]["accepted_candidates_count"] > 1),
        "min_match_score": MIN_MATCH_SCORE, "by_category": by_category,
        "document_ranks": dict(sorted(document_ranks.items())), "alias_ranks": dict(sorted(alias_ranks.items())),
        "attribute_coverage": attribute_coverage,
        "examples": [{"name": record["name"], "wiki_title": record["wiki_title"], "sk_title": record.get("sk_title"),
                      "key": record["match"]["key"], "score": record["match"]["score"]} for record in examples],
        "elapsed_seconds": round(time.time() - started, 1),
    }
    gold_pairs = load_gold_pairs(arguments.gold)
    gold_titles = spark.createDataFrame(sorted({(title,) for _, title in gold_pairs}) or [("",)], "name string")
    canonical_titles = {row["name"]: row["redirect"] for row in pages.where(F.col("redirect").isNotNull())
                        .select(F.col("title").alias("name"), "redirect").join(gold_titles, "name").collect()}
    report["gold"] = evaluate_gold(gold_pairs, records, canonical_titles)
    log("EVALUATED", "gold pairs", **{key: value for key, value in report["gold"].items() if key != "rows"})
    sample = random.Random(2026).sample(records, min(40, len(records)))
    report["sample"] = [{"name": record["name"], "wiki_title": record["wiki_title"],
                         "score": record["match"]["score"]} for record in sorted(sample, key=lambda r: r["name"])]
    save_report("wiki_join", report, build_tables(report))
    spark.stop()


if __name__ == "__main__":
    main()
