/**
 * The exam programmes, in one place.
 *
 * Mirrors backend/app/exam_levels.py. Chat, Revise and the MCQ generator all read this list
 * so a programme added on the server shows up in one edit here rather than three.
 *
 * Not to be confused with the MCQ "Format" control (`exam_profile`: FCPS A-E / USMLE A-E /
 * Quick recall A-D), which is the shape of a question, not the exam being sat. USMLE appears
 * in both lists and they mean different things.
 */

export const EXAM_LEVEL_ANY = "any";

export type ExamLevelOption = { value: string; label: string; hint: string };

export const EXAM_LEVELS: ExamLevelOption[] = [
  { value: EXAM_LEVEL_ANY, label: "General", hint: "General exam prep - no particular exam" },
  { value: "MBBS", label: "MBBS", hint: "Undergraduate: definitions, basics, common presentations" },
  { value: "FCPS-I", label: "FCPS-I", hint: "Basic sciences: mechanisms, pathology, pharmacology" },
  { value: "FCPS-II", label: "FCPS-II", hint: "Clinical: management, drug choice, complications" },
  { value: "PGET", label: "PGET", hint: "Postgraduate entrance: clinical essentials, broad" },
  { value: "MD", label: "MD", hint: "Medical specialty: investigation strategy and management" },
  { value: "MS", label: "MS", hint: "Surgical specialty: operative indications and complications" },
  { value: "USMLE", label: "USMLE", hint: "Step 1 style: basic science applied to a clinical stem" },
];

/** Dropdown items prefixed with "Level:" for the compact chat control. */
export const examLevelItems = (prefix = "Level: ") =>
  EXAM_LEVELS.map((o) => ({ value: o.value, label: `${prefix}${o.label}` }));

/** null (meaning "general") <-> the sentinel the dropdown needs. */
export const toLevelValue = (level: string | null) => level ?? EXAM_LEVEL_ANY;
export const fromLevelValue = (value: string) => (value === EXAM_LEVEL_ANY ? null : value);

export const levelHint = (level: string | null) =>
  EXAM_LEVELS.find((o) => o.value === toLevelValue(level))?.hint ?? "";
