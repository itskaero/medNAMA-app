"""Dashboard numbers, computed from persisted rows every time (seed_items, seed_batches, seed_notes, mcqs).

Definitions (also shown in the UI):

  generated   every question a batch produced and parsed, whatever happened to it next
  validated   unique questions that passed validation: status validated or inserted (a duplicate is not unique)
  inserted    validated questions saved to `mcqs` whose row still exists
  rejected    failed validation; duplicate: passed validation but repeats an existing question
  failed attempts   batch attempts that ended in an error (each retry counts)

  generation %   sum over units of min(generated, planned) / total planned
  validation %   sum over units of min(validated, planned) / total planned
  insertion %    sum over units of min(inserted, planned) / total planned
  notes %        leaves with a current revision note / leaves planned
  topic coverage units with at least one inserted question / units
  overall %      (capped inserted + notes done) / (planned questions + planned notes): each unit of work counts once

A unit over its target does not lift another unit's percentage (the min() caps), so 100% means every unit is full.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import text

from . import targets as T

from app.seed_models import SeedJob, SeedTosDocument, SeedTosNode  # noqa: E402

STAGE_LABEL = {"retrieving": "Retrieving evidence", "generating": "Generating", "validating": "Validating",
               "deduplicating": "Checking duplicates", "saving": "Saving", "writing_note": "Writing note"}


def node_dicts(db, doc_id: int) -> list[dict]:
    return [{"id": n.id, "parent_id": n.parent_id, "level": n.level, "name": n.name, "path": n.path,
             "position": n.position, "official_count": n.official_count, "min_per_child": n.min_per_child,
             "target_override": n.target_override, "count_note": n.count_note, "detail": n.detail, "page_no": n.page_no,
             "ambiguous": n.ambiguous, "ambiguity_note": n.ambiguity_note}
            for n in db.query(SeedTosNode).filter(SeedTosNode.document_id == doc_id).order_by(SeedTosNode.position)]


ITEM_SQL = """
SELECT i.node_id, i.difficulty, i.status, (i.status = 'inserted' AND i.mcq_id IS NOT NULL) AS live,
       i.review_state, count(*)
FROM seed_items i JOIN seed_tos_nodes n ON n.id = i.node_id
WHERE n.document_id = :d
GROUP BY 1, 2, 3, 4, 5
"""
BATCH_SQL = """
SELECT b.node_id, b.kind, b.status, b.stage, count(*), coalesce(sum(jsonb_array_length(b.errors)), 0),
       coalesce(sum(b.tokens_in), 0), coalesce(sum(b.tokens_out), 0),
       max(b.errors -> -1 ->> 'message') FILTER (WHERE b.status = 'failed')
FROM seed_batches b JOIN seed_jobs j ON j.id = b.job_id
WHERE j.document_id = :d AND NOT j.dry_run
GROUP BY 1, 2, 3, 4
"""
NOTE_SQL = """
SELECT s.node_id, bool_or(s.status = 'done'), max(s.reason) FILTER (WHERE s.status = 'failed')
FROM seed_notes s JOIN seed_tos_nodes n ON n.id = s.node_id WHERE n.document_id = :d GROUP BY 1
"""


def unit_counts(db, doc_id: int) -> dict[int, dict[str, Any]]:
    """Per node with any activity: question counts by outcome and difficulty, batch states, notes."""
    out: dict[int, dict[str, Any]] = defaultdict(lambda: {
        "generated": 0, "validated": 0, "inserted": 0, "rejected": 0, "duplicate": 0, "removed": 0,
        "flagged": 0, "approved": 0, "valid_by_difficulty": {d: 0 for d in range(1, 6)},
        "batches": defaultdict(int), "stages": defaultdict(int), "failed_attempts": 0,
        "tokens_in": 0, "tokens_out": 0, "last_error": None, "note": None, "note_error": None})
    for node_id, diff, status, live, review, n in db.execute(text(ITEM_SQL), {"d": doc_id}):
        c = out[node_id]
        c["generated"] += n
        if status in ("validated", "inserted"):
            c["validated"] += n
            if diff in c["valid_by_difficulty"]:
                c["valid_by_difficulty"][diff] += n
        if status == "inserted":
            if live:
                c["inserted"] += n
            else:
                c["removed"] += n
        if status == "rejected":
            c["rejected"] += n
        if status == "duplicate":
            c["duplicate"] += n
        if review == "flagged":
            c["flagged"] += n
        if review == "approved":
            c["approved"] += n
    for node_id, kind, status, stage, n, errs, tin, tout, last_err in db.execute(text(BATCH_SQL), {"d": doc_id}):
        c = out[node_id]
        c["batches"][f"{kind}:{status}"] += n
        if status == "running" and stage:
            c["stages"][stage] += n
        c["failed_attempts"] += int(errs)
        c["tokens_in"] += int(tin)
        c["tokens_out"] += int(tout)
        if last_err:
            c["last_error"] = last_err
    for node_id, done, reason in db.execute(text(NOTE_SQL), {"d": doc_id}):
        out[node_id]["note"] = bool(done)
        out[node_id]["note_error"] = reason
    return out


def unit_status(planned: int, c: dict) -> tuple[str, str | None]:
    """(status, why) for one unit. Never 'completed' just because nothing is running."""
    b = c["batches"]
    running = b.get("mcq:running", 0)
    pending = b.get("mcq:pending", 0)
    failed = b.get("mcq:failed", 0)
    if running:
        stage = max(c["stages"], key=c["stages"].get) if c["stages"] else "generating"
        return {"retrieving": "Retrieving evidence", "generating": "Generating", "validating": "Validating",
                "deduplicating": "Validating", "saving": "Saving"}.get(stage, "Generating"), None
    if pending:
        return "Queued", None
    if planned and c["inserted"] >= planned:
        return ("Needs review", f"{c['flagged']} question(s) flagged") if c["flagged"] else ("Completed", None)
    if c["generated"] == 0 and not failed:
        return "Not started", None
    why = []
    if failed:
        why.append(f"{failed} batch(es) failed" + (f": {c['last_error']}" if c["last_error"] else ""))
    if c["rejected"]:
        why.append(f"{c['rejected']} rejected in validation")
    if c["duplicate"]:
        why.append(f"{c['duplicate']} rejected as duplicates")
    if c["removed"]:
        why.append(f"{c['removed']} inserted question(s) since deleted from the bank")
    if planned and c["inserted"] < planned:
        why.append(f"short by {planned - c['inserted']}")
    if c["inserted"] == 0 and failed:
        return "Failed", "; ".join(why)
    if c["flagged"]:
        return "Needs review", "; ".join(why)
    return "Partially completed", "; ".join(why) or None


def difficulty_flag(c: dict, planned: int, dist: dict | None = None) -> str | None:
    """Missing or heavily imbalanced difficulty coverage among validated questions."""
    total = sum(c["valid_by_difficulty"].values())
    if total < 5:
        return None
    want = T.allocate_difficulty(total, dist)
    missing = [d for d in range(1, 6) if want[d] >= 2 and c["valid_by_difficulty"][d] == 0]
    if missing:
        return "No validated questions at difficulty " + ", ".join(map(str, missing))
    worst = max(range(1, 6), key=lambda d: abs(c["valid_by_difficulty"][d] - want[d]))
    if abs(c["valid_by_difficulty"][worst] - want[worst]) > max(3, 0.25 * total):
        return f"Difficulty {worst}: {c['valid_by_difficulty'][worst]} validated vs about {want[worst]} expected"
    return None


def document_tree(db, doc: SeedTosDocument) -> dict[str, Any]:
    """Every node with planned target (and its source), rolled-up counts and status: the explorer and topic table."""
    nodes = node_dicts(db, doc.id)
    plans = T.compute(nodes, doc.multiplier, doc.base_per_topic)
    counts = unit_counts(db, doc.id)
    kids = T._children(nodes)
    empty = {"generated": 0, "validated": 0, "inserted": 0, "rejected": 0, "duplicate": 0}
    rows: dict[int, dict] = {}

    def walk(n: dict) -> dict:
        p = plans.get(n["id"], T.Plan(0, "rollup"))
        unit = T.is_unit(n, kids)
        leaf = T.is_note_leaf(n, kids)
        row = {**n, "planned": p.planned, "planned_source": p.source, "planned_note": p.note, "unit": unit,
               "note_leaf": leaf, **empty, "capped_generated": 0, "capped_validated": 0, "capped_inserted": 0,
               "notes_planned": 1 if leaf else 0, "notes_done": 0, "units": 1 if unit else 0, "units_covered": 0,
               "status": None, "why": None, "difficulty_flag": None, "failed_attempts": 0}
        c = counts.get(n["id"])
        if unit:
            c = c or {**empty, "removed": 0, "flagged": 0, "approved": 0, "valid_by_difficulty": {d: 0 for d in range(1, 6)},
                      "batches": {}, "stages": {}, "failed_attempts": 0, "last_error": None, "note": None}
            for k in empty:
                row[k] = c[k]
            row["capped_generated"] = min(c["generated"], p.planned)
            row["capped_validated"] = min(c["validated"], p.planned)
            row["capped_inserted"] = min(c["inserted"], p.planned)
            row["units_covered"] = 1 if c["inserted"] else 0
            row["status"], row["why"] = unit_status(p.planned, c)
            row["difficulty_flag"] = difficulty_flag(c, p.planned)
            row["valid_by_difficulty"] = c["valid_by_difficulty"]
            row["failed_attempts"] = c["failed_attempts"]
        if leaf and counts.get(n["id"], {}).get("note"):
            row["notes_done"] = 1
        if leaf and counts.get(n["id"], {}).get("note_error") and not row["notes_done"]:
            row["note_error"] = counts[n["id"]]["note_error"]
        # A unit's question counts are its own (its sub-topics hold none); notes always roll up from the leaves.
        rolled = ("notes_planned", "notes_done") if unit else (
            "generated", "validated", "inserted", "rejected", "duplicate", "capped_generated", "capped_validated",
            "capped_inserted", "notes_planned", "notes_done", "units", "units_covered", "failed_attempts")
        for ch in kids.get(n["id"], []):
            cr = walk(ch)
            for k in rolled:
                row[k] += cr[k]
        if not unit:
            row["status"] = _rollup_status(row)
        rows[n["id"]] = row
        return row

    for root in kids.get(None, []):
        walk(root)
    return {"nodes": rows, "order": [n["id"] for n in nodes]}


def _rollup_status(r: dict) -> str:
    if r["units"] and r["units_covered"] == r["units"] and r["capped_inserted"] >= r["planned"]:
        return "Completed"
    if r["generated"] or r["notes_done"]:
        return "Partially completed"
    return "Not started"


def pct(a: int, b: int) -> float | None:
    return round(100.0 * a / b, 1) if b else None


def summarise(row: dict) -> dict[str, Any]:
    """The progress block for a node (or a document root)."""
    planned, notes = row["planned"], row["notes_planned"]
    return {
        "planned": planned, "generated": row["generated"], "validated": row["validated"], "inserted": row["inserted"],
        "rejected": row["rejected"], "duplicate": row["duplicate"], "failed_attempts": row["failed_attempts"],
        "notes_planned": notes, "notes_done": row["notes_done"], "units": row["units"],
        "units_covered": row["units_covered"], "remaining": max(0, planned - row["capped_inserted"]),
        "generation_pct": pct(row["capped_generated"], planned), "validation_pct": pct(row["capped_validated"], planned),
        "insertion_pct": pct(row["capped_inserted"], planned), "notes_pct": pct(row["notes_done"], notes),
        "coverage_pct": pct(row["units_covered"], row["units"]),
        "overall_pct": pct(row["capped_inserted"] + row["notes_done"], planned + notes),
    }


def global_metrics(db) -> dict[str, Any]:
    docs = db.query(SeedTosDocument).order_by(SeedTosDocument.id).all()
    totals: dict[str, Any] = defaultdict(int)
    per_doc = []
    exams, specialties = set(), set()
    for d in docs:
        tree = document_tree(db, d) if d.parse_status in ("parsed", "failed") else None
        if not tree or not tree["nodes"]:
            per_doc.append({"id": d.id, "title": d.title, "family": d.family, "parse_status": d.parse_status, "summary": None})
            continue
        root = next(r for r in tree["nodes"].values() if r["parent_id"] is None)
        s = summarise(root)
        per_doc.append({"id": d.id, "title": d.title, "family": d.family, "parse_status": d.parse_status, "summary": s})
        for k in ("planned", "generated", "validated", "inserted", "rejected", "duplicate", "failed_attempts",
                  "notes_planned", "notes_done", "units", "units_covered", "remaining"):
            totals[k] += s[k]
        totals["capped_generated"] += root["capped_generated"]
        totals["capped_validated"] += root["capped_validated"]
        totals["capped_inserted"] += root["capped_inserted"]
        for r in tree["nodes"].values():
            if r["level"] == "paper":
                exams.add(f"{d.family} · {r['name']}")
            if r["level"] == "specialty":
                specialties.add(f"{d.family} · {r['name']}")
        if d.family == "FCPS":
            specialties.add(f"FCPS · {d.title.split(': ', 1)[-1]}")
    active = db.query(SeedJob).filter(SeedJob.status.in_(("queued", "running", "paused", "cancelling"))).count()
    t = dict(totals)
    planned, notes = t.get("planned", 0), t.get("notes_planned", 0)
    return {
        "documents": len(docs), "documents_parsed": sum(1 for d in docs if d.parse_status == "parsed"),
        "examinations": len(exams), "specialties": len(specialties), "active_jobs": active,
        **{k: t.get(k, 0) for k in ("planned", "generated", "validated", "inserted", "rejected", "duplicate",
                                     "failed_attempts", "notes_planned", "notes_done", "units", "units_covered",
                                     "remaining")},
        "generation_pct": pct(t.get("capped_generated", 0), planned),
        "validation_pct": pct(t.get("capped_validated", 0), planned),
        "insertion_pct": pct(t.get("capped_inserted", 0), planned),
        "notes_pct": pct(t.get("notes_done", 0), notes),
        "coverage_pct": pct(t.get("units_covered", 0), t.get("units", 0)),
        "overall_pct": pct(t.get("capped_inserted", 0) + t.get("notes_done", 0), planned + notes),
        "per_document": per_doc,
    }
