"""Hybrid retrieval pipeline (vector + keyword search) with parent-child mapping,
Reciprocal Rank Fusion (RRF), Cross-Encoder reranking, exam-query rewriting and
neighbour-paragraph context expansion.

Why the extra steps (measured on the ingested library, see
docs/MEDNAMA_IMPROVEMENT_PLAN.md):
  * Exam wording ("fluid of choice in HPS") rarely matches textbook wording
    ("0.9% saline with 0.15% KCl in 5% glucose ... corrects the hypochloraemic
    alkalosis"). The answer passage ranked #7 by vector and was scored -5.9 by
    the reranker, so it never reached the LLM. A keyword rewrite of the
    question lifts it to #2 / -0.56. search() retrieves with both queries.
  * Parent chunks are single paragraphs (median ~300 chars), so the paragraph
    that matches the question is often not the one that holds the answer.
    expand_context() adds the neighbouring paragraphs from the same page.
  * websearch_to_tsquery ANDs every word; a question word the book doesn't use
    ("choice") returned zero keyword hits. keyword_search() falls back to an OR
    query with synonyms, UK spellings and lost-ligature variants.
"""

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from sqlalchemy import tuple_

from app.models import Book, Chunk, Figure

logger = logging.getLogger(__name__)

# BGE v1.5 models require this prefix for query embeddings
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# Abbreviation -> expansion. Expansions are OR-ed into the keyword query and
# appended to the rewrite hint; they never make the AND query stricter.
MEDICAL_SYNONYMS = {
    r"\bmi\b": "myocardial infarction",
    r"\bgis?\b": "gastrointestinal",
    r"\bgerd\b": "gastroesophageal reflux disease",
    r"\bgord\b": "gastro-oesophageal reflux disease",
    r"\bcopd\b": "chronic obstructive pulmonary disease",
    r"\buti\b": "urinary tract infection",
    r"\bsle\b": "systemic lupus erythematosus",
    r"\btb\b": "tuberculosis",
    r"\bcvs\b": "cardiovascular system",
    r"\bcns\b": "central nervous system",
    r"\b(i?hps)\b": "hypertrophic pyloric stenosis",
    r"\bdka\b": "diabetic ketoacidosis",
    r"\bhhs\b": "hyperosmolar hyperglycaemic state",
    r"\baki\b": "acute kidney injury",
    r"\bckd\b": "chronic kidney disease",
    r"\bards\b": "acute respiratory distress syndrome",
    r"\bpph\b": "postpartum haemorrhage",
    r"\bdvt\b": "deep vein thrombosis",
    r"\bpe\b": "pulmonary embolism",
    r"\bchf\b": "congestive heart failure",
    r"\bcap\b": "community acquired pneumonia",
    r"\bibd\b": "inflammatory bowel disease",
    r"\bms\b": "multiple sclerosis",
    r"\bmoa\b": "mechanism of action",
    r"\bdoc\b": "drug of choice",
    r"\bioc\b": "investigation of choice",
    r"\bns\b": "normal saline",
    r"\brl\b": "ringer lactate",
    r"\bsiadh\b": "syndrome of inappropriate antidiuretic hormone",
    r"\bdic\b": "disseminated intravascular coagulation",
    r"\bitp\b": "immune thrombocytopenic purpura",
    r"\bcsom\b": "chronic suppurative otitis media",
    r"\bnec\b": "necrotising enterocolitis",
}

# Exam phrasing that textbooks don't use; removed before the AND keyword query.
QUESTION_NOISE = re.compile(
    r"\b(of choice|choice|what is|what are|which is|which of the following|why does|why do|"
    r"explain|describe|define|tell me about|in detail)\b",
    re.IGNORECASE,
)

# US -> UK spelling fragments (the surgery/medicine texts are British).
US_UK_FRAGMENTS = [
    ("emia", "aemia"), ("edema", "oedema"), ("esophag", "oesophag"), ("pedia", "paedia"),
    ("hemo", "haemo"), ("hema", "haema"), ("hemor", "haemor"), ("anemi", "anaemi"),
    ("ischemi", "ischaemi"), ("leukemi", "leukaemi"), ("estrogen", "oestrogen"),
    ("diarrhea", "diarrhoea"), ("tumor", "tumour"), ("fetus", "foetus"), ("fetal", "foetal"),
    ("orthoped", "orthopaed"), ("color", "colour"), ("ize", "ise"), ("celiac", "coeliac"),
]

# Bailey & Love's PDF lost its fi/fl/ff ligatures ("fluid" -> "fuid"), so the
# keyword query also tries the damaged spelling until the text is repaired
# (scripts/repair_ligatures.py).
LIGATURES = ("ffi", "ffl", "fi", "fl", "ff")

_WORD = re.compile(r"[a-z][a-z0-9]+")
_STOPWORDS = {
    "the", "and", "for", "with", "are", "was", "what", "which", "why", "how", "does", "who",
    "that", "this", "from", "into", "than", "then", "when", "most", "best", "next", "step",
    "patient", "following", "about", "have", "has", "can", "will", "should", "used",
}

# Index pages ("Diuretics, 36, 36 t, 1 = Endocochlear potential, 17, 18 f") match
# almost any OR query; they are dropped before reranking.
_INDEX_ENTRY = re.compile(r"[A-Za-z)][^,\n]{0,40},\s*\d{1,4}(?:\s*[a-z])?\s*[,.=;]")

# Rerank scores below this are unrelated passages (observed: relevant >= -2,
# off-topic <= -8 for ms-marco-MiniLM-L-6).
MIN_RERANK_SCORE = -7.0
MAX_CONTEXT_CHARS = 3000   # per expanded context block sent to the LLM
FOCUS_CHARS = 1500         # text window around the matched child used for reranking
# Book diversity: every book whose best passage scores within BOOK_MARGIN of the
# top passage gets a context slot, and no book takes more than MAX_PER_BOOK
# slots while other relevant books are waiting. Stops one specialist book
# (e.g. Dhingra for ENT) from crowding out Bailey/Robbins on the same topic.
BOOK_MARGIN = 4.0
MAX_PER_BOOK = 2
# Neighbour paragraphs join a context block only if they are themselves
# relevant (first-stage score) or are a short caption/heading right next to it.
NEIGHBOR_MIN_SCORE = -6.0
SHORT_NEIGHBOR_CHARS = 200
# Figures: shown only when their printed caption matches the question.
FIGURE_MIN_SIM = 0.60
FIGURE_MENTIONED_MIN_SIM = 0.50
MAX_FIGURES = 4

_reranker_model = None
_second_stage_model = None
_second_stage_failed = False


def get_second_stage_reranker():
    """Lazy-load the biomedical second-stage cross-encoder, or None if disabled/unavailable."""
    global _second_stage_model, _second_stage_failed
    name = (settings.reranker_second_stage or "").strip()
    if not name or _second_stage_failed:
        return None
    if _second_stage_model is None:
        try:
            from sentence_transformers import CrossEncoder

            logger.info("Loading second-stage reranker (%s)...", name)
            _second_stage_model = CrossEncoder(name, max_length=512)
        except Exception as e:  # e.g. offline NAS without the model cached
            logger.warning("Second-stage reranker %s unavailable, using single stage: %s", name, e)
            _second_stage_failed = True
            return None
    return _second_stage_model


def get_reranker_model():
    """Lazy load and cache Cross-Encoder reranking model."""
    global _reranker_model
    if _reranker_model is None:
        from sentence_transformers import CrossEncoder

        logger.info("Loading Cross-Encoder reranker (ms-marco-MiniLM-L-6-v2)...")
        _reranker_model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    return _reranker_model


def expand_medical_query(query: str) -> str:
    """Return the query with abbreviation expansions appended (used for logging and rewriting)."""
    extra_terms = [exp for pattern, exp in MEDICAL_SYNONYMS.items() if re.search(pattern, query, flags=re.IGNORECASE)]
    return f"{query} {' '.join(extra_terms)}" if extra_terms else query


def _spelling_variants(word: str) -> set[str]:
    variants = set()
    for us, uk in US_UK_FRAGMENTS:
        if us in word:
            variants.add(word.replace(us, uk))
        if uk in word:
            variants.add(word.replace(uk, us))
    for lig in LIGATURES:
        if lig in word:
            variants.add(word.replace(lig, "f"))
    variants.discard(word)
    return variants


def keyword_terms(query: str) -> list[str]:
    """Significant words of the query plus synonyms and spelling variants, for the OR query."""
    base = QUESTION_NOISE.sub(" ", expand_medical_query(query).lower())
    words = [w for w in _WORD.findall(base) if len(w) >= 3 and w not in _STOPWORDS]
    terms: list[str] = []
    for w in words:
        for t in (w, *sorted(_spelling_variants(w))):
            if t not in terms:
                terms.append(t)
    return terms[:40]


def looks_like_index_page(content: str) -> bool:
    if len(content) < 400:
        return False
    return len(_INDEX_ENTRY.findall(content)) * 1000 / len(content) > 6


@lru_cache(maxsize=512)
def rewrite_query(query: str, context_hint: str = "") -> str | None:
    """Turn an exam-style question into textbook search keywords with a fast LLM call.

    Returns None when rewriting is disabled, the LLM is unavailable, or it fails;
    retrieval then proceeds with the original query only.
    """
    if not settings.query_rewrite:
        return None
    from app.llm import chat_completion, llm_configured

    if not llm_configured("fast"):
        return None
    hint = f"\nEarlier question in this conversation (for context): {context_hint}" if context_hint else ""
    try:
        out = chat_completion(
            [
                {
                    "role": "system",
                    "content": (
                        "You turn a medical student's question into search keywords for finding the answer "
                        "in standard textbooks (Bailey & Love, Davidson, Robbins, Guyton, Katzung, Nelson). "
                        "Expand abbreviations, name the underlying condition, and add the words the answer "
                        "passage itself would contain: mechanism, investigations, management, specific drug "
                        "or fluid names, lab findings. Where a keyword has a British spelling "
                        "(haemorrhage, oedema, paediatric) include that spelling too. "
                        "Output ONE line of at most 30 keywords separated by spaces. No sentences, "
                        "no commentary."
                    ),
                },
                {"role": "user", "content": f"{query}{hint}"},
            ],
            temperature=0.0,
            max_tokens=80,
            thinking=False,
            label="rewrite",
            role="fast",
        )
    except Exception as e:  # rewriting is an optimisation, never a failure
        logger.warning("Query rewrite failed for %r: %s", query, e)
        return None
    rewritten = " ".join(out.replace("\n", " ").split())[:400]
    return rewritten or None


@dataclass
class ContextChunk:
    """A retrieved context block: one or more neighbouring parent paragraphs.

    Duck-types the Chunk attributes the generation code reads, without creating
    transient ORM objects that would get flushed into the session.
    """

    id: int
    book_id: int
    book: Book | None
    chapter: str | None
    page_number: int | None
    content: str
    score: float = 0.0
    parts: dict[int, str] = field(default_factory=dict)  # parent chunk id -> paragraph

    @property
    def chunk_ids(self) -> list[int]:
        return sorted(self.parts)

    def refresh(self) -> None:
        """Rebuild content from the paragraphs in document order."""
        self.content = "\n\n".join(self.parts[c] for c in sorted(self.parts))


@dataclass
class SearchResult:
    context: list[ContextChunk]
    sources: list[dict[str, Any]]
    top_score: float | None
    queries: list[str]
    figures: list[dict[str, Any]] = field(default_factory=list)


def select_diverse(ranked: list[tuple[Any, float]], limit: int) -> list[tuple[Any, float]]:
    """Pick context passages so every relevant book is represented.

    1. The best passage of each book scoring within BOOK_MARGIN of the top one.
    2. Remaining slots by rank, at most MAX_PER_BOOK per book.
    3. If slots are still free (few books relevant), fill by rank without the cap.
    The result keeps the incoming rank order.
    """
    eligible = [r for r in ranked if r[1] >= MIN_RERANK_SCORE]
    if not eligible:
        return []
    top = max(sc for _, sc in eligible)
    chosen: list[tuple[Any, float]] = []
    per_book: dict[int, int] = {}

    def take(item):
        chosen.append(item)
        per_book[item[0].book_id] = per_book.get(item[0].book_id, 0) + 1

    for item in eligible:
        if len(chosen) >= limit:
            break
        if item[0].book_id not in per_book and item[1] >= top - BOOK_MARGIN:
            take(item)
    for capped in (True, False):
        for item in eligible:
            if len(chosen) >= limit:
                break
            if item in chosen or (capped and per_book.get(item[0].book_id, 0) >= MAX_PER_BOOK):
                continue
            take(item)
    order = {id(item): i for i, item in enumerate(eligible)}
    return sorted(chosen, key=lambda item: order[id(item)])


def _focus_text(parent_content: str, child_text: str | None, width: int = FOCUS_CHARS) -> str:
    """Window of the parent around the matched child text (whole parent if short)."""
    if len(parent_content) <= width:
        return parent_content
    pos = parent_content.find(child_text[:80]) if child_text else -1
    if pos < 0:
        return parent_content[:width]
    start = max(0, pos - width // 4)
    return parent_content[start:start + width]


class RetrievalService:
    @property
    def model(self):
        """Re-use the cached, dynamically quantized embedding model singleton."""
        from app.ingestion import get_embedding_model
        return get_embedding_model()

    def _embed_query(self, query: str) -> list[float]:
        """Embed search query with BGE-specific prefix."""
        prefixed_query = BGE_QUERY_PREFIX + query
        embedding = self.model.encode(prefixed_query, normalize_embeddings=True)
        return embedding.tolist()

    def vector_search(self, session: Session, query_embedding: list[float], limit: int = 50, book_id: int | None = None, chapter: str | None = None) -> list[tuple[Chunk, float]]:
        """Run vector similarity search on child chunks. Optional book/chapter filtering."""
        query_stmt = session.query(
            Chunk, (1.0 - Chunk.embedding.cosine_distance(query_embedding)).label("score")
        ).filter(Chunk.parent_id.isnot(None))

        if book_id is not None:
            query_stmt = query_stmt.filter(Chunk.book_id == book_id)
        if chapter:
            query_stmt = query_stmt.filter(Chunk.chapter.ilike(f"%{chapter.strip()}%"))

        stmt = query_stmt.order_by(text("score DESC")).limit(limit)
        return [(row[0], float(row[1])) for row in stmt.all()]

    def _run_keyword_sql(self, session: Session, tsquery_sql: str, query_param: str, limit: int,
                         book_id: int | None, chapter: str | None) -> list[tuple[int, float]]:
        # The to_tsvector expression and parent_id filter must match idx_chunks_child_fts.
        sql_query = f"""
            SELECT id, ts_rank_cd(to_tsvector('english', content), {tsquery_sql}) AS rank
            FROM chunks
            WHERE parent_id IS NOT NULL
              AND to_tsvector('english', content) @@ {tsquery_sql}
        """
        params: dict[str, Any] = {"query": query_param, "limit": limit}
        if book_id is not None:
            sql_query += " AND book_id = :book_id"
            params["book_id"] = book_id
        if chapter:
            sql_query += " AND chapter ILIKE :chapter"
            params["chapter"] = f"%{chapter.strip()}%"
        sql_query += " ORDER BY rank DESC LIMIT :limit"
        return [(row[0], float(row[1])) for row in session.execute(text(sql_query), params).fetchall()]

    def keyword_search(self, session: Session, query: str, limit: int = 50, book_id: int | None = None, chapter: str | None = None) -> list[tuple[Chunk, float]]:
        """Full-text search on child chunks: strict AND first, then a broad OR fallback.

        The AND query drops exam phrasing ("of choice"); if it finds fewer than 3
        chunks, an OR query over the significant words, abbreviation expansions,
        UK/US spellings and lost-ligature variants runs instead.
        """
        strict = " ".join(QUESTION_NOISE.sub(" ", query).split())
        rows = self._run_keyword_sql(
            session, "websearch_to_tsquery('english', :query)", strict, limit, book_id, chapter
        ) if strict else []

        if len(rows) < 3:
            terms = keyword_terms(query)
            if terms:
                or_rows = self._run_keyword_sql(
                    session, "to_tsquery('english', :query)", " | ".join(terms), limit, book_id, chapter
                )
                seen = {cid for cid, _ in rows}
                rows += [(cid, rank) for cid, rank in or_rows if cid not in seen]

        if not rows:
            return []
        # Keep SQL order: strict (AND) hits first, then OR hits. The two rank
        # scales are not comparable, so re-sorting by rank would bury exact matches.
        rows = rows[:limit]
        by_id = {c.id: c for c in session.query(Chunk).filter(Chunk.id.in_([cid for cid, _ in rows])).all()}
        return [(by_id[cid], rank) for cid, rank in rows if cid in by_id]

    def rerank_chunks(self, query: str, parent_chunks: list, limit: int = 5, texts: list[str] | None = None) -> list[tuple[Any, float]]:
        """Rerank parent chunks with the Cross-Encoder. `texts` overrides what is scored."""
        if not parent_chunks:
            return []
        reranker = get_reranker_model()
        passages = texts if texts is not None else [c.content for c in parent_chunks]
        scores = reranker.predict([(query, p) for p in passages])
        scored = sorted(zip(parent_chunks, (float(s) for s in scores)), key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def calculate_confidence(self, vector_results: list[tuple[Chunk, float]], keyword_results: list[tuple[Chunk, float]]) -> float:
        """Legacy vector-score confidence, kept for the evaluation scripts.

        The chat pipeline no longer gates on it: on this corpus every medical
        question scores 0.6-0.8, so it separated nothing. search() reports the
        top reranker score instead.
        """
        return vector_results[0][1] if vector_results else 0.0

    def expand_context(self, session: Session, ranked: list[tuple[Chunk, float]], window: int = 3,
                       max_chars: int = MAX_CONTEXT_CHARS, focus: dict[int, str] | None = None,
                       queries: list[str] | None = None) -> list[ContextChunk]:
        """Grow each ranked parent into a block of relevant neighbouring paragraphs from the same page.

        With `queries`, a neighbour joins only if the first-stage cross-encoder
        scores it >= NEIGHBOR_MIN_SCORE for any query, or it is a short
        caption/heading directly adjacent to the hit. This keeps the answer
        paragraph two lines below a matching heading (HPS fluids) while dropping
        unrelated paragraphs that merely share the page (septal perforation
        next to juvenile angiofibroma).

        Blocks that overlap an earlier (higher-ranked) block of the same book are
        merged into it, so the LLM never sees the same paragraph twice.
        """
        focus = focus or {}
        blocks: list[ContextChunk] = []
        covered: dict[int, ContextChunk] = {}  # parent chunk id -> block containing it

        for parent, score in ranked:
            if parent.id in covered:
                continue
            if len(parent.content) >= max_chars or parent.page_number is None:
                content = _focus_text(parent.content, focus.get(parent.id), max_chars)
                block = ContextChunk(parent.id, parent.book_id, parent.book, parent.chapter,
                                     parent.page_number, content, score=score, parts={parent.id: content})
                blocks.append(block)
                covered[parent.id] = block
                continue

            neighbours = (
                session.query(Chunk.id, Chunk.content)
                .filter(Chunk.book_id == parent.book_id, Chunk.parent_id.is_(None),
                        Chunk.page_number == parent.page_number,
                        Chunk.id.between(parent.id - window, parent.id + window))
                .order_by(Chunk.id)
                .all()
            )
            by_id = {nid: body for nid, body in neighbours}
            by_id.setdefault(parent.id, parent.content)
            if queries:
                others = [nid for nid in by_id if nid != parent.id]
                relevant_ids = set()
                if others:
                    scores = {nid: float("-inf") for nid in others}
                    model = get_reranker_model()
                    for q in queries:
                        for nid, sc in zip(others, model.predict([(q, by_id[nid][:FOCUS_CHARS]) for nid in others])):
                            scores[nid] = max(scores[nid], float(sc))
                    relevant_ids = {nid for nid, sc in scores.items() if sc >= NEIGHBOR_MIN_SCORE}
                relevant_ids |= {nid for nid in (parent.id - 1, parent.id + 1)
                                 if nid in by_id and len(by_id[nid]) < SHORT_NEIGHBOR_CHARS}
                by_id = {nid: body for nid, body in by_id.items() if nid == parent.id or nid in relevant_ids}

            # Grow outward from the hit, nearest paragraphs first, within budget.
            chosen = [parent.id]
            total = len(parent.content)
            for dist in range(1, window + 1):
                for nid in (parent.id + dist, parent.id - dist):
                    body = by_id.get(nid)
                    if body is None or total + len(body) > max_chars:
                        continue
                    chosen.append(nid)
                    total += len(body)
            chosen.sort()

            existing = next((covered[c] for c in chosen if c in covered), None)
            if existing is not None:
                # Overlaps a higher-ranked block: add the missing paragraphs to it.
                new_ids = [c for c in chosen if c not in existing.parts]
                if new_ids and len(existing.content) + sum(len(by_id[c]) for c in new_ids) <= max_chars * 2:
                    existing.parts.update({c: by_id[c] for c in new_ids})
                    existing.refresh()
                    for c in new_ids:
                        covered[c] = existing
                continue

            block = ContextChunk(parent.id, parent.book_id, parent.book, parent.chapter, parent.page_number,
                                 "", score=score, parts={c: by_id[c] for c in chosen})
            block.refresh()
            blocks.append(block)
            for c in chosen:
                covered[c] = block
        return blocks

    def search(self, session: Session, query: str, *, limit: int = 5, book_id: int | None = None,
               chapter: str | None = None, rewrite: bool = True, context_hint: str = "",
               exclude_chunk_ids: set[int] | None = None, num_candidates: int = 8) -> SearchResult:
        """One retrieval pass: rewrite -> vector+keyword per query -> RRF -> rerank -> expand.

        exclude_chunk_ids down-ranks parents already used (MCQ source rotation):
        they are kept only if there are not enough fresh candidates.
        """
        queries = [query]
        rewritten = rewrite_query(query, context_hint) if rewrite else None
        if rewritten and rewritten.lower() != query.lower():
            queries.append(rewritten)

        rrf: dict[int, float] = {}
        best_child: dict[int, str] = {}
        query_embeddings: list[list[float]] = []
        for q in queries:
            emb = self._embed_query(q)
            query_embeddings.append(emb)
            for results in (self.vector_search(session, emb, limit=30, book_id=book_id, chapter=chapter),
                            self.keyword_search(session, q, limit=30, book_id=book_id, chapter=chapter)):
                for rank, (child, _) in enumerate(results, 1):
                    pid = child.parent_id or child.id
                    rrf[pid] = rrf.get(pid, 0.0) + 1.0 / (60 + rank)
                    if pid not in best_child:
                        best_child[pid] = (child.extra_metadata or {}).get("original_text") or child.content

        if not rrf:
            return SearchResult([], [], None, queries)

        top_ids = sorted(rrf, key=rrf.get, reverse=True)[:24]
        parents = session.query(Chunk).filter(Chunk.id.in_(top_ids)).options(joinedload(Chunk.book)).all()
        pmap = {p.id: p for p in parents if not looks_like_index_page(p.content)}
        candidates = [pmap[pid] for pid in top_ids if pid in pmap]
        if not candidates:
            return SearchResult([], [], None, queries)

        # Rerank on the matched part of each parent, taking the best score across queries.
        focus = {p.id: _focus_text(p.content, best_child.get(p.id)) for p in candidates}
        best: dict[int, float] = {}
        for q in queries:
            for p, s in self.rerank_chunks(q, candidates, limit=len(candidates), texts=[focus[p.id] for p in candidates]):
                best[p.id] = max(best.get(p.id, float("-inf")), s)
        ranked = sorted(((p, best[p.id]) for p in candidates), key=lambda x: x[1], reverse=True)

        # Second stage: a biomedical cross-encoder re-orders the top N. Scores
        # kept on each tuple stay the first-stage ones (the MIN_RERANK_SCORE
        # cut-off is calibrated on them); only the order changes.
        second = get_second_stage_reranker()
        top_n = settings.reranker_second_stage_top_n
        if second is not None and len(ranked) > 1:
            head, tail = ranked[:top_n], ranked[top_n:]
            s2: dict[int, float] = {}
            for q in queries:
                scores = second.predict([(q, focus[p.id]) for p, _ in head])
                for (p, _), sc in zip(head, scores):
                    s2[p.id] = max(s2.get(p.id, float("-inf")), float(sc))
            ranked = sorted(head, key=lambda x: s2[x[0].id], reverse=True) + tail

        if exclude_chunk_ids:
            fresh = [r for r in ranked if r[0].id not in exclude_chunk_ids]
            used = [r for r in ranked if r[0].id in exclude_chunk_ids]
            ranked = fresh + used if len(fresh) >= limit else ranked

        top_score = max(s for _, s in ranked)
        relevant = select_diverse(ranked, limit)
        # Sources panel: the chosen (book-diverse) passages first, then the rest by rank.
        chosen_ids = {p.id for p, _ in relevant}
        source_order = relevant + [r for r in ranked if r[0].id not in chosen_ids]
        sources = []
        for rank, (chunk, score) in enumerate(source_order[:num_candidates], 1):
            snippet = " ".join(focus[chunk.id].split())
            if len(snippet) > 300:
                snippet = snippet[:300].rsplit(" ", 1)[0] + "…"
            sources.append({
                "chunk_id": chunk.id,
                "book_id": chunk.book_id,
                "book_title": chunk.book.title if chunk.book else "Unknown Textbook",
                "chapter": chunk.chapter,
                "page_number": chunk.page_number,
                "snippet": snippet,
                "rank": rank,
                "relevance_score": round(score, 4),
            })

        context = self.expand_context(session, relevant, focus=best_child, queries=queries)
        figures = self.select_figures(session, query_embeddings, context)
        logger.info(
            "search %r (+rewrite=%s): %d candidates, top rerank %.2f, context=%s",
            query, len(queries) > 1, len(candidates), top_score,
            [(c.book.title if c.book else "?", c.page_number, round(c.score, 2)) for c in context],
        )
        return SearchResult(context, sources, top_score, queries, figures)

    def select_figures(self, session: Session, query_embeddings: list[list[float]],
                       context: list) -> list[dict[str, Any]]:
        """Figures worth showing for this answer: captioned, non-decorative, on a
        context page, and whose printed caption matches the question.

        A figure qualifies when its caption embedding is >= FIGURE_MIN_SIM to any
        query embedding, or >= FIGURE_MENTIONED_MIN_SIM when the context text
        itself refers to it ("see Figure 49.1"). Uncaptioned figures are never
        returned: without a caption nobody can tell what the image shows.
        """
        pages = {(c.book_id, c.page_number) for c in context if c.page_number is not None}
        if not pages or not query_embeddings:
            return []
        sims = [(1.0 - Figure.caption_embedding.cosine_distance(e)) for e in query_embeddings]
        best_sim = sims[0] if len(sims) == 1 else func.greatest(*sims)
        rows = (
            session.query(Figure.id, Figure.book_id, Figure.page_number, Figure.figure_label,
                          Figure.caption, best_sim.label("sim"))
            .filter(Figure.caption_source == "printed", Figure.is_decorative.is_(False),
                    tuple_(Figure.book_id, Figure.page_number).in_(list(pages)))
            .all()
        )
        titles = {c.book_id: (c.book.title if c.book else None) for c in context}
        # References in the passages' own text, excluding the caption paragraphs themselves.
        body_text = " ".join(
            part for c in context for part in getattr(c, "parts", {c.id: c.content}).values()
            if not re.match(r"\s*(?:Figure|Fig\.?)\s*\d", part, re.IGNORECASE)
        )
        picked = []
        for fid, book_id, page, label, caption, sim in rows:
            number = (label or "").replace("Figure", "").strip()
            # "49.1" also matches "49-1". Built outside the f-string: Python 3.11
            # (the container) rejects backslashes inside f-string expressions.
            number_pattern = re.escape(number).replace(r"\.", r"[.\-]")
            mentioned = bool(number) and re.search(
                r"\bFig(?:ure|\.)?\s*" + number_pattern + r"\b", body_text, re.IGNORECASE
            ) is not None
            sim = float(sim or 0.0)
            if sim >= FIGURE_MIN_SIM or (mentioned and sim >= FIGURE_MENTIONED_MIN_SIM):
                picked.append({
                    "id": fid,
                    "figure_label": label,
                    "caption": caption,
                    "page_number": page,
                    "book_title": titles.get(book_id),
                    "relevance": round(sim, 3),
                    "mentioned": mentioned,
                })
        picked.sort(key=lambda f: (f["mentioned"], f["relevance"]), reverse=True)
        return picked[:MAX_FIGURES]

    def hybrid_search(self, session: Session, query: str, limit: int = 5, rrf_k: int = 60, book_id: int | None = None, chapter: str | None = None) -> list[ContextChunk]:
        """Context blocks for a query (compatibility wrapper around search())."""
        return self.search(session, query, limit=limit, book_id=book_id, chapter=chapter).context

    def candidate_search(self, session: Session, query: str, limit: int = 8, book_id: int | None = None, chapter: str | None = None) -> list[dict]:
        """Reranked candidates for the "matched sources" panel (compatibility wrapper)."""
        return self.search(session, query, book_id=book_id, chapter=chapter, num_candidates=limit).sources

    def get_or_generate_figure_caption(self, session: Session, figure: Figure) -> str | None:
        """Fetch figure caption. On-demand generation has been removed to reduce API overhead."""
        return figure.caption

    def retrieve_figures_for_chunks(self, session: Session, chunks: list) -> dict[int, list[dict]]:
        """Retrieve relevant figures for context blocks based on page numbers."""
        results = {}
        for chunk in chunks:
            if chunk.page_number is None:
                continue

            figures = (
                session.query(Figure)
                .filter(Figure.book_id == chunk.book_id)
                .filter(Figure.page_number == chunk.page_number)
                .all()
            )

            chunk_figs = []
            for fig in figures:
                caption = self.get_or_generate_figure_caption(session, fig)
                chunk_figs.append({
                    "id": fig.id,
                    "figure_label": fig.figure_label,
                    "page_number": fig.page_number,
                    "caption": caption,
                    "mime_type": fig.mime_type,
                })

            if chunk_figs:
                results[chunk.id] = chunk_figs

        return results


# Global singleton instance
retrieval_service = RetrievalService()
