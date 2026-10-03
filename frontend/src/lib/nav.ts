// The app's map: four hubs (plus Admin), each a set of tabs; every screen ("view") belongs to one hub.
// The sidebar lists hubs, the hub bar lists a hub's tabs, and the URL hash names the view (#/view?params).

export type ViewId =
  | "dashboard" | "daily" | "sprint"
  | "chat" | "revise" | "review" | "lookalikes" | "library"
  | "quiz" | "pastpapers" | "exams" | "mock" | "paper" | "duel" | "offline"
  | "mistakes" | "study" | "bookmarks" | "stats" | "mcq-bank"
  | "users" | "referee" | "reports" | "content";

export type HubId = "today" | "learn" | "practise" | "review" | "admin";

export interface Tab { view: ViewId; label: string; title?: string }
export interface Hub { id: HubId; label: string; tabs: Tab[]; admin?: boolean; blurb: string }

export const HUBS: Hub[] = [
  { id: "today", label: "Today", blurb: "Your plan for today", tabs: [
    { view: "dashboard", label: "Overview" },
    { view: "daily", label: "Daily Dose" },
    { view: "sprint", label: "Final sprint", title: "Opens in the last 7 days before your exam" },
  ] },
  { id: "learn", label: "Learn", blurb: "Understand it from the books", tabs: [
    { view: "chat", label: "Dr MedNama", title: "Ask, or be tutored, from your textbooks" },
    { view: "revise", label: "Revise", title: "One-page revision sheets from the pages you choose" },
    { view: "lookalikes", label: "Look-alikes" },
    { view: "library", label: "Library" },
  ] },
  { id: "practise", label: "Practise", blurb: "Questions, papers and exams", tabs: [
    { view: "quiz", label: "Custom", title: "Subjects and topics from past papers and the bank, any difficulty" },
    { view: "pastpapers", label: "Past papers" },
    { view: "exams", label: "Exams", title: "Timed papers and the weekly mock" },
    { view: "duel", label: "Challenge" },
    { view: "offline", label: "Offline" },
  ] },
  { id: "review", label: "Review", blurb: "Mistakes, saved items and progress", tabs: [
    { view: "mistakes", label: "Mistakes" },
    { view: "stats", label: "Progress" },
    { view: "bookmarks", label: "Bookmarks" },
    { view: "study", label: "Notes & cards" },
    { view: "mcq-bank", label: "Question bank" },
  ] },
  { id: "admin", label: "Admin", admin: true, blurb: "Accounts, content and quality", tabs: [
    { view: "users", label: "Users" },
    { view: "reports", label: "Reports" },
    { view: "content", label: "Content" },
    { view: "referee", label: "Answer-key referee" },
  ] },
];

// Screens that are not tabs still belong to a hub (for the highlighted sidebar item and the hub bar).
const EXTRA: Partial<Record<ViewId, HubId>> = { review: "learn", mock: "practise", paper: "practise" };

export function hubOf(view: ViewId): Hub {
  const id = EXTRA[view] ?? HUBS.find((h) => h.tabs.some((t) => t.view === view))?.id ?? "today";
  return HUBS.find((h) => h.id === id)!;
}

export const ADMIN_VIEWS: ViewId[] = ["users", "referee", "reports", "content"];
export const ALL_VIEWS: ViewId[] = [...HUBS.flatMap((h) => h.tabs.map((t) => t.view)), "review", "mock", "paper"];

export const VIEW_LABEL: Record<ViewId, string> = Object.fromEntries([
  ...HUBS.flatMap((h) => h.tabs.map((t) => [t.view, t.label])),
  ["review", "Rapid review"], ["mock", "Weekly mock"], ["paper", "Timed paper"],
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
