// The app's map: four hubs (plus Admin), each a set of tabs; every screen ("view") belongs to one hub.
// The sidebar lists hubs, the hub bar lists a hub's tabs, and the URL hash names the view (#/view?params).

export type ViewId =
  | "dashboard" | "daily" | "sprint"
  | "chat" | "revise" | "review" | "lookalikes" | "library"
  | "tos" | "toacs" | "station" | "quiz" | "pastpapers" | "exams" | "mock" | "paper" | "duel" | "offline"
  | "mistakes" | "study" | "bookmarks" | "stats" | "mcq-bank"
  | "check" | "highyield"
  | "users" | "referee" | "reports" | "content";

export type HubId = "today" | "learn" | "practise" | "review" | "more" | "admin";

export interface Tab { view: ViewId; label: string; title?: string; /** only for clinical / specialty papers */ clinical?: boolean }
export interface Hub { id: HubId; label: string; tabs: Tab[]; admin?: boolean; blurb: string }

// The core loop is four hubs: Today (what to do now) -> Study (learn it) -> Practise (test it) -> Review (fix it).
// Everything else is one click further, under More, until the usage counts (Admin -> Content) say it earns a place.
export const HUBS: Hub[] = [
  { id: "today", label: "Today", blurb: "Your plan for today", tabs: [
    { view: "dashboard", label: "Plan" },
    { view: "daily", label: "Daily Dose" },
    { view: "sprint", label: "Final sprint", title: "Opens in the last 7 days before your exam" },
  ] },
  { id: "learn", label: "Study", blurb: "Your paper's blueprint, notes and tutor", tabs: [
    { view: "tos", label: "My paper", title: "Your paper's official Table of Specifications: learn, practise and test each topic" },
    { view: "revise", label: "Revise", title: "One-page revision sheets from the pages you choose" },
    { view: "chat", label: "Dr MedNama", title: "Ask, or be tutored, from your textbooks" },
  ] },
  // Each tab has one job, because "Exams" and "Past papers" used to overlap: both filtered by
  // year and subject, and the Exams page's timed-paper tile only told you to go to Past papers.
  //   Programme exams sit a full paper for your exam (FCPS-I is Paper 1 + Paper 2, 100 each), or the weekly mock
  //   Custom exam     Paper 1 (subjects, topics) or Paper 2 (specialty) from the bank and past papers
  //                   together, your missed MCQs, or a special set (AI sets, collections)
  //   Past papers     go through the archive: by exam, year, sitting and faculty
  { id: "practise", label: "Practise", blurb: "Questions, papers and exams", tabs: [
    { view: "exams", label: "Programme exams", title: "Sit a full paper for your exam, or this week's mock" },
    { view: "quiz", label: "Custom exam", title: "Paper 1 or Paper 2 by subject or specialty, missed MCQs, or a special set" },
    { view: "pastpapers", label: "Past papers", title: "Browse the archive and review what you have answered" },
    { view: "toacs", label: "TOACS / viva", title: "Practise examiner-led viva stations (educational simulation)", clinical: true },
  ] },
  { id: "review", label: "Review", blurb: "Mistakes, cards and progress", tabs: [
    { view: "mistakes", label: "Mistakes" },
    { view: "study", label: "Notes & cards" },
    { view: "stats", label: "Progress" },
  ] },
  // Tools a student reaches for now and then. Check an answer works on any MCQ, including
  // one from outside the app, and keeps a new one in the bank (backend app/checked_mcqs.py).
  { id: "more", label: "More", blurb: "Tools and extras", tabs: [
    { view: "check", label: "Check an answer", title: "Is this key right? The textbooks decide, with the exact passages" },
    { view: "highyield", label: "High-yield map", title: "Which systems past papers ask about most" },
    { view: "lookalikes", label: "Look-alikes" },
    { view: "bookmarks", label: "Bookmarks" },
    { view: "mcq-bank", label: "Question bank" },
    { view: "duel", label: "Challenge" },
    { view: "offline", label: "Offline" },
    { view: "library", label: "Library" },
  ] },
  { id: "admin", label: "Admin", admin: true, blurb: "Accounts, content and quality", tabs: [
    { view: "users", label: "Users" },
    { view: "reports", label: "Reports" },
    { view: "content", label: "Content" },
    { view: "referee", label: "Recall bank", title: "The private recall bank and its referee verdicts" },
  ] },
];

/** A hub's tabs for this learner: TOACS only for clinical papers (or while it is the open screen). */
export function visibleTabs(hub: Hub, clinical: boolean, current?: ViewId): Tab[] {
  return hub.tabs.filter((t) => !t.clinical || clinical || t.view === current);
}

// Screens that are not tabs still belong to a hub (for the highlighted sidebar item and the hub bar).
// Rapid review sits in "practise", not "learn": it is opened from Past papers, and filing it under
// a different hub made the hub bar switch out from under the student mid-flow.
const EXTRA: Partial<Record<ViewId, HubId>> = { review: "practise", mock: "practise", paper: "practise", station: "practise" };

export function hubOf(view: ViewId): Hub {
  const id = EXTRA[view] ?? HUBS.find((h) => h.tabs.some((t) => t.view === view))?.id ?? "today";
  return HUBS.find((h) => h.id === id)!;
}

export const ADMIN_VIEWS: ViewId[] = ["users", "referee", "reports", "content"];
export const ALL_VIEWS: ViewId[] = [...HUBS.flatMap((h) => h.tabs.map((t) => t.view)), "review", "mock", "paper", "station"];

export const VIEW_LABEL: Record<ViewId, string> = Object.fromEntries([
  ...HUBS.flatMap((h) => h.tabs.map((t) => [t.view, t.label])),
  ["dashboard", "Today"], ["review", "Rapid review"], ["mock", "Weekly mock"], ["paper", "Timed paper"], ["station", "Station"],
]) as Record<ViewId, string>;

export type NavParams = Record<string, string | number | null | undefined>;

export function toHash(view: ViewId, params?: NavParams): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  const qs = q.toString();
  return `#/${view}${qs ? `?${qs}` : ""}`;
}

export function fromHash(hash: string): { view: ViewId; params: NavParams } | null {
  const m = /^#\/([a-z-]+)(?:\?(.*))?$/.exec(hash || "");
  if (!m || !ALL_VIEWS.includes(m[1] as ViewId)) return null;
  const params: NavParams = {};
  new URLSearchParams(m[2] || "").forEach((v, k) => { params[k] = v; });
  return { view: m[1] as ViewId, params };
}

/** The cross-links every view can use (built once in app/page.tsx): no view needs to know how another opens. */
export interface AppLinks {
  go: (view: ViewId, params?: NavParams) => void;
  back: (fallback?: ViewId) => void;
  /** Dr MedNama with this question in the box. */
  ask: (question: string) => void;
  /** A revision sheet on this topic. */
  revise: (topic: string) => void;
  /** A practice session; its summary leads back to the view it was started from. */
  practise: (filters: Record<string, unknown>, label: string) => void;
}

/** The text Dr MedNama gets when a student asks about a question they missed. */
export function askAboutQuestion(q: { question_text: string; options: Record<string, string>; correct_option?: string | null }): string {
  const answer = q.correct_option ? q.options?.[q.correct_option] : null;
  return answer ? `Explain why the answer is "${answer}": ${q.question_text}` : `Explain this question: ${q.question_text}`;
}
