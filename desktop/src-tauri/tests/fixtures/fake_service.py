from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

mode = sys.argv[1]
if mode == "exit":
    raise SystemExit(7)

host = os.environ["PERSONALMEMORY_HOST"]
port = int(os.environ["PERSONALMEMORY_PORT"])
token = os.environ["PERSONALMEMORY_API_TOKEN"]
data_dir = Path(os.environ["PERSONALMEMORY_DATA_DIR"])
data_dir.mkdir(parents=True, exist_ok=True)


class Handler(BaseHTTPRequestHandler):
    sync_paused = False
    privacy_mode = False

    def do_GET(self) -> None:
        if self.path == "/api/v1/connectors":
            if self.headers.get("Authorization") != f"Bearer {token}":
                self.send_error(401)
                return
            self.send_json(
                200,
                {
                    "items": [
                        {
                            "id": "connector-a",
                            "name": "Claude Memory",
                            "status": "attention",
                        }
                    ]
                },
            )
            return
        if self.path == "/api/v1/system/runtime":
            if self.headers.get("Authorization") != f"Bearer {token}":
                self.send_error(401)
                return
            self.send_json(
                200,
                {
                    "sync_paused": Handler.sync_paused,
                    "privacy_mode": Handler.privacy_mode,
                },
            )
            return
        if self.path != "/api/v1/health":
            self.send_error(404)
            return
        if mode == "unauthorized" or self.headers.get("Authorization") != f"Bearer {token}":
            self.send_error(401)
            return
        if mode == "never-ready":
            self.send_error(503)
            return
        body = b'{"status":"ready"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path == "/api/v1/system/sync":
            if self.headers.get("Authorization") != f"Bearer {token}":
                self.send_error(401)
                return
            self.send_json(200, {"ingested": 1, "projected": 2, "failed": 0})
            return
        if self.path != "/api/v1/system/shutdown":
            self.send_error(404)
            return
        if self.headers.get("Authorization") != f"Bearer {token}":
            self.send_error(401)
            return
        (data_dir / "shutdown.received").write_text("graceful", encoding="utf-8")
        self.send_response(202)
        self.send_header("Content-Length", "0")
        self.end_headers()
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def do_PATCH(self) -> None:
        if self.path != "/api/v1/system/runtime":
            self.send_error(404)
            return
        if self.headers.get("Authorization") != f"Bearer {token}":
            self.send_error(401)
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if "sync_paused" in payload:
            Handler.sync_paused = payload["sync_paused"]
        if "privacy_mode" in payload:
            Handler.privacy_mode = payload["privacy_mode"]
        self.send_json(
            200,
            {
                "sync_paused": Handler.sync_paused,
                "privacy_mode": Handler.privacy_mode,
            },
        )

    def send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


server = ThreadingHTTPServer((host, port), Handler)
server.serve_forever()
server.server_close()
