"""Small LAN file server so the ESP32 can fetch race packages over Wi-Fi.

It serves only the manifest and race packages. Anything else, including
wifi.txt with your password, returns 404.
"""

from __future__ import annotations

import re
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ALLOWED = re.compile(r"^/f1replay/(manifest\.txt|races/\d{4}_\d{2}\.f1r)$")


def resolve(root: Path, request_path: str) -> Path | None:
    """Return the file for an allowed request path, or None."""
    clean = request_path.split("?", 1)[0]
    if not ALLOWED.match(clean):
        return None
    path = Path(root) / clean.lstrip("/")
    return path if path.is_file() else None


def local_ip() -> str:
    """The address other devices on this network can reach (no traffic is sent)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def make_handler(root: Path, log=print):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = resolve(root, self.path)
            if path is None:
                self.send_error(404)
                return
            size = path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with path.open("rb") as fh:
                while chunk := fh.read(16384):
                    self.wfile.write(chunk)

        def log_message(self, fmt, *args):
            log(f"  {self.address_string()} {fmt % args}")

    return Handler


def serve(out_dir: Path, port: int, log=print) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(Path(out_dir), log))
    log(f"Serving race files at http://{local_ip()}:{port}")
    log("Put this address in wifi.txt as server=... and leave this window open.")
    log("If Windows Firewall asks, allow access on Private networks. Ctrl+C stops the server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Stopped.")
    finally:
        server.server_close()
