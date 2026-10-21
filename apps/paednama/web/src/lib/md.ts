import { marked } from "marked";

export interface Citation { n: number; book_id: number; book_title: string; chapter: string | null; page: number | null }

const escape = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/** Dr. Shifa's reply: [n] markers (reasoning mode) become chips like a sheet's; "[Book, Page N]" citations (the
 *  generator's own style, already checked against the passages) become book-and-page pills. */
export function renderAnswer(md: string, cites: { n?: number; book_title?: string; page?: number | null }[]): string {
  const numbered = cites.filter((c) => c.n).map((c) => ({ n: c.n!, book_id: 0, book_title: c.book_title || "", chapter: null, page: c.page ?? null }));
  return renderSheet(md, numbered).replace(/\[([^\]\n]{2,80}?),\s*(?:Page|p\.)\s*(\d+)\]/gi,
    (_m, book, page) => `<span class="pn-cite-pill">${book} · p.${page}</span>`);
}

/** The AI's Markdown as HTML: any HTML in it is escaped first (it is shown, never run), and [n] markers become
 *  citation chips that name the book and page. */
export function renderSheet(md: string, cites: Citation[]): string {
  const byN = new Map(cites.map((c) => [c.n, c]));
  const html = marked.parse(escape(md || ""), { async: false }) as string;
  return html.replace(/\[(\d+)\]/g, (m, n) => {
    const c = byN.get(Number(n));
    if (!c) return "";
    const label = `${c.book_title}${c.page ? `, p.${c.page}` : ""}`;
    return `<sup class="pn-cite" title="${escape(label)}">${n}</sup>`;
  });
}
