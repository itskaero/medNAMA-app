"""Re-ingesting a book without breaking what points into it.

A book's passages (chunks) and figures get new ids when the book is ingested again. Three things
refer to them by id:

    concept_cards.chunk_id / figure_id   the passage and figure a concept card quotes (FK, SET NULL)
    mcqs.source_chunk_ids                the passages an AI-written question was grounded on (JSON list)
    mcqs.figure_id                       a question's figure (FK, SET NULL)

`snapshot()` records those references and what each referenced chunk/figure looked like (page and
text) before the old rows are deleted. `remap()` then finds each one's successor in the new rows,
by page (the same page or one either side) and by word overlap, and points the references at it.
The old -> new map is saved so the same remap can run on the NAS after the book is copied there
(scripts/books_transfer.py).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

MAP_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "reingest"
_WORD = re.compile(r"[a-z0-9]+")


def _words(s: str | None, limit: int = 80) -> set[str]:
    body = s or ""
    if body.startswith("Textbook:"):                      # child chunks start with a context line
        nl = body.find("\n")
        body = body[nl + 1:] if nl >= 0 else ""
    return set(_WORD.findall(body.lower())[:limit])


def snapshot(db, book_id: int) -> dict[str, Any]:
    """References into this book's chunks and figures, and what they pointed at."""
    cards = db.execute(text(
        "SELECT cc.id, cc.chunk_id, cc.figure_id FROM concept_cards cc "
        "LEFT JOIN chunks c ON c.id = cc.chunk_id LEFT JOIN figures f ON f.id = cc.figure_id "
        "WHERE c.book_id = :b OR f.book_id = :b"), {"b": book_id}).fetchall()
    mcq_figs = db.execute(text(
        "SELECT m.id, m.figure_id FROM mcqs m JOIN figures f ON f.id = m.figure_id WHERE f.book_id = :b"),
        {"b": book_id}).fetchall()
    src_rows = db.execute(text(
        # CASE, not AND: Postgres may evaluate jsonb_array_length first, and it fails on a scalar value.
        "SELECT id, source_chunk_ids FROM mcqs WHERE CASE WHEN jsonb_typeof(source_chunk_ids) = 'array' "
        "THEN jsonb_array_length(source_chunk_ids) > 0 ELSE false END")).fetchall()
    book_chunk_ids = {i for (i,) in db.execute(text("SELECT id FROM chunks WHERE book_id = :b"), {"b": book_id})}
    src = [(mid, [int(x) for x in ids if isinstance(x, (int, float)) or str(x).isdigit()])
           for mid, ids in src_rows]
    src = [(mid, ids) for mid, ids in src if any(i in book_chunk_ids for i in ids)]

    wanted_chunks = {c for _, c, _ in cards if c} | {i for _, ids in src for i in ids if i in book_chunk_ids}
    wanted_figs = {f for _, _, f in cards if f} | {f for _, f in mcq_figs if f}
    chunks = {}
    if wanted_chunks:
        for cid, page, content, parent in db.execute(text(
                "SELECT id, page_number, content, parent_id FROM chunks WHERE id = ANY(:ids)"),
                {"ids": list(wanted_chunks)}):
            chunks[cid] = {"page": page, "text": content, "child": parent is not None}
    figs = {}
    if wanted_figs:
        for fid, page, label, caption in db.execute(text(
                "SELECT id, page_number, figure_label, caption FROM figures WHERE id = ANY(:ids) AND book_id = :b"),
                {"ids": list(wanted_figs), "b": book_id}):
            figs[fid] = {"page": page, "label": label, "caption": caption}
    snap = {"book_id": book_id, "chunks": chunks, "figures": figs,
            "cards": [[i, c, f] for i, c, f in cards], "mcq_figures": [[i, f] for i, f in mcq_figs],
            "mcq_sources": [[i, ids] for i, ids in src]}
    logger.info(f"Re-ingest snapshot for book {book_id}: {len(chunks)} cited passages, {len(figs)} figures, "
                f"{len(cards)} concept cards, {len(src)} grounded MCQs")
    return snap


def _snap_path(book_id: int) -> Path:
    return MAP_DIR / f"book-{book_id}-snapshot.json"


def save_snapshot(snap: dict) -> None:
    """Kept on disk until the remap has run, so an interrupted re-ingest can still remap when it resumes."""
    MAP_DIR.mkdir(parents=True, exist_ok=True)
    _snap_path(snap["book_id"]).write_text(json.dumps(snap), encoding="utf-8")


def load_snapshot(book_id: int) -> dict | None:
    p = _snap_path(book_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def clear_book(db, book_id: int) -> None:
    """Delete a book's passages and figures (the books row, and its id, stay)."""
    db.execute(text("DELETE FROM figures WHERE book_id = :b"), {"b": book_id})
    db.execute(text("DELETE FROM chunks WHERE book_id = :b AND parent_id IS NOT NULL"), {"b": book_id})
    db.execute(text("DELETE FROM chunks WHERE book_id = :b"), {"b": book_id})
    db.commit()


def match_chunks(db, book_id: int, old: dict) -> dict[int, int]:
    """old chunk id -> the new chunk on the same page (+-1) with the most words in common."""
    if not old:
        return {}
    pages = {p + d for v in old.values() if v.get("page") for p in [v["page"]] for d in (-1, 0, 1)}
    by_page: dict[int, list[tuple[int, bool, set[str]]]] = {}
    for cid, page, content, parent in db.execute(text(
            "SELECT id, page_number, content, parent_id FROM chunks WHERE book_id = :b AND page_number = ANY(:p)"),
            {"b": book_id, "p": list(pages)}):
        by_page.setdefault(page, []).append((cid, parent is not None, _words(content)))
    out: dict[int, int] = {}
    for oid, v in old.items():
        want = _words(v.get("text"))
        if not want or not v.get("page"):
            continue
        best, best_score = None, 0.0
        for d in (0, -1, 1):
            for cid, is_child, words in by_page.get(v["page"] + d, []):
                if not words:
                    continue
                score = len(want & words) / len(want | words)
                score *= 1.0 if d == 0 else 0.9
                score *= 1.0 if is_child == v.get("child", True) else 0.85
                if score > best_score:
                    best, best_score = cid, score
        if best is not None and best_score >= 0.3:
            out[int(oid)] = best
    return out


def match_figures(db, book_id: int, old: dict) -> dict[int, int]:
    """old figure id -> new figure with the same printed label, else the same page and caption."""
    if not old:
        return {}
    rows = db.execute(text("SELECT id, page_number, figure_label, caption FROM figures WHERE book_id = :b"),
                      {"b": book_id}).fetchall()
    out: dict[int, int] = {}
    for oid, v in old.items():
        cands = [r for r in rows if r.page_number is not None and v.get("page") is not None
                 and abs(r.page_number - v["page"]) <= 1]
        exact = [r for r in cands if r.figure_label == v.get("label")]
        if exact:
            out[int(oid)] = exact[0].id
            continue
        want = _words(v.get("caption"))
        scored = sorted(((len(want & _words(r.caption)) / max(1, len(want | _words(r.caption))), r.id)
                         for r in cands), reverse=True)
        if scored and (scored[0][0] >= 0.4 or (not want and len(cands) == 1)):
            out[int(oid)] = scored[0][1]
    return out


def apply_map(db, snap: dict, chunk_map: dict[int, int], fig_map: dict[int, int]) -> dict[str, int]:
    """Point the recorded references at the new rows. Unmatched references are dropped (set to null)."""
    stats = {"cards": 0, "mcq_sources": 0, "mcq_figures": 0}
    cm = {int(k): int(v) for k, v in chunk_map.items()}
    fm = {int(k): int(v) for k, v in fig_map.items()}
    for card_id, old_chunk, old_fig in snap["cards"]:
        new_chunk = cm.get(int(old_chunk)) if old_chunk else None
        new_fig = fm.get(int(old_fig)) if old_fig else None
        db.execute(text("UPDATE concept_cards SET chunk_id = :c, figure_id = COALESCE(:f, figure_id) WHERE id = :i"),
                   {"c": new_chunk, "f": new_fig, "i": card_id})
        stats["cards"] += 1
    for mcq_id, ids in snap["mcq_sources"]:
        new_ids = [cm.get(int(i), int(i)) for i in ids]
        new_ids = [i for i in new_ids if i not in {int(k) for k in snap["chunks"]} or i in cm.values()]
        db.execute(text("UPDATE mcqs SET source_chunk_ids = CAST(:j AS jsonb) WHERE id = :i"),
                   {"j": json.dumps(new_ids), "i": mcq_id})
        stats["mcq_sources"] += 1
    for mcq_id, old_fig in snap["mcq_figures"]:
        db.execute(text("UPDATE mcqs SET figure_id = :f WHERE id = :i"), {"f": fm.get(int(old_fig)), "i": mcq_id})
        stats["mcq_figures"] += 1
    db.commit()
    return stats


def remap(db, book_id: int, snap: dict) -> dict[str, Any]:
    """After the new rows exist: match, apply, and save the map for the NAS."""
    chunk_map = match_chunks(db, book_id, {int(k): v for k, v in snap["chunks"].items()})
    fig_map = match_figures(db, book_id, {int(k): v for k, v in snap["figures"].items()})
    stats = apply_map(db, snap, chunk_map, fig_map)
    MAP_DIR.mkdir(parents=True, exist_ok=True)
    out = {"book_id": book_id, "chunks": chunk_map, "figures": fig_map,
           "old_chunks": snap["chunks"], "old_figures": snap["figures"]}
    (MAP_DIR / f"book-{book_id}.json").write_text(json.dumps(out), encoding="utf-8")
    _snap_path(book_id).unlink(missing_ok=True)
    logger.info(f"Re-ingest remap for book {book_id}: {len(chunk_map)}/{len(snap['chunks'])} passages, "
                f"{len(fig_map)}/{len(snap['figures'])} figures matched; updated {stats}")
    return {"matched_chunks": len(chunk_map), "cited_chunks": len(snap["chunks"]),
            "matched_figures": len(fig_map), "cited_figures": len(snap["figures"]), **stats}
