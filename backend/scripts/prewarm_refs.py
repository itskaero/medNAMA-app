"""Add textbook page references to the most-asked past-paper explanations (app/textbook_refs.py).

Works over HTTP against a running medNAMA (as scripts/prewarm_twists.py). Questions already done (added,
disputed or no reference found) are skipped, so re-running continues where it stopped. Disputed keys land in
the admin's Reports queue.

    python scripts/prewarm_refs.py --base-url http://localhost:3000 --token-file token.txt --limit 500

Move the result to the NAS with scripts/refs_transfer.py.
"""

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prewarm_harder import call_json  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token")
    parser.add_argument("--token-file")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--min-years", type=int, default=1, help="only questions asked in at least this many years")
    args = parser.parse_args()
    token = args.token or open(args.token_file, encoding="utf-8").read().strip()

    status, body = call_json(args.base_url, token, "GET", f"/api/admin/refs/todo?limit={args.limit}&min_years={args.min_years}")
    if status != 200:
        raise SystemExit(f"Could not list questions ({status}): {body}")
    todo = body["mcq_ids"]
    print(f"{len(todo)} questions to reference", flush=True)
    counts: Counter = Counter()
    t0 = time.time()
    for i, mid in enumerate(todo, 1):
        status, r = call_json(args.base_url, token, "POST", f"/api/admin/refs/{mid}", timeout=300)
        for _ in range(6):   # backend restarting: wait instead of skipping
            if status not in (0, 502, 503, 504) or "not configured" in str(r.get("detail", "")):
                break
            time.sleep(30)
            status, r = call_json(args.base_url, token, "POST", f"/api/admin/refs/{mid}", timeout=300)
        if "not configured" in str(r.get("detail", "")):
            raise SystemExit("The backend has no AI key (rebuild the local stack with the root .env).")
        state = r.get("status", f"error {status}")
        counts[state] += 1
        if i % 10 == 0 or i == len(todo):
            left = (time.time() - t0) / i * (len(todo) - i) / 60
            print(f"[{i}/{len(todo)}] {dict(counts)} · ~{left:.0f} min left", flush=True)
    print(f"done: {dict(counts)}")


if __name__ == "__main__":
    main()
