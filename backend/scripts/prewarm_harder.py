"""Write harder versions ahead of time, per topic, so a student's harder set starts from ready-made questions.

Works over HTTP against a running medNAMA (as scripts/prewarm_twists.py), so the questions are written inside
the running backend. For each subject|topic of the shared list and each level (4 and 5/5) it asks for
--per-topic new harder versions of questions that have none yet at that level ("stock" requests skip what
already exists), so re-running grows the stock without repeating it. A harder set then takes its questions
from this stock first (app/hardening.py _ready_made) and writes only the rest.

    python scripts/prewarm_harder.py --base-url http://localhost:3000 --token-file token.txt --per-topic 4
    python scripts/prewarm_harder.py ... --subjects Anatomy Physiology --levels 4 --limit 10

Move the stock to the NAS with scripts/hardened_transfer.py.
"""

import argparse
import http.client
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.taxonomy import SUBJECTS, topics_of  # noqa: E402


def call_json(base: str, token: str, method: str, path: str, body: dict | None = None,
              timeout: int = 120) -> tuple[int, dict]:
    req = urllib.request.Request(base.rstrip("/") + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as e:
        return 0, {"detail": str(e)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token")
    parser.add_argument("--token-file")
    parser.add_argument("--per-topic", type=int, default=4, help="new harder versions per topic and level")
    parser.add_argument("--levels", type=int, nargs="+", default=[4, 5])
    parser.add_argument("--subjects", nargs="+", default=SUBJECTS)
    parser.add_argument("--limit", type=int, default=0, help="stop after this many topic requests (0 = all)")
    parser.add_argument("--max-wait", type=int, default=1800, help="seconds to wait for one topic")
    args = parser.parse_args()
    token = args.token or open(args.token_file, encoding="utf-8").read().strip()

    todo = [(s, t, lvl) for lvl in args.levels for s in args.subjects for t in topics_of(s)]
    if args.limit:
        todo = todo[: args.limit]
    kept = failed = 0
    t0 = time.time()
    for i, (subject, topic, level) in enumerate(todo, 1):
        body = {"scope": {"sources": ["past", "bank"], "topics": [f"{subject}|{topic}"]},
                "num_questions": args.per_topic, "difficulty": level, "stock": True,
                "label": f"{subject} · {topic}", "request_id": f"stock{level}-{int(time.time())}-{i}"}
        status, job = call_json(args.base_url, token, "POST", "/api/chat/harden/jobs", body)
        if "not configured" in str(job.get("detail", "")):
            raise SystemExit("The backend has no AI key (rebuild the local stack with the root .env).")
        if status != 202:
            print(f"[{i}/{len(todo)}] {subject} · {topic} ({level}/5): not started ({status} {job.get('detail')})",
                  flush=True)
            failed += 1
            continue
        start = time.time()
        while True:
            time.sleep(5)
            status, j = call_json(args.base_url, token, "GET", f"/api/chat/harden/jobs/{job['job_id']}")
            if status == 200 and j.get("status") in ("done", "failed"):
                break
            if time.time() - start > args.max_wait:
                j = {"status": "timeout"}
                break
        got = (j.get("result") or {}).get("harder", 0) if j.get("status") == "done" else 0
        kept += got
        failed += int(j.get("status") != "done")
        rate = (time.time() - t0) / i
        print(f"[{i}/{len(todo)}] {subject} · {topic} ({level}/5): {j.get('status')}, {got} new"
              f"{' - ' + str(j.get('detail')) if j.get('status') == 'failed' else ''}"
              f" · ~{rate * (len(todo) - i) / 60:.0f} min left", flush=True)
    print(f"done: {kept} harder versions written, {failed} topic requests failed")


if __name__ == "__main__":
    main()
