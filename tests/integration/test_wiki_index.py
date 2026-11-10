# ==============================================================================
# WIKI INDEX TESTS — MilSearch
# ==============================================================================
# PURPOSE:
#     Test the Wikipedia fields of the index (indexer.build --wiki) on a small joined file.
#
# RESPONSIBILITIES:
#     1. Make sure that the joined file gives the names, the abstract and the attribute fields.
#     2. Make sure that a query with a Wikipedia name and a wiki filter finds the document.
# ==============================================================================

from __future__ import annotations

import json
from pathlib import Path

from indexer.build import _load_wiki_fields, build_index, save_index
from indexer.search import SearchEngine

# ==============================================================================
# CONSTANTS
# ==============================================================================

_JOINED = {
    "id": "armor-369", "name": "BM-8 / BM-13 / BM-31", "category": "armor",
    "wiki_title": "Katyusha rocket launcher", "wiki_url": "https://en.wikipedia.org/wiki/Katyusha_rocket_launcher",
    "wiki_redirects": ["BM-13", "Stalin's organ"], "wiki_anchors": ["Katyusha", "Katyushas"],
    "wiki_abstract": "The Katyusha is a type of rocket artillery.", "wiki_inlinks_count": 120,
    "wiki_attributes": {"wars": "World War II; Korean War", "designer": "Georgy Langemak", "origin": "Soviet Union"},
    "sk_title": "Kaťuša (raketomet)", "sk_abstract": "Kaťuša je raketomet.",
}
_DOCUMENTS = [
    {"id": "armor-369", "category": "armor", "name": "BM-8 / BM-13 / BM-31", "type": "Rocket Launcher",
     "country": "Soviet Union", "year": 1939, "text": "A multiple rocket launcher on a truck."},
    {"id": "armor-1", "category": "armor", "name": "M1 Abrams", "type": "Main Battle Tank (MBT)",
     "country": "United States", "year": 1980, "text": "The M1 Abrams is a main battle tank."},
]


# ==============================================================================
# TESTS
# ==============================================================================

def test_load_wiki_fields(tmp_path: Path) -> None:
    path = tmp_path / "joined.jsonl"
    path.write_text(json.dumps(_JOINED, ensure_ascii=False) + "\n", encoding="utf-8")
    fields = _load_wiki_fields(path)["armor-369"]
    assert fields["wiki_names"] == ["Katyusha rocket launcher", "BM-13", "Stalin's organ", "Katyusha", "Katyushas",
                                    "Kaťuša (raketomet)"]
    assert fields["wiki_abstract"] == "The Katyusha is a type of rocket artillery.\nKaťuša je raketomet."
    assert fields["wiki_wars"] == "World War II; Korean War"
    assert fields["wiki_designer"] == "Georgy Langemak"
    assert "wiki_origin" not in fields


def test_load_wiki_fields_without_file(tmp_path: Path) -> None:
    assert _load_wiki_fields(tmp_path / "missing.jsonl") == {}


def test_search_finds_document_by_wikipedia_name(tmp_path: Path) -> None:
    path = tmp_path / "joined.jsonl"
    path.write_text(json.dumps(_JOINED, ensure_ascii=False) + "\n", encoding="utf-8")
    documents = [dict(document) for document in _DOCUMENTS]
    wiki_fields = _load_wiki_fields(path)
    for document in documents:
        document.update(wiki_fields.get(document["id"], {}))
    postings, rows = build_index(documents, {})
    save_index(postings, rows, {"documents_count": len(rows), "avg_length": sum(r["length"] for r in rows) / len(rows)},
               tmp_path)
    engine = SearchEngine(tmp_path)
    assert engine.search("katyusha")[0].row["id"] == "armor-369"
    assert engine.search("stalin organ")[0].row["id"] == "armor-369"
    assert [hit.row["id"] for hit in engine.search("wiki_wars:korean")] == ["armor-369"]
