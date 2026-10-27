"""Browser rendering for JavaScript pages, with an installed-browser fallback."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class RendererUnavailable(RuntimeError):
    """No usable browser renderer was found."""


def find_browser() -> Path | None:
    """Find a local Chrome/Edge installation without downloading a browser."""
    names = ("chrome.exe", "msedge.exe", "chromium.exe") if os.name == "nt" else (
        "google-chrome", "chromium", "chromium-browser", "microsoft-edge"
    )
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    if os.name == "nt":
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
            if not base:
                continue
            for relative in (
                Path("Google/Chrome/Application/chrome.exe"),
                Path("Microsoft/Edge/Application/msedge.exe"),
            ):
                candidate = Path(base) / relative
                if candidate.is_file():
                    return candidate
    return None


class Renderer:
    def __init__(self, user_agent: str, *, timeout_ms: int = 30000, scroll_passes: int = 6) -> None:
        self._user_agent = user_agent
        self._timeout_ms = timeout_ms
        self._scroll_passes = max(0, scroll_passes)
        self._playwright = None
        self._browser = None
        self._context = None
        self._binary = None

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            sync_playwright = None
        if sync_playwright is not None:
            try:
                self._playwright = sync_playwright().start()
                self._browser = self._playwright.chromium.launch()
                self._context = self._browser.new_context(
                    user_agent=user_agent, viewport={"width": 1440, "height": 900}
                )
                return
            except Exception:
                self.close()

        self._binary = find_browser()
        if self._binary is None:
            raise RendererUnavailable(
                "Dinamik sayfa için Chrome veya Edge gerekli. Tarayıcı yükleyin ya da "
                "Python sürümünde Playwright/Chromium kurun."
            )

    def html(self, url: str) -> str:
        if self._context is not None:
            page = self._context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(self._timeout_ms, 12000))
                except Exception:
                    pass
                for _ in range(self._scroll_passes):
                    page.mouse.wheel(0, 1800)
                    page.wait_for_timeout(250)
                page.wait_for_timeout(400)
                return page.content()
            finally:
                page.close()

        assert self._binary is not None
        with tempfile.TemporaryDirectory(prefix="ui-mirror-browser-") as temp_dir:
            command = [
                str(self._binary), "--headless=new", "--disable-gpu", "--disable-extensions",
                "--no-first-run", "--no-default-browser-check", "--hide-scrollbars",
                "--window-size=1440,900", f"--user-data-dir={Path(temp_dir) / 'profile'}",
                f"--user-agent={self._user_agent}",
                f"--virtual-time-budget={min(self._timeout_ms, 8000)}",
                "--dump-dom", url,
            ]
            try:
                result = subprocess.run(
                    command, capture_output=True, encoding="utf-8", errors="replace",
                    timeout=max(10, self._timeout_ms / 1000 + 5),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("Tarayıcı zaman aşımına uğradı") from exc
        if result.returncode != 0 or "<html" not in result.stdout.lower():
            raise RuntimeError(f"Tarayıcı sayfayı render edemedi (çıkış {result.returncode})")
        return result.stdout

    def close(self) -> None:
        for obj in (self._context, self._browser, self._playwright):
            if obj is None:
                continue
            try:
                obj.close() if hasattr(obj, "close") else obj.stop()
            except Exception:
                pass
        self._context = self._browser = self._playwright = None
