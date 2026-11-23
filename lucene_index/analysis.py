# ==============================================================================
# LUCENE ANALYSIS — MilSearch
# ==============================================================================
# PURPOSE:
#     Start the JVM of PyLucene and make the analyzers and the similarity that the
#     Lucene index uses. The text analyzer copies the behavior of the own tokenizer
#     (indexer/tokenizer.py) with standard Lucene components.
#
# RESPONSIBILITIES:
#     1. Start the JVM once; attach the current thread.
#     2. Make the text analyzer (index and query version) and the keyword analyzer.
#     3. Make the similarity: BM25 (k1 = 1.2, b = 0.75, as in the own index) or classic tf-idf.
#
# DEPENDENCIES:
#     PyLucene (lucene) — runs in Docker, image coady/pylucene:10 (Lucene 10.0.0).
#     lucene_index.schema — keyword fields.
#
# TEXT ANALYZER (own tokenizer -> Lucene component):
#     regex tokens [a-z0-9]+([.-/][a-z0-9]+)*  -> PatternTokenizer with the same pattern (Unicode letters)
#     compound forms ak-47, ak47, ak, 47        -> WordDelimiterGraphFilter (preserveOriginal, catenateAll)
#     lowercase, no diacritics (Škoda -> skoda) -> LowerCaseFilter, ASCIIFoldingFilter
#     caliber 7.62x39mm -> also 7.62x39         -> PatternCaptureGroupFilter
#     stop words (same list)                    -> StopFilter (resources/stopwords.txt)
#     S-stemmer (Harman)                        -> EnglishMinimalStemFilter (the same S-stemmer)
#     index time only                           -> FlattenGraphFilter (the index cannot store a token graph)
# ==============================================================================

from __future__ import annotations

from pathlib import Path

import lucene
from java.nio.file import Paths
from java.util import HashMap
from org.apache.lucene.analysis import Analyzer
from org.apache.lucene.analysis.custom import CustomAnalyzer
from org.apache.lucene.analysis.miscellaneous import PerFieldAnalyzerWrapper
from org.apache.lucene.search.similarities import BM25Similarity, ClassicSimilarity, Similarity

from lucene_index.schema import KEYWORD_FIELDS

# ==============================================================================
# CONSTANTS
# ==============================================================================

RESOURCES_DIR: Path = Path(__file__).resolve().parent / "resources"

SIMILARITY_BM25 = "bm25"
SIMILARITY_CLASSIC = "classic"
SIMILARITIES: tuple[str, ...] = (SIMILARITY_BM25, SIMILARITY_CLASSIC)
BM25_K1 = 1.2    # The same values as the own index (indexer/ranking.py).
BM25_B = 0.75

# The token pattern of indexer/tokenizer.py, with Unicode letters and digits instead of [a-z0-9]
# (the own tokenizer removes the diacritics before it finds the tokens).
TOKEN_PATTERN = r"[\p{L}\p{N}]+(?:[.\-/][\p{L}\p{N}]+)*"
CALIBER_PATTERN = r"^(\d+(?:\.\d+)?x\d+)mm$"

_WORD_DELIMITER_PARAMETERS: tuple[str, ...] = (
    "preserveOriginal", "1",      # Keep "ak-47".
    "catenateAll", "1",           # Add "ak47".
    "generateWordParts", "1",     # Add "ak".
    "generateNumberParts", "1",   # Add "47".
    "splitOnCaseChange", "0",     # "SEPv3" stays one token.
    "splitOnNumerics", "0",       # "m1a2" stays one token.
    "types", "wdg_types.txt",     # "." is a part of a number (7.62x39mm).
)


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def init_vm() -> None:
    """
    Start the JVM of PyLucene once and attach the current thread.

    Steps:
        1. Start the JVM when it does not run; otherwise attach the current thread.

    Returns:
        None.
    """
    # 1. Start the JVM when it does not run; otherwise attach the current thread
    environment = lucene.getVMEnv()
    if environment is None:
        lucene.initVM(vmargs=["-Djava.awt.headless=true"])
    else:
        environment.attachCurrentThread()


def build_text_analyzer(for_index: bool) -> Analyzer:
    """
    Make the analyzer of the text fields.

    Steps:
        1. Tokenize with the token pattern of the own tokenizer.
        2. Add the compound forms, lowercase, remove diacritics, add the caliber form.
        3. Remove stop words and apply the S-stemmer.
        4. At index time, flatten the token graph.

    Returns:
        Analyzer.
    """
    # 1. Tokenize with the token pattern of the own tokenizer
    builder = CustomAnalyzer.builder(Paths.get(str(RESOURCES_DIR))) \
        .withTokenizer("pattern", ["pattern", TOKEN_PATTERN, "group", "0"])

    # 2. Add the compound forms, lowercase, remove diacritics, add the caliber form
    builder = builder.addTokenFilter("wordDelimiterGraph", list(_WORD_DELIMITER_PARAMETERS)) \
        .addTokenFilter("lowercase", []) \
        .addTokenFilter("asciiFolding", []) \
        .addTokenFilter("patternCaptureGroup", ["pattern", CALIBER_PATTERN, "preserve_original", "true"])

    # 3. Remove stop words and apply the S-stemmer
    builder = builder.addTokenFilter("stop", ["words", "stopwords.txt", "ignoreCase", "true"]) \
        .addTokenFilter("englishMinimalStem", [])

    # 4. At index time, flatten the token graph
    if for_index:
        builder = builder.addTokenFilter("flattenGraph", [])
    return builder.build()


def build_analyzer(for_index: bool) -> Analyzer:
    """
    Make the analyzer of the whole document: text analyzer + keyword analyzer for the keyword fields.

    Steps:
        1. Make the keyword analyzer: the whole value is one lowercase token.
        2. Use it for the keyword fields and the text analyzer for all other fields.

    Returns:
        PerFieldAnalyzerWrapper.
    """
    # 1. Make the keyword analyzer: the whole value is one lowercase token
    keyword_analyzer = CustomAnalyzer.builder().withTokenizer("keyword", []).addTokenFilter("lowercase", []).build()

    # 2. Use it for the keyword fields and the text analyzer for all other fields
    analyzers = HashMap()
    for name in KEYWORD_FIELDS:
        analyzers.put(name, keyword_analyzer)
    return PerFieldAnalyzerWrapper(build_text_analyzer(for_index), analyzers)


def get_similarity(name: str) -> Similarity:
    """
    Make the similarity (score function) of the index.

    Steps:
        1. Return BM25 with the parameters of the own index, or the classic tf-idf similarity.

    Returns:
        Similarity.

    Raises:
        ValueError for an unknown name.
    """
    # 1. Return BM25 with the parameters of the own index, or the classic tf-idf similarity
    if name == SIMILARITY_BM25:
        return BM25Similarity(BM25_K1, BM25_B)
    if name == SIMILARITY_CLASSIC:
        return ClassicSimilarity()
    raise ValueError(f"unknown similarity: {name}")
