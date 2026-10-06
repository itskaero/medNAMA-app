"""Write the enriched PGET MCQs into a copy of the entry-test import template.

Fills columns A-Q (program .. keywords) of 'Questions Import' from row 3 down,
leaving the template's hidden helper formulas in T/U/V and its dropdown
validation untouched. Also writes a CSV of the same rows and a QA report.

Usage (from backend/):
    python scripts/pget_write_xlsx.py --records <enriched.json> --template <x.xlsx> \
        --out <filled.xlsx> --csv <rows.csv> --report <qa.csv>
"""

import argparse
import csv
import json
import warnings
from pathlib import Path

import openpyxl

warnings.filterwarnings("ignore", module="openpyxl")

HEADER_ROW = 2
FIRST_DATA_ROW = 3
COLUMNS = ["program", "subject", "topic", "sub_topic", "question", "option_1", "option_2",
           "option_3", "option_4", "correct_option", "difficulty_level", "cognitive_level",
           "explanation", "book_reference", "chapter_number", "page_number", "keywords"]
LETTERS = ["A", "B", "C", "D"]


def row_for(rec, program):
    """One template row. Five-option questions keep A-D; see --report for the note."""
    opts = dict(rec["options"])
    # before the enrichment pass only the source key exists
    key = rec.get("correct_option") or rec.get("correct")
    dropped = None
    if "E" in opts:
        # the template takes four options; drop E when it is not the key
        if key != "E":
            dropped = opts.pop("E")
        else:
            dropped = opts.pop("D")
            opts["D"], key = opts.pop("E"), "D"
    return {
        "program": program,
        "subject": rec.get("subject"),
        "topic": rec.get("topic"),
        "sub_topic": rec.get("sub_topic"),
        "question": rec.get("question"),
        "option_1": opts.get("A"),
        "option_2": opts.get("B"),
        "option_3": opts.get("C"),
        "option_4": opts.get("D"),
        "correct_option": f"option_{LETTERS.index(key) + 1}" if key in LETTERS else None,
        "difficulty_level": rec.get("difficulty_level"),
        "cognitive_level": rec.get("cognitive_level"),
        "explanation": rec.get("explanation"),
        "book_reference": rec.get("book_reference"),
        "chapter_number": rec.get("chapter_number"),
        "page_number": rec.get("page_number"),
        "keywords": rec.get("keywords"),
    }, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", required=True)
    ap.add_argument("--template", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()

    records = json.loads(Path(args.records).read_text(encoding="utf-8"))
    wb = openpyxl.load_workbook(args.template)
    ws = wb["Questions Import"]
    program = ws.cell(FIRST_DATA_ROW, 1).value or "Pediatric Medicine (PGET)-Paper II"

    rows, report = [], []
    for i, rec in enumerate(records):
        row, dropped = row_for(rec, program)
        rows.append(row)
        notes = []
        if dropped:
            notes.append(f"5th option dropped: {dropped}")
        if not row["correct_option"]:
            notes.append("NO KEY RESOLVED")
        key_source = rec.get("key_source") or ("source" if rec.get("correct") else "")
        if key_source.startswith("referee"):
            notes.append(f"key from textbooks ({key_source})")
        if key_source == "ai":
            notes.append(f"key decided by AI ({rec.get('key_confidence') or 'n/a'} confidence)")
        if rec.get("key_caveat"):
            notes.append("flawed question: " + rec["key_caveat"])
        if rec.get("referee", {}).get("agrees_with_key") is False:
            notes.append(f"referee disputes source key -> books say "
                         f"{rec['referee'].get('supported_option')}")
        if not row["sub_topic"]:
            notes.append("no sub_topic")
        if not row["book_reference"]:
            notes.append("no verified textbook citation")
        if rec.get("error"):
            notes.append("ERROR: " + rec["error"])
        if rec.get("review_notes"):
            notes.append(rec["review_notes"])
        ev = (rec.get("evidence") or [{}])[0]
        report.append({
            "row": FIRST_DATA_ROW + i, "n": rec.get("n"), "origin": rec.get("origin"),
            "src_line": rec.get("line"),
            "subject": row["subject"], "topic": row["topic"], "sub_topic": row["sub_topic"],
            "key": row["correct_option"], "key_source": key_source or None,
            "verdict": rec.get("referee", {}).get("verdict"),
            "book": row["book_reference"], "page": row["page_number"],
            "explanation_chars": len(row["explanation"] or ""),
            "quote": (ev.get("quote") or "")[:160],
            "notes": "; ".join(notes),
        })

    # clear any previous data in A..Q, then write
    for r in range(FIRST_DATA_ROW, ws.max_row + 1):
        for c in range(1, len(COLUMNS) + 1):
            ws.cell(r, c).value = None
    for i, row in enumerate(rows):
        for c, name in enumerate(COLUMNS, 1):
            ws.cell(FIRST_DATA_ROW + i, c).value = row[name]

    wb.save(args.out)
    with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    with open(args.report, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(report[0]))
        w.writeheader()
        w.writerows(report)

    flagged = sum(1 for r in report if r["notes"])
    print(f"{len(rows)} rows -> {args.out}")
    print(f"  no key        : {sum(1 for r in report if not r['key'])}")
    print(f"  no sub_topic  : {sum(1 for r in report if not r['sub_topic'])}")
    print(f"  no citation   : {sum(1 for r in report if not r['book'])}")
    print(f"  longest expl. : {max(r['explanation_chars'] for r in report)} chars")
    print(f"  flagged rows  : {flagged} (see {args.report})")


if __name__ == "__main__":
    main()
