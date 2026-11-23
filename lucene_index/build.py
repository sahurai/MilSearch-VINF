# ==============================================================================
# LUCENE INDEX BUILDER — MilSearch
# ==============================================================================
# PURPOSE:
#     Build the Lucene index from the same documents as the own index: the extracted
#     documents, the anchor texts and the Wikipedia data of the Spark join.
#
# RESPONSIBILITIES:
#     1. Load the documents (indexer.build.load_index_documents).
#     2. Change each document into a Lucene document with the fields of lucene_index.schema.
#     3. Write the index with IndexWriter; merge it into one segment.
#     4. Write the metadata: counts, terms per field, size, build time.
#
# DEPENDENCIES:
#     PyLucene — runs in Docker (python -m tools.lucene build).
#     indexer.build — documents of the index.
#     lucene_index.schema, lucene_index.analysis — fields, analyzers, similarity.
#
# USAGE:
#     python -m tools.lucene build              # with the Wikipedia data (data/wiki/joined.jsonl)
#     python -m tools.lucene build --no-wiki    # MilitaryFactory data only
#
# INVARIANTS:
#     The index directory is created again on each build (OpenMode.CREATE).
#     The index files are Lucene files on the disk. The index does not use a database.
# ==============================================================================

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

import lucene
from java.nio.file import Paths
from org.apache.lucene.document import (
    DoubleDocValuesField,
    DoublePoint,
    Field,
    IntPoint,
    NumericDocValuesField,
    StoredField,
    StringField,
    TextField,
)
from org.apache.lucene.document import Document as LuceneDocument
from org.apache.lucene.index import DirectoryReader, IndexWriter, IndexWriterConfig, MultiTerms
from org.apache.lucene.store import FSDirectory

from crawler.config import DATA_DIR
from indexer.build import load_index_documents
from lucene_index.analysis import SIMILARITY_BM25, build_analyzer, get_similarity, init_vm
from lucene_index.schema import FIELDS, KIND_FLOAT, KIND_INT, KIND_KEYWORD, KIND_STORED, KIND_TEXT, get_field_values

# ==============================================================================
# CONSTANTS
# ==============================================================================

logger = logging.getLogger("lucene_index.build")

LUCENE_DIR: Path = DATA_DIR / "lucene"
LUCENE_INDEX_DIR: Path = LUCENE_DIR / "index"
LUCENE_META_PATH: Path = LUCENE_DIR / "meta.json"

_RAM_BUFFER_MB = 256.0


# ==============================================================================
# HELPERS
# ==============================================================================

def _add_field(lucene_document: LuceneDocument, name: str, kind: str, value: Any) -> None:  # Any: str, int, float.
    """
    Add one value of one field to a Lucene document.

    Steps:
        1. Keyword: StringField, stored (exact token).
        2. Text: TextField, not stored (the display JSON holds the shown values).
        3. Int / float: point for range queries, doc values for sorting, stored value.
        4. Stored: StoredField only.

    Returns:
        None.
    """
    # 1. Keyword: StringField, stored (exact token)
    if kind == KIND_KEYWORD:
        lucene_document.add(StringField(name, value, Field.Store.YES))

    # 2. Text: TextField, not stored (the display JSON holds the shown values)
    elif kind == KIND_TEXT:
        lucene_document.add(TextField(name, value, Field.Store.NO))

    # 3. Int / float: point for range queries, doc values for sorting, stored value
    elif kind == KIND_INT:
        lucene_document.add(IntPoint(name, [int(value)]))
        lucene_document.add(NumericDocValuesField(name, int(value)))
        lucene_document.add(StoredField(name, int(value)))
    elif kind == KIND_FLOAT:
        lucene_document.add(DoublePoint(name, [float(value)]))
        lucene_document.add(DoubleDocValuesField(name, float(value)))
        lucene_document.add(StoredField(name, float(value)))

    # 4. Stored: StoredField only
    elif kind == KIND_STORED:
        lucene_document.add(StoredField(name, value))


def make_lucene_document(document: dict[str, Any], anchor_texts: list[str]) -> LuceneDocument:  # Any: JSON values.
    """
    Change one MilSearch document into a Lucene document.

    Steps:
        1. Get the field values from the schema.
        2. Add each value with the kind of its field.

    Returns:
        Lucene document.
    """
    # 1. Get the field values from the schema
    values = get_field_values(document, anchor_texts)

    # 2. Add each value with the kind of its field
    lucene_document = LuceneDocument()
    for spec in FIELDS:
        for value in values.get(spec.name, []):
            _add_field(lucene_document, spec.name, spec.kind, value)
    return lucene_document


def _get_directory_bytes(path: Path) -> int:
    """
    Sum the sizes of the files in a directory.

    Steps:
        1. Add the size of each file.

    Returns:
        Size in bytes.
    """
    # 1. Add the size of each file
    return sum(item.stat().st_size for item in path.iterdir() if item.is_file())


def _count_terms(index_dir: Path) -> dict[str, int]:
    """
    Count the different terms of each text and keyword field.

    Steps:
        1. Open the index and read the term count of each field (one segment: exact counts).

    Returns:
        Map from field name to its term count.
    """
    # 1. Open the index and read the term count of each field (one segment: exact counts)
    counts: dict[str, int] = {}
    reader = DirectoryReader.open(FSDirectory.open(Paths.get(str(index_dir))))
    try:
        for spec in FIELDS:
            if spec.kind in (KIND_TEXT, KIND_KEYWORD):
                terms = MultiTerms.getTerms(reader, spec.name)
                counts[spec.name] = int(terms.size()) if terms is not None else 0
    finally:
        reader.close()
    return counts


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def build_lucene_index(documents: list[dict[str, Any]], anchor_texts_by_url: dict[str, list[str]],
                       index_dir: Path) -> dict[str, Any]:  # Any: JSON values.
    """
    Write the Lucene index of the documents.

    Steps:
        1. Open an IndexWriter with the index analyzer and the BM25 similarity (norms).
        2. Add one Lucene document for each document.
        3. Merge into one segment and close the writer.
        4. Count the terms of each field and the index size.

    Returns:
        Statistics of the index.
    """
    init_vm()
    index_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.monotonic()

    # 1. Open an IndexWriter with the index analyzer and the BM25 similarity (norms)
    config = IndexWriterConfig(build_analyzer(for_index=True))
    config.setOpenMode(IndexWriterConfig.OpenMode.CREATE)
    config.setSimilarity(get_similarity(SIMILARITY_BM25))
    config.setRAMBufferSizeMB(_RAM_BUFFER_MB)
    writer = IndexWriter(FSDirectory.open(Paths.get(str(index_dir))), config)

    # 2. Add one Lucene document for each document
    try:
        for document in documents:
            writer.addDocument(make_lucene_document(document, anchor_texts_by_url.get(document.get("url") or "", [])))
        add_seconds = time.monotonic() - started_at

        # 3. Merge into one segment and close the writer
        writer.forceMerge(1)
        writer.commit()
    finally:
        writer.close()

    # 4. Count the terms of each field and the index size
    terms_by_field = _count_terms(index_dir)
    return {
        "documents_count": len(documents),
        "terms_by_field": terms_by_field,
        "terms_count": sum(terms_by_field.values()),
        "index_bytes": _get_directory_bytes(index_dir),
        "add_seconds": round(add_seconds, 2),
        "build_seconds": round(time.monotonic() - started_at, 2),
    }


# ==============================================================================
# ENTRY POINT
# ==============================================================================

def main() -> None:
    """
    Build the Lucene index in data/lucene/index and write data/lucene/meta.json.

    Steps:
        1. Read the options; load the documents (with the Wikipedia data unless --no-wiki).
        2. Build the index.
        3. Write the metadata.

    Returns:
        None.
    """
    # 1. Read the options; load the documents (with the Wikipedia data unless --no-wiki)
    parser = argparse.ArgumentParser(description="Build the MilSearch Lucene index.")
    parser.add_argument("--no-wiki", action="store_true", help="Do not add the Wikipedia data of the join.")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    documents, anchor_texts_by_url, wiki_documents_count = load_index_documents(not arguments.no_wiki)
    logger.info("STARTED lucene index | documents_count=%d wiki_documents_count=%d", len(documents), wiki_documents_count)

    # 2. Build the index
    stats = build_lucene_index(documents, anchor_texts_by_url, LUCENE_INDEX_DIR)

    # 3. Write the metadata
    meta = {**stats, "wiki_documents_count": wiki_documents_count, "lucene_version": lucene.VERSION,
            "fields": [{"name": spec.name, "kind": spec.kind, "boost": spec.boost, "purpose": spec.purpose}
                       for spec in FIELDS],
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    LUCENE_META_PATH.write_bytes(json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8"))
    logger.info("SAVED lucene index | documents_count=%d terms_count=%d index_mb=%.1f build_seconds=%.1f",
                stats["documents_count"], stats["terms_count"], stats["index_bytes"] / 1e6, stats["build_seconds"])


if __name__ == "__main__":
    main()
