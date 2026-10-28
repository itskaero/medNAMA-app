"""Copy PaedNAMA's approved questions (Seed Studio's Paediatrics waves) to the NAS, filed under the same topics.

    python -m apps.paednama.scripts.sync_questions_to_nas --dry-run
    python -m apps.paednama.scripts.sync_questions_to_nas

Copies every question filed under a PaedNAMA topic (paeds_topic_mcqs, source "seed") that is approved ("ready") on the
PC, with its topic links and programme tag. Question ids differ between the two databases (the NAS writes its own
questions when learners generate sets), so each question gets a new id there and is matched on its Seed Studio
reference (mcqs.source, source_ref): re-running copies only what is new, and never duplicates. Books and passages
keep their ids on both (backend/scripts/books_transfer.py), so book_id and source_chunk_ids carry over as they are.
Goes through the SSH tunnel in scripts/nas_tunnel.py.
"""

from __future__ import annotations

import argparse
import json
import sys

from apps.paednama.server import settings  # noqa: F401  (backend/ on the path)

sys.path.insert(0, str(settings.REPO / "scripts"))
from nas_tunnel import nas_database_url  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

from app.database import SessionLocal  # noqa: E402

# what travels; id, usage stats and links to PC-only rows (recall groups, twists, concept cards) do not
COLS = ["book_id", "quiz_set_id", "quiz_set_title", "question_text", "options", "correct_option", "topic",
        "main_category", "sub_category", "explanation_markdown", "explanation_citations", "explanation_figures",
        "status", "difficulty", "stem_embedding", "source_chunk_ids", "tested_concept", "grounding", "source",
        "source_ref", "access"]
JSON_COLS = {"options", "explanation_citations", "explanation_figures", "source_chunk_ids"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    rows = db.execute(text(
        f"SELECT m.id, {', '.join('m.' + c for c in COLS)} FROM mcqs m "
        "WHERE m.status = 'ready' AND m.source = 'seed-studio' AND m.source_ref IS NOT NULL "
        "AND m.id IN (SELECT mcq_id FROM paeds_topic_mcqs WHERE source = 'seed') ORDER BY m.id")).mappings().all()
    links = db.execute(text("SELECT stage, topic_id, mcq_id FROM paeds_topic_mcqs WHERE source = 'seed'")).all()
    tags = db.execute(text("SELECT mcq_id, axis, label FROM mcq_tags WHERE axis = 'programme' AND mcq_id = ANY(:ids)"),
                      {"ids": [r["id"] for r in rows]}).all()
    db.close()
    print(f"PC: {len(rows)} approved questions, {len(links)} topic links")

    with nas_database_url() as url:
        engine = create_engine(url)
        with engine.begin() as conn:
            if not conn.execute(text("SELECT to_regclass('public.paeds_topic_mcqs')")).scalar():
                sys.exit("The NAS has no paeds_topic_mcqs table: deploy PaedNAMA's backend there first.")
            have = {r.source_ref: r.id for r in conn.execute(text(
                "SELECT id, source_ref FROM mcqs WHERE source = 'seed-studio' AND source_ref IS NOT NULL"))}
            new = [r for r in rows if r["source_ref"] not in have]
            print(f"NAS: {len(have)} Seed Studio questions already there; {len(new)} to copy")
            if args.dry_run:
                return
            insert = text(f"INSERT INTO mcqs ({', '.join(COLS)}) VALUES ("
                          + ", ".join(f"CAST(:{c} AS jsonb)" if c in JSON_COLS else
                                      (f"CAST(:{c} AS vector)" if c == "stem_embedding" else f":{c}") for c in COLS)
                          + ") RETURNING id")
            for r in new:
                vals = {c: (json.dumps(r[c]) if c in JSON_COLS and r[c] is not None else r[c]) for c in COLS}
                if vals["stem_embedding"] is not None and not isinstance(vals["stem_embedding"], str):
                    vals["stem_embedding"] = "[" + ",".join(str(float(x)) for x in vals["stem_embedding"]) + "]"
                have[r["source_ref"]] = conn.execute(insert, vals).scalar()
            pc_to_nas = {r["id"]: have[r["source_ref"]] for r in rows}
            linked = conn.execute(text(
                "INSERT INTO paeds_topic_mcqs (stage, topic_id, mcq_id, source, created_at) "
                "SELECT x.stage, x.topic_id, x.mcq_id, 'seed', now() FROM jsonb_to_recordset(CAST(:rows AS jsonb)) "
                "AS x(stage text, topic_id text, mcq_id int) ON CONFLICT DO NOTHING"),
                {"rows": json.dumps([{"stage": s, "topic_id": t, "mcq_id": pc_to_nas[m]} for s, t, m in links if m in pc_to_nas])}).rowcount
            tagged = conn.execute(text(
                "INSERT INTO mcq_tags (mcq_id, axis, label) SELECT x.mcq_id, x.axis, x.label "
                "FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(mcq_id int, axis text, label text) "
                "WHERE NOT EXISTS (SELECT 1 FROM mcq_tags t WHERE t.mcq_id = x.mcq_id AND t.axis = x.axis)"),
                {"rows": json.dumps([{"mcq_id": pc_to_nas[m], "axis": a, "label": lb} for m, a, lb in tags if m in pc_to_nas])}).rowcount
            print(f"Copied {len(new)} questions; filed {linked} topic links; {tagged} tags.")


if __name__ == "__main__":
    main()
