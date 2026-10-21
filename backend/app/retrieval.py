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

from sqlalchemy import func, text, tuple_
from sqlalchemy.orm import Session, joinedload

from app.config import settings
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
# Exam-level nudge towards the books a programme leans on (app/exam_levels.py). Kept well
# under BOOK_MARGIN on purpose: it should settle near-ties, never outrank real relevance.
LEVEL_SUBJECT_BONUS = 0.75
# Neighbour paragraphs join a context block only if they are themselves
# relevant (first-stage score) or are a short caption/heading right next to it.
NEIGHBOR_MIN_SCORE = -6.0
SHORT_NEIGHBOR_CHARS = 200
NEIGHBOR_CHARS = 800       # text scored per neighbour paragraph
# Passages sent to the cross-encoder: the best RERANK_POOL by fused rank plus the
# top PER_LIST_TOP of every individual vector/keyword list (so a passage one method
# ranks highly is never lost in fusion). ~16-22 candidates instead of 24.
RERANK_POOL = 16
PER_LIST_TOP = 3
# Extra rerank candidates reserved for books the RERANK_POOL cut missed entirely, so
# select_diverse() can actually offer a slot to a book that discusses the topic in its own
# idiom (Katzung's drug pages for "diabetes mellitus"). Bounded: see search().
MAX_BOOK_HEADS = 8
# How many child chunks each vector/keyword list returns. Widened from 30 once levels
# existed: the cheap fix for "no Katzung passage was even a candidate" is a longer
# *unfiltered* list, because one HNSW query over everything costs far less than several
# book-filtered ones (a filtered scan is the slow path - see vector_search). Measured on
# "mechanism of beta blockers": 10 filtered probes took 37 s, this takes about 11 s.
CANDIDATE_LIMIT = 60
# Last resort for a preferred book even a 60-row list never reached. Kept tiny because each
# one is a filtered scan.
PREFERRED_PROBE = 2
MAX_PROBE_BOOKS = 3
# Which Book.subject a question of this kind should consult first, used to spend the few
# probe slots well. "Treatment of X" asking the pharmacology text is the whole point.
INTENT_SUBJECTS: dict[str, tuple[str, ...]] = {
    "management": ("Pharmacology", "Medicine", "Surgery", "Paediatrics"),
    "mechanism": ("Physiology", "Pathology", "Biochemistry"),
    "investigations": ("Pathology", "Microbiology", "Medicine"),
    "features": ("Medicine", "Paediatrics", "Surgery"),
    "complications": ("Medicine", "Pathology", "Surgery"),
    "differentiation": ("Pathology", "Medicine", "Physiology"),
    "definition": ("Physiology", "Pathology", "Anatomy"),
}
# Figures: shown only when their printed caption matches the question.
FIGURE_MIN_SIM = 0.60
FIGURE_MENTIONED_MIN_SIM = 0.50
MAX_FIGURES = 4

_reranker_model = None
_second_stage_model = None
_second_stage_failed = False
_model_lock = __import__("threading").Lock()   # one load per model, even under concurrent requests


def get_second_stage_reranker():
    """Lazy-load the biomedical second-stage cross-encoder, or None if disabled/unavailable."""
    global _second_stage_model, _second_stage_failed
    name = (settings.reranker_second_stage or "").strip()
    if not name or _second_stage_failed:
        return None
    if _second_stage_model is not None:
        return _second_stage_model
    with _model_lock:
        return _load_second_stage(name)


def _load_second_stage(name: str):
    global _second_stage_model, _second_stage_failed
    if _second_stage_model is None and not _second_stage_failed:
        try:
            from sentence_transformers import CrossEncoder

            import torch

            logger.info("Loading second-stage reranker (%s)...", name)
            # Raw logits, not sentence-transformers' default sigmoid: sigmoid saturates near 1.0 for strong
            # matches (ties), and the exam-level bonus below is added on this scale.
            _second_stage_model = CrossEncoder(name, max_length=512, activation_fn=torch.nn.Identity())
        except Exception as e:  # e.g. offline NAS without the model cached
            logger.warning("Second-stage reranker %s unavailable, using single stage: %s", name, e)
            _second_stage_failed = True
            return None
    return _second_stage_model


def get_reranker_model():
    """Lazy load and cache Cross-Encoder reranking model."""
    global _reranker_model
    if _reranker_model is not None:
        return _reranker_model
    with _model_lock:
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


_REWRITE_STOPWORDS = frozenset("a an the of in for with and or to on is are what which how why".split())


def looks_like_keywords(query: str) -> bool:
    """True when the query is already a search phrase, so rewriting would only dilute it.

    The rewrite turns an exam-style *question* into keywords. Given something that is already
    keywords it expands two words into thirty, and the OR-fallback in keyword_search then
    matches loosely related pages, which pushes the precise hit out of the candidate lists.
    Measured: searching "diabetes mellitus" with the rewrite off puts Katzung's drug page top
    at +10.21, and with it on that page reached the context in 0 of 3 trials. This is the real
    cause of "searching diabetes mellitus doesn't bring up treatment from Katzung".

    A question (ends with '?', or opens with an interrogative) is always rewritten.
    """
    text = " ".join((query or "").split())
    if not text or text.endswith("?"):
        return False
    words = [w for w in re.findall(r"[A-Za-z0-9'-]+", text.lower())]
    if any(w in ("what", "which", "how", "why", "when", "where", "who") for w in words[:1]):
        return False
    content = [w for w in words if w not in _REWRITE_STOPWORDS]
    return len(content) <= 4


@lru_cache(maxsize=512)
def rewrite_query(query: str, context_hint: str = "", level: str | None = None) -> str | None:
    """Turn an exam-style question into textbook search keywords with a fast LLM call.

    Returns None when rewriting is disabled, the LLM is unavailable, or it fails;
    retrieval then proceeds with the original query only.

    `level` is accepted but deliberately NOT used to steer the rewrite. Letting it add its
    programme's keywords was measured to do real harm: for "treatment of diabetes mellitus"
    at FCPS-I the mechanism-leaning rewrite stopped retrieving Guyton's p973 (+9.04, the best
    passage by a wide margin) while keeping passages scoring +0.23. The rewrite's one job is
    to find what the question asks for; the question's own intent outranks the programme.
    Programme preference is applied where it cannot lose information instead — an extra probe
    into preferred books, plus an ordering bonus (see search()).
    """
    if not settings.query_rewrite:
        return None
    if looks_like_keywords(query):
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
        from app.ingestion import EMBED_LOCK
        prefixed_query = BGE_QUERY_PREFIX + query
        model = self.model
        with EMBED_LOCK:
            embedding = model.encode(prefixed_query, normalize_embeddings=True)
        return embedding.tolist()

    def vector_search(self, session: Session, query_embedding: list[float], limit: int = 50, book_id: int | None = None, chapter: str | None = None, book_ids: list[int] | None = None) -> list[tuple[Chunk, float]]:
        """Run vector similarity search on child chunks. Optional book/chapter filtering.

        Ordered by cosine *distance* ascending so Postgres can use the HNSW index
        idx_chunks_child_embedding_hnsw (ordering by "1 - distance DESC" cannot
        use it and forced a full scan of ~400 MB of vectors per query: ~10 s
        cold on the NAS). With a book/chapter filter, iterative index scans keep
        returning rows until `limit` matches are found (pgvector >= 0.8).
        """
        distance = Chunk.embedding.cosine_distance(query_embedding)
        # SET LOCAL: scoped to this transaction, so it never leaks to other queries.
        session.execute(text(f"SET LOCAL hnsw.ef_search = {max(limit * 3, 100)}"))
        if book_id is not None or book_ids or chapter:
            session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))

        query_stmt = session.query(Chunk, (1.0 - distance).label("score")).filter(Chunk.parent_id.isnot(None))
        if book_id is not None:
            query_stmt = query_stmt.filter(Chunk.book_id == book_id)
        if book_ids:
            query_stmt = query_stmt.filter(Chunk.book_id.in_(book_ids))
        if chapter:
            query_stmt = query_stmt.filter(Chunk.chapter.ilike(f"%{chapter.strip()}%"))

        rows = query_stmt.order_by(distance).limit(limit).all()
        results = [(row[0], float(row[1])) for row in rows]
        # relaxed_order can return slightly out-of-order rows; restore strict order.
        results.sort(key=lambda r: r[1], reverse=True)
        return results

    def _run_keyword_sql(self, session: Session, tsquery_sql: str, query_param: str, limit: int,
                         book_id: int | None, chapter: str | None,
                         book_ids: list[int] | None = None) -> list[tuple[int, float]]:
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
        if book_ids:
            sql_query += " AND book_id = ANY(:book_ids)"
            params["book_ids"] = list(book_ids)
        if chapter:
            sql_query += " AND chapter ILIKE :chapter"
            params["chapter"] = f"%{chapter.strip()}%"
        sql_query += " ORDER BY rank DESC LIMIT :limit"
        return [(row[0], float(row[1])) for row in session.execute(text(sql_query), params).fetchall()]

    def keyword_search(self, session: Session, query: str, limit: int = 50, book_id: int | None = None, chapter: str | None = None, book_ids: list[int] | None = None) -> list[tuple[Chunk, float]]:
        """Full-text search on child chunks: strict AND first, then a broad OR fallback.

        The AND query drops exam phrasing ("of choice"); if it finds fewer than 3
        chunks, an OR query over the significant words, abbreviation expansions,
        UK/US spellings and lost-ligature variants runs instead.
        """
        strict = " ".join(QUESTION_NOISE.sub(" ", query).split())
        rows = self._run_keyword_sql(
            session, "websearch_to_tsquery('english', :query)", strict, limit, book_id, chapter, book_ids
        ) if strict else []

        if len(rows) < 3:
            terms = keyword_terms(query)
            if terms:
                or_rows = self._run_keyword_sql(
                    session, "to_tsquery('english', :query)", " | ".join(terms), limit, book_id, chapter,
                    book_ids
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
        from app.ingestion import RERANK_LOCK
        with RERANK_LOCK:
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
                    # One query (the rewrite when present: it carries the textbook terms)
                    # and a shorter window keep this cheap on a slow CPU.
                    q = queries[-1]
                    model = get_reranker_model()
                    from app.ingestion import RERANK_LOCK
                    with RERANK_LOCK:
                        scores = model.predict([(q, by_id[nid][:NEIGHBOR_CHARS]) for nid in others])
                    relevant_ids = {nid for nid, sc in zip(others, scores) if float(sc) >= NEIGHBOR_MIN_SCORE}
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

    @staticmethod
    def _preferred_book_ids(session: Session, level: str | None, book_id: int | None,
                            book_ids: list[int] | None, intent: str = "mixed") -> list[int]:
        """Ready books whose subject this programme prefers, best candidates first.

        Ordered by what the question is asking, because only a few can be probed: a treatment
        question should consult the pharmacology text, a mechanism question the physiology
        one. Probing by arbitrary book id missed Katzung on exactly the query that prompted
        this work.

        Scope wins: probing must never reach a book the student excluded, so when a scope is
        set the result is the intersection (and is empty when they do not overlap).
        """
        from app.exam_levels import preferred_subjects

        wanted = preferred_subjects(level)
        if not wanted:
            return []
        rows = session.query(Book.id, Book.subject).filter(
            Book.status == "ready", Book.subject.in_(wanted)).all()
        scope = {*([book_id] if book_id is not None else []), *(book_ids or [])}
        if scope:
            rows = [r for r in rows if r[0] in scope]
        first = INTENT_SUBJECTS.get(intent, ())
        # Stable: books whose subject the intent calls for, then the rest.
        return [r[0] for r in sorted(rows, key=lambda r: (r[1] or "") not in first)]

    def search(self, session: Session, query: str, *, limit: int = 5, book_id: int | None = None,
               chapter: str | None = None, rewrite: bool = True, context_hint: str = "",
               exclude_chunk_ids: set[int] | None = None, num_candidates: int = 8,
               deep_rerank: bool = True, level: str | None = None,
               book_ids: list[int] | None = None) -> SearchResult:
        """One retrieval pass: rewrite -> vector+keyword per query -> RRF -> rerank -> expand.

        Two separate controls, deliberately not the same thing:

        `book_ids` (and `book_id`) are **source scope**: a hard SQL filter. If the student
        picked Katzung and Guyton, nothing else is retrieved, whatever the level prefers.

        `level` is a **soft preference**, applied in two ways that cannot lose information:
        an extra probe so the programme's books are candidates at all (PREFERRED_PROBE), and
        a small ordering bonus (LEVEL_SUBJECT_BONUS) far below BOOK_MARGIN that never touches
        the MIN_RERANK_SCORE gate. A clearly more relevant passage from a non-preferred book
        still wins. It deliberately does *not* steer the query rewrite — see rewrite_query().

        deep_rerank=False skips the second-stage (biomedical cross-encoder) reranker: background AI jobs
        (harder versions, twists) search dozens of times per job, and on the NAS CPU that reranker was most of
        the time per question.

        exclude_chunk_ids down-ranks parents already used (MCQ source rotation):
        they are kept only if there are not enough fresh candidates.
        """
        queries = [query]
        rewritten = rewrite_query(query, context_hint, level) if rewrite else None
        if rewritten and rewritten.lower() != query.lower():
            queries.append(rewritten)

        rrf: dict[int, float] = {}
        best_child: dict[int, str] = {}
        book_of: dict[int, int] = {}  # parent id -> book, for the per-book pool heads below
        query_embeddings: list[list[float]] = []
        list_heads: list[int] = []  # top of every individual result list
        for q in queries:
            emb = self._embed_query(q)
            query_embeddings.append(emb)
            for results in (self.vector_search(session, emb, limit=CANDIDATE_LIMIT, book_id=book_id,
                                               chapter=chapter, book_ids=book_ids),
                            self.keyword_search(session, q, limit=CANDIDATE_LIMIT, book_id=book_id,
                                                chapter=chapter, book_ids=book_ids)):
                head: list[int] = []
                for rank, (child, _) in enumerate(results, 1):
                    pid = child.parent_id or child.id
                    rrf[pid] = rrf.get(pid, 0.0) + 1.0 / (60 + rank)
                    book_of.setdefault(pid, child.book_id)
                    if pid not in best_child:
                        best_child[pid] = (child.extra_metadata or {}).get("original_text") or child.content
                    if len(head) < PER_LIST_TOP and pid not in head:
                        head.append(pid)
                list_heads.extend(head)

        # One extra probe into the books this programme leans on. Reserving rerank slots for
        # books already in the candidate lists is not enough: across a 25-book library the
        # 30+30 vector/keyword rows for "treatment of diabetes mellitus" contain no Katzung
        # at all, so there is nothing to promote. Measured: scoped to Katzung+Guyton its drug
        # page reranks at +3.30, well above passages that were taking context slots at +1.23 —
        # it was losing on candidacy, not on merit. This buys candidacy only; the reranker
        # still decides whether the passage earns a slot.
        if level and rrf:
            seen_books = {b for b in book_of.values() if b is not None}
            # One probe PER BOOK, not one over all of them: a combined probe crowds exactly
            # like the main lists do (six slots went to Satyanarayana and Lippincott and
            # Katzung still missed out). Only books the main lists never reached are probed,
            # so this usually costs nothing on a narrow query, and it is capped.
            from app.exam_levels import classify_intent

            probe_ids = [b for b in self._preferred_book_ids(session, level, book_id, book_ids,
                                                             classify_intent(query))
                         if b not in seen_books][:MAX_PROBE_BOOKS]
            for bid in probe_ids:
                for emb in query_embeddings[:1]:   # the original query; the rewrite adds little here
                    for child, _ in self.vector_search(session, emb, limit=PREFERRED_PROBE,
                                                       chapter=chapter, book_ids=[bid]):
                        pid = child.parent_id or child.id
                        if pid not in rrf:
                            # Joins at the bottom of the RRF order: enough to be considered,
                            # never enough to displace a passage the main lists ranked.
                            rrf[pid] = 1.0 / (60 + 30)
                            book_of.setdefault(pid, child.book_id)
                            best_child.setdefault(
                                pid, (child.extra_metadata or {}).get("original_text") or child.content)

        if not rrf:
            return SearchResult([], [], None, queries)

        # Pool = best RRF passages + the top few of each individual list. A passage
        # one method ranks very highly but the others miss (Bailey's angiofibroma
        # paragraph: #2 for the rewritten query's vector search, RRF #19) would
        # otherwise never reach the reranker.
        top_ids = sorted(rrf, key=rrf.get, reverse=True)[:RERANK_POOL]
        top_ids += [pid for pid in dict.fromkeys(list_heads) if pid not in top_ids]
        # ...and every book's own best passage. select_diverse() below is meant to give each
        # relevant book a context slot, but it can only choose from what survived the
        # RERANK_POOL cut: for a broad query across 20 books those 16 slots fill with the
        # texts that discuss the topic most densely, and a pharmacology book's treatment
        # pages never reach the reranker at all ("diabetes mellitus" never surfaced Katzung).
        # Pool growth is bounded by the number of books in scope.
        # Capped at MAX_BOOK_HEADS: with a 20-book library an uncapped sweep would add a
        # passage per book and roughly double the reranker's work, which is the one
        # CPU-bound step that hurts on the NAS. The books with the strongest best-passage
        # get the slots; a book whose best passage is weak had nothing to contribute anyway.
        # Only when a level is set. These slots exist to serve exam-level preference, and on
        # the default path they widen the pool enough that select_diverse() spreads context
        # across more books than the question wants: "fluid of choice in hypertrophic pyloric
        # stenosis" gave 3 of 5 slots to anatomy texts describing the condition rather than
        # the page answering it. Keeping the no-level path as it was limits the blast radius.
        if level:
            per_book_best: dict[int, int] = {}
            for pid, bid in book_of.items():
                if bid is None:
                    continue
                current = per_book_best.get(bid)
                if current is None or rrf[pid] > rrf[current]:
                    per_book_best[bid] = pid
            heads = sorted(per_book_best.values(), key=lambda p: rrf[p], reverse=True)
            top_ids += [pid for pid in heads if pid not in top_ids][:MAX_BOOK_HEADS]
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
        second = get_second_stage_reranker() if deep_rerank else None
        top_n = settings.reranker_second_stage_top_n
        s2: dict[int, float] = {}
        if second is not None and len(ranked) > 1:
            head, tail = ranked[:top_n], ranked[top_n:]
            for q in queries:
                from app.ingestion import RERANK_LOCK
                with RERANK_LOCK:
                    scores = second.predict([(q, focus[p.id]) for p, _ in head])
                for (p, _), sc in zip(head, scores):
                    s2[p.id] = max(s2.get(p.id, float("-inf")), float(sc))
            ranked = sorted(head, key=lambda x: s2[x[0].id], reverse=True) + tail

        # Exam-level preference: a tie-break, not a filter. Books whose subject this
        # programme leans on (Katzung for FCPS-I, Davidson for FCPS-II) move up by
        # LEVEL_SUBJECT_BONUS. The stored score stays the raw reranker score, so the
        # MIN_RERANK_SCORE floor and the BOOK_MARGIN comparison in select_diverse() are
        # unchanged; only the order the two see is nudged.
        if level:
            from app.exam_levels import classify_intent, preferred_subjects

            wanted = preferred_subjects(level)
            # Narrowed by what the question asks. A flat per-subject bonus was measured to
            # backfire: for "treatment of diabetes mellitus" at FCPS-I every basic science
            # counted equally, so histology (+6.62) and microbiology (+4.13) were promoted
            # over the pharmacology page (+1.78) and FCPS-I did worse than no level at all.
            # Emphasis has to be about the question, not only about the exam.
            focus_subjects = INTENT_SUBJECTS.get(classify_intent(query), ())
            if wanted and focus_subjects:
                narrowed = wanted & frozenset(focus_subjects)
                if narrowed:
                    wanted = narrowed
            if wanted:
                # The bonus goes on the score of the stage that ordered each passage: the second-stage
                # score inside the re-ordered head, the first-stage score below it. Re-sorting everything
                # by first-stage score + bonus (as before 2026-10-10) threw the second stage's order away
                # whenever a level was set.
                def _preference(item: tuple[Any, float]) -> float:
                    subject = (item[0].book.subject if item[0].book else None) or ""
                    base = s2.get(item[0].id, item[1])
                    return base + (LEVEL_SUBJECT_BONUS if subject in wanted else 0.0)

                head = [r for r in ranked if r[0].id in s2]
                tail = [r for r in ranked if r[0].id not in s2]
                ranked = sorted(head, key=_preference, reverse=True) + sorted(tail, key=_preference, reverse=True)

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
