# ==============================================================================
# TESTS: WIKITEXT — MilSearch
# ==============================================================================
# PURPOSE:
#     Test the regex functions for the Wikipedia dump: record fields, military filter,
#     templates, infobox, categories, abstract, links and interlanguage links.
# ==============================================================================

from wiki.wikitext import (get_abstract, get_categories, get_infobox_fields, get_langlinks, get_links,
                           get_page_record, is_military_page, parse_page, resolve_templates)

# ==============================================================================
# CONSTANTS
# ==============================================================================

RECORD = """  <page>
    <title>AT&amp;T M1 &quot;test&quot;</title>
    <ns>0</ns>
    <id>1348</id>
    <revision>
      <id>1381316340</id>
      <text bytes="10" xml:space="preserve">'''AK-47''' &lt;ref&gt;x&lt;/ref&gt; text</text>
    </revision>
"""

REDIRECT_RECORD = """  <page>
    <title>AK47</title>
    <ns>0</ns>
    <id>1151</id>
    <redirect title="AK-47" />
    <revision><id>1</id><text bytes="19" xml:space="preserve">#REDIRECT [[AK-47]]</text></revision>
"""

WEAPON_PAGE = """{{Short description|Soviet assault rifle}}
{{Infobox weapon
| name               = AK-47
| origin             = [[Soviet Union]]
| type               = [[Assault rifle]]
| designer           = [[Mikhail Kalashnikov]]
| design_date        = {{Start date and age|1947}}<ref>{{cite web|url=x|title=y}}</ref>
| manufacturer       = {{ubl|[[Kalashnikov Concern]]|[[Norinco]]}}
| weight             = {{convert|3.47|kg|lb|abbr=on}}<br />{{convert|0.43|kg|lb}} (magazine)
| wars               = See ''[[#Conflicts|Conflicts]]''
}}
The '''AK-47''' is a [[gas-operated]] [[assault rifle|rifle]] designed by [[Mikhail Kalashnikov]].<ref name="a"/>

It is widely used.

== History ==
Design work began in 1945.
[[Category:Assault rifles of the Soviet Union]]
[[Category:7.62×39mm assault rifles|AK]]
"""

SHIP_PAGE = """{{Infobox ship
|section1={{Infobox ship/image |image=x.jpg }}
|section2={{Infobox ship/career
 |ship_builder=[[Newport News Shipbuilding]]
 |ship_laid_down={{start date|1968|6|22}}
 |ship_commissioned={{start date|1975|5|3}}
 }}
}}
'''USS ''Nimitz''''' is an aircraft carrier of the {{sclass|Nimitz|aircraft carrier|0}}.
"""


# ==============================================================================
# TESTS
# ==============================================================================

def test_get_page_record_decodes_entities_and_fields() -> None:
    record = get_page_record(RECORD)
    assert record == {"id": 1348, "title": 'AT&T M1 "test"', "ns": 0, "redirect": None,
                      "text": "'''AK-47''' <ref>x</ref> text"}


def test_get_page_record_redirect() -> None:
    record = get_page_record(REDIRECT_RECORD)
    assert record["redirect"] == "AK-47"
    assert record["id"] == 1151


def test_get_page_record_without_title() -> None:
    assert get_page_record("<mediawiki><siteinfo></siteinfo>") is None


def test_is_military_page_by_infobox_and_category() -> None:
    assert is_military_page("{{Infobox weapon\n| name = X\n}}")
    assert is_military_page("{{Infobox ship begin}}")
    assert is_military_page("text [[Category:Main battle tanks of Germany]]")
    assert not is_military_page("{{Infobox person\n| name = X}} [[Category:1947 births]]")


def test_resolve_templates_innermost_first() -> None:
    text, infoboxes = resolve_templates("A {{nowrap|{{convert|5|to|10|km}}}} B {{cite web|url=x}}C")
    assert text == "A 5–10 km B C"
    assert infoboxes == []


def test_parse_page_weapon_infobox() -> None:
    page = parse_page(WEAPON_PAGE)
    assert page["infobox_kinds"] == ["weapon"]
    fields = page["infobox"]
    assert fields["origin"] == "Soviet Union"
    assert fields["designer"] == "Mikhail Kalashnikov"
    assert fields["design_date"] == "1947"
    assert fields["manufacturer"] == "Kalashnikov Concern; Norinco"
    assert fields["weight"] == "3.47 kg; 0.43 kg (magazine)"


def test_parse_page_abstract_and_categories() -> None:
    page = parse_page(WEAPON_PAGE)
    assert page["abstract"] == ("The AK-47 is a gas-operated rifle designed by Mikhail Kalashnikov. "
                                "It is widely used.")
    assert page["categories"] == ["Assault rifles of the Soviet Union", "7.62×39mm assault rifles"]
    assert "Design work began in 1945." in page["text"]


def test_parse_page_nested_ship_infoboxes() -> None:
    page = parse_page(SHIP_PAGE)
    assert page["infobox_kinds"] == ["ship"]
    assert page["infobox"]["ship_builder"] == "Newport News Shipbuilding"
    assert page["infobox"]["ship_laid_down"] == "1968-06-22"
    assert page["abstract"] == "USS Nimitz is an aircraft carrier of the Nimitz-class aircraft carrier."


def test_get_infobox_fields_first_value_wins() -> None:
    kinds, fields = get_infobox_fields([("ship", {"name": "A"}), ("ship", {"name": "B", "beam": "30 m"})])
    assert kinds == ["ship"]
    assert fields == {"name": "A", "beam": "30 m"}


def test_get_abstract_cuts_at_sentence() -> None:
    assert get_abstract("One. Two three. Four", max_length=12) == "One."


def test_get_categories_without_duplicates() -> None:
    assert get_categories("[[Category:A]] [[category: B |x]] [[Category:A]]") == ["A", "B"]


def test_get_links_normalizes_targets() -> None:
    text = "[[ak-47|Kalashnikov]] [[T-72#Variants]] [[File:x.jpg|[[y]]]] [[Category:Z]] [[de:Panzer]]"
    assert get_links(text) == [("Ak-47", "Kalashnikov"), ("T-72", "T-72#Variants")]


def test_get_langlinks_from_sql() -> None:
    line = ("INSERT INTO `langlinks` VALUES (12,'en','Leopard 2'),(12,'de','Leopard 2'),"
            "(15,'en','Tiger I'),(16,'en','Hunter\\'s rifle');")
    assert get_langlinks(line, "en") == [(12, "Leopard 2"), (15, "Tiger I"), (16, "Hunter's rifle")]


def test_parse_page_marks_index_pages() -> None:
    page = parse_page("'''USS Arizona''' may refer to:\n* USS Arizona (BB-39)\n{{shipindex}}")
    assert page["infobox_kinds"] == ["index"]


def test_parse_page_removes_list_bullets_in_values() -> None:
    page = parse_page("{{Infobox weapon\n| manufacturer = {{plainlist|\n* KraussMaffei\n* Rheinmetall\n}}\n}}")
    assert page["infobox"]["manufacturer"] == "KraussMaffei; Rheinmetall"


def test_parse_page_ubli_list_template() -> None:
    page = parse_page("{{Infobox weapon\n| type = {{ubli|[[Carbine]]|[[Assault rifle]]}}\n}}")
    assert page["infobox"]["type"] == "Carbine; Assault rifle"
