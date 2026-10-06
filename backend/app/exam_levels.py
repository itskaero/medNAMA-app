"""Exam programmes: one definition of what MBBS / FCPS-I / FCPS-II / PGET / MD / MS / USMLE mean.

Before this module, "level" was a single line of prompt text read in one place
(app/generation.py), so Chat knew about it, Revision and MCQ generation did not, and
retrieval never saw it at all. Three features would have drifted into three different
notions of the same word. Everything a programme implies now lives here:

    depth and emphasis      chat_guidance / revision_guidance / mcq_guidance
    which books to favour   preferred_subjects, matched against Book.subject
    how to search           rewrite_terms, appended to the query rewrite's keywords
    what a sheet covers     section priority, combined with the question's intent
    how hard a question is  mcq_cognitive_mix (separate from the user's difficulty 1-5)

What a programme must NEVER do is license a fact the retrieved passages do not state.
The hierarchy is: source scope (hard) -> retrieved passages -> user intent -> programme
emphasis -> output style. A programme changes what gets *emphasised*, never what is true.

Unknown or missing values resolve to None and every consumer falls back to the general
exam-prep defaults, so an old client that still sends "fcps1" (or nothing) keeps working.

Not to be confused with `exam_profile` in app/quiz_generation.py, which is an MCQ *format*
(A-D vs A-E, vignette length) and happens to have a value also called "usmle". Programme
and format are orthogonal: USMLE-the-programme can be asked in any format.
"""

import re

# Canonical programme ids. The display name is what a student would call the exam.
MBBS = "MBBS"
FCPS_1 = "FCPS-I"
FCPS_2 = "FCPS-II"
PGET = "PGET"
MD = "MD"
MS = "MS"
USMLE = "USMLE"

# Book.subject values as the ingested library actually spells them (app/book_meta.py
# fills this column; all 20 books carry one). Preference is expressed in these terms so
# no book title is ever hard-coded: a new pharmacology text is preferred automatically.
BASIC_SCIENCE = ("Anatomy", "Physiology", "Biochemistry", "Pathology", "Pharmacology", "Microbiology")
CLINICAL = ("Medicine", "Surgery", "Paediatrics", "Gynaecology & Obstetrics", "ENT", "Ophthalmology")

# Every section a revision sheet may contain. A sheet asks for the ones the topic and
# programme actually call for, not all of them (see sections_for).
ALL_SECTIONS = (
    "Core concepts",
    "Numbers & values",
    "Clinical features",
    "Classic presentations & associations",
    "Investigations",
    "Management & treatment",
    "Complications",
    "Look-alikes & traps",
    "Mnemonics",
)

LEVELS: dict[str, dict] = {
    MBBS: {
        "description": "MBBS undergraduate",
        # Broad and deliberately weak: an undergraduate reads whatever covers the topic.
        "preferred_subjects": BASIC_SCIENCE + CLINICAL,
        "rewrite_terms": "definition classification basic mechanism clinical features common investigations",
        "chat_guidance": (
            "The reader is an MBBS undergraduate: explain clearly, define terms, keep it exam-focused."
        ),
        "revision_guidance": (
            "Favour definitions, core concepts, basic mechanisms, common clinical features, the "
            "fundamental investigations and first-line management. Define any term a final-year "
            "student may not know."
        ),
        "mcq_guidance": (
            "Test foundational knowledge: definitions, basic mechanisms, anatomy/physiology/pathology "
            "fundamentals, straightforward clinical recognition and basic pharmacology. Keep stems short "
            "and unambiguous."
        ),
        "mcq_cognitive_mix": "mostly recall and comprehension, a little straightforward application",
        "sections": ("Core concepts", "Clinical features", "Numbers & values", "Investigations",
                     "Management & treatment", "Mnemonics"),
    },
    FCPS_1: {
        "description": "FCPS-I candidate (basic sciences)",
        "preferred_subjects": ("Physiology", "Pathology", "Pharmacology", "Biochemistry",
                               "Microbiology", "Anatomy"),
        "rewrite_terms": (
            "mechanism pathophysiology pathogenesis physiology pharmacology receptor enzyme "
            "diagnostic criteria investigations laboratory findings"
        ),
        "chat_guidance": (
            "The reader is preparing for FCPS-I (basic sciences): emphasise mechanisms, physiology, "
            "pathology and pharmacology."
        ),
        "revision_guidance": (
            "Favour high-yield exam facts: mechanisms, pathology, physiology, pharmacology, diagnostic "
            "thresholds, classic associations, investigations, first-line treatment and the traps "
            "candidates fall for."
        ),
        "mcq_guidance": (
            "Test integrated basic science: mechanisms, pathophysiology, pharmacology, pathology, classic "
            "associations, interpretation of investigations and the features that distinguish two similar "
            "conditions. Distractors should be the misconceptions candidates actually hold."
        ),
        "mcq_cognitive_mix": "mostly comprehension and application; difficulty from discriminating mechanisms",
        "sections": ("Core concepts", "Numbers & values", "Classic presentations & associations",
                     "Investigations", "Look-alikes & traps", "Mnemonics"),
    },
    FCPS_2: {
        "description": "FCPS-II resident (clinical)",
        "preferred_subjects": CLINICAL,
        "rewrite_terms": (
            "management treatment first-line drug dose regimen indications contraindications "
            "complications differential diagnosis prognosis guideline"
        ),
        "chat_guidance": (
            "The reader is a resident preparing for FCPS-II: emphasise clinical management, guidelines, "
            "doses and pitfalls."
        ),
        "revision_guidance": (
            "Favour clinical decision-making: management, drug choice, doses where the passages state them, "
            "complications, contraindications, differential diagnosis, prognostic factors and the nuanced "
            "distinctions that change management."
        ),
        "mcq_guidance": (
            "Write clinical vignettes: diagnosis, next best step, management, treatment selection, drug "
            "choice, complications, contraindications and nuanced differentials. Use realistic scenarios "
            "only where the passages support them; never invent guideline detail."
        ),
        "mcq_cognitive_mix": "mostly application; difficulty from choosing the right step in a real scenario",
        "sections": ("Clinical features", "Investigations", "Management & treatment", "Complications",
                     "Look-alikes & traps", "Numbers & values"),
    },
    PGET: {
        "description": "PGET candidate (postgraduate entrance test)",
        # A clinical entry test: broad clinical recognition rather than subspecialty depth.
        "preferred_subjects": CLINICAL,
        "rewrite_terms": (
            "clinical features diagnosis management first-line investigations common causes "
            "complications emergency"
        ),
        "chat_guidance": (
            "The reader is preparing for a postgraduate entrance test (PGET): emphasise clinical "
            "recognition, the common causes, first-line investigations and standard management across "
            "all the major specialties."
        ),
        "revision_guidance": (
            "Favour the clinical essentials an entrance test asks: recognition, the commonest cause, "
            "first-line investigation and standard management. Breadth over subspecialty depth."
        ),
        "mcq_guidance": (
            "Write single-best-answer items on clinical essentials across the major specialties: "
            "recognition, commonest cause, first-line investigation, standard management. Breadth over "
            "subspecialty depth."
        ),
        "mcq_cognitive_mix": "balanced recall and application across specialties",
        "sections": ("Clinical features", "Investigations", "Management & treatment",
                     "Classic presentations & associations", "Complications"),
    },
    MD: {
        "description": "MD resident (internal medicine specialty)",
        "preferred_subjects": ("Medicine", "Paediatrics", "Pathology", "Pharmacology"),
        "rewrite_terms": (
            "management treatment drug dose regimen investigations complications differential diagnosis "
            "prognosis guideline"
        ),
        "chat_guidance": (
            "The reader is an MD resident: emphasise internal-medicine reasoning, investigation "
            "strategy, management and the evidence behind it."
        ),
        "revision_guidance": (
            "Favour specialty-level medical reasoning: differential diagnosis, investigation strategy, "
            "management, drug choice and doses where stated, complications and prognosis."
        ),
        "mcq_guidance": (
            "Write specialty-level medical vignettes: differential diagnosis, investigation strategy, "
            "management and the nuances that change it. Only what the passages support."
        ),
        "mcq_cognitive_mix": "application and analysis; difficulty from clinical nuance",
        "sections": ("Clinical features", "Investigations", "Management & treatment", "Complications",
                     "Look-alikes & traps"),
    },
    MS: {
        "description": "MS resident (surgical specialty)",
        "preferred_subjects": ("Surgery", "Anatomy", "Pathology", "ENT", "Ophthalmology"),
        "rewrite_terms": (
            "operative management surgical anatomy indications approach complications postoperative "
            "investigations differential diagnosis"
        ),
        "chat_guidance": (
            "The reader is an MS resident: emphasise surgical anatomy, operative indications and "
            "approach, perioperative care and complications."
        ),
        "revision_guidance": (
            "Favour surgical decision-making: relevant anatomy, operative indications and approach, "
            "perioperative management, complications and their handling."
        ),
        "mcq_guidance": (
            "Write surgical vignettes: operative indication, approach, perioperative management and "
            "complications, plus the surgical anatomy that decides them. Only what the passages support."
        ),
        "mcq_cognitive_mix": "application and analysis; difficulty from operative judgement",
        "sections": ("Clinical features", "Investigations", "Management & treatment", "Complications",
                     "Core concepts"),
    },
    USMLE: {
        "description": "USMLE Step 1 candidate",
        "preferred_subjects": ("Mixed (First Aid)", "Pathology", "Physiology", "Pharmacology",
                               "Biochemistry", "Microbiology"),
        "rewrite_terms": (
            "mechanism pathophysiology pathogenesis most likely diagnosis next step enzyme deficiency "
            "receptor adverse effect classic association"
        ),
        "chat_guidance": (
            "The reader is preparing for USMLE Step 1: apply basic science to a clinical stem — "
            "mechanism first, then the clinical consequence. Favour classic associations and buzzwords."
        ),
        "revision_guidance": (
            "Favour basic science applied clinically: mechanisms, classic associations and buzzwords, "
            "enzyme and receptor detail, drug adverse effects, and the one-line links Step 1 rewards."
        ),
        "mcq_guidance": (
            "Write Step-1 style items: a clinical stem that is answered by a basic-science mechanism. "
            "First-order reasoning, classic associations, plausible same-category distractors."
        ),
        "mcq_cognitive_mix": "application of basic science to a clinical stem; first-order reasoning",
        "sections": ("Core concepts", "Classic presentations & associations", "Numbers & values",
                     "Look-alikes & traps", "Mnemonics"),
    },
}

# Everything a client might send for a programme. The first three keep the original
# chat dropdown values working unchanged.
ALIASES: dict[str, str] = {
    "undergraduate": MBBS, "mbbs": MBBS,
    "fcps1": FCPS_1, "fcps-1": FCPS_1, "fcps i": FCPS_1, "fcps-i": FCPS_1, "fcps_1": FCPS_1,
    "fcps2": FCPS_2, "fcps-2": FCPS_2, "fcps ii": FCPS_2, "fcps-ii": FCPS_2, "fcps_2": FCPS_2,
    "pget": PGET,
    "md": MD,
    "ms": MS,
    "usmle": USMLE, "usmle1": USMLE, "usmle_step1": USMLE, "usmle step 1": USMLE,
}

# What a reader gets when no programme is set. Unchanged from the original
# DEFAULT_LEVEL_GUIDANCE so an unset level behaves exactly as it does today.
DEFAULT_CHAT_GUIDANCE = (
    "The reader is a medical student or resident preparing for MBBS/FCPS (CPSP) exams: "
    "be concise, high-yield and exam-oriented."
)
DEFAULT_REVISION_GUIDANCE = (
    "Favour the high-yield facts an examiner asks: the numbers, thresholds, doses and named signs."
)
DEFAULT_MCQ_GUIDANCE = (
    "Write exam-oriented single-best-answer questions with plausible, same-category distractors."
)
DEFAULT_SECTIONS = ("Core concepts", "Numbers & values", "Classic presentations & associations",
                    "Look-alikes & traps", "Mnemonics")


def normalise(value: str | None) -> str | None:
    """Canonical programme id, or None when unset/unknown (callers then use the defaults)."""
    key = " ".join((value or "").strip().lower().split())
    if not key or key == "any":
        return None
    if key in LEVELS:                      # already canonical
        return key
    for canonical in LEVELS:
        if key == canonical.lower():
            return canonical
    return ALIASES.get(key)


def config(level: str | None) -> dict:
    """The programme's settings, or an empty dict when unset (so .get() falls through)."""
    canonical = normalise(level)
    return LEVELS.get(canonical, {}) if canonical else {}


def label(level: str | None) -> str:
    return config(level).get("description") or "medical student or resident"


def chat_guidance(level: str | None) -> str:
    return config(level).get("chat_guidance") or DEFAULT_CHAT_GUIDANCE


def revision_guidance(level: str | None) -> str:
    return config(level).get("revision_guidance") or DEFAULT_REVISION_GUIDANCE


def mcq_guidance(level: str | None) -> str:
    return config(level).get("mcq_guidance") or DEFAULT_MCQ_GUIDANCE


def mcq_cognitive_mix(level: str | None) -> str:
    return config(level).get("mcq_cognitive_mix") or ""


def preferred_subjects(level: str | None) -> frozenset[str]:
    """Book.subject values this programme leans towards. Empty = no preference.

    A *preference*, never a filter: app/retrieval.py adds a small ordering bonus for these
    and nothing more, so a clearly better passage from any other book still wins.
    """
    return frozenset(config(level).get("preferred_subjects") or ())


def rewrite_terms(level: str | None) -> str:
    """Extra keywords for the query rewrite. They shift which facet of a topic is found
    (mechanism vs management), never the topic itself."""
    return config(level).get("rewrite_terms") or ""


# ─── user intent ──────────────────────────────────────────────────────────────────────
#
# What the question is actually asking for. Without this, "DKA management" retrieves the
# diabetes chapter and a revision sheet spends half its bullets on definitions, because
# the passages that come back are mostly definitional. Intent decides which sections a
# sheet asks for and which facet the rewrite leans on; it never overrides the subject.

INTENTS = ("definition", "mechanism", "features", "investigations", "management",
           "differentiation", "complications", "mixed")

_INTENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("differentiation", re.compile(
        r"\b(differentiat\w*|difference|distinguish\w*|compare|versus|vs\.?|rather than|"
        r"differential diagnosis|ddx)\b", re.I)),
    ("management", re.compile(
        r"\b(manage\w*|treat\w*|therapy|therapeutic|drug of choice|first[- ]line|regimen|dose|dosage|"
        r"prescrib\w*|next (?:best )?step|resuscitat\w*|operat\w*|surger\w*|prophylaxis|prevent\w*)\b",
        re.I)),
    ("investigations", re.compile(
        r"\b(investigat\w*|diagnos\w*|test|tests|workup|work[- ]up|lab\w*|imaging|x[- ]?ray|"
        r"ultrasound|ct\b|mri\b|biopsy|screening|confirm\w*|marker)\b", re.I)),
    ("complications", re.compile(
        r"\b(complicat\w*|sequela\w*|prognos\w*|outcome|mortality|morbidity|side[- ]effect|"
        r"adverse|toxicity)\b", re.I)),
    ("mechanism", re.compile(
        r"\b(mechanism|pathophysiolog\w*|pathogenesis|how does|why does|physiolog\w*|"
        r"mode of action|aetiolog\w*|etiolog\w*|cause[sd]?\b)\b", re.I)),
    ("features", re.compile(
        r"\b(clinical features|features|presentation|presents?|signs?|symptoms?|manifestation\w*|"
        r"findings)\b", re.I)),
    ("definition", re.compile(
        r"\b(what is|what are|define[sd]?|definition|classif\w*|types? of|criteria)\b", re.I)),
)


def classify_intent(query: str) -> str:
    """Best guess at what the question wants. "mixed" when nothing specific is asked for.

    Ordered deliberately: "how do you manage DKA" is management even though it also matches
    "how does", and "differentiate nephrotic from nephritic" is a comparison even though it
    mentions neither treatment nor tests.
    """
    text = " ".join((query or "").split())
    if not text:
        return "mixed"
    for intent, pattern in _INTENT_PATTERNS:
        if pattern.search(text):
            return intent
    return "mixed"


def intent_line(intent: str) -> str:
    """One instruction keeping the answer on the facet that was asked for."""
    return {
        "definition": "The question asks what something is: define and classify it. Do not drift into management.",
        "mechanism": "The question asks why or how: lead with the mechanism.",
        "features": "The question asks how it presents: lead with the clinical features.",
        "investigations": "The question asks how it is diagnosed: lead with the investigations and their interpretation.",
        "management": "The question asks what to do: lead with management. Do not spend the answer defining the condition.",
        "differentiation": "The question asks for a comparison: contrast the entities point by point on the features that separate them.",
        "complications": "The question asks what goes wrong: lead with complications, prognosis and how they are handled.",
        "mixed": "",
    }.get(intent, "")


def sections_for(level: str | None, intent: str) -> tuple[str, ...]:
    """Which revision-sheet sections to ask for, and in what order.

    The intent's own sections come first (a management question leads with management), then
    the programme's, then anything left over. Nothing is forced: the model is told to skip a
    section it cannot fill from the passages.
    """
    by_intent: dict[str, tuple[str, ...]] = {
        "definition": ("Core concepts", "Numbers & values", "Clinical features"),
        "mechanism": ("Core concepts", "Classic presentations & associations"),
        "features": ("Clinical features", "Classic presentations & associations", "Look-alikes & traps"),
        "investigations": ("Investigations", "Numbers & values", "Look-alikes & traps"),
        "management": ("Management & treatment", "Numbers & values", "Complications"),
        "differentiation": ("Look-alikes & traps", "Clinical features", "Investigations"),
        "complications": ("Complications", "Clinical features", "Management & treatment"),
        "mixed": (),
    }
    ordered: list[str] = []
    for name in by_intent.get(intent, ()) + tuple(config(level).get("sections") or DEFAULT_SECTIONS):
        if name in ALL_SECTIONS and name not in ordered:
            ordered.append(name)
    return tuple(ordered)


def options(include_unset: bool = True) -> list[dict[str, str]]:
    """Programmes for a UI picker, in teaching order."""
    order = (MBBS, FCPS_1, FCPS_2, PGET, MD, MS, USMLE)
    out = [{"value": "", "label": "General exam prep"}] if include_unset else []
    return out + [{"value": k, "label": k, "description": LEVELS[k]["description"]} for k in order]
