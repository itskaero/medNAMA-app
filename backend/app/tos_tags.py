"""Which TOS topic (and subtopic) each question belongs to, written down once in mcq_tags.

The TOS screens used to decide this on every request by string matching (`ilike` on MCQ.topic, keyword
overlap), and fell back to any question of the subject when a topic had too few matches. That made the
per-topic counts unreliable and served questions from other topics without saying so.

Now `scripts/tag_tos.py` tags questions ahead of time and every TOS query reads the tags:

    axis 'tos_topic'     label '<exam_id>:<topic_id>'                 e.g. 'pget:mbbs-p1/anatomy/upper-limb'
    axis 'tos_subtopic'  label '<exam_id>:<topic_id>#<subtopic slug>'
    axis 'tos_review'    label '<exam_id>:<topic_id>'   a guess too close to call: kept out of practice,
                                                        listed for an admin to confirm

How a question gets its topic, best evidence first:
  1. Seed Studio questions carry a 'seed_node' tag: the TOS node they were written for. Exact.
  2. Otherwise the topic whose description is nearest in meaning to the question stem, among the topics of
     the subject the question was filed under. Calibrated on the 792 seed questions whose topic is known
     (calibration, 2026-10-14): 90% right at top-1; 93% right when the best topic beats the runner-up by
     >= MARGIN 0.01 (93% of those questions), 95% at 0.02 (85%). Below the margin -> 'tos_review'.

Topic ids include the paper id, so the same question can be tagged once per paper that uses it (PGET
Paper I and each FCPS specialty's Paper 1 share the basic-science bank). Re-running the tagger for a paper
replaces that paper's tags, so a new TOS version is a re-run, not a migration.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session

from app.models import MCQ, MCQTag

logger = logging.getLogger("app.tos_tags")

AXIS_TOPIC = "tos_topic"
AXIS_SUBTOPIC = "tos_subtopic"
AXIS_REVIEW = "tos_review"
AXIS_MANUAL = "tos_manual"   # an admin's decision on a close call: '<exam>:<topic id>', or '<paper prefix>-'
                             # for "not this paper's syllabus". Never deleted by a re-tag; applied first.
MARGIN = 0.01          # best topic must beat the runner-up by this much (cosine) to be trusted
MIN_SIM = 0.45         # and be at least this close at all; below it the question is off-syllabus here
SUB_MARGIN = 0.02

# Question pools per paper (MCQ.main_category). A question is only considered for a paper whose pool
# holds it, then only for the subject it was filed under.
PAPER1_POOL = ("Paper 1 · Basic sciences", "Past papers · FCPS Part 1")
PAPER2_POOL = ("Paper 2 · Faculty", "Past papers · FCPS Part 1")
PGET_SEED = "Seed · PGET Paper I (MBBS)"
DENTAL_PAST = "Past papers · FCPS Part 1 (Dentistry)"


def slug(text_: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text_ or "").lower()).strip("-") or "x"


def topic_label(exam_id: str, topic_id: str) -> str:
    return f"{exam_id}:{topic_id}"


def subtopic_label(exam_id: str, topic_id: str, subtopic: str) -> str:
    return f"{exam_id}:{topic_id}#{slug(subtopic)}"


def paper_prefix(exam_id: str, paper_id: str) -> str:
    """Every topic label of a paper starts with this (topic ids start with the paper's own id)."""
    if exam_id == "fcps1":   # FCPS topic ids are 'fcps1-<specialty>-p1/...'
        return f"{exam_id}:fcps1-{paper_id}/"
    return f"{exam_id}:{paper_id}/"


def paper_pool(exam_id: str, paper: dict[str, Any]) -> tuple[str, ...]:
    """The question pool (main categories) a paper draws on."""
    if exam_id == "pget":
        if paper["id"] == "mbbs-p1":
            return (PGET_SEED, *PAPER1_POOL)
        if paper["id"] == "bds-p1":
            return (DENTAL_PAST, *PAPER1_POOL)
        return PAPER2_POOL
    if exam_id == "fcps1":
        pool = PAPER1_POOL if paper.get("category") == "basic" else PAPER2_POOL
        if "dent" in paper["id"]:
            pool = (DENTAL_PAST, *pool)
        return pool
    # MBBS modular blocks mix basic and clinical subjects
    return (*PAPER1_POOL, "Paper 2 · Faculty")


def subject_condition(subject_name: str):
    """Questions filed under a TOS subject. Compound names ("Community Medicine & Behavioral Sciences")
    match either part; British/American spellings of the common ones are folded."""
    parts = [p.strip() for p in re.split(r"&|,|\band\b|/", subject_name) if p.strip()]
    conds = []
    for p in parts or [subject_name]:
        for v in {p, p.replace("Behavioral", "Behavioural"), p.replace("Behavioural", "Behavioral"),
                  p.replace("Gynaecology", "Gynecology"), p.replace("Paediatric", "Pediatric")}:
            conds.append(MCQ.sub_category.ilike(f"%{v}%"))
            conds.append(MCQ.id.in_(select(MCQTag.mcq_id).where(
                MCQTag.axis.in_(("fcps_subject", "subject", "specialty")), MCQTag.label.ilike(f"%{v}%"))))
    return or_(*conds)


# Paper II specialty -> the bank's spellings (practice_scope.SPECIALTIES: Paper 2 bank sub_category, and the
# 'specialty' tag on past papers). A specialty with no entry has no questions here yet, and says so.
_SPECIALTY_WORDS = (
    ("dent", "Dentistry"), ("orthodont", "Dentistry"), ("prosthodont", "Dentistry"), ("periodont", "Dentistry"),
    ("oral", "Dentistry"), ("operative", "Dentistry"),
    ("gyn", "Gynaecology & Obstetrics"), ("obstet", "Gynaecology & Obstetrics"),
    ("otorhino", "ENT"), ("ent", "ENT"), ("ophthal", "Ophthalmology"), ("radiolog", "Radiology"),
    ("psychiat", "Psychiatry"), ("anaesth", "Anaesthesia"), ("anesth", "Anaesthesia"), ("patholog", "Pathology"),
    ("surg", "Surgery"), ("paediatric", None), ("pediatric", None), ("community", None), ("medicine", "Medicine"),
)


def is_specialty_paper(exam_id: str, paper: dict[str, Any]) -> bool:
    return exam_id in ("pget", "fcps1") and paper.get("category") != "basic"


def specialty_key(paper: dict[str, Any]) -> str | None:
    name = (paper.get("specialty") or paper.get("title") or "").lower()
    for word, key in _SPECIALTY_WORDS:
        if (word == "ent" and re.search(r"ent", name)) or (word != "ent" and word in name):
            return key
    return None


def specialty_condition(key: str | None):
    """A specialty's questions: its Paper 2 bank and the past papers its candidates sat."""
    from app.practice_scope import SPECIALTIES

    if not key or key not in SPECIALTIES:
        return MCQ.id.is_(None)
    spell = SPECIALTIES[key]
    conds = [and_(MCQ.main_category.in_(PAPER2_POOL[1:] + (DENTAL_PAST,)),
                  MCQ.id.in_(select(MCQTag.mcq_id).where(MCQTag.axis == "specialty", MCQTag.label.in_(spell["past"]))))]
    if spell["bank"]:
        conds.append(and_(MCQ.main_category == PAPER2_POOL[0], MCQ.sub_category.in_(spell["bank"])))
    return or_(*conds)


# Pool embeddings by (pool, subject): FCPS specialties share Paper 1's pool, so one load serves them all.
_POOL_CACHE: dict[tuple, tuple[list[int], Any]] = {}
CACHE_MAX_ROWS = int(os.environ.get("TOS_TAG_CACHE_ROWS", "120000"))


@dataclass
class PaperTagStats:
    exam_id: str
    paper_id: str
    pool: int = 0
    seeded: int = 0
    confident: int = 0
    review: int = 0
    off_syllabus: int = 0
    subtopics: int = 0
    per_topic: dict[str, int] = field(default_factory=dict)


# Topic-text vectors, keyed by a hash of the text. Filled from a file made on a machine with the model
# (scripts/tag_tos.py --save-vectors), so a small server can tag without loading the model a second time.
_VECTORS: dict[str, np.ndarray] = {}
_VECTORS_MISSED = 0


def _key(t: str) -> str:
    return hashlib.sha1(t.encode("utf-8")).hexdigest()


def load_vectors(path: str) -> int:
    data = np.load(path)
    for k, v in zip(data["keys"], data["vecs"]):
        _VECTORS[str(k)] = v
    return len(data["keys"])


def save_vectors(path: str) -> int:
    keys = sorted(_VECTORS)
    np.savez_compressed(path, keys=np.array(keys), vecs=np.stack([_VECTORS[k] for k in keys]) if keys else np.zeros((0, 1024)))
    return len(keys)


def _embed(texts: list[str]) -> np.ndarray:
    global _VECTORS_MISSED
    missing = [t for t in dict.fromkeys(texts) if _key(t) not in _VECTORS]
    if missing:
        from app.quiz_generation import _embed as embed
        _VECTORS_MISSED += len(missing)
        for t, v in zip(missing, embed(missing)):
            _VECTORS[_key(t)] = v
    return np.stack([_VECTORS[_key(t)] for t in texts]).astype(np.float32)


def _parse_vec(v: Any) -> np.ndarray:
    if isinstance(v, str):
        return np.fromstring(v.strip("[]"), sep=",", dtype=np.float32)
    return np.asarray(v, dtype=np.float32)


def seed_path_prefix(exam_id: str, paper: dict[str, Any]) -> tuple[str, str | None]:
    """Seed Studio's path for a paper ('PGET > Paper I (MBBS)', 'FCPS > Paper I'), and for FCPS the
    specialty its document is about (each FCPS specialty is its own Seed Studio document)."""
    if exam_id == "pget":
        if paper["id"] == "mbbs-p1":
            return "PGET > Paper I (MBBS)", None
        if paper["id"] == "bds-p1":
            return "PGET > Paper I (BDS)", None
        return f"PGET > Paper II > {paper.get('specialty') or ''}", None
    if exam_id == "fcps1":
        return ("FCPS > Paper I" if paper.get("category") == "basic" else "FCPS > Paper II"), paper.get("specialty")
    return "", None


def seed_topic_ids(db: Session, exam_id: str, paper: dict[str, Any]) -> dict[int, tuple[str, str | None]]:
    """seed_tos_nodes.id -> (topic id, subtopic or None) for this paper's topics."""
    prefix, specialty = seed_path_prefix(exam_id, paper)
    if not prefix:
        return {}
    sql = "select n.id, n.path from seed_tos_nodes n join seed_tos_documents d on d.id = n.document_id where n.path like :p"
    params: dict[str, Any] = {"p": prefix + " > %"}
    if specialty:
        sql += " and d.title ilike :sp"
        params["sp"] = f"%{specialty}%"
    try:
        rows = db.execute(text(sql), params).fetchall()
    except Exception:   # Seed Studio tables absent (older database)
        db.rollback()
        return {}
    by_name: dict[tuple[str, str], dict[str, Any]] = {}
    for s in paper["subjects"]:
        for t in s["topics"]:
            by_name[(s["subject_name"].lower(), t["topic_name"].lower())] = t
    out: dict[int, tuple[str, str | None]] = {}
    depth = prefix.count(" > ") + 1
    for node_id, path in rows:
        segs = path.split(" > ")[depth:]
        if len(segs) < 2:
            continue
        t = by_name.get((segs[0].lower(), segs[1].lower()))
        if t is None:
            continue
        sub = segs[2] if len(segs) > 2 and segs[2] in t["subtopics"] else None
        out[node_id] = (t["id"], sub)
    return out


def tag_paper(db: Session, exam_id: str, paper: dict[str, Any], *, dry_run: bool = False,
              margin: float = MARGIN, min_sim: float = MIN_SIM) -> PaperTagStats:
    """Tag every question in this paper's pool with its topic (and subtopic where clear)."""
    stats = PaperTagStats(exam_id, paper["id"])
    pool_cats = paper_pool(exam_id, paper)
    seeds = seed_topic_ids(db, exam_id, paper)
    rows_out: list[tuple[int, str, str]] = []

    # 1. Seed Studio questions: their node says the topic.
    seeded_ids: set[int] = set()
    if seeds:
        for mcq_id, label in db.query(MCQTag.mcq_id, MCQTag.label).join(MCQ, MCQ.id == MCQTag.mcq_id).filter(
                MCQTag.axis == "seed_node", MCQ.status == "ready").all():
            hit = seeds.get(int(label)) if label.isdigit() else None
            if hit is None:
                continue
            topic_id, sub = hit
            seeded_ids.add(mcq_id)
            rows_out.append((mcq_id, AXIS_TOPIC, topic_label(exam_id, topic_id)))
            if sub:
                rows_out.append((mcq_id, AXIS_SUBTOPIC, subtopic_label(exam_id, topic_id, sub)))
                stats.subtopics += 1
            stats.per_topic[topic_id] = stats.per_topic.get(topic_id, 0) + 1
        stats.seeded = len(seeded_ids)

    # 1b. Admin decisions on earlier close calls: they win over the embedding guess on every re-run.
    prefix_like = paper_prefix(exam_id, paper["id"]).replace("_", r"\_") + "%"
    for mcq_id, label in db.query(MCQTag.mcq_id, MCQTag.label).filter(
            MCQTag.axis == AXIS_MANUAL, MCQTag.label.like(prefix_like, escape="\\")).all():
        if mcq_id in seeded_ids:
            continue
        seeded_ids.add(mcq_id)
        if not label.endswith("/-"):
            rows_out.append((mcq_id, AXIS_TOPIC, label))
            tid = label.split(":", 1)[1]
            stats.per_topic[tid] = stats.per_topic.get(tid, 0) + 1

    # 2. Everything else: the nearest topic by meaning. On a basic-science or MBBS paper each subject's
    #    questions are matched to that subject's topics. A specialty paper's subjects are organ systems or
    #    sub-areas ("Cardiovascular System", "Ear") that the bank does not file by, so its pool is the
    #    specialty's questions and each is matched against every topic of the paper.
    spec = specialty_key(paper) if is_specialty_paper(exam_id, paper) else None
    # A question filed under two subjects (e.g. Physiology and Biochemistry tags) is scored in both; it keeps
    # the single best: confident beats review, then the higher score. One topic per question per paper.
    decided: dict[int, tuple[int, float, list[tuple[int, str, str]], str | None]] = {}

    def decide(mcq_id: int, rank: int, top: float, rows: list[tuple[int, str, str]], topic_id: str | None) -> None:
        cur = decided.get(mcq_id)
        if cur is None or (rank, top) > (cur[0], cur[1]):
            decided[mcq_id] = (rank, top, rows, topic_id)
    if is_specialty_paper(exam_id, paper):
        groups = [(("specialty", spec or "-"), specialty_condition(spec),
                   [(s["subject_name"], t) for s in paper["subjects"] for t in s["topics"]])]
    else:
        groups = [((pool_cats, s["subject_name"]),
                   and_(MCQ.main_category.in_(pool_cats), subject_condition(s["subject_name"])),
                   [(s["subject_name"], t) for t in s["topics"]]) for s in paper["subjects"]]
    for key, cond, items in groups:
        if not items:
            continue
        topics = [t for _, t in items]
        if key not in _POOL_CACHE:
            # Bounded by rows (1024 floats each): ~120k rows is ~500 MB, safe on the 4 GB NAS.
            if sum(len(v[0]) for v in _POOL_CACHE.values()) > CACHE_MAX_ROWS:
                _POOL_CACHE.clear()
            got = (db.query(MCQ.id, MCQ.stem_embedding)
                   .filter(MCQ.status == "ready", MCQ.twist_of.is_(None), MCQ.stem_embedding.isnot(None), cond)
                   .all())
            _POOL_CACHE[key] = ([r[0] for r in got], np.stack([_parse_vec(r[1]) for r in got]) if got else None)
        ids_all, mat_all = _POOL_CACHE[key]
        keep = [i for i, mid in enumerate(ids_all) if mid not in seeded_ids]
        rows = [(ids_all[i], None) for i in keep]
        stats.pool += len(rows)
        if not rows:
            continue
        mat = mat_all[keep]
        # One vector per topic, and one per topic-name/subtopic part; score = mean of the two (method C).
        whole = _embed([f"{sn}: {t['topic_name']}. {', '.join(t['subtopics'])}" for sn, t in items])
        parts, owner, sub_names = [], [], []
        for i, (sn, t) in enumerate(items):
            for p in [t["topic_name"], *t["subtopics"]]:
                parts.append(f"{sn}: {t['topic_name']}: {p}")
                owner.append(i)
                sub_names.append(p if p != t["topic_name"] else None)
        part_vecs = _embed(parts)
        owner_arr = np.array(owner)
        s_whole = mat @ whole.T                                  # (n, topics)
        s_parts = mat @ part_vecs.T                              # (n, parts)
        s_best_part = np.full_like(s_whole, -1.0)
        for i in range(len(topics)):
            s_best_part[:, i] = s_parts[:, owner_arr == i].max(axis=1)
        score = 0.5 * s_whole + 0.5 * s_best_part
        order = np.argsort(-score, axis=1)
        for r, (mcq_id, _) in enumerate(rows):
            best = order[r, 0]
            top = score[r, best]
            gap = top - (score[r, order[r, 1]] if len(topics) > 1 else -1.0)
            t = topics[best]
            if top < min_sim:
                decide(mcq_id, 0, top, [], None)
                continue
            label = topic_label(exam_id, t["id"])
            if gap < margin:
                decide(mcq_id, 1, top, [(mcq_id, AXIS_REVIEW, label)], None)
                continue
            chosen = [(mcq_id, AXIS_TOPIC, label)]
            # Subtopic: the best part of this topic, if it clearly beats the topic's next part.
            idx = np.where(owner_arr == best)[0]
            cand = sorted(((s_parts[r, j], sub_names[j]) for j in idx if sub_names[j]), key=lambda x: -x[0])
            if cand and (len(cand) == 1 or cand[0][0] - cand[1][0] >= SUB_MARGIN):
                chosen.append((mcq_id, AXIS_SUBTOPIC, subtopic_label(exam_id, t["id"], cand[0][1])))
            decide(mcq_id, 2, top, chosen, t["id"])

    for mcq_id, (rank, _, rows, topic_id) in decided.items():
        rows_out.extend(rows)
        if rank == 0:
            stats.off_syllabus += 1
        elif rank == 1:
            stats.review += 1
        else:
            stats.confident += 1
            stats.per_topic[topic_id] = stats.per_topic.get(topic_id, 0) + 1
            stats.subtopics += sum(1 for r in rows if r[1] == AXIS_SUBTOPIC)
    stats.pool = len(decided) + stats.seeded

    if dry_run:
        return stats

    prefix = paper_prefix(exam_id, paper["id"])
    db.query(MCQTag).filter(MCQTag.axis.in_((AXIS_TOPIC, AXIS_SUBTOPIC, AXIS_REVIEW)),
                            MCQTag.label.like(prefix.replace("%", r"\%").replace("_", r"\_") + "%", escape="\\")
                            ).delete(synchronize_session=False)
    seen: set[tuple[int, str, str]] = set()
    batch = []
    for row in rows_out:
        if row in seen:
            continue
        seen.add(row)
        batch.append({"mcq_id": row[0], "axis": row[1], "label": row[2]})
    for i in range(0, len(batch), 5000):
        db.execute(MCQTag.__table__.insert(), batch[i:i + 5000])
    db.commit()
    return stats


def tag_new_questions(db: Session, exam_id: str, topic_id: str, mcq_ids: list[int],
                      subtopic: str | None = None) -> None:
    """Questions written for a topic (on-demand generation) belong to it: tag them directly."""
    for mid in mcq_ids:
        db.merge(MCQTag(mcq_id=mid, axis=AXIS_TOPIC, label=topic_label(exam_id, topic_id)))
        if subtopic:
            db.merge(MCQTag(mcq_id=mid, axis=AXIS_SUBTOPIC, label=subtopic_label(exam_id, topic_id, subtopic)))
    db.commit()


def topic_condition(exam_id: str, topic_id: str, subtopic: str | None = None):
    """SQL condition: questions tagged with this topic (or this subtopic of it)."""
    axis, label = (AXIS_SUBTOPIC, subtopic_label(exam_id, topic_id, subtopic)) if subtopic \
        else (AXIS_TOPIC, topic_label(exam_id, topic_id))
    return MCQ.id.in_(select(MCQTag.mcq_id).where(and_(MCQTag.axis == axis, MCQTag.label == label)))


def paper_tagged(db: Session, exam_id: str, paper_id: str) -> bool:
    """Has the tagger been run for this paper? (Without tags, counts are unknown, not zero.)"""
    prefix = paper_prefix(exam_id, paper_id)
    return db.query(MCQTag.mcq_id).filter(
        MCQTag.axis.in_((AXIS_TOPIC, AXIS_REVIEW)),
        MCQTag.label.like(prefix.replace("_", r"\_") + "%", escape="\\")).first() is not None


def no_topic_label(exam_id: str, paper_id: str) -> str:
    """AXIS_MANUAL label meaning "an admin ruled this question is not on this paper's syllabus"."""
    return paper_prefix(exam_id, paper_id) + "-"


def record_review(db: Session, exam_id: str, paper_id: str, mcq_id: int, topic_id: str | None,
                  subtopic: str | None = None) -> None:
    """Settle a close call: the chosen topic (or none) becomes a manual tag and the live tags follow it."""
    prefix_like = paper_prefix(exam_id, paper_id).replace("_", r"\_") + "%"
    db.query(MCQTag).filter(MCQTag.mcq_id == mcq_id,
                            MCQTag.axis.in_((AXIS_TOPIC, AXIS_SUBTOPIC, AXIS_REVIEW, AXIS_MANUAL)),
                            MCQTag.label.like(prefix_like, escape="\\")).delete(synchronize_session=False)
    if topic_id is None:
        db.add(MCQTag(mcq_id=mcq_id, axis=AXIS_MANUAL, label=no_topic_label(exam_id, paper_id)))
    else:
        db.add(MCQTag(mcq_id=mcq_id, axis=AXIS_MANUAL, label=topic_label(exam_id, topic_id)))
        db.add(MCQTag(mcq_id=mcq_id, axis=AXIS_TOPIC, label=topic_label(exam_id, topic_id)))
        if subtopic:
            db.add(MCQTag(mcq_id=mcq_id, axis=AXIS_SUBTOPIC, label=subtopic_label(exam_id, topic_id, subtopic)))
    db.commit()
