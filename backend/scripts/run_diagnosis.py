"""One-command retrieval diagnosis against a running medNAMA backend container.

Works the same on the Windows PC and on the NAS: it finds the backend
container, copies diagnose_query.py into it, runs it for each query, and saves
the combined output to a report file you can paste back for review.

Read-only: it never writes to the database, never restarts or rebuilds a
container, and does not call DeepSeek. The only change is one file copied into
the container's /tmp (gone when the container is recreated).

Usage (from the repo root or backend/, on the PC or the NAS):

    python backend/scripts/run_diagnosis.py                       # full mixed-subject suite (~16 queries)
    python backend/scripts/run_diagnosis.py --hps                 # only the pyloric stenosis queries
    python backend/scripts/run_diagnosis.py "paradoxical aciduria" "drug of choice for absence seizures"
    python backend/scripts/run_diagnosis.py --container mednama-backend --threshold 0.55
    python backend/scripts/run_diagnosis.py --local               # no Docker: run in this Python env

--local needs the backend's Python dependencies installed and DATABASE_URL (or
backend/.env) pointing at a reachable database, e.g. the PC's localhost:5433.
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
DIAGNOSE_SCRIPT = SCRIPTS_DIR / "diagnose_query.py"
REPO_ROOT = SCRIPTS_DIR.parent.parent

# (category, query): mixed subjects and phrasing styles, one or more per ingested book,
# so the report shows whether refusals are HPS-specific or systemic.
QUERY_SUITE = [
    ("of-choice / surgery", "fluid of choice in hypertrophic pyloric stenosis"),
    ("short term / surgery", "paradoxical aciduria"),
    ("full question / surgery", "Why does paradoxical aciduria occur in pyloric stenosis?"),
    ("vignette / surgery", "A 5 week old boy has projectile non-bilious vomiting and a palpable olive-shaped mass. What is the next step before surgery?"),
    ("of-choice / pharmacology", "drug of choice for absence seizures"),
    ("antidote / pharmacology", "antidote for heparin overdose"),
    ("abbreviation / medicine", "management of DKA"),
    ("most common / medicine", "most common cause of community acquired pneumonia"),
    ("investigation / ENT", "investigation of choice for cholesteatoma"),
    ("eponym / ENT", "Gradenigo syndrome triad"),
    ("mechanism / microbiology", "mechanism of action of cholera toxin"),
    ("applied / anatomy", "nerve injured in fracture of surgical neck of humerus"),
    ("concept / physiology", "Frank-Starling law of the heart"),
    ("most common / pathology", "most common site of carcinoid tumour"),
    ("US spelling / pathology", "causes of hypochloremic metabolic alkalosis"),
    ("UK spelling / pathology", "causes of hypochloraemic metabolic alkalosis"),
    # Topics covered by several books: "books" should be >= 2.
    ("multi-book / ENT+surgery", "pathology of angiofibroma"),
    ("multi-book / surgery+anatomy", "hypertrophic pyloric stenosis"),
    ("multi-book / medicine+pharm", "insulin regimen in diabetic ketoacidosis"),
    ("multi-book / path+physio", "causes of jaundice"),
    ("multi-book / path+medicine", "tuberous sclerosis"),
]
HPS_QUERIES = [q for c, q in QUERY_SUITE if "surgery" in c]
CONTAINER_PATH = "/tmp/diagnose_query.py"
QUERY_MARKER = "### QUERY: "  # must match diagnose_query.py


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def find_backend_container(explicit: str | None) -> str:
    """Return the running backend container name (explicit, mednama-backend, or any '*backend*')."""
    try:
        result = run(["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"])
    except FileNotFoundError:
        raise SystemExit("docker was not found on PATH. Start Docker Desktop, or use --local.")
    if result.returncode != 0:
        raise SystemExit(f"'docker ps' failed (is Docker running?):\n{result.stderr.strip()}")

    rows = [line.split("\t", 1) for line in result.stdout.splitlines() if line.strip()]
    names = [r[0] for r in rows]

    if explicit:
        if explicit not in names:
            raise SystemExit(f"Container '{explicit}' is not running. Running: {', '.join(names) or '(none)'}")
        return explicit
    if "mednama-backend" in names:
        return "mednama-backend"
    candidates = [r[0] for r in rows if "backend" in r[0].lower() or (len(r) > 1 and "backend" in r[1].lower())]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise SystemExit(
            "No running backend container found. Start the stack (docker compose up -d), "
            f"or pass --container NAME. Running: {', '.join(names) or '(none)'}"
        )
    raise SystemExit(f"Several backend-like containers are running ({', '.join(candidates)}). Pass --container NAME.")


def container_backend_dir(container: str) -> str:
    """Find the directory in the container that holds the `app` package (the image's WORKDIR, normally /app/backend)."""
    probe = run([
        "docker", "exec", container, "python", "-c",
        "import app, os; print(os.path.dirname(os.path.dirname(os.path.abspath(app.__file__))))",
    ])
    if probe.returncode == 0 and probe.stdout.strip():
        return probe.stdout.strip().splitlines()[-1]
    return "/app/backend"


def diagnose_in_container(container: str, queries: list[str], extra: list[str]) -> list[tuple[str, str]]:
    copied = run(["docker", "cp", str(DIAGNOSE_SCRIPT), f"{container}:{CONTAINER_PATH}"])
    if copied.returncode != 0:
        raise SystemExit(f"docker cp failed:\n{copied.stderr.strip()}")
    workdir = container_backend_dir(container)
    print(f"Using container '{container}' (backend dir {workdir}).")

    # One process for all queries so the models load once. PYTHONPATH makes
    # `import app` resolve even though the script runs from /tmp.
    return run_and_split([
        "docker", "exec", "-w", workdir, "-e", f"PYTHONPATH={workdir}", container,
        "python", CONTAINER_PATH, *queries, *extra,
    ], queries)


def diagnose_locally(queries: list[str], extra: list[str]) -> list[tuple[str, str]]:
    return run_and_split([sys.executable, str(DIAGNOSE_SCRIPT), *queries, *extra], queries)


def run_and_split(cmd: list[str], queries: list[str]) -> list[tuple[str, str]]:
    """Run diagnose_query.py once for all queries and split its output per query."""
    print(f"Diagnosing {len(queries)} queries (first one waits for the models to load)...\n")
    result = run(cmd)
    print(result.stdout)
    if result.returncode != 0:
        print(f"[stderr]\n{result.stderr}")

    sections: dict[str, str] = {}
    current = None
    for line in result.stdout.splitlines(keepends=True):
        if line.startswith(QUERY_MARKER):
            current = line[len(QUERY_MARKER):].rstrip("\n")
            sections[current] = ""
        elif current is not None:
            sections[current] += line
    stderr_note = f"\n[stderr]\n{result.stderr}" if result.returncode != 0 else ""
    return [(q, sections.get(q, "(no output)") + ("" if q in sections else stderr_note)) for q in queries]


def build_summary(outputs: list[tuple[str, str]], threshold: float) -> str:
    """Tabulate the SUMMARY line diagnose_query.py prints for each query."""
    categories = {q: c for c, q in QUERY_SUITE}
    rows = []
    for q, text in outputs:
        line = next((l for l in reversed(text.splitlines()) if l.startswith("SUMMARY|")), None)
        if line:
            _, vec, kw, conf, gate, books, figs, top = line.split("|", 7)
        else:
            vec = kw = conf = books = figs = "-"
            gate, top = "ERROR", "see output above"
        rows.append((categories.get(q, "custom"), q[:55], vec, kw, conf, gate, books, figs, top))

    header = ("type", "query", "vector", "kw hits", "rerank", "context", "books", "figs", "top context source")
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = ["== Summary ==", fmt.format(*header), fmt.format(*("-" * w for w in widths))]
    lines += [fmt.format(*r) for r in rows]
    failed = sum(1 for r in rows if r[5] != "YES")
    lines.append(f"\n{failed}/{len(rows)} queries got no textbook context (or errored).")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("queries", nargs="*", help="queries to diagnose (default: mixed-subject suite)")
    parser.add_argument("--hps", action="store_true", help="only the pyloric stenosis queries")
    parser.add_argument("--container", help="backend container name (default: auto-detect)")
    parser.add_argument("--local", action="store_true", help="run in this Python env instead of Docker")
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--book-id", type=int, default=None)
    parser.add_argument("--out", help="report file (default: diagnosis_<timestamp>.txt in the repo root)")
    args = parser.parse_args()

    queries = args.queries or (HPS_QUERIES if args.hps else [q for _, q in QUERY_SUITE])
    extra = ["--threshold", str(args.threshold)]
    if args.book_id is not None:
        extra += ["--book-id", str(args.book_id)]

    if args.local:
        outputs = diagnose_locally(queries, extra)
        where = "local Python environment"
    else:
        container = find_backend_container(args.container)
        outputs = diagnose_in_container(container, queries, extra)
        where = f"container {container}"

    summary = build_summary(outputs, args.threshold)
    print(summary)

    out_path = Path(args.out) if args.out else REPO_ROOT / f"diagnosis_{datetime.now():%Y%m%d_%H%M%S}.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"medNAMA retrieval diagnosis - {datetime.now():%Y-%m-%d %H:%M} - {where}\n\n")
        f.write(summary + "\n\n")
        for q, text in outputs:
            f.write(f"{'=' * 70}\n>>> {q}\n{'=' * 70}\n{text}\n")
    print(f"Report saved to {out_path}")


if __name__ == "__main__":
    main()
