# ==============================================================================
# TESTS: JOIN KEYS AND MATCH — MilSearch
# ==============================================================================
# PURPOSE:
#     Test the name keys of both sides of the join and the score of a join candidate.
# ==============================================================================

from wiki.keys import build_document_keys, build_title_aliases, get_name_key, split_name
from wiki.match import (MIN_MATCH_SCORE, compute_match_score, get_page_kind, get_wiki_attributes,
                        get_wiki_years)


# ==============================================================================
# TESTS: KEYS
# ==============================================================================

def test_get_name_key_removes_case_diacritics_and_punctuation() -> None:
    assert get_name_key("Krag–Jørgensen") == "kragjorgensen"
    assert get_name_key("T-72") == get_name_key("T72") == "t72"
    assert get_name_key("Smith & Wesson M&P") == "smithandwessonmandp"
    assert get_name_key("Škoda LT vz. 38") == "skodaltvz38"


def test_split_name() -> None:
    assert split_name("Nanuchka (class) / (Project 1234 Ovod)") == ("Nanuchka", ["class", "Project 1234 Ovod"])
    assert split_name("Landing Vehicle Tracked (LVT-2 / LVT-4)") == ("Landing Vehicle Tracked", ["LVT-2", "LVT-4"])


def test_build_document_keys_ship_prefix_and_hull() -> None:
    keys = build_document_keys("USS Nimitz (CVN-68)", "ships")
    assert keys["ussnimitzcvn68"] == 0
    assert keys["ussnimitz"] == 1
    assert keys["nimitz"] == 1


def test_build_document_keys_class() -> None:
    keys = build_document_keys("Nanuchka (class) / (Project 1234 Ovod)", "ships")
    assert keys["nanuchkaclass"] == 0
    assert keys["project1234ovod"] == 2


def test_build_document_keys_manufacturer_suffix_and_prefix() -> None:
    keys = build_document_keys("Fabrique Nationale FN M2HB", "smallarms")
    assert keys["fnm2hb"] == 3
    assert keys["m2hb"] == 3
    prefix_keys = build_document_keys("Krag-Jorgensen Model 1894 (Series)", "smallarms")
    assert prefix_keys["kragjorgensen"] == 4
    assert "series" not in prefix_keys


def test_build_title_aliases() -> None:
    assert build_title_aliases("German battleship Bismarck", False) == {"germanbattleshipbismarck": 0, "bismarck": 1}
    assert build_title_aliases("Comet (tank)", False) == {"comettank": 0, "comet": 1}
    assert build_title_aliases("Nanuchka-class corvette", False) == {
        "nanuchkaclasscorvette": 0, "nanuchkaclass": 1}
    assert build_title_aliases("T-60 tank", True) == {"t60tank": 0, "t60": 1}


# ==============================================================================
# TESTS: MATCH
# ==============================================================================

def test_get_wiki_attributes_maps_parameters() -> None:
    attributes = get_wiki_attributes({"ship_builder": "Newport News", "ship_laid_down": "1968-06-22",
                                      "wars": "See Conflicts", "number": "~75 million", "designer": "Kalashnikov"})
    assert attributes == {"designer": "Kalashnikov", "manufacturer": "Newport News", "number_built": "~75 million",
                          "laid_down": "1968-06-22"}


def test_get_wiki_years_only_date_parameters() -> None:
    assert get_wiki_years({"design_date": "1947", "service": "1949–1974", "weight": "1960 kg"}) == [1947, 1949, 1974]


def test_get_page_kind() -> None:
    assert get_page_kind(["ship"], []) == "ship"
    assert get_page_kind(["weapon"], []) == "weapon"
    assert get_page_kind([], ["Main battle tanks of Germany"]) == "heavy"
    assert get_page_kind(["weapon"], [], "Semi-automatic rifle") == "small_arm"
    assert get_page_kind(["person"], ["1947 births"]) == "other"
    assert get_page_kind([], ["1947 births"]) is None


def test_compute_match_score_accepts_good_and_rejects_wrong_kind() -> None:
    good = compute_match_score(1, 1, "ships", 1940, "Germany", "ship", [1936, 1939, 1941],
                               "German battleship Bismarck | Kriegsmarine")
    assert good == 10 - 4 + 3 + 3 + 2
    wrong_kind = compute_match_score(0, 1, "armor", 1979, "Germany", "ship", [1912], "SMS Leopard")
    assert wrong_kind < MIN_MATCH_SCORE
    weak_prefix = compute_match_score(4, 1, "smallarms", 1894, "Norway", None, [], "")
    assert weak_prefix < MIN_MATCH_SCORE


def test_build_document_keys_skips_type_word_prefixes() -> None:
    keys = build_document_keys("Cruiser Tank Mk VI Crusader (A15)", "armor")
    assert keys["crusader"] == 3
    assert "cruisertank" not in keys
    assert "cruiser" not in keys


def test_build_document_keys_skips_hull_number_alone() -> None:
    keys = build_document_keys("Juan Carlos I (L61)", "ships")
    assert "l61" not in keys
    assert keys["juancarlosi"] == 1


def test_compute_match_score_penalties() -> None:
    other_hull = compute_match_score(1, 0, "ships", 2018, "United States", "ship", [2016], "United States Navy",
                                     "USS Montgomery (LCS-8)", "Littoral Combat Ship", "USS Montgomery (DD-121)")
    assert other_hull < MIN_MATCH_SCORE
    other_type = compute_match_score(1, 1, "ships", 2009, "Japan", "ship", [1937], "Japanese",
                                     "JS Soryu (SS-501)", "Diesel-Electric Attack Submarine",
                                     "Japanese aircraft carrier Sōryū")
    assert other_type < MIN_MATCH_SCORE
    index_page = compute_match_score(1, 0, "ships", 1916, "United States", "index", [], "", "USS Arizona (BB-39)",
                                     "Battleship", "USS Arizona")
    assert index_page < MIN_MATCH_SCORE
    same_hull = compute_match_score(0, 0, "ships", 1916, "United States", "ship", [1915, 1916], "United States",
                                    "USS Arizona (BB-39)", "Battleship", "USS Arizona (BB-39)")
    assert same_hull == 18


def test_get_page_kind_index() -> None:
    assert get_page_kind([], ["Set index articles on ships"]) == "index"


def test_build_document_keys_suffix_needs_a_name_word() -> None:
    krag = build_document_keys("Krag-Jorgensen Model 1894 (Series)", "smallarms")
    assert "model1894" not in krag
    panzer = build_document_keys("SdKfz 161 Panzer IV", "armor")
    assert panzer["panzeriv"] == 3
    namer = build_document_keys("Merkava Namer (Leopard)", "armor")
    assert namer["namer"] == 4


def test_compute_match_score_rejects_small_arm_for_artillery() -> None:
    score = compute_match_score(1, 1, "armor", 1939, "United States", "small_arm", [1941, 1949], "Soviet Union",
                                "M59 (M2 Long Tom)", "155mm Towed Field Gun", "SKS")
    assert score < MIN_MATCH_SCORE


def test_get_page_kind_type_and_other_articles() -> None:
    assert get_page_kind([], ["Medium tanks", "Tanks by type"], "", "Medium tank") == "type"
    assert get_page_kind([], ["Grenades", "Hand grenades"], "", "Grenade") == "type"
    assert get_page_kind(["company"], ["Firearm manufacturers of Germany"], "", "Mauser") == "other"
    assert get_page_kind([], ["World War II tanks of France"], "", "Renault R40") == "heavy"


def test_compute_match_score_type_article_only_for_exact_name() -> None:
    generic = compute_match_score(2, 0, "armor", 2019, "Turkey", "type", [], "Medium tank",
                                  "FNSS KAPLAN MT (Medium Tank)", "Medium Tank", "Medium tank")
    assert generic < MIN_MATCH_SCORE
    exact = compute_match_score(0, 0, "ships", 1450, "Portugal", "type", [], "Carrack", "Carrack", "Ship", "Carrack")
    assert exact >= MIN_MATCH_SCORE


def test_compute_match_score_weapon_type_mismatch() -> None:
    mortar_vs_carbine = compute_match_score(3, 0, "smallarms", 1975, "South Africa", "small_arm", [1994], "",
                                            "Denel M4 Commando", "60mm Light Mortar", "M4 carbine", "Carbine")
    assert mortar_vs_carbine < MIN_MATCH_SCORE
    same_type = compute_match_score(0, 0, "smallarms", 1949, "Soviet Union", "small_arm", [1947, 1949], "Soviet Union",
                                    "Kalashnikov AK-47", "Assault Rifle", "AK-47", "Assault rifle")
    assert same_type == 18
