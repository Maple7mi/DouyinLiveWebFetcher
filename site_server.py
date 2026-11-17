# -*- coding: utf-8 -*-
"""Local static-site server with a tiny delete API.

The archive is plain HTML, but deleting a day needs to update the local
logs/manifest.  Serving the site from 127.0.0.1 lets the page call a local
API without requiring an external server.
"""

from __future__ import annotations

import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Callable
from urllib.parse import urlparse


class SiteServer:
    def __init__(
        self,
        directory: str | Path,
        on_delete: Callable[[str], bool],
    ):
        self.directory = Path(directory)
        self.on_delete = on_delete
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    def start(self) -> "SiteServer":
        self.thread.start()
        return self

    def stop(self) -> None:
        try:
            self.httpd.shutdown()
        finally:
            self.httpd.server_close()

    def _handler(self):
        outer = self

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(
                    *args, directory=str(outer.directory), **kwargs
                )

            def log_message(self, fmt, *args):
                pass

            def _json(self, code: int, payload: dict):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if urlparse(self.path).path != "/api/delete-day":
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0") or 0)
                    if length <= 0 or length > 1_000_000:
                        raise ValueError("empty request")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    day = str(payload.get("day", "")).strip()
                    if len(day) != 10 or day[4] != "-" or day[7] != "-":
                        raise ValueError("invalid day")
                except Exception as exc:
                    self._json(400, {"ok": False, "error": str(exc)})
                    return
                try:
                    ok = bool(outer.on_delete(day))
                    self._json(
                        200 if ok else 400,
                        {"ok": ok, "error": "" if ok else "删除失败"},
                    )
                except Exception as exc:
                    self._json(500, {"ok": False, "error": str(exc)})

        return Handler


def start_site_server(
    directory: str | Path,
    on_delete: Callable[[str], bool],
) -> SiteServer:
    return SiteServer(directory, on_delete).start()
