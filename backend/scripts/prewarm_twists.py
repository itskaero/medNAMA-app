"""Write Past-paper Twists ahead of time for the most-asked past-paper questions.

Works over HTTP against a running medNAMA (PC or NAS), so the twists are written inside the running
backend: no second process loads the embedding/reranker models (the NAS has little RAM). One question
at a time; seeds that already have twists are skipped, so re-running only fills gaps.

Usage (any machine that can reach the site; the token must belong to an admin):
    python scripts/prewarm_twists.py --base-url http://192.168.1.34:3000 --token-file token.txt
    python scripts/prewarm_twists.py --base-url http://localhost:3000 --token eyJ... --min-years 2 --limit 20
"""

import argparse
import http.client
import json
import time
import urllib.error
import urllib.request


def call(base: str, token: str, method: str, path: str, timeout: int = 120) -> tuple[int, dict]:
    req = urllib.request.Request(base.rstrip("/") + path, method=method, data=b"" if method == "POST" else None,
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as e:
        return 0, {"detail": str(e)}   # backend restarting (a deploy or rebuild): the caller retries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token")
    parser.add_argument("--token-file")
    parser.add_argument("--min-years", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0, help="stop after this many seeds (0 = all)")
    parser.add_argument("--max-wait", type=int, default=600, help="seconds to wait for one seed")
    args = parser.parse_args()
    token = args.token or open(args.token_file, encoding="utf-8").read().strip()

    status, body = call(args.base_url, token, "GET", f"/api/twists/seeds?min_years={args.min_years}")
    if status != 200:
        raise SystemExit(f"Could not list seeds ({status}): {body}")
    have = set(body["with_twists"])
    todo = [s for s in body["seeds"] if s not in have]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(body['seeds'])} seeds asked in >= {args.min_years} years; {len(have)} done; {len(todo)} to write",
          flush=True)
    written = failed = 0
    for n, seed in enumerate(todo, 1):
        t0 = time.time()
        status, body = call(args.base_url, token, "POST", f"/api/mcqs/{seed}/twists")
        for _ in range(8):   # the backend was down or restarting: wait for it instead of skipping the seed
            if status not in (0, 500, 502, 503, 504):
                break
            time.sleep(30)
            status, body = call(args.base_url, token, "POST", f"/api/mcqs/{seed}/twists")
        while ((status in (200, 429) and body.get("status") in ("running", None)) or status in (0, 502, 503, 504)) \
                and time.time() - t0 < args.max_wait:
            time.sleep(5 if status == 200 else 20)
            status, body = call(args.base_url, token, "GET", f"/api/mcqs/{seed}/twists")
        if body.get("status") == "done":
            written += 1
            kinds = ", ".join(t.get("twist_type") or "?" for t in body.get("twists", []))
            state = f"{len(body.get('twists', []))} twists ({kinds})"
        else:
            failed += 1
            state = f"FAILED {status} {body.get('status')} {body.get('detail', '')}"[:160]
        print(f"[{n}/{len(todo)}] seed {seed}: {state} ({time.time() - t0:.0f}s)", flush=True)
    print(f"done: {written} seeds written, {failed} failed")


if __name__ == "__main__":
    main()
