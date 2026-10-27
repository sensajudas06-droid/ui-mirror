"""Integration checks using a local site; no outside network is required."""

from __future__ import annotations

import functools
import gc
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

from bs4 import BeautifulSoup

from ui_mirror.check import analyze
from ui_mirror.mirror import MirrorOptions, SiteMirror
from ui_mirror.fetcher import Fetcher
from ui_mirror.gui import MirrorApp
from ui_mirror.preview import PreviewServer
from ui_mirror.render import find_browser


class _Handler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


class MirrorIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(gc.collect)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        site = self.root / "site"
        site.mkdir()
        (site / "index.html").write_text(
            '<!doctype html><html><head><link rel="stylesheet" href="/style.css"></head>'
            '<body><a href="/about.html">About</a><script src="/app.js"></script></body></html>',
            encoding="utf-8",
        )
        (site / "about.html").write_text("<!doctype html><title>About</title>", encoding="utf-8")
        (site / "login.html").write_text(
            '<!doctype html><html><head><link rel="stylesheet" href="/style.css">'
            '<link rel="prefetch" href="/about.html"></head><body>'
            '<h1>Login</h1><a href="/about.html">Other page</a>'
            '<iframe src="/about.html"></iframe><script src="/app.js"></script>'
            '</body></html>', encoding="utf-8",
        )
        (site / "style.css").write_text('body { background: url("/icon.svg"); }', encoding="utf-8")
        (site / "app.js").write_text("document.body.dataset.ready = 'yes';", encoding="utf-8")
        (site / "icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
        (site / "dynamic.html").write_text(
            '<!doctype html><html><head><title>Dynamic</title></head><body>'
            '<main id="root"></main><script>document.getElementById("root").textContent='
            '"Rendered content is visible after JavaScript runs"</script></body></html>',
            encoding="utf-8",
        )
        (site / "deferred.html").write_text(
            "<!doctype html><html><head><title>Forum</title></head><body>"
            + "<header>Static navigation and sidebar content. </header>" * 25
            + '<main id="feed"></main>'
            + "<script></script>" * 7
            + "<script>document.getElementById('feed').innerHTML = "
              "'<article><a href=\"/thread.html\">Rendered forum topic</a></article>'.repeat(40)</script>"
            + "</body></html>",
            encoding="utf-8",
        )
        handler = functools.partial(_Handler, directory=str(site))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def test_mirror_and_http_preview(self) -> None:
        output = self.root / "copy"
        report = SiteMirror(MirrorOptions(
            start_url=self.base + "/index.html", output_dir=output,
            max_depth=1, delay=0, verbose=False,
        )).run()
        self.assertIsNotNone(report.entry_file)
        self.assertEqual(report.as_dict()["stats"]["successful_pages"], 2)
        self.assertGreaterEqual(report.as_dict()["stats"]["assets"], 3)
        entry = output / report.entry_file
        self.assertTrue(entry.is_file())
        self.assertIn("about.html", entry.read_text(encoding="utf-8"))
        self.assertTrue((output / "mirror-report.json").is_file())

        preview = PreviewServer(output)
        self.addCleanup(preview.close)
        with urlopen(preview.url_for(report.entry_file), timeout=5) as response:
            self.assertIn(b"About", response.read())
        with self.assertRaises(ValueError):
            preview.url_for("../outside.html")

    def test_default_scope_copies_only_exact_login_url(self) -> None:
        url = self.base + "/login.html"
        output = self.root / "one-page"
        report = SiteMirror(MirrorOptions(
            start_url=url, output_dir=output, delay=0, verbose=False,
        )).run()
        self.assertEqual(report.entry_file, "login.html")
        self.assertEqual([page["url"] for page in report.pages], [url])
        self.assertFalse((output / "about.html").exists())
        self.assertTrue((output / "style.css").exists())
        self.assertTrue((output / "app.js").exists())
        saved = json.loads((output / "mirror-report.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["stats"]["pages"], 1)
        self.assertNotIn(self.base + "/about.html", [asset["url"] for asset in saved["assets"]])

    def test_embedded_assets_are_saved_when_crawler_rules_exclude_them(self) -> None:
        (self.root / "site" / "robots.txt").write_text(
            "User-agent: *\nDisallow: /style.css\nDisallow: /app.js\n",
            encoding="utf-8",
        )
        fetcher = Fetcher(delay=0)
        try:
            self.assertFalse(fetcher.allowed(self.base + "/style.css"))
        finally:
            fetcher.close()
        output = self.root / "resource-copy"
        report = SiteMirror(MirrorOptions(
            start_url=self.base + "/login.html", output_dir=output,
            delay=0, verbose=False,
        )).run()
        self.assertEqual(report.as_dict()["stats"]["successful_pages"], 1)
        self.assertTrue((output / "style.css").is_file())
        self.assertTrue((output / "app.js").is_file())

    def test_missing_start_page_is_failure(self) -> None:
        report = SiteMirror(MirrorOptions(
            start_url=self.base + "/missing", output_dir=self.root / "failed",
            delay=0, retries=0, verbose=False,
        )).run()
        self.assertIsNone(report.entry_file)
        self.assertEqual(report.as_dict()["stats"]["successful_pages"], 0)
        self.assertEqual(report.as_dict()["stats"]["failed_pages"], 1)

    def test_dynamic_shell_is_reported_instead_of_false_success(self) -> None:
        url = self.base + "/dynamic.html"
        assessment = analyze(url, timeout=5)
        self.assertTrue(assessment.render_needed)
        self.assertTrue(assessment.empty_shell)
        self.assertEqual(assessment.verdict, "conditional")
        self.assertLess(assessment.score, 80)
        report = SiteMirror(MirrorOptions(
            start_url=url, output_dir=self.root / "static-shell",
            delay=0, verbose=False,
        )).run()
        self.assertIsNone(report.entry_file)
        self.assertEqual(report.as_dict()["stats"]["successful_pages"], 0)

    @unittest.skipUnless(find_browser(), "Chrome/Edge unavailable")
    def test_dynamic_shell_is_rendered_with_installed_browser(self) -> None:
        report = SiteMirror(MirrorOptions(
            start_url=self.base + "/dynamic.html", output_dir=self.root / "rendered",
            auto_render=True, delay=0, verbose=False,
        )).run()
        self.assertIsNotNone(report.entry_file)
        self.assertEqual(report.as_dict()["stats"]["successful_pages"], 1)
        self.assertIn(
            "Rendered content is visible",
            (self.root / "rendered" / report.entry_file).read_text(encoding="utf-8"),
        )

    @unittest.skipUnless(find_browser(), "Chrome/Edge unavailable")
    def test_nonempty_shell_renders_deferred_main_content(self) -> None:
        url = self.base + "/deferred.html"
        assessment = analyze(url, timeout=5)
        self.assertTrue(assessment.render_needed)
        self.assertFalse(assessment.empty_shell)
        report = SiteMirror(MirrorOptions(
            start_url=url, output_dir=self.root / "deferred-copy",
            auto_render=True, delay=0, verbose=False,
        )).run()
        self.assertIsNotNone(report.entry_file)
        entry = (self.root / "deferred-copy" / report.entry_file).read_text(encoding="utf-8")
        self.assertEqual(
            sum(link.get_text(strip=True) == "Rendered forum topic"
                for link in BeautifulSoup(entry, "html.parser").find_all("a")),
            40,
        )

    def test_desktop_copy_workflow(self) -> None:
        output = self.root / "chosen-output"
        output.mkdir()
        app = MirrorApp(
            initial_url=self.base + "/index.html",
            initial_output=str(output), splash=False,
        )
        try:
            app.start_copy()
            deadline = time.monotonic() + 15
            while (app.worker is not None or not (output / "mirror-report.json").exists()) and time.monotonic() < deadline:
                app.update()
                time.sleep(0.02)
            self.assertEqual(app.last_dir, output)
            self.assertIsNotNone(app.last_entry)
            self.assertIn("Tamamlandı", app.status.cget("text"))
            self.assertTrue((output / "mirror-report.json").is_file())
        finally:
            app._close()

    def test_preview_and_progress_are_available_while_an_asset_is_slow(self) -> None:
        site = self.root / "site"
        entry = site / "index.html"
        entry.write_text(
            entry.read_text(encoding="utf-8").replace(
                "</body>", '<img src="/slow.svg"></body>'
            ), encoding="utf-8",
        )
        (site / "slow.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
        entered = threading.Event()
        release = threading.Event()
        original_fetch = Fetcher.fetch

        def delayed_fetch(fetcher, url, **kwargs):
            if url.endswith("/slow.svg"):
                entered.set()
                release.wait(5)
            return original_fetch(fetcher, url, **kwargs)

        output = self.root / "early-preview"
        app = MirrorApp(initial_url=self.base + "/index.html",
                        initial_output=str(output), splash=False)
        try:
            with patch.object(Fetcher, "fetch", delayed_fetch):
                app.start_copy()
                self.assertTrue(entered.wait(5))
                deadline = time.monotonic() + 3
                while app.last_entry is None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.02)
                self.assertEqual(app.last_entry, "index.html")
                self.assertTrue((output / "index.html").is_file())
                self.assertTrue(app.open_browser_button._enabled)
                self.assertGreater(app.target_pct, 0)
                self.assertIn("/s", app._stat_values["speed"].cget("text"))
                self.assertIsNotNone(app.worker)
                with patch("ui_mirror.gui.webbrowser.open", return_value=True) as opener:
                    app.open_in_browser()
                    opener.assert_called_once()
                release.set()
                deadline = time.monotonic() + 10
                while (app.worker is not None or not (output / "mirror-report.json").exists()) and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.02)
                self.assertIsNone(app.worker)
                self.assertTrue((output / "mirror-report.json").is_file())
        finally:
            release.set()
            app._close()

    def test_desktop_start_analyzes_then_copies_in_one_run(self) -> None:
        output = self.root / "unified-output"
        output.mkdir()
        app = MirrorApp(initial_url=self.base + "/login.html",
                        initial_output=str(output), splash=False)
        try:
            app.start_run()
            deadline = time.monotonic() + 15
            while (app.worker is not None or not (output / "mirror-report.json").exists()) and time.monotonic() < deadline:
                app.update()
                time.sleep(0.02)
            self.assertEqual(app.last_dir, output)
            log = app.log_text.get("1.0", "end")
            self.assertIn("analiz sonucu", log)
            self.assertIn("KOPYALAMA BAŞLIYOR", log)
            self.assertIn("bitti", log)
            self.assertTrue((output / "login.html").is_file())
            self.assertFalse((output / "about.html").exists())
        finally:
            app._close()

    def test_desktop_stop_after_analysis_prevents_copy(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        report = analyze(self.base + "/index.html")

        def slow_analysis(url: str):
            entered.set()
            release.wait(5)
            return report

        app = MirrorApp(initial_url=self.base + "/index.html",
                        initial_output=str(self.root / "stopped-output"), splash=False)
        try:
            with patch("ui_mirror.gui.analyze_site", side_effect=slow_analysis):
                app.start_run()
                self.assertTrue(entered.wait(3))
                app.stop_copy()
                self.assertIsNone(app.worker)
                self.assertIn("Durduruldu", app.status.cget("text"))
                release.set()
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.02)
            self.assertIsNone(app.worker)
            self.assertIsNone(app.last_dir)
            self.assertIn("kopyalama başlatılmadı", app.log_text.get("1.0", "end"))
        finally:
            release.set()
            app._close()

    def test_desktop_full_site_scope_follows_same_site_links(self) -> None:
        output = self.root / "full-site"
        output.mkdir()
        app = MirrorApp(
            initial_url=self.base + "/login.html",
            initial_output=str(output), splash=False,
        )
        try:
            app._set_scope("site")
            self.assertEqual(app.scope_var.get(), "site")
            self.assertTrue(app.scope_buttons["site"]._selected)
            app.start_copy()
            deadline = time.monotonic() + 15
            while (app.worker is not None or not (output / "mirror-report.json").exists()) and time.monotonic() < deadline:
                app.update()
                time.sleep(0.02)
            report = json.loads((output / "mirror-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["stats"]["pages"], 2)
            self.assertTrue((output / "about.html").is_file())
        finally:
            app._close()

    def test_full_site_page_limit_is_reported(self) -> None:
        report = SiteMirror(MirrorOptions(
            start_url=self.base + "/index.html", output_dir=self.root / "capped",
            max_depth=60, max_pages=1, delay=0, verbose=False,
        )).run()
        self.assertEqual(len(report.pages), 1)
        self.assertIn("page-limit", {error["kind"] for error in report.errors})

    def test_cancel_interrupts_rate_limit_wait(self) -> None:
        cancel = threading.Event()
        fetcher = Fetcher(delay=5, cancel_event=cancel)
        fetcher._next_allowed = time.monotonic() + 5
        result = []
        worker = threading.Thread(target=lambda: result.append(fetcher.fetch(self.base + "/index.html")))
        started = time.monotonic()
        worker.start()
        time.sleep(0.1)
        cancel.set()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0].error, "cancelled")
        self.assertLess(time.monotonic() - started, 1)


if __name__ == "__main__":
    unittest.main()
