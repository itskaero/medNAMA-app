"""OCR a scanned book page-by-page into Markdown, layout-aware and resumable.

Used for private source books (e.g. a recall/pearls book) whose embedded text
layer is garbled. Docling's layout model separates columns before EasyOCR reads
them, so "question = answer" recall lines are not interleaved across columns.
Everything runs locally; no page images leave the machine.

Output: data/private/<name>/pages/p0001.md ... (one file per page; existing
files are skipped, so an interrupted run just continues).

Usage (from backend/):
    python scripts/ocr_book.py ../rafiullah-14th.pdf --name rafiullah
    python scripts/ocr_book.py ../rafiullah-14th.pdf --name rafiullah --pages 21-40 --workers 2
"""

import argparse
import os
import sys
import tempfile
import time
from multiprocessing import get_context
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _converter():
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import EasyOcrOptions, PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = True
    opts.do_table_structure = False
    opts.ocr_options = EasyOcrOptions(force_full_page_ocr=True, lang=["en"])
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


def _worker(args: tuple[str, list[int], str]) -> int:
    pdf_path, pages, out_dir = args
    import fitz

    src = fitz.open(pdf_path)
    conv = _converter()
    done = 0
    for pno in pages:
        target = Path(out_dir) / f"p{pno:04d}.md"
        if target.exists():
            continue
        single = fitz.open()
        single.insert_pdf(src, from_page=pno - 1, to_page=pno - 1)
        tmp = Path(tempfile.gettempdir()) / f"ocr_{os.getpid()}_{pno}.pdf"
        single.save(tmp)
        single.close()
        t0 = time.time()
        try:
            md = conv.convert(str(tmp)).document.export_to_markdown()
        except Exception as e:  # keep going; the page can be retried later
            print(f"page {pno}: FAILED {type(e).__name__}: {e}", flush=True)
            continue
        finally:
            tmp.unlink(missing_ok=True)
        tmp_out = target.with_suffix(".tmp")
        tmp_out.write_text(md, encoding="utf-8")
        tmp_out.replace(target)  # atomic: a half-written page is never mistaken for done
        done += 1
        print(f"page {pno}: {len(md)} chars in {time.time() - t0:.0f}s", flush=True)
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf")
    parser.add_argument("--name", required=True, help="folder name under data/private/")
    parser.add_argument("--pages", help="range like 21-40 (default: all)")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()

    import fitz

    pdf_path = str(Path(args.pdf).resolve())
    total = fitz.open(pdf_path).page_count
    if args.pages:
        a, b = (int(x) for x in args.pages.split("-"))
        pages = list(range(max(1, a), min(total, b) + 1))
    else:
        pages = list(range(1, total + 1))
    out_dir = REPO_ROOT / "data" / "private" / args.name / "pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [p for p in pages if not (out_dir / f"p{p:04d}.md").exists()]
    print(f"{len(todo)} of {len(pages)} pages to OCR -> {out_dir} ({args.workers} workers)", flush=True)
    if not todo:
        return
    # Interleave pages across workers so progress is even.
    shards = [todo[i::args.workers] for i in range(args.workers)]
    with get_context("spawn").Pool(args.workers) as pool:
        counts = pool.map(_worker, [(pdf_path, shard, str(out_dir)) for shard in shards if shard])
    print(f"Done: {sum(counts)} pages written.", flush=True)


if __name__ == "__main__":
    sys.exit(main())
