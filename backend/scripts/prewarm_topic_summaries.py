"""Generate Rapid Review summaries ahead of time, for every subject and topic of a past-paper exam.

Works over HTTP against a running medNAMA (PC or NAS), so the summaries are written inside the
running backend: no second process loads the embedding/reranker models (the NAS has little RAM).
Already-cached summaries return instantly, so re-running only fills gaps.

Usage (any machine that can reach the site; the token must belong to an admin):
    python scripts/prewarm_topic_summaries.py --base-url http://192.168.1.34:3000 --token-file token.txt
    python scripts/prewarm_topic_summaries.py --base-url http://localhost:3000 --token eyJ... --exam "FCPS Part 1"
"""

import argparse
import json
import time
import urllib.error
import urllib.request


def post(base: str, token: str, path: str, body: dict, timeout: int = 300) -> tuple[int, dict]:
    req = urllib.request.Request(base.rstrip("/") + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
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
    parser.add_argument("--exam", default="FCPS Part 1")
    parser.add_argument("--axes", default="subject,topic")
    args = parser.parse_args()
    token = args.token or open(args.token_file, encoding="utf-8").read().strip()

    status, scope = post(args.base_url, token, "/api/past-papers/scope", {"exam": args.exam})
    if status != 200:
        raise SystemExit(f"Could not read the exam's labels ({status}): {scope}")
    jobs = [(axis, f["label"]) for axis in args.axes.split(",") for f in scope["facets"].get(axis, [])]
    print(f"{len(jobs)} summaries for {args.exam}", flush=True)
    for n, (axis, label) in enumerate(jobs, 1):
        t0 = time.time()
        status, body = post(args.base_url, token, "/api/study/topic-summary", {"exam": args.exam, "tags": {axis: [label]}})
        state = "cached" if body.get("cached") else ("written" if status == 200 else f"FAILED {status} {body.get('detail', '')}")
        print(f"[{n}/{len(jobs)}] {axis:8} {label[:45]:45} {state} ({time.time() - t0:.0f}s, "
              f"{len(body.get('citations', []))} refs)", flush=True)


if __name__ == "__main__":
    main()
