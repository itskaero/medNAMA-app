"""What a revision note is planned from: which books (subject first) and which sections (the AI outline, checked)."""

from types import SimpleNamespace

from labs.seed_studio import generate as G
from app import revision


def leaf(name, detail=None):
    return SimpleNamespace(id=1, name=name, detail=detail)


def test_subject_family_basic_sciences_by_subject():
    assert G.subject_family({"subject": "Physiology"}) == "Physiology"
    assert G.subject_family({"subject": "Anatomy / Histology / Embryology"}) == "Anatomy"
    assert G.subject_family({"subject": "CHEMICAL PATHOLOGY"}) == "Biochemistry"
    assert G.subject_family({"subject": "GENERAL PATHOLOGY"}) == "Pathology"
    # PGET Surgery's anatomy goes to the anatomy books, not the surgery book
    assert G.subject_family({"specialty": "Surgery & Allied (Group-1: General Surgery, Urology)",
                             "subject": "Anatomy, Radiological and Applied Anatomy"}) == "Anatomy"


def test_subject_family_clinical_and_none():
    assert G.subject_family({"specialty": "Medicine & Allied", "subject": "Hematologic Disorders"}) == "Medicine"
    assert G.subject_family({"specialty": "Medicine & Allied", "subject": "Cardiovascular Disorders"}) == "Medicine"
    assert G.subject_family({"specialty": "Paediatric Medicine", "subject": "Infectious Diseases"}) == "Paediatrics"
    assert G.subject_family({"specialty": "Otorhinolaryngology (ENT)", "subject": "Ear"}) == "ENT"
    assert G.subject_family({"subject": "Behavioural Science & Medical Ethics - General Principles"}) is None


def test_outline_keeps_every_listed_item():
    data = {"items": ["Distribution within body compartments"], "intent": "mechanisms and values",
            "sections": [{"heading": "Distribution within body compartments", "focus": "ICF/ECF volumes"},
                         {"heading": "Starling forces", "focus": "equation", "item": False}]}
    out = G.validate_outline(data, leaf("Fluid Distribution and Edema",
                                        "Distribution within body compartments, Forces of filtration and absorption, "
                                        "Edema (types, stages)"))
    heads = [s["heading"] for s in out["sections"]]
    assert "Forces of filtration and absorption" in heads and "Edema (types, stages)" in heads
    assert [s["item"] for s in out["sections"]] == [True, False, True, True]
    assert out["by"] == "ai" and out["intent"] == "mechanisms and values"


def test_outline_caps_added_headings_never_items():
    items = [f"Group {i}" for i in range(6)]
    data = {"items": items, "sections": [{"heading": i, "focus": ""} for i in items]
            + [{"heading": f"Extra {i}", "focus": ""} for i in range(5)]}
    out = G.validate_outline(data, leaf("Drugs"))
    assert sum(s["item"] for s in out["sections"]) == 6
    assert len(out["sections"]) == 8


def test_outline_bad_json_still_yields_the_tos_list():
    out = G.validate_outline("not a dict", leaf("X", "a, b (c, d)"))
    assert [s["heading"] for s in out["sections"]] == ["a", "b (c, d)"]


def test_revision_sections_accept_outline_dicts_and_strings():
    secs = revision._sections({"sections": ["Item one", {"heading": "Item two", "focus": "why", "item": False}]})
    assert secs == [{"heading": "Item one", "focus": "", "item": True},
                    {"heading": "Item two", "focus": "why", "item": False}]
    prompt = revision._outline_prompt(secs)
    assert "## Item one" in prompt and "## Item two (optional)" in prompt and "cover: why" in prompt
    # a plain-string sheet keeps the cache key it had before outlines existed
    base = {"book_ids": [1], "topic": "t"}
    assert revision.scope_key({**base, "sections": ["A"]}) != revision.scope_key(base)
    assert revision.scope_key({**base, "sections": ["A"]}) == revision.scope_key({**base, "sections": [{"heading": "A"}]})
