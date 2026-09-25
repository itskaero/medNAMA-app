"""Book ingestion pipeline: PDF -> parse -> figures -> chunks -> embeddings -> DB.

Supports parallelized page-sliced conversion, dynamic INT8 quantization, and
optimized batch database transaction flushes (Proposals 1, 2 & 3).

The pipeline is RAM-aware: the worker count and the page-slice width are
auto-tuned from the live free RAM (psutil), worker pools opened for a book are
fully reaped (their Docling + embedding model copies released) before the next
book starts, and embedding batches shrink under memory pressure. This keeps a
long multi-book run moving in small chunks instead of piling up model copies
until the machine OOMs and slices start timing out ("it was fine for the first
N books").
"""

import gc
import io
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal
from app.models import Book, Chunk, Figure

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF"


def _setup_logging() -> None:
    """Append INFO logs to the file named by $MEDNAMA_INGEST_LOG.

    Multiprocessing on Windows uses 'spawn', so worker subprocesses do NOT inherit
    the parent's logging configuration. Setting this env var before ingest_book()
    fans out log lines (including Docling's "Processing document ..." messages)
    from every worker into the same file, which run_ingestion.py / watch_ingest.py
    rely on to track live progress.
    """
    log_file = os.environ.get("MEDNAMA_INGEST_LOG")
    if not log_file:
        return
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if any(getattr(h, "_mednama_ingest_file", False) for h in root.handlers):
        return
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler._mednama_ingest_file = True
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.addHandler(handler)


_setup_logging()
PAGE_CHUNK_SIZE = 50  # Slice size to avoid memory bloat
_embedding_model = None
_document_converter = None

try:  # pragma: no cover - psutil is a dev dependency but must not break prod
    import psutil
except Exception:
    psutil = None

# Rough per-worker peak RAM: Docling layout + table models, quantized
# bge-large embedding, and one page-slice's worth of text/images. Tuned so a
# single worker fits comfortably in ~4-5 GB of free RAM on a 16 GB box.
WORKER_RAM_ESTIMATE_MB = 3200
# Free RAM that must always be left for the OS, Postgres, and page cache.
MIN_FREE_RAM_MB = 2000
# Page-slice width tiers: when RAM is scarce, use smaller slices so each
# in-flight work unit (and its transient text/image buffers) stays small.
_SLICE_TIERS_MB = ((12000, 50), (8000, 25), (4500, 12), (0, 6))


def _available_ram_mb() -> int:
    """Current free RAM in MB (psutil's 'available' includes reclaimable cache)."""
    if psutil is None:
        return 2**31  # unknown -> pretend there is plenty
    try:
        return int(psutil.virtual_memory().available / (1024 * 1024))
    except Exception:
        return 2**31


def _total_ram_mb() -> int:
    if psutil is None:
        return 2**31
    try:
        return int(psutil.virtual_memory().total / (1024 * 1024))
    except Exception:
        return 2**31


def _ram_budget_mb(total_mb: int, requested: int | None) -> int:
    """RAM budget for worker models: caller override, else ~60% of the machine."""
    if requested and requested > 0:
        return requested
    if total_mb >= 2**31:
        return 2**31
    return max(1, int(total_mb * 0.6))


def auto_workers(free_mb: int, ram_budget_mb: int | None = None) -> int:
    """Max parallel slice workers that fit in the currently free RAM (>= 1).

    Reserves MIN_FREE_RAM_MB for the OS/DB, then fits WORKER_RAM_ESTIMATE_MB per
    worker into whatever is left, capped by the CPU count.
    """
    import os
    budget = _ram_budget_mb(_total_ram_mb(), ram_budget_mb)
    usable = max(0, min(budget, free_mb) - MIN_FREE_RAM_MB)
    n = usable // WORKER_RAM_ESTIMATE_MB
    cap = os.cpu_count() or 1
    return max(1, min(n, cap))


def auto_slice_size(free_mb: int) -> int:
    """Pick a page-slice width that keeps each work unit small vs. free RAM."""
    for threshold, size in _SLICE_TIERS_MB:
        if free_mb >= threshold:
            return size
    return _SLICE_TIERS_MB[-1][1]


_embedding_model_lock = __import__("threading").Lock()


def get_embedding_model():
    """Lazy load, cache and dynamically quantize embedding model (Proposal 2).

    Thread-safe: background jobs (concept cards, quiz jobs) may ask for the model
    at the same moment; without the lock each would load its own ~1.3 GB copy.
    """
    global _embedding_model
    if _embedding_model is not None:
        return _embedding_model
    with _embedding_model_lock:
        return _load_embedding_model()


def _load_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        import torch
        from sentence_transformers import SentenceTransformer

        logger.info("Loading embedding model (bge-large-en-v1.5) with dynamic INT8 CPU quantization...")
        model = SentenceTransformer("BAAI/bge-large-en-v1.5")
        
        # Quantize CPU linear operations to INT8 to accelerate inference by 2x-3x
        _embedding_model = torch.quantization.quantize_dynamic(
            model, {torch.nn.Linear}, dtype=torch.qint8
        )
    return _embedding_model


def get_document_converter():
    """Lazy load and cache DocumentConverter singleton to avoid reinitialization overhead."""
    global _document_converter
    if _document_converter is None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        logger.info("Initializing DocumentConverter singleton (OCR = False)...")
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = False
        pipeline_options.generate_picture_images = True
        pipeline_options.images_scale = 2.0

        _document_converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            }
        )
    return _document_converter


def validate_pdf(path: Path) -> None:
    """Check magic bytes and file size. Raises ValueError on failure."""
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    size = path.stat().st_size
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if size > max_bytes:
        raise ValueError(f"File too large: {size / 1e6:.0f}MB (limit: {settings.max_upload_size_mb}MB)")
    with open(path, "rb") as f:
        magic = f.read(4)
    if magic != PDF_MAGIC:
        raise ValueError(f"Not a valid PDF (magic bytes: {magic!r})")


def _parse_pdf(pdf_path: Path):
    """Parse PDF with cached DocumentConverter singleton."""
    converter = get_document_converter()
    return converter.convert(str(pdf_path))


def split_into_children(text: str, max_words: int = 150) -> list[str]:
    """Split a larger text chunk into smaller sentences-grouped child segments."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    chunks = []
    current_chunk = []
    current_words = 0

    for sentence in sentences:
        if not sentence.strip():
            continue
        sentence_words = len(sentence.split())
        if current_words + sentence_words > max_words and current_chunk:
            chunks.append(" ".join(current_chunk))
            current_chunk = [sentence]
            current_words = sentence_words
        else:
            current_chunk.append(sentence)
            current_words += sentence_words

    if current_chunk:
        chunks.append(" ".join(current_chunk))
    return chunks


def _process_slice_worker(args: dict) -> dict:
    """Independent worker processing a single page-slice of a book (Proposal 3).

    Handles Docling layout conversion, figure extraction, parent-child split,
    quantized embedding calculation, and batch database flushes (Proposal 1).
    """
    pdf_path = Path(args["pdf_path"])
    book_id = args["book_id"]
    title = args["title"]
    start_idx = args["start_idx"]
    end_idx = args["end_idx"]

    # Open isolated database session for this worker process
    session = SessionLocal()
    try:
        from pypdf import PdfReader, PdfWriter
        import tempfile
        import gc

        # 1. Slice PDF
        reader = PdfReader(pdf_path)
        writer = PdfWriter()
        for i in range(start_idx, end_idx):
            writer.add_page(reader.pages[i])

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temp_pdf:
            temp_pdf_path = Path(temp_pdf.name)

        try:
            with open(temp_pdf_path, "wb") as f:
                writer.write(f)

            # 2. Parse slice with Docling
            result = _parse_pdf(temp_pdf_path)

            # 3. Extract Figures (Labels use page-specific names to prevent sync conflicts)
            figures_data = []
            from docling_core.types.doc import PictureItem
            doc = result.document
            fig_idx = 0
            
            for element, _level in doc.iterate_items():
                if not isinstance(element, PictureItem):
                    continue
                pil_image = element.get_image(doc)
                if pil_image is None:
                    continue
                # Skip decorative images (icons, bullets, QR codes); they were
                # ~25% of extracted figures and never useful in answers.
                if min(pil_image.width, pil_image.height) < 120 or (
                    max(pil_image.width, pil_image.height) / max(1, min(pil_image.width, pil_image.height)) > 6
                ):
                    continue
                fig_idx += 1
                try:
                    caption_text = " ".join((element.caption_text(doc) or "").split())
                except Exception:
                    caption_text = ""
                label_match = re.match(r"(?:Figure|Fig\.?)\s*(\d+[.\-]\d+[A-Za-z]?)", caption_text, re.IGNORECASE)

                # Convert image to RGB (JPEGs don't support RGBA)
                if pil_image.mode in ("RGBA", "P"):
                    pil_image = pil_image.convert("RGB")
                
                width, height = pil_image.width, pil_image.height

                # Resize if the image is too large (max 1000px on either side)
                max_size = 1000
                if max(pil_image.width, pil_image.height) > max_size:
                    pil_image.thumbnail((max_size, max_size))

                buf = io.BytesIO()
                pil_image.save(buf, format="JPEG", quality=80, optimize=True)
                image_bytes = buf.getvalue()
                del pil_image, buf  # release the decoded image asap

                page_num = None
                if hasattr(element, "prov") and element.prov:
                    page_num = element.prov[0].page_no + start_idx

                figures_data.append({
                    "book_id": book_id,
                    # Real printed label/caption when Docling found one (retrieval only
                    # shows captioned figures); synthetic label otherwise.
                    "figure_label": (f"Figure {label_match.group(1).replace('-', '.')}" if label_match
                                     else f"Figure {page_num or 'N/A'}-{fig_idx}"),
                    "caption": caption_text[:2000] or None,
                    "caption_source": "printed" if caption_text else None,
                    "is_decorative": False,
                    "width": width,
                    "height": height,
                    "page_number": page_num,
                    "image_data": image_bytes,
                    "mime_type": "image/jpeg",
                })

            for fig in figures_data:
                session.add(Figure(**fig))

            # 4. Chunk & Map Parents (Proposals 1 & 10)
            from docling.chunking import HierarchicalChunker
            chunker = HierarchicalChunker(max_tokens=512)
            doc_chunks = list(chunker.chunk(result.document))

            parents_to_add = []
            for dc in doc_chunks:
                page_num = None
                if hasattr(dc, "meta") and dc.meta:
                    prov = getattr(dc.meta, "doc_items", None)
                    if prov:
                        for item in prov:
                            if hasattr(item, "prov") and item.prov:
                                page_num = item.prov[0].page_no + start_idx
                                break

                chapter = None
                headings_list = []
                if hasattr(dc, "meta") and dc.meta:
                    headings = getattr(dc.meta, "headings", None)
                    if headings:
                        headings_list = list(headings)
                        chapter = " > ".join(headings_list)

                is_table = bool(re.search(r"\|.*\|.*?\n\|[-:| ]+\|", dc.text) or "table" in (chapter or "").lower())

                parent_chunk = Chunk(
                    book_id=book_id,
                    chapter=chapter,
                    page_number=page_num,
                    content=dc.text,
                    parent_id=None,
                    embedding=None,
                    extra_metadata={
                        "type": "parent",
                        "is_table": is_table,
                        "headings": headings_list,
                        # Which 50-page slice produced this chunk. Used by
                        # _committed_slices() to resume a book without
                        # re-processing slices that already committed.
                        "slice": f"{start_idx}-{end_idx}",
                    }
                )
                session.add(parent_chunk)
                parents_to_add.append(parent_chunk)

            # Single Database Flush to assign primary keys in one transaction (Proposal 1)
            session.flush()

            # 5. Map Children
            chunks_to_embed = []
            for parent_chunk, dc in zip(parents_to_add, doc_chunks):
                child_texts = split_into_children(dc.text, max_words=150)
                for child_text in child_texts:
                    context_prefix = f"Textbook: {title} | Chapter: {parent_chunk.chapter or 'N/A'} | Page: {parent_chunk.page_number or 'N/A'}"
                    enriched_content = f"{context_prefix}\n{child_text}"

                    child_chunk = Chunk(
                        book_id=book_id,
                        chapter=parent_chunk.chapter,
                        page_number=parent_chunk.page_number,
                        content=enriched_content,
                        parent_id=parent_chunk.id,
                        embedding=None,
                        extra_metadata={
                            "type": "child",
                            "original_text": child_text,
                            "is_table": parent_chunk.extra_metadata["is_table"]
                        }
                    )
                    session.add(child_chunk)
                    chunks_to_embed.append(child_chunk)

            # 6. Embed Child Chunks (Proposal 2)
            if chunks_to_embed:
                # Keep each encode() batch small enough for the free RAM so a
                # big slice degrades into a little more work instead of an
                # allocation failure that takes down the whole pool.
                _free_mb = _available_ram_mb()
                _batch = 64 if _free_mb >= 4000 else (32 if _free_mb >= 2500 else 16)
                texts_to_embed = [c.content for c in chunks_to_embed]
                model = get_embedding_model()
                embeddings = model.encode(texts_to_embed, batch_size=_batch,
                                          show_progress_bar=False, normalize_embeddings=True)
                for c_obj, emb in zip(chunks_to_embed, embeddings):
                    c_obj.embedding = emb.tolist()

            # 7. Commit transaction
            session.commit()

            _parent_count = len(parents_to_add)
            _child_count = len(chunks_to_embed)
            _fig_count = len(figures_data)

            # Drop the heavy parsed structures before returning so the worker
            # releases this slice's Docling/embedding memory for its next task.
            del doc, result, parents_to_add, chunks_to_embed, figures_data
            gc.collect()

            return {
                "success": True,
                "parent_count": _parent_count,
                "child_count": _child_count,
                "fig_count": _fig_count,
            }

        finally:
            if temp_pdf_path.exists():
                temp_pdf_path.unlink()
            gc.collect()

    except Exception as e:
        session.rollback()
        logger.error(f"Error in slice worker {start_idx}-{end_idx}: {e}")
        return {"success": False, "error": str(e)}
    finally:
        session.close()


def _committed_slices(session, book_id: int, num_pages: int, slice_size: int = PAGE_CHUNK_SIZE) -> set[str]:
    """Return the page-slice keys (e.g. '150-199') already fully committed.

    Works with a changing slice_size: committed ranges are detected via the
    explicit 'slice' marker in parent chunks' extra_metadata (written by
    _process_slice_worker), where a marker covers the whole page range it was
    written for, so a current slice counts as done when the committed ranges'
    *union fully covers* its page interval (regardless of whether the marker
    was wider or narrower). A row-coverage fallback covers data committed
    before markers existed (a slice counts as done with >= 30 chunk rows or
    >= 15 figure rows with pages inside [start, end)). Slices commit atomically
    (single DB transaction), so a non-empty committed range is complete, and
    only whole ranges are ever skipped - a crash can never leave a partly
    skipped slice, and shrinking the slice size on retry just splits the
    *uncommitted* ranges.
    """
    done: set[str] = set()

    # 1) Explicit markers from parent chunks -> mergeable page intervals.
    rows = session.execute(text(
        "SELECT DISTINCT c.extra_metadata->>'slice' AS s FROM chunks c "
        "WHERE c.book_id = :b AND c.extra_metadata ? 'slice'"
    ), {"b": book_id}).fetchall()
    intervals: list[tuple[int, int]] = []
    for (key,) in rows:
        if not key or "-" not in key:
            continue
        lo_s, hi_s = key.split("-", 1)
        if lo_s.strip().isdigit() and hi_s.strip().isdigit():
            intervals.append((int(lo_s), int(hi_s)))

    intervals.sort()

    def _covered(start_idx: int, end_idx: int) -> bool:
        """True if [start_idx, end_idx) lies fully inside the committed union."""
        pos = start_idx
        # Greedy reach: extend pos as far right as any committed range allows.
        changed = True
        while pos < end_idx and changed:
            changed = False
            for lo, hi in intervals:
                if lo <= pos and hi > pos:
                    pos = max(pos, hi)
                    changed = True
        return pos >= end_idx

    # 2) Coverage fallback for legacy/pre-marker chunks
    for start_idx in range(0, num_pages, slice_size):
        end_idx = min(start_idx + slice_size, num_pages)
        key = f"{start_idx}-{end_idx}"
        if _covered(start_idx, end_idx):
            done.add(key)
            continue
        n = session.execute(text(
            "SELECT count(*) FROM chunks WHERE book_id = :b "
            "AND page_number IS NOT NULL AND page_number >= :s AND page_number < :e"
        ), {"b": book_id, "s": start_idx, "e": end_idx}).scalar() or 0
        f = session.execute(text(
            "SELECT count(*) FROM figures WHERE book_id = :b "
            "AND page_number IS NOT NULL AND page_number >= :s AND page_number < :e"
        ), {"b": book_id, "s": start_idx, "e": end_idx}).scalar() or 0
        if n >= 30 or f >= 15:
            done.add(key)

    return done


def _kill_pool_workers(executor) -> None:
    """Best-effort hard-kill of a ProcessPoolExecutor's worker children.

    Only used when a slice times out or a worker dies. A corrective cannot
    cancel an in-flight task, so without this a wedged Docling worker keeps
    burning a core forever (and could commit late, duplicating a retried
    range). os.kill(pid, 9) is TerminateProcess on Windows.
    """
    try:
        for pid in list(getattr(executor, "_processes", {}).keys()):
            try:
                os.kill(pid, 9)
            except OSError:
                pass
    except Exception:
        pass


def _run_slice_tasks(tasks: list[dict], workers: int, slice_timeout: int) -> dict[tuple[int, int], dict]:
    """Run slice tasks with a per-slice wall-clock timeout.

    Returns {(start_idx, end_idx): result_dict}. If any slice exceeds
    slice_timeout the pool is hard-killed and RuntimeError is raised, so the
    caller can retry the book with a smaller slice size instead of hanging
    forever on a pathological page.
    """
    from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

    executor = ProcessPoolExecutor(max_workers=workers)
    futures = {executor.submit(_process_slice_worker, t): t for t in tasks}
    deadlines = {f: time.monotonic() + slice_timeout for f in futures}
    results: dict[tuple[int, int], dict] = {}
    timed_out: list[dict] = []
    pool_ok = True
    try:
        while futures:
            for f in list(futures):
                if f.done():
                    try:
                        res = f.result()  # propagate worker exceptions
                    except BaseException:
                        pool_ok = False
                        raise
                    t = futures.pop(f)
                    deadlines.pop(f, None)
                    results[(t["start_idx"], t["end_idx"])] = res
            if not futures:
                break
            _ = wait(list(futures), timeout=2.0, return_when=FIRST_COMPLETED)
            now = time.monotonic()
            expired = [f for f in futures if deadlines[f] <= now]
            if expired:
                for f in expired:
                    timed_out.append(futures.pop(f))
                    deadlines.pop(f, None)
                pool_ok = False
                break
    finally:
        if pool_ok:
            # Success path: wait for every worker to exit so the Docling +
            # embedding model copies they hold are returned to the OS *before*
            # the next book's pool is spawned. Without this, workers from the
            # finished book are still resident while the next book's workers
            # are spawned, so model memory compounds across books until the
            # machine OOMs and slices time out in a cascade (the signature
            # "it was fine for the first N books" failure mode).
            executor.shutdown(wait=True, cancel_futures=True)
        else:
            executor.shutdown(wait=False, cancel_futures=True)
            _kill_pool_workers(executor)

    if timed_out:
        ranges = ", ".join(f"{t['start_idx']}-{t['end_idx']}" for t in timed_out)
        raise RuntimeError(
            f"slice(s) {ranges} exceeded the {slice_timeout}s timeout - "
            f"retrying the book will use a smaller slice size"
        )
    return results


def embed_figure_captions(session, book_id: int | None = None, batch: int = 128) -> int:
    """Embed captions of figures that have one but no caption_embedding yet. Returns the count."""
    from sqlalchemy import text as sql_text

    params = {"b": book_id} if book_id is not None else {}
    where = "AND book_id = :b" if book_id is not None else ""
    rows = session.execute(sql_text(
        f"SELECT id, caption FROM figures WHERE caption IS NOT NULL AND caption <> '' "
        f"AND caption_embedding IS NULL {where} ORDER BY id"
    ), params).fetchall()
    if not rows:
        return 0
    model = get_embedding_model()
    for start in range(0, len(rows), batch):
        part = rows[start:start + batch]
        vecs = model.encode([c for _, c in part], normalize_embeddings=True, batch_size=32)
        for (fid, _), vec in zip(part, vecs):
            session.execute(sql_text("UPDATE figures SET caption_embedding = CAST(:e AS vector) WHERE id = :i"),
                            {"e": str(vec.tolist()), "i": fid})
        session.commit()
    logger.info(f"Embedded {len(rows)} figure captions" + (f" for book {book_id}" if book_id is not None else ""))
    return len(rows)


def ingest_book(pdf_path: str | Path,
                title: str | None = None,
                workers: int | None = None,
                slice_size: int | None = None,
                slice_timeout: int = 600,
                ram_budget_mb: int | None = None) -> int:
    """Ingest a PDF book.

    Splits the conversion work into parallel page slices using a ProcessPoolExecutor
    to protect resources (Proposal 3). Books already in status "ready" are
    skipped. A book left in "processing"/"failed" by an interrupted run is NOT
    reprocessed from scratch: slices that already committed are detected by
    _committed_slices() and skipped, so each restart resumes the book exactly
    where it stopped instead of throwing its progress away.

    The pipeline is RAM-aware and never lets model copies pile up across books:

      * workers: number of parallel page-slice workers. None/<=0 -> auto-tuned
        to the currently free RAM (leaving MIN_FREE_RAM_MB for the OS/DB).
      * slice_size: page-slice width. None -> auto-shrunk when free RAM is low,
        so each in-flight unit stays a small chunk. You can still pass an
        explicit value to isolate a pathological page.
      * ram_budget_mb: how much of the machine's RAM may be used by worker
        models (None -> ~60% of total). Clamps how many workers can start.
      * slice_timeout: wall-clock budget per slice; a slice that exceeds it
        fails loudly (hard-killing the pool) instead of hanging forever.

    The pool created for a book is fully reaped (shutdown wait=True) before
    ingest_book returns, freeing the workers' Docling + embedding models, so
    the next book starts from a clean slate instead of accumulating memory.
    """
    path = Path(pdf_path)
    validate_pdf(path)

    if title is None:
        title = path.stem.replace("-", " ").replace("_", " ").title()

    session = SessionLocal()
    try:
        # Check if book already exists
        existing: Book | None = session.query(Book).filter(Book.filename == path.name).first()
        if existing and existing.status == "ready":
            logger.info(f"Book '{path.name}' already ingested successfully (id={existing.id}). Skipping.")
            return existing.id

        has_partial = False
        resume_slices: set[str] = set()
        if existing:
            has_partial = session.query(Chunk.id).filter(Chunk.book_id == existing.id).limit(1).first()
            if has_partial:
                # Reuse the record + already-committed slices; don't wipe progress.
                logger.info(
                    f"Book '{path.name}' died mid-ingest (status={existing.status}, "
                    f"id={existing.id}). Resuming - committed slices will be kept."
                )
                existing.status = "processing"
                existing.error_message = None
                existing.total_pages = None
                session.commit()
                book_id = existing.id
                if title is None:
                    title = existing.title
            else:
                # Empty leftover record - cleanly reset it.
                logger.info(f"Book '{path.name}' has status '{existing.status}' with no data. Re-ingesting from scratch.")
                session.delete(existing)
                session.commit()
                book = Book(title=title, filename=path.name, status="processing")
                session.add(book)
                session.commit()
                book_id = book.id
        else:
            # Brand-new book
            book = Book(title=title, filename=path.name, status="processing")
            session.add(book)
            session.commit()
            book_id = book.id
            logger.info(f"Book record created: id={book_id}, title='{title}'")

        logger.info(f"Book '{path.name}' (id={book_id}, title='{title}') ready for ingestion.")

        # RAM-aware auto-tuning: unless the caller pinned them explicitly, pick
        # the worker count and page-slice width from the RAM that is currently
        # free so parent + worker model copies + one slice of data all fit
        # comfortably. The previous book's pool was fully reaped by
        # _run_slice_tasks before we got here, so this probe reflects reality.
        free_mb = _available_ram_mb()
        total_mb = _total_ram_mb()
        if workers is None or workers <= 0:
            workers = auto_workers(free_mb, ram_budget_mb=ram_budget_mb)
        workers = max(1, int(workers))
        if slice_size is None:
            slice_size = auto_slice_size(free_mb)
        slice_size = max(1, int(slice_size))
        if free_mb < MIN_FREE_RAM_MB:
            workers = 1
        logger.info(
            f"RAM-aware plan for '{path.name}': {free_mb} MB free / {total_mb} MB total "
            f"-> {workers} worker(s), {slice_size}-page slices"
        )
        if free_mb < MIN_FREE_RAM_MB:
            logger.warning(
                f"Only {free_mb} MB free (floor is {MIN_FREE_RAM_MB} MB) - running with "
                f"1 worker and {slice_size}-page slices; the per-slice timeout still protects "
                f"against a hang."
            )

        try:
            from pypdf import PdfReader

            reader = PdfReader(path)
            num_pages = len(reader.pages)
            logger.info(f"Book '{path.name}' has {num_pages} pages. Processing in parallel slices of {slice_size}...")

            # Prepare all slice tasks, then drop the ones already committed.
            all_tasks = []
            for start_idx in range(0, num_pages, slice_size):
                end_idx = min(start_idx + slice_size, num_pages)
                all_tasks.append({
                    "pdf_path": str(path),
                    "book_id": book_id,
                    "title": title,
                    "start_idx": start_idx,
                    "end_idx": end_idx
                })

            # Only compute resume set when we reused a record with partial data.
            if existing and has_partial:
                resume_slices = _committed_slices(session, book_id, num_pages, slice_size)
                tasks = [t for t in all_tasks
                         if f"{t['start_idx']}-{t['end_idx']}" not in resume_slices]
                if resume_slices:
                    logger.info(f"Resume: {len(resume_slices)}/{len(all_tasks)} slices already committed - processing the remaining {len(tasks)}.")
            else:
                tasks = all_tasks

            if not tasks:
                logger.info(f"Book '{path.name}' has no pending slices - marking ready.")
                book = session.get(Book, book_id)
                if book:
                    book.status = "ready"
                    book.total_pages = num_pages
                    session.commit()
                return book_id

            total_parent_chunks = 0
            total_child_chunks = 0
            total_figures = 0

            # Execute slices in parallel (workers fits CPU resource boundaries
            # perfectly). Each slice has a wall-clock budget: a slice that hangs
            # (pathological page, OOM, wedged Docling worker) FAILS cleanly and
            # the pool is hard-killed, instead of hanging the whole book forever.
            results = _run_slice_tasks(tasks, workers, slice_timeout)

            # Verify and gather results
            for (start, end), res in results.items():
                if not res.get("success", False):
                    raise RuntimeError(f"Parallel slice ingestion {start}-{end} failed: {res.get('error')}")
                
                total_parent_chunks += res.get("parent_count", 0)
                total_child_chunks += res.get("child_count", 0)
                total_figures += res.get("fig_count", 0)
                logger.info(f"✓ Slice {start}-{end} complete.")

            # Embed printed figure captions so retrieval can match figures to questions.
            try:
                embed_figure_captions(session, book_id)
            except Exception as e:  # never fail a finished book over captions
                logger.warning(f"Figure caption embedding failed for book {book_id}: {e}")

            # Mark book ready
            book = session.get(Book, book_id)
            if book:
                book.status = "ready"
                book.total_pages = num_pages
                session.commit()

            logger.info(
                f"Ingestion complete: book_id={book_id}. "
                f"Total {total_parent_chunks} parent chunks, {total_child_chunks} child chunks, {total_figures} figures."
            )
            return book_id

        except Exception as e:
            session.rollback()
            book = session.get(Book, book_id)
            if book:
                book.status = "failed"
                book.error_message = str(e)[:500]
                session.commit()
            logger.error(f"Ingestion failed for '{path.name}': {e}")
            raise

    finally:
        session.close()
