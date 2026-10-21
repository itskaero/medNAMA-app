#!/usr/bin/env python3
"""Build and transfer updated medNAMA backend & frontend Docker images to Synology NAS.

Usage:
  python scripts/transfer_images_to_nas.py
  python scripts/transfer_images_to_nas.py --skip-build   # if already built/saved
  python scripts/transfer_images_to_nas.py --frontend-only # only rebuild & deploy frontend
  python scripts/transfer_images_to_nas.py --backend-only  # only rebuild & deploy backend
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    import paramiko
except ImportError:
    paramiko = None

REPO_ROOT = Path(__file__).resolve().parent.parent
BUNDLE_DIR = REPO_ROOT / "mednama-nas-deploy"

NAS_HOST = os.getenv("NAS_HOST", "192.168.1.42")
NAS_PORT = int(os.getenv("NAS_PORT", "22"))
NAS_USER = os.getenv("NAS_USER", "root")
NAS_KEY = os.getenv("NAS_KEY", str(REPO_ROOT / "_local" / "nas_key"))
NAS_DEPLOY_DIR = "/DATA/mednama/mednama-nas-deploy"
NAS_APP_DIR = "/DATA/mednama/app"


def log(msg: str):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def connect_ssh():
    if not paramiko:
        raise RuntimeError("paramiko is required. Install via `pip install paramiko` or use .venv-gpu.")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    key_path = NAS_KEY if (NAS_KEY and os.path.isfile(NAS_KEY)) else None
    
    log(f"Connecting to NAS at {NAS_USER}@{NAS_HOST}:{NAS_PORT}...")
    client.connect(
        NAS_HOST, port=NAS_PORT, username=NAS_USER,
        key_filename=key_path,
        timeout=20, banner_timeout=30, auth_timeout=30,
        look_for_keys=False, allow_agent=False,
    )
    return client


def ssh_exec_stream(client, cmd: str, timeout: int = 1800) -> int:
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    # Channel stream
    while True:
        line = stdout.readline()
        if not line:
            break
        print(line.rstrip(), flush=True)
    rc = stdout.channel.recv_exit_status()
    err = stderr.read().decode("utf-8", "replace").strip()
    if err and rc != 0:
        print(f"Error output: {err}", flush=True)
    return rc


def upload_with_progress(sftp, local_path: Path, remote_path: str):
    total_size = local_path.stat().st_size
    start_time = time.time()
    last_print = 0

    def progress_callback(transferred, total):
        nonlocal last_print
        now = time.time()
        if now - last_print >= 1.0 or transferred == total:
            last_print = now
            pct = (transferred / total) * 100 if total else 0
            mb_done = transferred / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            elapsed = now - start_time
            speed = mb_done / elapsed if elapsed > 0 else 0
            sys.stdout.write(
                f"\r  Uploading {local_path.name}: {mb_done:.1f} / {mb_total:.1f} MB ({pct:.1f}%) @ {speed:.2f} MB/s...   "
            )
            sys.stdout.flush()

    sftp.put(str(local_path), remote_path, callback=progress_callback)
    sys.stdout.write("\n")
    sys.stdout.flush()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def run_deployment(args):
    log("=====================================================================")
    log("medNAMA Docker Images Build & Transfer -> Synology NAS")
    log("=====================================================================")

    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    backend_tar = BUNDLE_DIR / "mednama-backend.tar"
    frontend_tar = BUNDLE_DIR / "mednama-frontend.tar"

    # Step 1: Pre-checks & Build
    if not args.skip_build:
        log("[1/5] Building Docker images locally...")
        compose_file = REPO_ROOT / "NAS" / "docker-compose.yaml"
        build_services = []
        if not args.frontend_only:
            build_services.append("backend")
        if not args.backend_only:
            build_services.append("frontend")

        cmd = ["docker", "compose", "--env-file", str(REPO_ROOT / ".env"), "-f", str(compose_file), "build"] + build_services
        log(f"      Running: {' '.join(cmd)}")
        res = subprocess.run(cmd, cwd=str(REPO_ROOT))
        if res.returncode != 0:
            raise RuntimeError(f"Docker build failed with code {res.returncode}")

        # Step 2: Save tar files
        log("[2/5] Exporting image tar archives...")
        if not args.frontend_only:
            log(f"      Saving nas-backend:latest -> {backend_tar.name}...")
            t0 = time.time()
            res = subprocess.run(["docker", "save", "nas-backend:latest", "-o", str(backend_tar)])
            if res.returncode != 0:
                raise RuntimeError("docker save nas-backend failed")
            mb = backend_tar.stat().st_size / (1024 * 1024)
            log(f"      Saved {mb:.1f} MB in {time.time() - t0:.1f}s")

        if not args.backend_only:
            log(f"      Saving nas-frontend:latest -> {frontend_tar.name}...")
            t0 = time.time()
            res = subprocess.run(["docker", "save", "nas-frontend:latest", "-o", str(frontend_tar)])
            if res.returncode != 0:
                raise RuntimeError("docker save nas-frontend failed")
            mb = frontend_tar.stat().st_size / (1024 * 1024)
            log(f"      Saved {mb:.1f} MB in {time.time() - t0:.1f}s")
    else:
        log("[1-2/5] Skipping build (--skip-build requested)...")

    # Step 3: Compute Checksums & Manifest
    log("[3/5] Computing checksums...")
    sums_lines = []
    for tar in [backend_tar, frontend_tar]:
        if tar.exists():
            log(f"      Hashing {tar.name}...")
            sums_lines.append(f"{sha256_file(tar)}  ./{tar.name}")
    
    sha_file = BUNDLE_DIR / "SHA256SUMS"
    sha_file.write_text("\n".join(sums_lines) + "\n", encoding="utf-8")

    # Update manifest
    manifest_file = BUNDLE_DIR / "MANIFEST.txt"
    manifest_lines = [
        "medNAMA NAS bundle",
        f"built:          {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "git branch:     main",
        "backend image:  nas-backend:latest",
        "frontend image: nas-frontend:latest",
        "database dump:  none (retaining live NAS database)",
    ]
    manifest_file.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")

    # Step 4: SFTP Upload to NAS
    log("[4/5] Uploading image bundle to NAS...")
    client = connect_ssh()
    try:
        # Ensure remote directory
        client.exec_command(f"mkdir -p {NAS_DEPLOY_DIR}/app/docker")
        sftp = client.open_sftp()
        try:
            # Upload scripts and manifests
            for rel in ["deploy.sh", "rollback.sh", "restore_nas.sh", "SHA256SUMS", "MANIFEST.txt"]:
                local_p = BUNDLE_DIR / rel
                if local_p.exists():
                    log(f"      Uploading {rel}...")
                    sftp.put(str(local_p), f"{NAS_DEPLOY_DIR}/{rel}")
            
            # Upload compose
            compose_p = BUNDLE_DIR / "app" / "docker-compose.yml"
            if compose_p.exists():
                sftp.put(str(compose_p), f"{NAS_DEPLOY_DIR}/app/docker-compose.yml")

            # Upload large tars
            if not args.frontend_only and backend_tar.exists():
                upload_with_progress(sftp, backend_tar, f"{NAS_DEPLOY_DIR}/mednama-backend.tar")
            if not args.backend_only and frontend_tar.exists():
                upload_with_progress(sftp, frontend_tar, f"{NAS_DEPLOY_DIR}/mednama-frontend.tar")
        finally:
            sftp.close()

        # Step 5: Execute Deploy on NAS
        log("[5/5] Deploying updated images on NAS...")
        deploy_cmd = (
            f"cd {NAS_DEPLOY_DIR} && "
            f"chmod +x deploy.sh rollback.sh restore_nas.sh 2>/dev/null; "
            f"bash deploy.sh --no-backup --keep-env"
        )
        rc = ssh_exec_stream(client, deploy_cmd)
        if rc != 0:
            raise RuntimeError(f"deploy.sh on NAS returned non-zero exit code: {rc}")

        log("=====================================================================")
        log("SUCCESS! NEW FRONTEND & BACKEND IMAGES ARE LIVE ON NAS")
        log("=====================================================================")
        log(f"Open: http://{NAS_HOST}:3000")
        log("=====================================================================")

    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true", help="Skip docker build/save, only upload existing tars")
    parser.add_argument("--frontend-only", action="store_true", help="Only build and transfer frontend")
    parser.add_argument("--backend-only", action="store_true", help="Only build and transfer backend")
    args = parser.parse_args()
    run_deployment(args)


if __name__ == "__main__":
    main()

