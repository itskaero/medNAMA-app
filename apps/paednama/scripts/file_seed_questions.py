"""File Seed Studio's Paediatrics questions under PaedNAMA's syllabus topics (paeds_topic_mcqs), so each topic's
Practise tab offers them. Safe to repeat; run it while a wave is going or after it.

    python -m apps.paednama.scripts.file_seed_questions

Every question Seed Studio inserted for the CPSP Paediatrics document (IMM theory) is filed under its IMM topic, and
under the FCPS-II topic of the same name where there is one. Pending questions are filed too, but PaedNAMA, like
medNAMA, only serves a question once it is approved (status 'ready').
"""

from __future__ import annotations

from sqlalchemy import text

from apps.paednama.server import registry, settings  # noqa: F401  (backend/ on the path)
from apps.paednama.server.models import PaedsTopicMcq, create_tables

from app.database import SessionLocal  # noqa: E402

FAMILY = "CPSP-PAEDS"     # labs/seed_studio/tos_json.PAEDS_FAMILY


def _key(s: str) -> str:
    return " ".join(s.lower().split())


def main() -> None:
    create_tables()
    imm = {(_key(sys_["system"].title()), _key(t["name"])): t["id"]
           for sys_ in registry.stage("imm")["syllabus"] for t in sys_["topics"]}
    by_name: dict[str, list[str]] = {}
    for sys_ in registry.stage("fcps2")["syllabus"]:
        for t in sys_["topics"]:
            by_name.setdefault(_key(t["name"]), []).append(t["id"])
    fcps2 = {k: v[0] for k, v in by_name.items() if len(v) == 1}     # only an unambiguous name match

    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT i.mcq_id, n.name AS topic, p.name AS subject FROM seed_items i "
            "JOIN seed_batches b ON b.id = i.batch_id JOIN seed_jobs j ON j.id = b.job_id "
            "JOIN seed_tos_documents d ON d.id = j.document_id JOIN seed_tos_nodes n ON n.id = b.node_id "
            "JOIN seed_tos_nodes p ON p.id = n.parent_id "
            "WHERE d.family = :f AND i.mcq_id IS NOT NULL AND i.status = 'inserted'"), {"f": FAMILY}).all()
        have = {(r.stage, r.topic_id, r.mcq_id) for r in db.query(PaedsTopicMcq)}
        added = unmatched = 0
        for r in rows:
            targets = []
            t_imm = imm.get((_key(r.subject), _key(r.topic)))
            if not t_imm:     # Seed Studio keeps a long topic's heading only ("Viral infections, including ... (but
                #               not limited to)"): take the one topic in the same system whose name starts with it
                pre = [tid for (s, n), tid in imm.items() if s == _key(r.subject) and n.startswith(_key(r.topic))]
                t_imm = pre[0] if len(pre) == 1 else None
            if t_imm:
                targets.append(("imm", t_imm))
            else:
                unmatched += 1
            t_f2 = fcps2.get(_key(r.topic))
            if t_f2:
                targets.append(("fcps2", t_f2))
            for stage, tid in targets:
                if (stage, tid, r.mcq_id) not in have:
                    db.add(PaedsTopicMcq(stage=stage, topic_id=tid, mcq_id=r.mcq_id, source="seed"))
                    have.add((stage, tid, r.mcq_id))
                    added += 1
        db.commit()
        print(f"{len(rows)} seeded questions; filed {added} new topic links; {unmatched} with no IMM topic match")
    finally:
        db.close()


if __name__ == "__main__":
    main()
