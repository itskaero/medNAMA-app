"""One topic list for FCPS Part 1 practice: Subject -> Topic, shared by Practice and Past papers.

The question sources name topics differently. The past-paper archives tag "Head and Neck Anatomy", "Head &
Neck", "C.V.S", "GIT"; the Paper 1 bank has its own "Cardiac and Renal Pharma", "Inflammation and Repair";
some labels are really a whole subject ("Pharmacology", "Biochemistry"). This module is the canonical list:
each topic names the source labels it absorbs, keyed by subject, because the same label means different
things in different subjects ("Genetics" is Genetic Disorders in Pathology but Molecular Genetics in
Biochemistry, "Cardiovascular" is a physiology or a pathology topic).

Questions are tagged with it by scripts/tag_taxonomy.py (axes `fcps_subject`, `fcps_topic`); a question no
source label places is placed by its most similar placed questions in the same subject.
Paper 2 (faculty) questions keep their faculty and the bank's topics: they are organised by faculty.
"""

from __future__ import annotations

# subject -> [(topic, [source labels it absorbs])]. Order is the order shown to students.
TAXONOMY: dict[str, list[tuple[str, list[str]]]] = {
    "Anatomy": [
        ("Upper Limb", ["Upper Limb"]),
        ("Lower Limb", ["Lower Limb"]),
        ("Thorax", ["Thorax"]),
        ("Abdomen", ["Abdomen"]),
        ("Pelvis & Perineum", ["Pelvis and Perineum"]),
        ("Head & Neck", ["Head and Neck Anatomy", "Head & Neck"]),
        ("Back & Vertebral Column", ["Back and Vertebral Column"]),
        ("Embryology", ["Embryology"]),
        ("Histology", ["Histology"]),
        ("General Anatomy", ["General Anatomy", "Systems"]),
    ],
    "Physiology": [
        ("Cell Physiology", ["Cell Physiology"]),
        ("Cardiovascular", ["Cardiovascular", "C.V.S"]),
        ("Respiratory", ["Respiratory"]),
        ("Renal", ["Renal"]),
        ("Gastrointestinal", ["Gastrointestinal", "GIT"]),
        ("Endocrine", ["Endocrinology"]),
        ("Reproduction", ["Reproduction", "Pregnancy"]),
    ],
    "Biochemistry": [
        ("Proteins & Enzymes", ["Amino Acids, Proteins and Enzymes"]),
        ("Energy Metabolism", ["Overview of Energy Metabolism"]),
        ("Hormones & Signalling", ["Hormones and Signal Transduction"]),
        ("Molecular Genetics", ["Genetics"]),
    ],
    "Pathology": [
        ("Cell Injury", ["Cellular Reaction to Injury", "Cell Injury"]),
        ("Inflammation & Repair", ["Inflammation", "Inflammation and Repair", "Wound Healing"]),
        ("Haemodynamics", ["Hemodynamic Dysfunction", "Haemodynamics"]),
        ("Immunopathology", ["Immunology"]),
        ("Neoplasia", ["Neoplasia"]),
        ("Genetic Disorders", ["Genetic Disorders", "Genetics"]),
        ("Haematology", ["Anaemia", "Hematology", "Hematopoietic Lymphoid",
                         "Neoplastic, Proliferative and Haemorrhagic Disorders"]),
        ("Cardiovascular", ["Cardiovascular", "Cardiology"]),
        ("Respiratory", ["Respiratory"]),
        ("Gastrointestinal", ["Gastrointestinal", "GIT"]),
        ("Renal", ["Renal"]),
        ("Reproductive", ["Reproduction", "Pregnancy", "Benign conditions of genital tract",
                          "Malignant conditions of lower genital tract",
                          "Disorders of puberty, ovulation and Menstruation"]),
        ("Endocrine", ["Endocrinology"]),
        ("Musculoskeletal", ["Musculoskeletal System"]),
    ],
    "Pharmacology": [
        ("General Pharmacology", ["General Pharma"]),
        ("Autonomic (ANS)", ["ANS"]),
        ("CNS", ["CNS"]),
        ("Cardiovascular & Renal", ["Cardiac and Renal Pharma"]),
        ("Endocrine", ["Thyroid Pharma"]),
        ("Respiratory", ["Respiratory Pharma"]),
        ("Gastrointestinal", ["Gastro Pharma"]),
        ("Blood", ["Drugs used in Blood Disorders"]),
        ("Chemotherapy", ["Chemotherapy"]),
    ],
    "Microbiology": [
        ("Immunology", ["Immunology"]),
        ("Virology", ["Virology"]),
        ("Bacteriology & General", ["Microbiology", "Bacteriology"]),
    ],
    "Neurology & Special Senses": [
        ("Neuroscience", ["Neurology", "Cerebrum", "Cerebellum", "Spinal Cord"]),
        ("Special Senses", ["Special Senses"]),
    ],
    "Community Medicine": [("Community Medicine", ["Public Health Sciences", "Community Medicine"])],
    "Behavioural Sciences": [("Behavioural Sciences", ["Behavioural Sciences"])],
}

# Question sub_category -> subject (Paper 1 bank and the past papers use these names already).
SUBJECTS = list(TAXONOMY)
# Labels that name a whole subject, not a topic in it: questions carrying only these are placed by neighbours.
SUBJECT_LEVEL_LABELS = {"Pharmacology", "Biochemistry", "Physiology", "Mixed", "MIXED", "Anatomy", "Pathology"}


def topics_of(subject: str) -> list[str]:
    return [t for t, _ in TAXONOMY.get(subject, [])]


def canonical_topic(subject: str | None, label: str | None) -> str | None:
    """The canonical topic a source label means within a subject, or None."""
    if not subject or not label or label in SUBJECT_LEVEL_LABELS:
        return None
    key = label.strip().lower()
    for topic, aliases in TAXONOMY.get(subject, []):
        if key == topic.lower() or key in (a.lower() for a in aliases):
            return topic
    return None


def tree() -> list[dict]:
    """The list as the UI shows it."""
    return [{"subject": s, "topics": topics_of(s)} for s in SUBJECTS]
