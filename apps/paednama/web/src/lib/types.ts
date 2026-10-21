export type StageId = "imm" | "fcps2";

export interface Profile {
  stage: StageId | null;
  exam_date: string | null;
  training_year: number | null;
  rotation: string | null;
  minutes_per_day: number;
  needs_setup: boolean;
  rotations?: string[];
  stages?: { id: StageId; title: string }[];
}

export interface Topic { id: string; name: string; subtopics: string[]; page: number | null; group?: string; whole_system?: boolean }
export interface System { id: string; system: string; page: number | null; topics: Topic[] }

export interface Clinical {
  component: string;
  page: number;
  stations?: string;
  changeover_minutes?: number;
  all_interactive?: boolean;
  tasks?: string[];
  examiner?: string;
  count?: number | string;
  minutes?: number;
  parts?: string[];
  assessed_on?: Record<string, string[]>;
  note?: string;
}

export interface StageSummary {
  id: StageId;
  title: string;
  training_years: string;
  eligibility: { page: number; items: string[] };
  theory: { page: number; papers: { name: string; format: string; questions: number }[]; gate: string };
  clinical: Clinical[];
  rotations?: { page: number; items: { name: string; months: number; each?: boolean }[] } | null;
  systems: number;
  topics: number;
  mini_cex: number;
  dops: number;
}

export interface PaedsBook {
  id: number;
  title: string;
  status: string;
  pages: number | null;
  subject: string | null;
  role: string | null;
  role_label: string | null;
  role_set: boolean;
}

export type View = "home" | "exam" | "syllabus" | "library" | "topic" | "shifa";
