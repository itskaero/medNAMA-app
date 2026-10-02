"""End-to-end checks through the Next.js proxy on :3000 (same path as the browser)."""
import json
import os
import sys
import time
import urllib.request

BASE = os.environ.get("MEDNAMA_BASE_URL", "http://localhost:3000")
TOKEN = os.environ["MEDNAMA_ADMIN_TOKEN"]
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def post(path, body, timeout=300):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers=H, method="POST")
    return urllib.request.urlopen(req, timeout=timeout)


def get(path):
    req = urllib.request.Request(BASE + path, headers=H)
    return json.load(urllib.request.urlopen(req, timeout=60))


def stream_chat(query, **extra):
    t0 = time.monotonic()
    res = post("/api/chat/query/stream", {"query": query, **extra})
    first_byte, stages, beats, final = None, [], 0, None
    buf = b""
    while True:
        chunk = res.read1(4096)
        if not chunk:
            break
        if first_byte is None:
            first_byte = time.monotonic() - t0
        buf += chunk
        while b"\n\n" in buf:
            raw, buf = buf.split(b"\n\n", 1)
            text = raw.decode()
            if text.startswith(":"):
                beats += 1
                continue
            ev = next((l[6:].strip() for l in text.splitlines() if l.startswith("event:")), "message")
            data = json.loads("\n".join(l[5:].strip() for l in text.splitlines() if l.startswith("data:")))
            if ev == "stage":
                stages.append(data["stage"])
            elif ev in ("answer", "error"):
                final = (ev, data)
    return time.monotonic() - t0, first_byte, stages, beats, final


mode = sys.argv[1] if len(sys.argv) > 1 else "chat"

if mode == "chat":
    questions = [
        ("define shock", {}),
        ("what is anemia", {}),
        ("causes of jaundice", {}),
        ("hi", {}),
        ("fluid of choice in hypertrophic pyloric stenosis", {}),
        ("paradoxical aciduria", {}),
        ("Gradenigo syndrome", {"book_id": 2}),
        ("DOC for absence seizure", {}),
    ]
    for q, extra in questions:
        total, fb, stages, beats, final = stream_chat(q, **extra)
        ev, data = final
        if ev == "answer":
            a = data["answer"]
            cites = sorted({f"{c['book_title']} p{c['page_number']}" for c in a["citations"]})[:3]
            print(f"{total:5.1f}s (1st byte {fb:.2f}s, {beats} heartbeats, stages {stages}) "
                  f"[{a.get('status')}/{a.get('grounding')}] {q!r}: {cites} | {a['answer_markdown'][:90]!r}")
        else:
            print(f"{total:5.1f}s ERROR {q!r}: {data}")

elif mode == "quiz":
    t0 = time.monotonic()
    start = json.load(post("/api/chat/generate-ai-quiz/jobs",
                           {"prompt": "acute appendicitis", "count": 10, "exam_profile": "fcps", "request_id": "e2e-appx-job-001"}))
    print(f"start: {start} in {time.monotonic() - t0:.2f}s")
    polls = 0
    while True:
        time.sleep(2.5)
        polls += 1
        job = get(f"/api/chat/generate-ai-quiz/jobs/{start['job_id']}")
        if job["status"] != "running":
            break
    r = job.get("result") or {}
    print(f"job {job['status']} after {time.monotonic() - t0:.1f}s ({polls} polls): n={r.get('total_questions')} "
          f"dup={r.get('duplicates_skipped')} grounding={r.get('grounding_counts')} detail={job.get('detail')}")
    again = json.load(post("/api/chat/generate-ai-quiz/jobs",
                           {"prompt": "acute appendicitis", "count": 10, "exam_profile": "fcps", "request_id": "e2e-appx-job-001"}))
    print("restart same request_id ->", again)

elif mode == "report":
    rep = json.load(post("/api/reports", {"kind": "chat", "reason": "E2E test report - please dismiss",
                                          "question": "e2e", "answer_excerpt": "e2e"}))
    print("created:", rep["id"], rep["status"])
    listed = get("/api/reports")
    print("open reports:", [r["id"] for r in listed])
    req = urllib.request.Request(f"{BASE}/api/reports/{rep['id']}", data=json.dumps({"status": "dismissed"}).encode(),
                                 headers=H, method="PATCH")
    print("dismissed:", json.load(urllib.request.urlopen(req))["status"])
