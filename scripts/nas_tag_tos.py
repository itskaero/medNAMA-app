#!/usr/bin/env python3
"""Tag the NAS database's questions with their TOS topics, running the tagger on THIS PC.

backend/scripts/tag_tos.py loads tens of thousands of question embeddings at a time. Run inside the NAS
container (3.8 GB RAM) it swapped the box into an hour-long hang on 2026-10-14, so never run it there.
This opens an SSH tunnel to the NAS's Postgres (scripts/nas_tunnel.py) and runs the tagger here; the NAS only
answers queries and receives the final tag writes.

Usage (repo root, .venv-gpu):
    python scripts/nas_tag_tos.py --save-vectors-first        # make topic vectors here, then tag the NAS
    python scripts/nas_tag_tos.py --dry-run --exam pget       # any tag_tos.py arguments pass through
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nas_tunnel import ROOT, nas_database_url  # noqa: E402

PYTHON = str(ROOT / ".venv-gpu" / "Scripts" / "python.exe") if os.name == "nt" else sys.executable


def main() -> int:
    args = sys.argv[1:]
    if "--save-vectors-first" in args:
        args.remove("--save-vectors-first")
        vectors = str(Path(tempfile.gettempdir()) / "tos_vectors.npz")
        print("Making topic vectors on this PC (dev DB, dry run)...", flush=True)
        rc = subprocess.call([PYTHON, "scripts/tag_tos.py", "--dry-run", "--save-vectors", vectors],
                             cwd=str(ROOT / "backend"), env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        if rc:
            return rc
        args += ["--vectors", vectors]
    with nas_database_url() as url:
        env = {**os.environ, "DATABASE_URL": url, "PYTHONIOENCODING": "utf-8", "TOS_TAG_CACHE_ROWS": "400000"}
        rc = subprocess.call([PYTHON, "scripts/tag_tos.py", *args], cwd=str(ROOT / "backend"), env=env)
    print("tag_tos.py exit", rc, flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
