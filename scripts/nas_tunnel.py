"""An SSH tunnel from this PC to the NAS's Postgres, for batch work that must not run on the 3.8 GB NAS.

    from nas_tunnel import nas_database_url
    with nas_database_url() as url:      # postgresql://...@127.0.0.1:<port>/<nas db>
        ...

The database is only reachable on the NAS's Docker network, so this forwards a local port through SSH to the
mednama-db container. Credentials come from the NAS's /DATA/mednama/app/.env over SSH and stay in memory.
Needs the NAS key (_local/nas_key, used by _local/nas_ssh.py).
"""

from __future__ import annotations

import os
import select
import socket
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from _local import nas_ssh  # noqa: E402

LOCAL_PORT = int(os.environ.get("NAS_TUNNEL_PORT", "55432"))


@contextmanager
def nas_database_url(port: int = LOCAL_PORT) -> Iterator[str]:
    client = nas_ssh.connect()
    transport = client.get_transport()
    transport.set_keepalive(30)

    def remote(cmd: str) -> str:
        _, out, _ = client.exec_command(cmd, timeout=60)
        return out.read().decode().strip()

    env = dict(line.partition("=")[::2] for line in remote(
        "grep -E '^(POSTGRES_USER|POSTGRES_DB|POSTGRES_PASSWORD)=' /DATA/mednama/app/.env").splitlines())
    db_ip = remote("docker inspect mednama-db --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'")
    if not db_ip or "POSTGRES_PASSWORD" not in env:
        client.close()
        raise RuntimeError("Could not find the NAS database (is mednama-db running?).")

    def pipe(sock, chan):
        try:
            while True:
                ready, _, _ = select.select([sock, chan], [], [], 60)
                if sock in ready:
                    data = sock.recv(65536)
                    if not data:
                        break
                    chan.sendall(data)
                if chan in ready:
                    data = chan.recv(65536)
                    if not data:
                        break
                    sock.sendall(data)
        finally:
            chan.close()
            sock.close()

    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(16)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                sock, addr = server.accept()
            except OSError:
                break
            chan = transport.open_channel("direct-tcpip", (db_ip, 5432), addr)
            threading.Thread(target=pipe, args=(sock, chan), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    print(f"NAS database {env['POSTGRES_DB']} at {db_ip}:5432 -> 127.0.0.1:{port}", flush=True)
    try:
        yield f"postgresql://{env['POSTGRES_USER']}:{env['POSTGRES_PASSWORD']}@127.0.0.1:{port}/{env['POSTGRES_DB']}"
    finally:
        stop.set()
        server.close()
        client.close()
