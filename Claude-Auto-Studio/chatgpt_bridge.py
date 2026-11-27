"""Loopback-only, token-authenticated handoff endpoint for the browser extension."""
from __future__ import annotations

import hashlib
import hmac
import json
import queue
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


MAX_BODY = 64 * 1024
EXTENSION_ORIGIN = re.compile(r"^chrome-extension://[a-p]{32}$")


class ChatGPTBridge:
    def __init__(self, port: int = 8769):
        self.port = port
        self.tasks: queue.Queue[dict] = queue.Queue(maxsize=20)
        self.replies: queue.Queue[str] = queue.Queue(maxsize=20)
        self.token_hash = ""
        self.auto_enabled = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def _origin(self):
                origin = self.headers.get("Origin", "")
                return origin if EXTENSION_ORIGIN.fullmatch(origin) else ""

            def _send(self, status: int, payload: dict, *, cors: bool = True):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                if cors and self._origin():
                    self.send_header("Access-Control-Allow-Origin", self._origin())
                    self.send_header("Vary", "Origin")
                    self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS, GET")
                    self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):
                if not self._origin():
                    self._send(403, {"error": "origin rejected"}, cors=False)
                    return
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", self._origin())
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS, GET")
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                self.send_header("Access-Control-Max-Age", "300")
                self.end_headers()

            def do_GET(self):
                if self.path == "/health":
                    self._send(200, {"ok": True})
                    return
                if self.path not in {"/status", "/reply"} or not self._origin():
                    self._send(404, {"error": "not found"})
                    return
                expected = owner.token_hash
                supplied = self.headers.get("Authorization", "")
                token = supplied[7:] if supplied.startswith("Bearer ") else ""
                actual = hashlib.sha256(token.encode("utf-8")).hexdigest()
                if not expected or not hmac.compare_digest(actual, expected):
                    self._send(401, {"error": "pairing required"})
                    return
                if self.path == "/status":
                    self._send(200, {"auto_handoff_enabled": owner.auto_enabled})
                    return
                reply = owner.get_reply_nowait()
                self._send(200, {"reply": reply})

            def do_POST(self):
                if self.path not in {"/task", "/auto-task", "/pair"} or not self._origin():
                    self._send(403, {"error": "origin rejected"}, cors=False)
                    return
                expected = owner.token_hash
                supplied = self.headers.get("Authorization", "")
                token = supplied[7:] if supplied.startswith("Bearer ") else ""
                actual = hashlib.sha256(token.encode("utf-8")).hexdigest()
                if not expected or not hmac.compare_digest(actual, expected):
                    self._send(401, {"error": "pairing required"})
                    return
                if self.path == "/pair":
                    self._send(200, {"paired": True})
                    return
                if self.path == "/auto-task" and not owner.auto_enabled:
                    self._send(409, {"error": "automatic handoff is disabled"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if not 0 < length <= MAX_BODY:
                    self._send(413, {"error": "message too large"})
                    return
                try:
                    data = json.loads(self.rfile.read(length))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._send(400, {"error": "invalid JSON"})
                    return
                task = data.get("text") if isinstance(data, dict) else None
                if not isinstance(task, str) or not task.strip() or len(task) > 40000:
                    self._send(400, {"error": "invalid message"})
                    return
                try:
                    owner.tasks.put_nowait({"text": task.strip(), "automatic": self.path == "/auto-task"})
                except queue.Full:
                    self._send(429, {"error": "task queue full"})
                    return
                self._send(202, {"accepted": True})

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="chatgpt-local-bridge", daemon=True)

    def start(self):
        self._thread.start()

    def set_token_hash(self, token_hash: str):
        self.token_hash = token_hash

    def set_auto_enabled(self, enabled: bool):
        self.auto_enabled = bool(enabled)

    def get_task_nowait(self) -> dict | None:
        try:
            return self.tasks.get_nowait()
        except queue.Empty:
            return None

    def queue_reply(self, text: str) -> bool:
        value = str(text or "").strip()
        if not value or len(value) > 50000:
            return False
        try:
            self.replies.put_nowait(value)
            return True
        except queue.Full:
            return False

    def get_reply_nowait(self) -> str | None:
        try:
            return self.replies.get_nowait()
        except queue.Empty:
            return None

    def close(self):
        self._server.shutdown()
        self._server.server_close()
