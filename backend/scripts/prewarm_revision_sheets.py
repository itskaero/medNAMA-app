"""Generate book-scope revision sheets ahead of time, for the most useful chapter/section
headings of each ready book (and any topics you name).

Works over HTTP against a running medNAMA (PC or NAS), so the sheets are written inside
the running backend: no second process loads the embedding/reranker models (the NAS has
little RAM). Already-cached sheets return instantly, so re-running only fills gaps.

Usage (any machine that can reach the site; the token must belong to an admin):
    python scripts/prewarm_revision_sheets.py --base-url http://192.168.1.34:3000 --token-file token.txt
    python scripts/prewarm_revision_sheets.py --base-url http://localhost:3000 --token eyJ... \
        --books 40,43 --headings 8
    python scripts/prewarm_revision_sheets.py --base-url http://localhost:3000 --token eyJ... \
        --topics "sepsis,opioid analgesics,acute glomerulonephritis" --length full

Each sheet costs one LLM call for a fresh scope (~30-60s); --headings 3 on ten books is
thirty calls, so keep the first run small. Cached scopes (already saved) skim past it.
"""

import argparse
import json
import time
import urllib.error
import urllib.request


def call(base: str, token: str, method: str, path: str, body: dict | None = None, timeout: int = 400
         ) -> tuple[int, any]:
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token")
    parser.add_argument("--token-file")
    parser.add_argument("--books", default="", help="comma-separated book ids; default: every ready book")
    parser.add_argument("--headings", type=int, default=3,
                        help="most heading-like chapter/section names to sheet per book (default 3)")
    parser.add_argument("--topics", default="",
                        help="comma-separated topics to sheet in every selected book, in addition to headings")
    parser.add_argument("--length", default="quick", choices=["quick", "full"])
    args = parser.parse_args()
    token = args.token or open(args.token_file, encoding="utf-8").read().strip()

    status, books = call(args.base_url, token, "GET", "/api/study/revision-books")
    if status != 200:
        raise SystemExit(f"Could not read the books ({status}): {books}")
    wanted = [int(b) for b in args.books.split(",") if b.strip()]
    books = [b for b in books if not wanted or b["id"] in wanted]

    jobs: list[dict] = []
    for b in books:
        status, headings = call(args.base_url, token, "GET",
                                f"/api/study/revision-chapters?book_ids={b['id']}&q=")
        if status != 200:
            print(f"  no headings for {b['title']} ({status})", flush=True)
            continue
        for h in headings[:args.headings]:
            jobs.append({"book_ids": [b["id"]], "chapter": h, "length": args.length})
        for t in (x.strip() for x in args.topics.split(",") if x.strip()):
            jobs.append({"book_ids": [b["id"]], "topic": t, "length": args.length})
    if not jobs:
        raise SystemExit("Nothing to prewarm (no books, or no headings/topics requested).")
    print(f"{len(jobs)} sheets across {len(books)} books (each fresh scope is one LLM call)", flush=True)

    ok = failures = 0
    for n, scope in enumerate(jobs, 1):
        t0 = time.time()
        status, body = call(args.base_url, token, "POST", "/api/study/revision-sheet", scope)
        state = ("cached" if body.get("cached") else "written" if status == 200
                 else f"FAILED {status} {body.get('detail', '')}")
        if status == 200:
            ok += 1
        else:
            failures += 1
        where = scope.get("chapter") or scope.get("topic") or "?"
        cov = body.get("coverage", {})
        print(f"[{n}/{len(jobs)}] {body.get('label', where)[:55]:55} {state} ({time.time() - t0:.0f}s, "
              f"{len(body.get('citations', []))} refs, read {cov.get('read', '?')})", flush=True)
    print(f"done: {ok} ok, {failures} failed")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()