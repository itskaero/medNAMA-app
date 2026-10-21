"""Curated Tables of Specifications (data/pget_tos.json, data/fcps_part1_tos.json): the source of truth for the tree.

The page reader (tos_parse.py) transcribes scanned pages with a vision model and needs checking; these files were
prepared by hand from the same documents, so when one exists its document's tree is built from it instead. Counts
come only from the file (`total_mcqs`, `mcq_count`); nothing is computed. Nodes keep their ids where the heading
path matches the previous tree, so jobs, questions and notes stay attached (tos_parse.build_tree).

    pget_tos.json        {"examination", "specifications": [{candidate_stream?, paper, specialty_name?, total_mcqs,
                          subjects: [{subject_name, mcq_count, topics: [{topic_name, mcq_count?, subtopics: [str]}]}]}]}
    fcps_part1_tos.json  {"specialties": [{specialty_name, syllabus: {paper_1|paper_2: {subjects: [{subject_name,
                          sub_subjects: [{sub_subject_name, topics: [str]}]}]}}}]}   (no MCQ counts: CPSP prints none)

Imported automatically on start-up whenever a file is newer than the last import; also from the explorer.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

from . import settings
from .common import audit, session
from .tos_parse import _key, build_tree

from app.seed_models import SeedTosDocument  # noqa: E402

logger = logging.getLogger("seed_studio.tos_json")

PGET_FILE = settings.DATA / "pget_tos.json"
FCPS_FILE = settings.DATA / "fcps_part1_tos.json"
MBBS_FILE = settings.DATA / "mbbs_tos.json"
MAX_NAME = 1200   # the heading is split from its items before it becomes a node name


def _count(v: Any) -> int | None:
    try:
        return int(str(v).strip()) if v is not None and str(v).strip() != "" else None
    except ValueError:
        return None


def _name(v: Any) -> str | None:
    s = " ".join(str(v or "").split())
    return s[:MAX_NAME] or None


def split_heading(text: str) -> tuple[str, str | None]:
    """"Sensory & Motor: Cervical plexus, Brachial plexus, ..." -> ("Sensory & Motor", "Cervical plexus, Brachial plexus, ...").

    The source lists items as bullets under a heading; the heading is the leaf (one revision note), the items are its
    detail (questions spread across them). Text with no heading before a colon stays whole."""
    head, sep, rest = text.partition(":")
    head, rest = head.strip(), rest.strip()
    if sep and head and rest and len(head) <= 90:
        return head, rest
    return text, None


def pget_rows(data: dict) -> list[tuple[None, dict]]:
    rows: list[tuple[None, dict]] = []
    for spec in data.get("specifications") or []:
        raw_paper = str(spec.get("paper") or "").replace("-", " ").strip()
        numeral = "II" if raw_paper.upper().endswith("II") else "I"
        stream = spec.get("candidate_stream")
        paper = f"Paper {numeral}" + (f" ({stream})" if numeral == "I" and stream else "")
        specialty = _name(spec.get("specialty_name")) if numeral == "II" else None
        base = {"paper": paper, "specialty": specialty}
        total = _count(spec.get("total_mcqs"))
        if total is not None:
            rows.append((None, {**base, "count": total, "count_level": "specialty" if specialty else "paper"}))
        for subj in spec.get("subjects") or []:
            subject = _name(subj.get("subject_name"))
            if not subject:
                continue
            rows.append((None, {**base, "subject": subject, "count": _count(subj.get("mcq_count")), "count_level": "subject"}))
            for top in subj.get("topics") or []:
                topic = _name(top.get("topic_name"))
                if not topic:
                    continue
                rows.append((None, {**base, "subject": subject, "topic": topic,
                                    "count": _count(top.get("mcq_count")), "count_level": "topic"}))
                for st in top.get("subtopics") or []:
                    if _name(st):
                        head, detail = split_heading(_name(st))
                        rows.append((None, {**base, "subject": subject, "topic": topic, "subtopic": head, "detail": detail}))
    return rows


def fcps_rows(spec: dict) -> list[tuple[None, dict]]:
    rows: list[tuple[None, dict]] = []
    syllabus = spec.get("syllabus") or {}
    for key, paper in (("paper_1", "Paper I"), ("paper_2", "Paper II")):
        for subj in (syllabus.get(key) or {}).get("subjects") or []:
            subject = _name(subj.get("subject_name"))
            if not subject:
                continue
            rows.append((None, {"paper": paper, "subject": subject}))
            for sub in subj.get("sub_subjects") or []:
                topic = _name(sub.get("sub_subject_name"))
                if not topic:
                    continue
                rows.append((None, {"paper": paper, "subject": subject, "topic": topic}))
                for t in sub.get("topics") or []:
                    if _name(t):
                        head, detail = split_heading(_name(t))
                        rows.append((None, {"paper": paper, "subject": subject, "topic": topic, "subtopic": head,
                                            "detail": detail}))
    return rows


def mbbs_rows(data: dict) -> list[tuple[None, dict]]:
    rows: list[tuple[None, dict]] = []
    years = data.get("years") or []
    for y in years:
        year_name = _name(y.get("academic_year")) or "Year"
        # 1. Modular Blocks (Years 1 to 4)
        for b in y.get("blocks") or []:
            b_id = b.get("block_id") or "Block"
            modules = ", ".join(b.get("modules") or [])
            paper_name = f"{year_name}: {b_id}" + (f" ({modules})" if modules else "")
            base = {"paper": paper_name}
            t_mcqs = _count(b.get("written_exam", {}).get("total_mcqs"))
            if t_mcqs is not None:
                rows.append((None, {**base, "count": t_mcqs, "count_level": "paper"}))
            for subj in b.get("subjects") or []:
                s_name = _name(subj.get("subject_name"))
                if not s_name:
                    continue
                s_count = _count(subj.get("mcq_count"))
                rows.append((None, {**base, "subject": s_name, "count": s_count, "count_level": "subject"}))
                for top in subj.get("topics") or []:
                    t_name = _name(top.get("topic_name"))
                    if not t_name:
                        continue
                    rows.append((None, {**base, "subject": s_name, "topic": t_name}))
                    for st in top.get("subtopics") or []:
                        if _name(st):
                            head, detail = split_heading(_name(st))
                            rows.append((None, {**base, "subject": s_name, "topic": t_name, "subtopic": head, "detail": detail}))

        # 2. Clerkships (Year 5)
        for c in y.get("clerkships") or []:
            sp_name = _name(c.get("specialty")) or "Clerkship"
            paper_name = f"{year_name}: {sp_name}"
            base = {"paper": paper_name}
            w_exam = c.get("written_exam") or {}
            t_mcqs = 0
            for pk, pv in w_exam.items():
                if isinstance(pv, dict) and "mcq_count" in pv:
                    t_mcqs += pv["mcq_count"]
            if t_mcqs > 0:
                rows.append((None, {**base, "count": t_mcqs, "count_level": "paper"}))
            rows.append((None, {**base, "subject": sp_name, "count": t_mcqs if t_mcqs > 0 else None, "count_level": "subject"}))
            for top in c.get("topics") or []:
                t_name = _name(top.get("topic_name"))
                if not t_name:
                    continue
                rows.append((None, {**base, "subject": sp_name, "topic": t_name}))
                for st in top.get("subtopics") or []:
                    if _name(st):
                        head, detail = split_heading(_name(st))
                        rows.append((None, {**base, "subject": sp_name, "topic": t_name, "subtopic": head, "detail": detail}))
    return rows


def _by_word(docs: dict, name: str):
    """"Otorhinolaryngology (E.N.T)" vs "ENT (Otorhinolaryngology)": match on a shared distinctive word."""
    words = {w for w in _key(name).split() if len(w) >= 6}
    hits = [d for k, d in docs.items() if words & set(k.split())]
    return hits[0] if len(hits) == 1 else None


def _apply(db, doc: SeedTosDocument, rows: list, source: str, actor: str) -> int:
    n = build_tree(db, doc, rows)
    doc.parse_status, doc.parse_error = "parsed", None
    doc.parse_method = f"curated JSON ({source})"
    doc.parsed_at = datetime.utcnow()
    audit(db, actor, "tos_import_json", "document", doc.id, source=source, nodes=n)
    db.commit()
    return n


def import_all(actor: str = "system", force: bool = False) -> dict[str, Any]:
    """Build every document that has a curated file. Skips a document whose last import is newer than its file
    (unless `force`). Returns what was imported."""
    out: dict[str, Any] = {"imported": [], "skipped": [], "unmatched": []}
    with session() as db:
        if PGET_FILE.exists():
            mtime = datetime.utcfromtimestamp(os.path.getmtime(PGET_FILE))
            doc = db.query(SeedTosDocument).filter(SeedTosDocument.family == "PGET").order_by(SeedTosDocument.id).first()
            if doc is None:
                out["unmatched"].append("PGET: no PGET document registered")
            elif not force and (doc.parse_method or "").startswith("curated") and doc.parsed_at and doc.parsed_at >= mtime:
                out["skipped"].append(doc.title)
            else:
                data = json.loads(PGET_FILE.read_text(encoding="utf-8"))
                n = _apply(db, doc, pget_rows(data), PGET_FILE.name, actor)
                out["imported"].append({"document": doc.title, "nodes": n})
        if FCPS_FILE.exists():
            mtime = datetime.utcfromtimestamp(os.path.getmtime(FCPS_FILE))
            data = json.loads(FCPS_FILE.read_text(encoding="utf-8"))
            docs = {_key(d.title.split(": ")[-1]): d for d in db.query(SeedTosDocument).filter(SeedTosDocument.family == "FCPS")}
            for spec in data.get("specialties") or []:
                name = _name(spec.get("specialty_name")) or ""
                doc = (docs.get(_key(name))
                       or next((d for k, d in docs.items() if _key(name) in k or k in _key(name)), None)
                       or _by_word(docs, name))
                if doc is None:
                    out["unmatched"].append(f"FCPS: {name}")
                    continue
                if not force and (doc.parse_method or "").startswith("curated") and doc.parsed_at and doc.parsed_at >= mtime:
                    out["skipped"].append(doc.title)
                    continue
                n = _apply(db, doc, fcps_rows(spec), FCPS_FILE.name, actor)
                out["imported"].append({"document": doc.title, "nodes": n})
        if MBBS_FILE.exists():
            mtime = datetime.utcfromtimestamp(os.path.getmtime(MBBS_FILE))
            doc = db.query(SeedTosDocument).filter(SeedTosDocument.family == "MBBS").order_by(SeedTosDocument.id).first()
            if doc is None:
                doc = SeedTosDocument(
                    family="MBBS",
                    title="UHS Modular Integrated Curriculum 2K23 (MBBS)",
                    source_path="data/mbbs_tos.json",
                    source_url=None,
                    version="2K23",
                    has_counts=True,
                    page_count=5,
                    multiplier=5.0,
                    base_per_topic=2,
                    parse_status="not_parsed",
                )
                db.add(doc)
                db.flush()
            if not force and (doc.parse_method or "").startswith("curated") and doc.parsed_at and doc.parsed_at >= mtime:
                out["skipped"].append(doc.title)
            else:
                data = json.loads(MBBS_FILE.read_text(encoding="utf-8"))
                n = _apply(db, doc, mbbs_rows(data), MBBS_FILE.name, actor)
                out["imported"].append({"document": doc.title, "nodes": n})
    return out
