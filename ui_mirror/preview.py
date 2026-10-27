"""Loopback-only HTTP preview for a completed site copy."""

from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote


class _PreviewHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


class PreviewServer:
    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()
        handler = functools.partial(_PreviewHandler, directory=str(self.directory))
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def url_for(self, entry: str) -> str:
        target = (self.directory / entry).resolve()
        if not target.is_relative_to(self.directory) or not target.is_file():
            raise ValueError("Önizlenecek sayfa bulunamadı")
        relative = target.relative_to(self.directory).as_posix()
        return f"http://127.0.0.1:{self._httpd.server_port}/{quote(relative, safe='/')}"

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)
