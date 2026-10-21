"""Status and progress rules: never 'completed' by stopping, failures and shortfalls explained."""

from types import SimpleNamespace

from labs.seed_studio import progress as P
from labs.seed_studio import tos_parse as TP


def counts(**kw):
    base = {"generated": 0, "validated": 0, "inserted": 0, "rejected": 0, "duplicate": 0, "removed": 0, "flagged": 0,
            "approved": 0, "valid_by_difficulty": {d: 0 for d in range(1, 6)}, "batches": {}, "stages": {},
            "failed_attempts": 0, "last_error": None}
    base.update(kw)
    return base


def test_completed_only_when_inserted_reaches_target():
    assert P.unit_status(10, counts(generated=12, validated=10, inserted=10))[0] == "Completed"
    status, why = P.unit_status(10, counts(generated=12, validated=8, inserted=8, rejected=3, duplicate=1))
    assert status == "Partially completed"
    assert "short by 2" in why and "3 rejected" in why and "1 rejected as duplicates" in why


def test_stopped_job_with_nothing_saved_is_failed_not_complete():
    status, why = P.unit_status(10, counts(batches={"mcq:failed": 2}, last_error="Insufficient relevant evidence"))
    assert status == "Failed" and "Insufficient relevant evidence" in why


def test_live_states_and_not_started():
    assert P.unit_status(10, counts(batches={"mcq:running": 1}, stages={"validating": 1}))[0] == "Validating"
    assert P.unit_status(10, counts(batches={"mcq:pending": 3}))[0] == "Queued"
    assert P.unit_status(10, counts())[0] == "Not started"
    assert P.unit_status(10, counts(inserted=10, flagged=1))[0] == "Needs review"


def test_deleted_questions_do_not_count_as_inserted():
    status, why = P.unit_status(5, counts(generated=5, validated=5, inserted=3, removed=2))
    assert status == "Partially completed" and "since deleted" in why


def test_difficulty_imbalance_flagged():
    c = counts(valid_by_difficulty={1: 0, 2: 0, 3: 20, 4: 0, 5: 0})
    assert "No validated questions at difficulty" in P.difficulty_flag(c, 20)
    c = counts(valid_by_difficulty={1: 2, 2: 5, 3: 7, 4: 4, 5: 2})
    assert P.difficulty_flag(c, 20) is None


def test_percentages_are_capped_per_unit():
    row = {"planned": 20, "generated": 30, "validated": 25, "inserted": 25, "rejected": 5, "duplicate": 0,
           "failed_attempts": 0, "notes_planned": 2, "notes_done": 1, "units": 2, "units_covered": 1,
           "capped_generated": 15, "capped_validated": 12, "capped_inserted": 12}
    s = P.summarise(row)
    assert s["insertion_pct"] == 60.0 and s["remaining"] == 8 and s["overall_pct"] == round(100 * 13 / 22, 1)


def test_tos_name_cleaning_and_minimums():
    assert TP.clean_name("2 Physiology (MCQs--3)") == "Physiology"
    assert TP.clean_name(" Peptic ulcer") == "Peptic ulcer"
    assert TP.min_per_child("Minimum of two MCQ's from each topic") == 2
    assert TP.min_per_child("at least 3 per topic") is None


def test_count_check_flags_parts_that_do_not_add_up():
    paper = SimpleNamespace(id=1, parent_id=None, level="paper", official_count=100, ambiguous=False, ambiguity_note=None)
    subs = [SimpleNamespace(id=i, parent_id=1, level="subject", official_count=c, ambiguous=False, ambiguity_note=None)
            for i, c in ((2, 20), (3, 19), (4, 60))]
    TP.check_counts([paper, *subs], has_counts=True)
    assert paper.ambiguous and "add up to 99" in paper.ambiguity_note


def test_stitching_fills_headings_and_routes_totals():
    pages = [
        SimpleNamespace(page_no=40, rows=[
            {"paper": "Paper II", "specialty": "Obstetrics & Gynaecology", "subject": "Obstetrics", "topic": "Anaemia"}]),
        SimpleNamespace(page_no=41, rows=[
            {"topic": "Pre-eclampsia", "subtopic": "Magnesium sulphate"},          # continuation page, no headings
            {"paper": "Paper II", "count": 100, "count_level": "paper"}]),           # the specialty's own total
        SimpleNamespace(page_no=60, rows=[
            {"paper": "Paper I (BDS)", "subject": "Anatomy", "count": 10, "count_level": "subject"},
            {"paper": "Paper I", "subject": "Physiology"}]),                           # unqualified on a later page
    ]
    rows = [r for _, r in TP.stitch_rows(pages)]
    assert rows[1]["specialty"] == "Obstetrics & Gynaecology" and rows[1]["subject"] == "Obstetrics"
    assert rows[2]["count_level"] == "specialty" and rows[2]["specialty"] == "Obstetrics & Gynaecology"
    assert rows[4]["paper"] == "Paper I (BDS)"
    assert TP._key("Obstetrics & Gynaecology") == TP._key("Obstetrics and Gynecology")
    assert TP._key("Surgery & Allied") == TP._key("Surgery and Allied")


def test_curated_json_rows_carry_official_counts_and_structure():
    from labs.seed_studio import tos_json as J

    pget = {"specifications": [
        {"candidate_stream": "MBBS", "paper": "Paper-I", "total_mcqs": "100", "subjects": [
            {"subject_name": "Physiology", "mcq_count": "19", "topics": [
                {"topic_name": "Neurophysiology", "mcq_count": 3, "subtopics": ["Sensory Physiology: receptors"]}]}]},
        {"paper": "Paper-II", "specialty_name": "Paediatric Medicine", "total_mcqs": "100", "subjects": []}]}
    rows = [r for _, r in J.pget_rows(pget)]
    assert rows[0] == {"paper": "Paper I (MBBS)", "specialty": None, "count": 100, "count_level": "paper"}
    assert any(r.get("topic") == "Neurophysiology" and r["count"] == 3 for r in rows)
    assert any(r.get("subtopic") == "Sensory Physiology" and r.get("detail") == "receptors" for r in rows)
    assert rows[-1]["specialty"] == "Paediatric Medicine" and rows[-1]["count_level"] == "specialty"
    fcps = {"specialty_name": "ENT", "syllabus": {"paper_1": {"subjects": [
        {"subject_name": "Anatomy", "sub_subjects": [{"sub_subject_name": "Embryology", "topics": ["Pharyngeal arches"]}]}]}}}
    frows = [r for _, r in J.fcps_rows(fcps)]
    assert frows[-1] == {"paper": "Paper I", "subject": "Anatomy", "topic": "Embryology", "subtopic": "Pharyngeal arches",
                         "detail": None}
    assert all("count" not in r for r in frows)          # CPSP prints no counts: none are invented


def test_heading_with_listed_items_becomes_one_leaf_with_detail():
    from labs.seed_studio.tos_json import split_heading

    head, detail = split_heading("Sensory & Motor: Cervical plexus, Brachial plexus, Intercostal nerves, Lumbar plexus")
    assert head == "Sensory & Motor" and detail.startswith("Cervical plexus, Brachial plexus")
    assert split_heading("Cells and Tissues Related in the Organization of the Body") == (
        "Cells and Tissues Related in the Organization of the Body", None)


def test_note_items_and_coverage():
    from labs.seed_studio.generate import item_coverage, split_items

    items = split_items("Distribution within body compartments, Forces of filtration and absorption, "
                        "Edema (types, pathophysiology, systemic edema stages)")
    assert items == ["Distribution within body compartments", "Forces of filtration and absorption",
                     "Edema (types, pathophysiology, systemic edema stages)"]
    md = ("## Distribution within body compartments\n- ICF is two thirds [Guyton, Page 309]\n"
          "## Forces of filtration and absorption\n"
          "## Edema (types, pathophysiology, systemic edema stages)\n- Pitting edema [Guyton, Page 322]\n")
    cov = item_coverage(md, items)
    assert cov["covered"] == 2 and cov["missing"] == ["Forces of filtration and absorption"]
    assert item_coverage(md, []) is None
