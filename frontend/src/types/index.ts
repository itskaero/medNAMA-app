export interface Book {
  id: number;
  title: string;
  filename: string;
  status: "pending" | "processing" | "ready" | "failed";
  total_pages: number | null;
  error_message: string | null;
  created_at: string;
}

export interface Citation {
  book_title: string;
  page_number: number;
  excerpt: string;
}

export interface Figure {
  id: number;
  figure_label: string;
  reason_to_include?: string;
  caption?: string | null;
  page_number?: number;
  book_title?: string | null;
}

/** A reranked candidate shown in the collapsible "All matched sources" panel. */
export interface RAGSource {
  chunk_id: number;
  book_id: number | null;
  book_title: string;
  chapter: string | null;
  page_number: number | null;
  snippet: string;
  rank: number;
  relevance_score: number;
}

/** How much of an answer is backed by the ingested textbooks. */
export type Grounding = "textbook" | "partial" | "ai_only" | "none";

export interface AnswerResponse {
  answer_markdown: string;
  citations: Citation[];
  figures: Figure[];
  sources?: RAGSource[];
  /** Cited part from the textbooks (answer_markdown = this + labelled supplement). */
  textbook_answer_markdown?: string;
  /** Uncited AI clinical knowledge beyond the textbooks. */
  supplementary_markdown?: string;
  grounding?: Grounding;
  status?: "ok" | "llm_error" | "not_configured";
  /** Books with a strong matching passage that the answer did not cite. */
  also_in?: { book_title: string; page_number: number | null; chunk_id?: number }[];
  /** Exam buzzwords / mnemonics (AI-made ones are labelled "(AI mnemonic)"). */
  buzzwords_markdown?: string;
}

export interface Message {
  id: string;
  type: "user" | "ai" | "thinking" | "error";
  content?: string;
  answer?: AnswerResponse;
  errorMsg?: string;
  query?: string;
  timestamp?: string;
  /** Live pipeline stage reported by the streaming chat endpoint (thinking messages). */
  stage?: string;
}

export interface Note {
  id: number;
  title: string;
  content: string;
  book_title: string | null;
  page_number: number | null;
  source_context: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface Flashcard {
  id: number;
  front: string;
  back: string;
  topic: string | null;
  book_title: string | null;
  page_number: number | null;
  box: number;
  review_count: number;
  last_reviewed: string | null;
  next_due: string | null;
  created_at: string | null;
}

export type StudyTab = "notes" | "flashcards" | "sheets";

/** A revision sheet this user wrote, listed in Study Corner (scope needed to reopen it). */
export interface SavedSheet {
  id: number;
  label: string;
  book_ids: number[];
  chapter: string | null;
  topic: string | null;
  length: "quick" | "full";
  created_at: string | null;
}

export type ActiveView =
  | "chat"
  | "dashboard"
  | "mcq-bank"
  | "bookmarks"
  | "quiz"
  | "stats"
  | "reader"
  | "study";