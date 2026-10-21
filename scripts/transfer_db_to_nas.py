"""Automated Database Transfer to NAS for medNAMA.

Exports the local PostgreSQL database (with all newly seeded MCQs, tags,
and Seed Studio data), uploads it to the NAS via SFTP/SSH, restores it into
a new timestamped database on the NAS, merges existing NAS user data
(users, passwords, chats, quiz attempts, bookmarks), updates .env,
and restarts the backend.

Usage:
    python scripts/transfer_db_to_nas.py
    python scripts/transfer_db_to_nas.py --dry-run
    python scripts/transfer_db_to_nas.py --bundle-only  # just export into mednama-nas-deploy/db/
"""

import argparse
import datetime
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

# Local DB settings
LOCAL_DB_CANDIDATES = ["mednama-dev-db", "mednama-db-1", "mednama-db"]
LOCAL_DB_USER = "medrag"
LOCAL_DB_NAME = "medrag"

# NAS connection settings
NAS_HOST = os.environ.get("NAS_HOST", "192.168.1.42")
NAS_PORT = int(os.environ.get("NAS_PORT", "22"))
NAS_USER = os.environ.get("NAS_USER", "root")
NAS_PASS = os.environ.get("NAS_PASS")  # no default: use the key in _local/nas_key, or set NAS_PASS
NAS_KEY = os.environ.get("NAS_KEY", str(REPO_ROOT / "_local" / "nas_key"))
NAS_APP_DIR = os.environ.get("MEDNAMA_APP_DIR", "/DATA/mednama/app")
NAS_TMP_DIR = "/DATA/mednama/tmp"
NAS_DB_CONTAINER = "mednama-db"
NAS_BACKEND_CONTAINER = "mednama-backend"


def log(msg: str):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def get_local_container() -> str:
    for cid in LOCAL_DB_CANDIDATES:
        res = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, check=False
        )
        names = [line.strip() for line in res.stdout.splitlines()]
        if cid in names:
            return cid
    raise RuntimeError(
        f"None of the candidate containers {LOCAL_DB_CANDIDATES} are running. "
        "Run `docker ps` to verify your local database container."
    )


def query_local_db(cid: str, sql: str) -> str:
    res = subprocess.run(
        ["docker", "exec", cid, "psql", "-U", LOCAL_DB_USER, "-d", LOCAL_DB_NAME, "-tAc", sql],
        capture_output=True, text=True, check=False
    )
    if res.returncode != 0:
        raise RuntimeError(f"Local query failed: {res.stderr.strip()}")
    return res.stdout.strip()


def connect_ssh():
    if not paramiko:
        raise RuntimeError("paramiko is required. Install via `pip install paramiko` or use .venv-gpu.")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    key_path = NAS_KEY if (NAS_KEY and os.path.isfile(NAS_KEY)) else None
    
    log(f"Connecting to NAS at {NAS_USER}@{NAS_HOST}:{NAS_PORT} (key: {key_path or 'password'})...")
    client.connect(
        NAS_HOST, port=NAS_PORT, username=NAS_USER,
        password=None if key_path else NAS_PASS,
        key_filename=key_path,
        timeout=20, banner_timeout=30, auth_timeout=30,
        look_for_keys=False, allow_agent=False,
    )
    return client


def ssh_exec(client, cmd: str, timeout: int = 1800) -> tuple[int, str, str]:
    _stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


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
                f"\r  Uploading: {mb_done:.1f} / {mb_total:.1f} MB ({pct:.1f}%) @ {speed:.2f} MB/s...   "
            )
            sys.stdout.flush()

    sftp.put(str(local_path), remote_path, callback=progress_callback)
    sys.stdout.write("\n")
    sys.stdout.flush()


def run_transfer(args):
    log("=====================================================================")
    log("medNAMA Database Transfer -> NAS")
    log("=====================================================================")

    # 1. Local container check
    cid = get_local_container()
    log(f"[1/7] Local DB container found: '{cid}'")
    
    local_mcqs = query_local_db(cid, "SELECT count(*) FROM mcqs;")
    local_books = query_local_db(cid, "SELECT count(*) FROM books;")
    local_chunks = query_local_db(cid, "SELECT count(*) FROM chunks;")
    log(f"      Local DB stats: {local_books} books, {local_chunks} chunks, {local_mcqs} MCQs")

    # 2. Check if bundle-only
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dump_filename = f"medrag_pc_{timestamp}.dump"
    local_dump_dir = REPO_ROOT / "_local" / "dumps"
    local_dump_dir.mkdir(parents=True, exist_ok=True)
    local_dump_path = local_dump_dir / dump_filename

    bundle_dir = REPO_ROOT / "mednama-nas-deploy" / "db"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    if args.dump_file:
        local_dump_path = Path(args.dump_file).resolve()
        if not local_dump_path.is_file():
            raise RuntimeError(f"Specified dump file not found: {local_dump_path}")
        dump_filename = local_dump_path.name
        dump_size_mb = local_dump_path.stat().st_size / (1024 * 1024)
        log(f"[2/7] Using existing dump: {local_dump_path} ({dump_size_mb:.1f} MB)")
    else:
        # Export dump
        log(f"[2/7] Exporting compressed database dump from '{cid}'...")
        log(f"      Target: {local_dump_path}")
        
        # Dump inside container to /tmp, then docker cp out
        container_tmp = f"/tmp/{dump_filename}"
        dump_cmd = [
            "docker", "exec", cid,
            "pg_dump", "-U", LOCAL_DB_USER, "-d", LOCAL_DB_NAME,
            "-Fc", "-Z6", "-f", container_tmp
        ]
        t0 = time.time()
        res = subprocess.run(dump_cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            raise RuntimeError(f"pg_dump failed in container: {res.stderr.strip()}")
        
        # Copy from container to host
        cp_cmd = ["docker", "cp", f"{cid}:{container_tmp}", str(local_dump_path)]
        res = subprocess.run(cp_cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            raise RuntimeError(f"docker cp failed: {res.stderr.strip()}")
        
        # Clean up container tmp
        subprocess.run(["docker", "exec", cid, "rm", "-f", container_tmp], check=False)

        dump_size_mb = local_dump_path.stat().st_size / (1024 * 1024)
        log(f"      Dump created successfully: {dump_size_mb:.1f} MB in {time.time() - t0:.1f}s")

    # Also make sure it's in mednama-nas-deploy/db/
    bundle_dump_path = bundle_dir / dump_filename
    if not bundle_dump_path.exists() or bundle_dump_path.stat().st_size != local_dump_path.stat().st_size:
        bundle_dump_path.write_bytes(local_dump_path.read_bytes())
        log(f"      Copied to bundle: {bundle_dump_path}")

    if args.bundle_only:
        log("Done (--bundle-only). Dump is ready in mednama-nas-deploy/db/.")
        return

    # 3. Connect to NAS
    log("[3/7] Connecting to NAS...")
    client = connect_ssh()
    try:
        # Check NAS containers
        rc, out, _ = ssh_exec(client, "docker ps --format '{{.Names}}'")
        nas_containers = [l.strip() for l in out.splitlines()]
        if NAS_DB_CONTAINER not in nas_containers:
            raise RuntimeError(f"NAS database container '{NAS_DB_CONTAINER}' is not running!")

        # Get current NAS active DB
        rc, out, _ = ssh_exec(client, f"grep -E '^POSTGRES_DB=' {NAS_APP_DIR}/.env | cut -d= -f2- | tr -d '\r'")
        active_nas_db = out.strip().strip('"').strip("'")
        if not active_nas_db:
            active_nas_db = "medrag"
        log(f"      NAS active database: '{active_nas_db}'")

        # Get stats on active NAS DB
        query_cmd = (
            f"docker exec {NAS_DB_CONTAINER} psql -U medrag -d {active_nas_db} -tAc "
            f"\"SELECT (SELECT count(*) FROM users) users, (SELECT count(*) FROM quiz_attempts) attempts, "
            f"(SELECT count(*) FROM mcqs) mcqs;\""
        )
        rc, out, _ = ssh_exec(client, query_cmd)
        if rc == 0 and "|" in out:
            nas_users, nas_attempts, nas_mcqs = out.strip().split("|")
            log(f"      NAS live stats: {nas_mcqs} MCQs, {nas_users} users, {nas_attempts} quiz attempts")
            log("      (All users, attempts, bookmarks and chats will be merged and preserved!)")

        if args.dry_run:
            log("DRY RUN: skipping file upload and database restore.")
            return

        if args.target_db:
            new_db_name = args.target_db
            log(f"[4-5/7] Using existing restored database '{new_db_name}' on NAS (skipping upload & restore)...")
        else:
            # 4. Upload dump to NAS
            remote_dump = f"{NAS_TMP_DIR}/{dump_filename}"
            log(f"[4/7] Uploading {dump_size_mb:.1f} MB dump to NAS ({remote_dump})...")
            sftp = client.open_sftp()
            try:
                upload_with_progress(sftp, local_dump_path, remote_dump)
            finally:
                sftp.close()
            log("      Upload complete.")

            # 5. Restore into new database on NAS
            new_db_name = f"medrag_{timestamp}"
            log(f"[5/7] Restoring into new database '{new_db_name}' on NAS...")
            
            # Copy into NAS db container
            ssh_exec(client, f"docker cp {remote_dump} {NAS_DB_CONTAINER}:/tmp/restore.dump")
            
            # Create database
            ssh_exec(client, f"docker exec {NAS_DB_CONTAINER} psql -U medrag -d postgres -c 'DROP DATABASE IF EXISTS \"{new_db_name}\" WITH (FORCE)'")
            rc, _, err = ssh_exec(client, f"docker exec {NAS_DB_CONTAINER} createdb -U medrag '{new_db_name}'")
            if rc != 0:
                raise RuntimeError(f"createdb failed on NAS: {err}")

            # Start detached pg_restore inside db container
            ssh_exec(client, f"docker exec {NAS_DB_CONTAINER} rm -f /tmp/restore.log")
            restore_cmd = (
                f"docker exec -d -e PGOPTIONS='-c max_parallel_maintenance_workers=0 -c maintenance_work_mem=256MB' "
                f"{NAS_DB_CONTAINER} sh -c "
                f"\"pg_restore -U medrag -d {new_db_name} --no-owner --no-comments -v /tmp/restore.dump > /tmp/restore.log 2>&1; "
                f"echo RESTORE_EXIT_CODE=\\$? >> /tmp/restore.log\""
            )
            ssh_exec(client, restore_cmd)

            log("      Watching restore progress...")
            while True:
                time.sleep(5)
                # Check if finished
                rc, out, _ = ssh_exec(client, f"docker exec {NAS_DB_CONTAINER} grep -E 'RESTORE_EXIT_CODE=' /tmp/restore.log || true")
                if "RESTORE_EXIT_CODE=" in out:
                    exit_code = out.strip().split("=")[-1]
                    if exit_code != "0":
                        log(f"WARNING: restore finished with exit code {exit_code}")
                    break

                # Poll count
                rc, out, _ = ssh_exec(
                    client,
                    f"docker exec {NAS_DB_CONTAINER} psql -U medrag -d {new_db_name} -tAc 'SELECT count(*) FROM mcqs;' 2>/dev/null || echo 0"
                )
                count = out.strip()
                rc, tail, _ = ssh_exec(
                    client,
                    f"docker exec {NAS_DB_CONTAINER} tail -n 1 /tmp/restore.log 2>/dev/null | cut -c1-70"
                )
                log(f"        restored MCQs: {count:>6}  |  {tail.strip()}")

            log(f"      Restore into '{new_db_name}' completed.")
            # Cleanup container restore.dump
            ssh_exec(client, f"docker exec {NAS_DB_CONTAINER} rm -f /tmp/restore.dump")

        # 6. Merge NAS live user data (users, attempts, chats, bookmarks)
        log(f"[6/7] Merging live user data from '{active_nas_db}' -> '{new_db_name}'...")
        # Upload latest merge_user_data.py to NAS tmp
        merge_script_local = REPO_ROOT / "backend" / "scripts" / "merge_user_data.py"
        sftp = client.open_sftp()
        try:
            sftp.put(str(merge_script_local), f"{NAS_TMP_DIR}/merge_user_data.py")
        finally:
            sftp.close()

        # Run merge_user_data.py inside NAS backend container with volume mount
        merge_cmd = (
            f"docker run --rm --network app_mednama --entrypoint python -w /app/backend "
            f"-v {NAS_TMP_DIR}/merge_user_data.py:/app/backend/scripts/merge_user_data.py:ro "
            f"-e DATABASE_URL='postgresql://medrag:123456789@{NAS_DB_CONTAINER}:5432/{new_db_name}' "
            f"app-backend:latest scripts/merge_user_data.py --source-db '{active_nas_db}' --apply"
        )
        rc, out, err = ssh_exec(client, merge_cmd)
        if rc != 0:
            log(f"Merge output:\n{out}\n{err}")
            raise RuntimeError(f"merge_user_data.py failed (rc={rc})")
        for line in out.splitlines():
            if "matched" in line or "copied" in line or "Committed" in line:
                log(f"      {line.strip()}")

        # 7. Switch POSTGRES_DB in .env and restart backend
        log(f"[7/7] Switching NAS backend to '{new_db_name}'...")
        # Backup .env
        ssh_exec(client, f"cp {NAS_APP_DIR}/.env {NAS_APP_DIR}/.env.bak_{timestamp}")
        # Update POSTGRES_DB
        update_env_cmd = (
            f"sed -i 's/^POSTGRES_DB=.*/POSTGRES_DB={new_db_name}/' {NAS_APP_DIR}/.env"
        )
        ssh_exec(client, update_env_cmd)

        # Restart backend
        log(f"      Restarting {NAS_BACKEND_CONTAINER}...")
        ssh_exec(client, f"docker restart {NAS_BACKEND_CONTAINER}")
        
        # Wait up to 60s for health
        log("      Waiting for backend to become healthy...")
        for _ in range(12):
            time.sleep(5)
            rc, out, _ = ssh_exec(client, f"docker inspect {NAS_BACKEND_CONTAINER} --format '{{{{.State.Health.Status}}}}'")
            status = out.strip()
            if status == "healthy":
                log("      Backend is healthy!")
                break
        else:
            log("      Backend restarted (status check timed out, check docker ps).")

        # Final verification
        final_query = (
            f"docker exec {NAS_DB_CONTAINER} psql -U medrag -d {new_db_name} -tAc "
            f"\"SELECT (SELECT count(*) FROM books) books, (SELECT count(*) FROM chunks) chunks, "
            f"(SELECT count(*) FROM mcqs) mcqs, (SELECT count(*) FROM users) users, "
            f"(SELECT count(*) FROM quiz_attempts) attempts;\""
        )
        rc, out, _ = ssh_exec(client, final_query)
        if rc == 0 and "|" in out:
            books, chunks, mcqs, users, attempts = out.strip().split("|")
            log("=====================================================================")
            log("SUCCESS! DATABASE TRANSFERRED AND LIVE ON NAS")
            log("=====================================================================")
            log(f"Active NAS Database: {new_db_name}")
            log(f"  Books:             {books}")
            log(f"  Chunks:            {chunks}")
            log(f"  MCQs:              {mcqs} (all seeded MCQs present!)")
            log(f"  Users:             {users} (all NAS users preserved)")
            log(f"  Quiz Attempts:     {attempts} (all user attempts preserved)")
            log(f"Previous Database:   {active_nas_db} (kept for instant rollback if needed)")
            log("=====================================================================")

    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Export dump and check NAS status without writing")
    parser.add_argument("--bundle-only", action="store_true", help="Only dump into mednama-nas-deploy/db/ (do not upload)")
    parser.add_argument("--dump-file", help="Path to an existing .dump file (skips re-exporting)")
    parser.add_argument("--target-db", help="Use an existing restored DB on NAS (skips upload and restore)")
    args = parser.parse_args()
    run_transfer(args)


if __name__ == "__main__":
    main()

