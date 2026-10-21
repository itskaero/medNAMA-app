"""One-off generator for questions.json (kept so the dataset's provenance is reviewable).

Rerunning it OVERWRITES questions.json, including any verified chunk ids. Edit questions.json directly
once annotation has started; use this only to rebuild the starter set.

Each row: (id, subject, difficulty, tags, query, all-terms, any-groups, source).
`source` names where a question came from when it was reused (backend/scripts/eval_rerankers.py).
"""
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "questions.json"

Q = [
    # Anatomy
    ("q001", "anatomy", "medium", ["clinical_scenario", "fcps_style", "anatomy_localization"],
     "A 70-year-old woman falls on her outstretched hand and fractures the surgical neck of the humerus. "
     "She cannot abduct her arm beyond 15 degrees and has numbness over the lower deltoid. Which nerve is injured?",
     ["axillary nerve", "surgical neck"], [], "eval_rerankers.py (reworded as a vignette)"),
    ("q002", "anatomy", "easy", ["factual", "anatomy_localization"],
     "Which cranial nerve supplies the lateral rectus muscle?",
     ["lateral rectus"], [["abducent", "abducens", "sixth cranial", "=vi nerve", "cranial nerve vi"]], None),
    ("q003", "anatomy", "medium", ["factual", "anatomy_localization", "buzzword"],
     "Which muscle is the only abductor of the vocal cords?",
     ["posterior cricoarytenoid"], [["abduct"]], None),
    ("q004", "anatomy", "medium", ["anatomy_localization", "factual"],
     "What are the contents of the anatomical snuffbox?",
     ["radial artery"], [["snuffbox", "snuff box"]], None),
    ("q005", "anatomy", "easy", ["factual", "anatomy_localization"],
     "Root value of the ankle jerk reflex",
     [], [["ankle jerk", "ankle reflex", "achilles reflex", "calcaneal tendon reflex"], ["=s1"]], None),
    ("q006", "anatomy", "hard", ["factual", "anatomy_localization", "confusable"],
     "Which artery is the main blood supply to the head of the femur in adults?",
     [], [["medial circumflex femoral", "medial femoral circumflex"],
          ["head of the femur", "femoral head", "head of femur"]], None),
    # Physiology
    ("q007", "physiology", "easy", ["mechanism", "buzzword"],
     "Frank-Starling law of the heart", ["frank starling"], [], "eval_rerankers.py"),
    ("q008", "physiology", "medium", ["factual", "mechanism"],
     "Which part of the nephron reabsorbs most of the filtered sodium?",
     ["proximal"], [["65 per cent", "65%", "65 percent", "two thirds", "67 per cent", "67%"]], None),
    ("q009", "physiology", "medium", ["mechanism", "confusable"],
     "Which way does an increase in 2,3-BPG shift the oxygen-haemoglobin dissociation curve?",
     ["right"], [["2,3 bpg", "2,3 dpg", "2,3 bisphosphoglycerate", "2,3 diphosphoglycerate"]], None),
    ("q010", "physiology", "easy", ["factual", "mechanism"],
     "Which hormone causes contraction of the gallbladder?",
     ["cholecystokinin", "gallbladder"], [["contract"]], None),
    ("q011", "physiology", "hard", ["mechanism"],
     "How does the countercurrent multiplier of the loop of Henle concentrate urine?",
     ["countercurrent", "loop of henle"], [["multiplier", "multiplication"]], None),
    # Biochemistry
    ("q012", "biochemistry", "medium", ["factual", "buzzword"],
     "Which enzyme is deficient in classic galactosemia?",
     ["galactos"], [["galactose 1 phosphate uridyl", "=galt", "uridyltransferase", "uridyl transferase"]], None),
    ("q013", "biochemistry", "medium", ["mechanism"],
     "What is the role of the carnitine shuttle in fatty acid oxidation?",
     ["carnitine", "mitochondri"], [["fatty acid", "fatty acyl", "acyl coa"]], None),
    ("q014", "biochemistry", "easy", ["factual", "buzzword"],
     "Rate-limiting enzyme of cholesterol synthesis",
     ["hmg coa reductase"], [["rate limiting", "rate controlling", "regulated step", "committed step",
                               "key regulatory"]], None),
    ("q015", "biochemistry", "easy", ["factual"],
     "Deficiency of which vitamin causes Wernicke encephalopathy?",
     ["wernicke"], [["thiamine", "thiamin", "vitamin b1"]], None),
    ("q016", "biochemistry", "medium", ["clinical_scenario", "fcps_style"],
     "A 4-year-old boy has self-mutilation, choreoathetosis and gouty arthritis. Which enzyme is deficient?",
     ["lesch nyhan"], [["hgprt", "hypoxanthine guanine phosphoribosyl", "=hprt"]], None),
    # Pathology
    ("q017", "pathology", "medium", ["pathology_association", "factual"],
     "Most common site of carcinoid tumour",
     ["carcinoid"], [["most common site", "appendix"]], "eval_rerankers.py"),
    ("q018", "pathology", "easy", ["pathology_association", "buzzword"],
     "In which disease are Reed-Sternberg cells seen?", ["reed sternberg"], [["hodgkin"]], None),
    ("q019", "pathology", "medium", ["pathology_association", "buzzword", "confusable"],
     "Psammoma bodies are characteristically seen in which tumours?", ["psammoma"], [["papillary"]], None),
    ("q020", "pathology", "easy", ["pathology_association"],
     "Which type of necrosis is characteristic of tuberculosis?", ["caseous"], [["tubercul"]], None),
    ("q021", "pathology", "medium", ["pathology_association", "buzzword", "confusable"],
     "Mallory hyaline bodies are seen in which liver disease?", ["mallory"], [["alcohol"]], None),
    # Pharmacology
    ("q022", "pharmacology", "easy", ["drug_mechanism", "mechanism"],
     "What is the mechanism of action of pioglitazone?",
     [], [["pioglitazone", "thiazolidinedione", "glitazone"], ["ppar", "peroxisome proliferator"]], None),
    ("q023", "pharmacology", "easy", ["factual", "fcps_style"],
     "drug of choice for absence seizures", ["ethosuximide", "absence"], [], "eval_rerankers.py"),
    ("q024", "pharmacology", "easy", ["factual", "fcps_style"],
     "antidote for heparin overdose", ["protamine", "heparin"], [], "eval_rerankers.py"),
    ("q025", "pharmacology", "medium", ["drug_mechanism", "mechanism"],
     "How does low-dose aspirin prevent platelet aggregation?",
     ["aspirin", "thromboxane"], [["cyclooxygenase", "=cox", "cox 1"]], None),
    ("q026", "pharmacology", "medium", ["drug_mechanism", "mechanism", "confusable"],
     "What is the mechanism of action of metformin?",
     ["metformin"], [["ampk", "amp activated", "hepatic glucose", "gluconeogenesis"]], None),
    ("q027", "pharmacology", "easy", ["drug_mechanism", "mechanism"],
     "Mechanism of action of omeprazole", ["omeprazole"], [["atpase", "proton pump"]], None),
    # Microbiology
    ("q028", "microbiology", "medium", ["mechanism", "buzzword"],
     "mechanism of action of cholera toxin", ["cholera"], [["camp", "cyclic amp", "adenyl"]], "eval_rerankers.py"),
    ("q029", "microbiology", "easy", ["factual", "fcps_style"],
     "most common cause of community acquired pneumonia", ["pneumoniae", "community"], [], "eval_rerankers.py"),
    ("q030", "microbiology", "medium", ["clinical_scenario", "fcps_style"],
     "A farmer's deep crushed leg wound becomes swollen and painful with crepitus and a brownish foul discharge "
     "2 days later. Which organism is responsible?", ["gas gangrene"], [["perfringens"]], None),
    ("q031", "microbiology", "medium", ["mechanism", "confusable"],
     "How does diphtheria toxin kill cells?", ["diphtheria"], [["ef 2", "elongation factor"]], None),
    ("q032", "microbiology", "hard", ["mechanism", "factual"],
     "Which virulence factor of Streptococcus pyogenes is antiphagocytic?", ["m protein"], [["phagocyt"]], None),
    # Paediatrics
    ("q033", "paediatrics", "medium", ["clinical_scenario", "fcps_style"],
     "A 5-week-old boy has projectile non-bilious vomiting and a palpable olive in the epigastrium. Blood gas shows "
     "hypochloraemic metabolic alkalosis. What intravenous fluid is used to correct him before surgery?",
     ["kcl", "glucose", "pylor"], [], "eval_rerankers.py (reworded as a vignette)"),
    ("q034", "paediatrics", "hard", ["mechanism", "buzzword"],
     "paradoxical aciduria", ["paradoxical aciduria"], [], "eval_rerankers.py"),
    ("q035", "paediatrics", "easy", ["factual"],
     "What is the most common cause of bronchiolitis in infants?",
     ["bronchiolitis"], [["respiratory syncytial", "=rsv"]], None),
    ("q036", "paediatrics", "medium", ["factual", "fcps_style"],
     "What is the treatment of Kawasaki disease?", ["kawasaki"], [["immunoglobulin", "ivig"]], None),
    ("q037", "paediatrics", "medium", ["factual"],
     "Most common cause of early-onset neonatal sepsis",
     ["early onset"], [["group b strep", "streptococcus agalactiae", "=gbs"]], None),
    # Medicine
    ("q038", "medicine", "medium", ["factual", "fcps_style"],
     "management of DKA", ["ketoacidosis", "insulin"], [], "eval_rerankers.py"),
    ("q039", "medicine", "easy", ["factual"],
     "What is the first-line drug for type 2 diabetes mellitus?",
     ["metformin"], [["first line", "first choice", "initial therapy", "drug of choice"]], None),
    ("q040", "medicine", "medium", ["lab_findings", "factual"],
     "What fasting plasma glucose level is diagnostic of diabetes mellitus?",
     ["fasting"], [["7.0 mmol", "126 mg"]], None),
    ("q041", "medicine", "medium", ["factual", "confusable"],
     "What is the most common cause of Cushing's syndrome?",
     ["cushing"], [["exogenous", "iatrogenic", "glucocorticoid therapy", "corticosteroid therapy",
                    "steroid therapy"]], None),
    ("q042", "medicine", "medium", ["clinical_scenario", "lab_findings", "fcps_style"],
     "A woman 10 days after a hip replacement develops sudden pleuritic chest pain, breathlessness and "
     "tachycardia. What is the investigation of choice?",
     ["pulmonary embol"], [["ctpa", "ct pulmonary angiogra", "computed tomography pulmonary angiogra"]], None),
    # Surgery
    ("q043", "surgery", "easy", ["factual", "anatomy_localization"],
     "Most common site of an anal fissure",
     ["fissure"], [["posterior midline", "6 o'clock", "midline posteriorly", "posteriorly in the midline"]], None),
    ("q044", "surgery", "easy", ["clinical_scenario", "buzzword"],
     "A patient develops fever with rigors, jaundice and right upper quadrant pain after ERCP. "
     "What is this triad called?", ["charcot"], [["cholangitis"]], None),
    ("q045", "surgery", "easy", ["factual", "buzzword"],
     "Parkland formula for fluid resuscitation in burns", ["parkland"], [["burn"]], None),
    ("q046", "surgery", "medium", ["factual", "buzzword"],
     "Goodsall's rule in fistula-in-ano", ["goodsall"], [], None),
    # ENT
    ("q047", "ent", "medium", ["lab_findings", "fcps_style"],
     "investigation of choice for cholesteatoma",
     ["cholesteatoma"], [["=ct", "hrct", "computed tomography", "diffusion"]], "eval_rerankers.py"),
    ("q048", "ent", "medium", ["clinical_scenario", "buzzword"],
     "A child with chronic ear discharge develops diplopia from a lateral rectus palsy and deep retro-orbital "
     "pain. What is this syndrome?", ["gradenigo"], [], "eval_rerankers.py (reworded as a vignette)"),
    ("q049", "ent", "easy", ["factual", "anatomy_localization"],
     "What is the most common site of epistaxis?",
     [], [["little's area", "littles area", "kiesselbach"]], None),
    ("q050", "ent", "medium", ["factual", "anatomy_localization", "confusable"],
     "Which muscle tenses the vocal cords?", ["cricothyroid"], [["tens"]], None),
    # Neurology
    ("q051", "neurology", "easy", ["anatomy_localization", "pathology_association"],
     "Lesion in which area causes Broca's (expressive) aphasia?", ["broca"], [["inferior frontal"]], None),
    ("q052", "neurology", "hard", ["clinical_scenario", "anatomy_localization", "fcps_style"],
     "A 60-year-old man has sudden vertigo, dysphagia, hoarseness, ipsilateral Horner syndrome, and loss of pain "
     "and temperature on the ipsilateral face and contralateral body. Which artery is occluded?",
     [], [["lateral medullary", "wallenberg"], ["posterior inferior cerebellar", "=pica", "vertebral"]], None),
    ("q053", "neurology", "hard", ["confusable", "anatomy_localization"],
     "How do lesions of the conus medullaris differ clinically from cauda equina lesions?",
     ["conus medullaris", "cauda equina"], [], None),
    ("q054", "neurology", "easy", ["factual", "fcps_style"],
     "What is the first-line drug for status epilepticus?",
     ["status epilepticus"], [["lorazepam", "diazepam", "midazolam", "benzodiazepine"]], None),
    # Haematology
    ("q055", "haematology", "medium", ["clinical_scenario", "buzzword", "confusable"],
     "The blood film of a patient who had a splenectomy after trauma shows small round purple nuclear remnants "
     "inside red cells. What are these?", ["howell jolly"], [["splen", "asplen", "hyposplen"]], None),
    ("q056", "haematology", "medium", ["pathology_association", "confusable"],
     "Which red cell inclusions are seen in G6PD deficiency?",
     ["heinz"], [["g6pd", "glucose 6 phosphate dehydrogenase"]], None),
    ("q057", "haematology", "medium", ["lab_findings", "buzzword", "confusable"],
     "Basophilic stippling of red cells is a feature of which poisoning?",
     ["basophilic stippling"], [["lead"]], None),
    ("q058", "haematology", "medium", ["lab_findings"],
     "What are the iron studies in iron deficiency anaemia?",
     ["ferritin", "iron deficiency"], [["tibc", "total iron binding", "transferrin"]], None),
    ("q059", "haematology", "medium", ["lab_findings", "fcps_style"],
     "Which test confirms hereditary spherocytosis?",
     ["spherocytosis"], [["osmotic fragility", "eosin 5 maleimide", "=ema"]], None),
    # Genetics
    ("q060", "genetics", "easy", ["factual"],
     "What is the mode of inheritance of Duchenne muscular dystrophy?", ["duchenne"], [["x linked"]], None),
    ("q061", "genetics", "medium", ["clinical_scenario", "fcps_style"],
     "A short 15-year-old girl with a webbed neck, widely spaced nipples and primary amenorrhoea. "
     "What is her karyotype?", ["turner"], [["45,x", "45 x", "45,xo", "45 xo"]], None),
    ("q062", "genetics", "medium", ["factual", "buzzword"],
     "Which trinucleotide repeat causes fragile X syndrome?", ["fragile x"], [["cgg"]], None),
    ("q063", "genetics", "easy", ["pathology_association", "buzzword"],
     "HLA-B27 is associated with which diseases?", ["hla b27"], [["ankylosing"]], None),
    # Emergency medicine
    ("q064", "emergency_medicine", "easy", ["factual", "fcps_style"],
     "What is the antidote for paracetamol poisoning?",
     [], [["paracetamol", "acetaminophen"], ["acetylcysteine"]], None),
    ("q065", "emergency_medicine", "medium", ["clinical_scenario", "fcps_style"],
     "A farmer presents with pinpoint pupils, excessive salivation, bradycardia and muscle fasciculations after "
     "spraying pesticide. What is the treatment?", ["organophosphate"], [["atropine", "pralidoxime", "oxime"]], None),
    ("q066", "emergency_medicine", "easy", ["factual", "fcps_style"],
     "What is the first drug to give in anaphylaxis, and by which route?",
     ["anaphyla"], [["adrenaline", "epinephrine"], ["intramuscular", "=im"]], None),
    ("q067", "emergency_medicine", "medium", ["factual", "lab_findings"],
     "How is hyperkalaemia with ECG changes treated first?",
     [], [["hyperkal"], ["calcium gluconate", "calcium chloride"]], None),
]


def main() -> None:
    if OUT.exists() and "--force" not in sys.argv:
        sys.exit(f"{OUT} exists; pass --force to overwrite it (this discards verified chunk ids).")
    out = []
    for qid, subject, difficulty, tags, query, all_terms, any_groups, source in Q:
        item = {
            "id": qid, "query": query, "subject": subject, "difficulty": difficulty, "tags": tags,
            "relevant_chunk_ids": [], "relevant_book": "", "relevant_chapter": "",
            "gold": {"all": all_terms, "any": any_groups},
            "annotation": {"status": "unverified", "basis": "predicate"},
        }
        if source:
            item["source"] = source
        out.append(item)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(out)} questions to {OUT}")


if __name__ == "__main__":
    main()
