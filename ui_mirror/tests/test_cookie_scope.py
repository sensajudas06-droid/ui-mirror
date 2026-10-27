"""Credential boundaries use the selected origin, including redirects."""

import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import requests

from ui_mirror.check import analyze
from ui_mirror.fetcher import Fetcher
from ui_mirror.http_session import OriginScopedSession


HEADERS = {"Authorization": "Bearer DUMMY", "X-API-Key": "DUMMY", "Cookie": "raw=DUMMY"}


class CookieScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.seen: list[dict] = []
        observed = self.seen

        class Handler(BaseHTTPRequestHandler):
            def record(self) -> None:
                observed.append({
                    "method": self.command, "host": self.headers.get("Host"), "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "api_key": self.headers.get("X-API-Key"), "cookie": self.headers.get("Cookie"),
                })

            def do_GET(self) -> None:
                self.record()
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", f"http://localhost:{self.server.server_port}/page")
                    self.end_headers()
                    return
                body = b'<html><body><img src="/pixel"></body></html>' if self.path == "/page" else b"ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/html" if self.path == "/page" else "text/plain")
                if self.path == "/set-cookie":
                    self.send_header("Set-Cookie", "server_cookie=one; Path=/")
                self.end_headers()
                self.wfile.write(body)

            def do_HEAD(self) -> None:
                self.record()
                self.send_response(200)
                self.send_header("Content-Length", "1")
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.external = f"http://localhost:{self.server.server_port}"
        self.env = patch.dict(os.environ, {"NO_PROXY": "localhost,127.0.0.1", "no_proxy": "localhost,127.0.0.1"})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def assertNoSecrets(self, record: dict) -> None:
        self.assertIsNone(record["authorization"])
        self.assertIsNone(record["api_key"])
        self.assertIsNone(record["cookie"])

    def test_headers_and_cookies_use_exact_https_origin(self) -> None:
        with OriginScopedSession(
            "https://example.test", extra_headers={"Authorization": "Bearer DUMMY", "X-API-Key": "DUMMY", "Accept": "custom"},
            cookies="session=DUMMY",
        ) as session:
            for url in ("https://example.test/path", "https://example.test:443/path"):
                request = session.prepare_request(requests.Request("GET", url))
                self.assertEqual(request.headers["Authorization"], "Bearer DUMMY")
                self.assertEqual(request.headers["X-API-Key"], "DUMMY")
                self.assertEqual(request.headers["Cookie"], "session=DUMMY")
            for url in ("https://cdn.example.test", "http://example.test", "https://example.test:444", "https://other.test"):
                request = session.prepare_request(requests.Request("GET", url))
                for name in ("Authorization", "X-API-Key", "Cookie"):
                    self.assertNotIn(name, request.headers)
                self.assertEqual(request.headers["Accept"], "*/*")

    def test_fetcher_direct_external_and_redirect_do_not_receive_secrets(self) -> None:
        fetcher = Fetcher(origin_url=self.origin, extra_headers=HEADERS, cookies="session=DUMMY", respect_robots=False, delay=0)
        try:
            for url in (self.origin + "/page", self.external + "/asset", self.origin + "/redirect"):
                self.assertTrue(fetcher.fetch(url).ok)
            target = [row for row in self.seen if row["host"].startswith("127.0.0.1:")]
            external = [row for row in self.seen if row["host"].startswith("localhost:")]
            self.assertEqual(len(external), 2)
            for row in target:
                self.assertEqual(row["authorization"], "Bearer DUMMY")
                self.assertEqual(row["api_key"], "DUMMY")
                self.assertEqual(row["cookie"], "raw=DUMMY")
            for row in external:
                self.assertNoSecrets(row)
        finally:
            fetcher.close()

    def test_redirect_send_rechecks_scheme_subdomain_and_port(self) -> None:
        class RedirectAdapter(requests.adapters.BaseAdapter):
            def __init__(self, destination: str) -> None:
                self.destination = destination
                self.sent: list[dict] = []

            def send(self, request, **kwargs):
                self.sent.append(dict(request.headers))
                response = requests.Response()
                response.request = request
                response.url = request.url
                response.status_code = 302 if request.url.endswith("/start") else 200
                if response.status_code == 302:
                    response.headers["Location"] = self.destination
                response._content = b"ok"
                response._content_consumed = True
                return response

            def close(self) -> None:
                pass

        for destination in ("http://example.test/final", "https://cdn.example.test/final", "https://example.test:444/final"):
            adapter = RedirectAdapter(destination)
            with OriginScopedSession("https://example.test", extra_headers=HEADERS) as session:
                session.mount("https://", adapter)
                session.mount("http://", adapter)
                session.get("https://example.test/start", timeout=5).close()
                self.assertEqual(len(adapter.sent), 2)
                self.assertEqual(adapter.sent[0]["X-API-Key"], "DUMMY")
                for name in ("Authorization", "X-API-Key", "Cookie"):
                    self.assertNotIn(name, adapter.sent[1])

    def test_analyzer_probes_after_external_redirect_do_not_receive_secrets(self) -> None:
        analyze(self.origin + "/redirect", extra_headers=HEADERS, cookies="session=DUMMY", timeout=5)
        external = [row for row in self.seen if row["host"].startswith("localhost:")]
        self.assertEqual({row["path"] for row in external}, {"/page", "/robots.txt", "/pixel"})
        self.assertTrue(any(row["method"] == "HEAD" for row in external))
        for row in external:
            self.assertNoSecrets(row)

    def test_same_origin_server_cookies_merge_with_user_cookies(self) -> None:
        with OriginScopedSession(self.origin, cookies="session=DUMMY") as session:
            session.get(self.origin + "/set-cookie", timeout=5).close()
            session.get(self.origin + "/check", timeout=5).close()
            self.assertIn("server_cookie=one", self.seen[-1]["cookie"])
            self.assertIn("session=DUMMY", self.seen[-1]["cookie"])
            self.assertEqual(self.seen[-1]["cookie"].count("session="), 1)
            session.get(self.external + "/check", timeout=5).close()
            self.assertNoSecrets(self.seen[-1])

    def test_implicit_netrc_is_not_read_initially_or_on_redirect(self) -> None:
        with patch("requests.sessions.get_netrc_auth", side_effect=AssertionError("Unexpected .netrc access")) as netrc:
            with OriginScopedSession(self.origin) as session:
                session.get(self.origin + "/redirect", timeout=5).close()
            netrc.assert_not_called()

    def test_environment_proxy_configuration_remains_available(self) -> None:
        with OriginScopedSession("http://example.test") as session:
            with patch.dict(os.environ, {"HTTP_PROXY": "http://proxy.test:8080", "http_proxy": "http://proxy.test:8080", "NO_PROXY": "", "no_proxy": ""}):
                settings = session.merge_environment_settings("http://example.test", {}, False, None, None)
                self.assertEqual(settings["proxies"]["http"], "http://proxy.test:8080")

    def test_explicit_url_auth_is_scoped_and_ambient_auth_is_not_used(self) -> None:
        with OriginScopedSession("https://example.test") as session:
            target = session.prepare_request(requests.Request("GET", "https://user:dummy@example.test"))
            external = session.prepare_request(requests.Request("GET", "https://user:dummy@other.test"))
            self.assertEqual(target.headers.get("Authorization"), "Basic dXNlcjpkdW1teQ==")
            self.assertNotIn("Authorization", external.headers)

    def test_credentials_require_an_origin_and_reject_newline_headers(self) -> None:
        with self.assertRaises(ValueError):
            Fetcher(cookies="session=DUMMY")
        with self.assertRaises(ValueError):
            OriginScopedSession(None, extra_headers=HEADERS)
        with self.assertRaises(requests.exceptions.InvalidHeader):
            OriginScopedSession(self.origin, extra_headers={"X-API-Key": "DUMMY\r\nInjected: x"})


if __name__ == "__main__":
    unittest.main()
