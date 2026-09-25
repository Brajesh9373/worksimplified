"""BA chatbot catalogue: structure, applicability and fulfilled-criteria."""

from __future__ import annotations

import sys
from pathlib import Path

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))

from ba_chat import catalogue as cat


def test_order_matches_items():
    assert set(cat.ORDER) == set(cat.ITEMS)
    assert len(cat.ORDER) == len(cat.ITEMS)


def test_groups_cover_a_to_g():
    assert [g["id"] for g in cat.GROUPS] == ["A", "B", "C", "D", "E", "F", "G"]


def test_every_item_has_a_question_and_a_known_check():
    for item_id, item in cat.ITEMS.items():
        assert item["ask"], item_id
        assert item["check"] in cat._CHECKS, item_id
        assert item["group"] in "ABCDEFG", item_id


def test_placeholders_never_fulfill():
    for junk in ("TBD", "not sure", "n/a", "...", ""):
        ok, _ = cat.check_item(cat.ITEMS["a1"], junk)
        assert not ok, junk


def test_named_person_rejects_roles_but_accepts_names():
    ok, _ = cat.check_item(cat.ITEMS["a5"], "The Manager")
    assert not ok
    ok, _ = cat.check_item(cat.ITEMS["a5"], "Priya Shah, COO")
    assert ok


def test_numbers_required_where_they_matter():
    ok, _ = cat.check_item(cat.ITEMS["a3"], "something reasonable")
    assert not ok
    ok, _ = cat.check_item(cat.ITEMS["a3"], "between 5 and 8 lakh rupees")
    assert ok
    ok, _ = cat.check_item(cat.ITEMS["a4"], "go-live by March, before the financial year ends")
    assert ok


def test_stories_need_as_a_form():
    ok, hint = cat.check_item(cat.ITEMS["c2"], "we need reports and speed")
    assert not ok and "As a" in hint
    ok, _ = cat.check_item(
        cat.ITEMS["c2"],
        "As a store manager, I want low-stock alerts, so that shelves never go empty.\n"
        "As an accountant, I want GST reports, so that filing takes minutes.")
    assert ok


def test_acceptance_must_be_testable():
    ok, _ = cat.check_item(cat.ITEMS["c3"], "it should work well and fast")
    assert not ok
    ok, _ = cat.check_item(
        cat.ITEMS["c3"],
        "The system must send the alert within 60 seconds. "
        "Given 100 orders, when the report runs, then it finishes under 5 seconds.")
    assert ok


def test_transition_items_apply_only_where_relevant():
    erp = [i for i in cat.ORDER if cat.item_applies(cat.ITEMS[i], "erp_frappe")]
    web = [i for i in cat.ORDER if cat.item_applies(cat.ITEMS[i], "website")]
    assert "f1" in erp and "f1" not in web
    assert "a1" in erp and "a1" in web


def test_pre_classification_only_viability_is_asked():
    asked = [i for i in cat.ORDER if cat.item_applies(cat.ITEMS[i], None)]
    assert asked == [i for i in cat.ORDER if cat.ITEMS[i]["group"] == "A"]


def test_erp_overrides_exist_where_wording_matters():
    assert "erp_frappe" in cat.ITEMS["c1"]["ask_overrides"]
    assert "erp_frappe" in cat.ITEMS["d7"]["ask_overrides"]
    assert cat.question_for(cat.ITEMS["c1"], "erp_frappe") != cat.ITEMS["c1"]["ask"]
    assert cat.question_for(cat.ITEMS["c1"], "website") == cat.ITEMS["c1"]["ask"]
