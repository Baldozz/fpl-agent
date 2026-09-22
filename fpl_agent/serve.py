"""Local server for the site so the page's Refresh button can rebuild it.

    python -m fpl_agent --serve        # http://localhost:8765

Serves ``docs/`` and handles ``POST /refresh`` by re-running
``python -m fpl_agent --site --html --no-cache`` (fresh FPL data, including the
authenticated my-team squad when ~/.fpl-mcp credentials exist). Bound to
localhost only — nothing is exposed to the network.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
_lock = threading.Lock()


def rebuild() -> tuple[bool, str]:
    with _lock:  # one rebuild at a time
        r = subprocess.run(
            [sys.executable, "-m", "fpl_agent", "--site", "--html", "--no-cache"],
            cwd=ROOT, capture_output=True, text=True, timeout=600)
    return r.returncode == 0, (r.stdout + r.stderr)[-4000:]


class Handler(SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path.rstrip("/") != "/refresh":
            self.send_error(404)
            return
        ok, log = rebuild()
        print(log)
        body = json.dumps({"ok": ok, "log": log}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def serve(port: int = 8765, build_first: bool = True) -> int:
    if build_first or not (DOCS / "index.html").exists():
        print("Building site…")
        ok, log = rebuild()
        print(log)
    srv = ThreadingHTTPServer(("127.0.0.1", port),
                              partial(Handler, directory=str(DOCS)))
    print(f"Serving on http://localhost:{port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
