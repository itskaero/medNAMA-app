"""Predicate audit of 2026-10-07 (applied once; kept for the record).

After the first full run, every top-1 passage (current, ms-marco, MedCPT, BGE v2-m3, BGE base) that a
gold predicate marked irrelevant was read. Where the passage plainly answers the question and the
predicate rejected it only because of wording - an abbreviation, a plural, a synonym, or a correct
answer the predicate was too narrow for - the predicate was broadened. Rules followed:

  * broaden only; no predicate was narrowed
  * the change is decided by the passage's content, never by which model ranked it
  * every change is recorded on the item under "audit"
  * the audit is by Claude (an AI reader), not a clinician: these items stay status "unverified"

Passages judged NOT to answer (kept as misses): q024 "Heparin is administered for anticoagulation",
q036 bibliography entries, q041 "excess ACTH is the most common cause" (the FCPS answer is exogenous
steroids), q008 / q009 / q030 / q033 / q042 / q047 / q056 / q058-q061 / q064 / q065 passages that discuss
the topic without stating the asked fact.
"""
import json
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "questions.json"
NOTE = "2026-10-07 audit: "

CHANGES = {
    "q002": ({"all": ["lateral rectus"], "any": [["abducent", "abducens", "sixth cranial", "=vi nerve", "cranial nerve vi", "cn vi"]]},
             "accept 'CN VI' (Moore p933 'CN VI supplies the lateral rectus')"),
    "q010": ({"all": ["gallbladder"], "any": [["cholecystokinin", "=cck"], ["contract"]]},
             "accept 'CCK' (Guyton p809 'the most potent stimulus for gallbladder contractions is the hormone CCK')"),
    "q011": ({"all": ["countercurrent"], "any": [["loop of henle", "loops of henle"], ["multiplier", "multiplication"]]},
             "accept the plural 'loops of Henle' (Guyton p373)"),
    "q012": ({"all": ["galactos"], "any": [["galactose 1 phosphate uridyl", "=galt", "uridyltransferase", "uridyl transferase", "transferase deficiency"]]},
             "accept 'transferase deficiency' (Nelson p956 on classic galactosemia)"),
    "q018": ({"all": ["reed sternberg"], "any": [["hodgkin", "=hl"]]},
             "accept the abbreviation 'HL' (Nelson p908 'pathognomonic feature of HL')"),
    "q019": ({"all": ["psammoma"], "any": [["papillary", "meningioma", "mesothelioma", "serous"]]},
             "psammoma bodies are also characteristic of meningioma, mesothelioma and serous ovarian tumours "
             "(Ramadas p798, First Aid p716): a passage naming any of them answers 'which tumours'"),
    "q020": ({"any": [["caseous", "caseating", "caseation"], ["tubercul"]]},
             "accept 'caseating' (First Aid p159 'caseating granulomas ... characteristic of 2° tuberculosis')"),
    "q031": ({"all": ["diphtheria"], "any": [["ef 2", "elongation factor", "protein synthesis", "polypeptide chains", "adp ribos", "nicotinamide"]]},
             "accept passages that state the mechanism without naming EF-2 (Pelczar p735 'halts polypeptide "
             "synthesis'; Levinson p144 'cleaves nicotinamide from NAD')"),
    "q037": ({"any": [["early onset", "neonatal sepsis"], ["group b strep", "streptococcus agalactiae", "=gbs"]]},
             "accept 'neonatal sepsis' (Levinson p729 'Group B streptococci are the most common cause of neonatal sepsis')"),
    "q038": ({"any": [["ketoacidosis", "=dka"], ["insulin", "fluid", "potassium"]]},
             "accept 'DKA' and the other pillars of management (Davidson p755/p757 are DKA management pages "
             "that never spell out 'ketoacidosis')"),
    "q051": ({"all": ["broca"], "any": [["inferior frontal", "motor aphasia", "motor speech area", "expressive aphasia"]]},
             "accept passages that tie the Broca area to motor/expressive aphasia (Guyton p722 'Loss of the Broca "
             "area causes motor aphasia')"),
    "q052": ({"any": [["lateral medullary", "wallenberg", "lateral part of the medulla"], ["posterior inferior cerebellar", "=pica", "vertebral"]]},
             "accept 'lateral part of the medulla' (Snell Neuroanatomy p243)"),
}


def main() -> None:
    data = json.loads(PATH.read_text(encoding="utf-8"))
    done = 0
    for item in data:
        change = CHANGES.get(item["id"])
        if not change or any(NOTE in a for a in item.get("audit", [])):
            continue
        gold, why = change
        item.setdefault("audit", []).append(f"{NOTE}{why}. Previous gold: {json.dumps(item['gold'])}")
        item["gold"] = {"all": gold.get("all", []), "any": gold.get("any", [])}
        done += 1
    PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"broadened {done} predicates")


if __name__ == "__main__":
    main()
