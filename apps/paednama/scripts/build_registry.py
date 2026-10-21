"""Build data/cpsp_paeds.json from the CPSP Paediatrics curriculum PDF.

    python apps/paednama/scripts/build_registry.py [path/to/prospectus.pdf]

The exam components (papers, TOACS, long and short cases) are written out below with the page each fact comes from;
the syllabus, the Mini-CEX topics and the DOPS procedures are parsed from the PDF so the registry can be rebuilt when
CPSP revises the curriculum. Nothing here is invented: a fact the curriculum does not state is not in the file.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
DEFAULT_PDF = REPO / "pdfs" / "paeds" / "prospectus.pdf"
OUT = ROOT / "data" / "cpsp_paeds.json"

BULLET = "•"
# Words the PDF's text layer splits or mis-encodes.
FIXES = {"ELECTROL YTES": "ELECTROLYTES", "HEMATOLOGY ,": "HEMATOLOGY,", "’": "'", "�": "'"}
# Two topics the PDF prints on one line without a bullet between them.
SPLITS = {"Neonatal resuscitation and delivery room emergencies neonatal sepsis (early and late onset)":
          ["Neonatal resuscitation and delivery room emergencies", "Neonatal sepsis (early and late onset)"]}
CONTINUES = (",", "&", "-", "/", " and", " of", " in", " with", " the", " for", " to", " or")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80]


def fix(s: str) -> str:
    for a, b in FIXES.items():
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def pages(pdf: Path) -> list[tuple[int, list[str]]]:
    """(printed page number, text lines) per PDF page, with the running header removed."""
    out = []
    for p in PdfReader(str(pdf)).pages:
        lines = (p.extract_text() or "").splitlines()
        printed = None
        if lines and lines[0].strip() == "PAEDIATRICS":
            lines = lines[1:]
            if lines and lines[0].strip().isdigit():
                printed = int(lines[0].strip())
                lines = lines[1:]
        out.append((printed, lines))
    return out


def is_heading(line: str) -> bool:
    s = line.strip()
    letters = [c for c in s if c.isalpha()]
    return (not s.startswith(BULLET) and len(letters) >= 4 and all(c.isupper() for c in letters)
            and not s.startswith(("[", "(")))


def section(all_pages, start_marker: str, stop_marker: str, occurrence: int) -> list[tuple[int, str]]:
    """Lines (with their printed page) between the n-th start marker and the next stop marker."""
    seen, on, out = 0, False, []
    for printed, lines in all_pages:
        for line in lines:
            s = line.strip()
            if not on and s.startswith(start_marker):
                seen += 1
                on = seen == occurrence
                continue
            if on and s.startswith(stop_marker):
                return out
            if on and s:
                out.append((printed, line))
    return out


def parse_syllabus(lines: list[tuple[int, str]]) -> list[dict]:
    systems: list[dict] = []
    group: str | None = None
    last: dict | None = None            # the item the next continuation line belongs to
    pending_heading: list[str] = []
    for printed, raw in lines:
        s = fix(raw)
        if is_heading(raw):
            unfinished = re.search(r"(\b(IN|AND|OF|THE|FOR|TO)|[&,/-])$", systems[-1]["system"]) if systems else None
            if pending_heading and not systems[-1]["topics"] and unfinished:
                systems[-1]["system"] = fix(systems[-1]["system"] + " " + s)      # a heading wrapped onto 2 lines
                systems[-1]["id"] = slug(systems[-1]["system"])
                continue
            systems.append({"id": slug(s), "system": s, "page": printed, "topics": []})
            pending_heading, group, last = [s], None, None
            continue
        if not systems:
            continue
        pending_heading = []
        sub = raw.startswith(" ") and raw.lstrip().startswith(BULLET)
        if s.startswith(BULLET):
            text = s.lstrip(BULLET).strip()
            if sub and systems[-1]["topics"]:
                parent = systems[-1]["topics"][-1]
                parent["subtopics"].append(text)
                last = {"kind": "sub", "topic": parent}
            else:
                t = {"id": "", "name": text, "subtopics": [], "page": printed}
                if group:
                    t["group"] = group
                systems[-1]["topics"].append(t)
                last = {"kind": "topic", "topic": t}
            continue
        # A line without a bullet: the rest of the previous item, or a sub-heading such as "Respiratory Intensive Care".
        prev = (last["topic"]["subtopics"][-1] if last and last["kind"] == "sub" else
                last["topic"]["name"] if last else "")
        if last and (s[:1].islower() or s[:1] in "([" or prev.endswith(CONTINUES) or prev.count("(") > prev.count(")")):
            if last["kind"] == "sub":
                last["topic"]["subtopics"][-1] = fix(prev + " " + s)
            else:
                last["topic"]["name"] = fix(prev + " " + s)
            continue
        group, last = s.rstrip(":").strip(), None
    # Split run-together topics, number the ids, drop empty systems (a heading the PDF prints with no list under it).
    for sysm in systems:
        if not sysm["topics"]:              # a heading the curriculum lists with nothing under it is one topic itself
            sysm["topics"] = [{"id": "", "name": sysm["system"].capitalize(), "subtopics": [], "page": sysm["page"],
                               "whole_system": True}]
        fixed = []
        for t in sysm["topics"]:
            for name in SPLITS.get(t["name"], [t["name"]]):
                fixed.append({**t, "name": name})
        seen: dict[str, int] = {}
        for t in fixed:
            base = f"{sysm['id']}/{slug(t['name'])}"
            seen[base] = seen.get(base, 0) + 1
            t["id"] = base if seen[base] == 1 else f"{base}-{seen[base]}"
        sysm["topics"] = fixed
    return systems


def parse_bullets(lines: list[tuple[int, str]]) -> list[str]:
    items: list[str] = []
    for _, raw in lines:
        s = fix(raw)
        if s.startswith(BULLET):
            items.append(s.lstrip(BULLET).strip())
        elif items and (s[:1].islower() or items[-1].endswith(CONTINUES)):
            items[-1] = fix(items[-1] + " " + s)
    out, seen = [], set()
    for i in items:                                   # the DOPS list repeats some procedures
        k = i.lower()
        if k not in seen:
            seen.add(k)
            out.append(i)
    return out


TOACS = {"stations": "12 to 18", "changeover_minutes": 1, "all_interactive": True,
         "tasks": ["taking history", "performing clinical examination", "counselling", "assembling an instrument",
                   "any other task"],
         "examiner": "One examiner per station rates the performance and asks questions testing critical thinking "
                     "and problem-solving skills."}


def build(pdf: Path) -> dict:
    all_pages = pages(pdf)
    imm_syl = section(all_pages, "SYLLABUS", "CLINICAL AND PROCEDURAL COMPETENCIES", 1)
    f2_syl = section(all_pages, "SYLLABUS", "CLINICAL AND PROCEDURAL COMPETENCIES", 2)
    imm_cex = section(all_pages, "Topics for Mini-CEX", "Direct Observation of Procedural Skills", 1)
    f2_cex = section(all_pages, "Topics for Mini-CEX", "Direct Observation of Procedural Skills", 2)
    imm_dops = section(all_pages, "Topics", "SUMMATIVE ASSESSMENT", 2)        # "Topics' List for DOPS" (2nd "Topics")
    f2_dops = section(all_pages, "Topics", "SUMMATIVE ASSESSMENT", 4)
    return {
        "source": "College of Physicians and Surgeons Pakistan: Paediatrics curriculum (total duration of training "
                  "4 years), applicable to batches entering the Paediatrics training program in July 2026 and onwards",
        "source_file": "pdfs/paeds/prospectus.pdf",
        "note": "Page numbers are the curriculum's printed page numbers. CPSP notifies changes before each "
                "examination; check the current notification.",
        "stages": {
            "imm": {
                "title": "Intermediate Module (IMM)",
                "training_years": "1-2",
                "eligibility": {"page": 66, "items": [
                    "Passed FCPS Part-I in Medicine and Allied or granted exemption",
                    "Registered with the Research & Training Monitoring Cell (RTMC)",
                    "Completed two years of training under an approved supervisor in a CPSP-recognised institution",
                    "Completed and validated e-logbook", "Certificates of attendance of mandatory workshops",
                    "Submitted synopsis for one research paper"]},
                "theory": {"page": 67, "papers": [
                    {"name": "Paper-I", "format": "100 Single Best Type MCQs", "questions": 100},
                    {"name": "Paper-II", "format": "100 Single Best Type MCQs", "questions": 100}],
                    "gate": "Only candidates who qualify in the theory are eligible to take TOACS"},
                "clinical": [{"component": "TOACS", "page": 68, **TOACS}],
                "syllabus": parse_syllabus(imm_syl),
                "mini_cex": {"page": 61, "topics": parse_bullets(imm_cex)},
                "dops": {"page": 64, "procedures": parse_bullets(imm_dops)},
            },
            "fcps2": {
                "title": "Post-Intermediate Module (FCPS-II)",
                "training_years": "3-4",
                "rotations": {"page": 70, "items": [
                    {"name": "General Paediatrics (inpatient and outpatient)", "months": 10},
                    {"name": "Neonatology / Nursery / Neonatal Paediatrics (including Perinatology)", "months": 4},
                    {"name": "Emergency Medicine / Casualty", "months": 2},
                    {"name": "Paediatric Critical Care Medicine / ICU", "months": 2},
                    {"name": "Three sub-specialties of choice", "months": 2, "each": True}]},
                "eligibility": {"page": 121, "items": [
                    "Passed FCPS Part-I in Medicine and Allied, or official exemption",
                    "Two years of the specified training in Paediatrics under an approved supervisor",
                    "Certificate of having passed the Intermediate Module Examination in Paediatrics",
                    "Regular entries and completed e-logbook", "Certificate of attendance of mandatory workshops",
                    "Evidence of publication of one research paper in a CPSP-approved journal"]},
                "theory": {"page": 122, "papers": [
                    {"name": "Paper-I", "format": "100 Single Best Type MCQs", "questions": 100},
                    {"name": "Paper-II", "format": "100 Single Best Type MCQs", "questions": 100}],
                    "gate": "Only those who pass TOACS go on to the remaining clinical components"},
                "clinical": [
                    {"component": "TOACS", "page": 123, **TOACS},
                    {"component": "Long case", "page": 123, "count": 1, "minutes": 60,
                     "parts": ["30 minutes history taking and clinical examination, observed by a pair of examiners",
                               "30 minutes case presentation and discussion with the pair of examiners"],
                     "assessed_on": {
                         "Interviewing skills": ["Introduces oneself; listens patiently and is polite",
                                                 "Extracts relevant information"],
                         "Clinical examination skills": ["Takes informed consent",
                                                         "Uses correct clinical methods systematically, with appropriate "
                                                         "exposure and re-draping"],
                         "Case presentation / discussion": [
                             "Presents skillfully", "Gives correct findings",
                             "Gives logical interpretations of findings and discusses the differential diagnosis",
                             "Enumerates and justifies relevant investigations",
                             "Outlines and justifies the treatment plan, including rehabilitation",
                             "Discusses prevention and prognosis",
                             "Has knowledge of recent advances relevant to the case"]},
                     "note": "Candidates may ask for laboratory investigations; a relevant suggestion earns credit "
                             "even when the result is not available (p.124)"},
                    {"component": "Short cases", "page": 124, "count": "at least 4", "minutes": 40,
                     "assessed_on": {
                         "Clinical examination skills": ["Takes informed consent", "Uses correct clinical methods",
                                                         "Examines systematically"],
                         "Discussion": ["Gives correct findings", "Gives logical interpretations of findings",
                                        "Justifies diagnosis/es"]},
                     "note": "Answers should be precise, succinct and relevant to the patient under discussion"}],
                "syllabus": parse_syllabus(f2_syl),
                "mini_cex": {"page": 116, "topics": parse_bullets(f2_cex)},
                "dops": {"page": 118, "procedures": parse_bullets(f2_dops)},
            },
        },
    }


def main() -> None:
    logging.disable(logging.CRITICAL)                 # pypdf warns about the PDF's fonts
    pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PDF
    reg = build(pdf)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(reg, indent=1, ensure_ascii=False), encoding="utf-8")
    for key, st in reg["stages"].items():
        topics = sum(len(s["topics"]) for s in st["syllabus"])
        print(f"{key}: {len(st['syllabus'])} systems, {topics} topics, "
              f"{len(st['mini_cex']['topics'])} Mini-CEX, {len(st['dops']['procedures'])} DOPS")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
