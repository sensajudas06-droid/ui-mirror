"""Crawl one site and write an offline, browsable copy of its interface."""

from __future__ import annotations

import json
import posixpath
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .check import is_render_candidate, is_unrendered_shell
from .fetcher import DEFAULT_USER_AGENT, FetchResult, Fetcher, decode_text
from .localmap import (
    ASSET_EXTENSIONS,
    PAGE_EXTENSIONS,
    LocalMap,
    guess_extension,
    url_key,
)
from .rewrite import (
    URL_ATTR_RULES,
    LinkResolver,
    absolutize,
    classify_anchor,
    iter_css_refs,
    link_is_asset,
    rewrite_css,
    rewrite_document,
)

REPORT_NAME = "mirror-report.json"

DEFAULT_DENY_PATTERNS = (
    r"/(?:log-?out|sign-?out|wp-login\.php|wp-admin)(?:[/?#]|$)",
    r"[?&](?:replytocom|action=logout)=",
)

# Third-party analytics beacons add no visual fidelity and slow the crawl down.
BLOCKED_ASSET_HOSTS = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "hotjar.com",
    "clarity.ms",
    "segment.io",
    "segment.com",
    "mixpanel.com",
    "matomo.cloud",
)

CSS_TYPES = frozenset({"text/css"})


@dataclass
class MirrorOptions:
    start_url: str
    output_dir: Path
    max_depth: int = 0
    max_pages: int = 40
    max_assets: int = 2000
    stay_on_host: bool = True
    external_assets: bool = True
    jobs: int = 4
    delay: float = 0.2
    timeout: float = 20.0
    retries: int = 2
    max_asset_bytes: int = 25 * 1024 * 1024
    time_budget: float | None = 600.0
    user_agent: str = DEFAULT_USER_AGENT
    headers: dict[str, str] = field(default_factory=dict)
    cookies: str | None = None
    respect_robots: bool = True
    render: bool = False
    auto_render: bool = False
    verbose: bool = True
    deny_patterns: tuple[str, ...] = DEFAULT_DENY_PATTERNS
    allow_patterns: tuple[str, ...] = ()


@dataclass
class MirrorReport:
    start_url: str
    output_dir: str
    entry_file: str | None
    pages: list[dict]
    assets: list[dict]
    errors: list[dict]
    bytes_written: int
    duration_seconds: float
    report_path: str

    def as_dict(self) -> dict:
        return {
            "start_url": self.start_url,
            "output_dir": self.output_dir,
            "entry_file": self.entry_file,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "stats": {
                "pages": len(self.pages),
                "successful_pages": sum(1 for page in self.pages if not page.get("error")),
                "failed_pages": sum(1 for page in self.pages if page.get("error")),
                "assets": sum(1 for asset in self.assets if asset.get("local")),
                "errors": len(self.errors),
                "bytes_written": self.bytes_written,
                "duration_seconds": round(self.duration_seconds, 2),
            },
            "pages": self.pages,
            "assets": self.assets,
            "errors": self.errors,
        }


class SiteMirror:
    def __init__(
        self,
        options: MirrorOptions,
        *,
        on_event=None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.opt = options
        self.output_dir = Path(options.output_dir)
        self.map = LocalMap(self.output_dir)
        self._cancel = cancel_event if cancel_event is not None else threading.Event()
        self._transfer_lock = threading.Lock()
        self._progress_lock = threading.Lock()
        self._transfer_samples: deque[tuple[float, int]] = deque()
        self._recent_transfer_bytes = 0
        self._downloaded_bytes = 0
        self.fetcher = Fetcher(
            user_agent=options.user_agent,
            timeout=options.timeout,
            delay=options.delay,
            retries=options.retries,
            extra_headers=options.headers,
            cookies=options.cookies,
            origin_url=options.start_url,
            jobs=options.jobs,
            respect_robots=options.respect_robots,
            max_bytes=options.max_asset_bytes,
            time_budget=options.time_budget,
            cancel_event=self._cancel,
            on_transfer=self._on_transfer,
        )
        self.pages: dict[str, dict] = {}
        self.assets: dict[str, dict] = {}
        self.errors: list[dict] = []

        base_host = (urlsplit(options.start_url).hostname or "").lower()
        hosts = {base_host}
        hosts.add(base_host[4:] if base_host.startswith("www.") else f"www.{base_host}")
        self._site_hosts = {host for host in hosts if host}

        self._queue: deque[tuple[str, int]] = deque()
        self._seen_pages: set[str] = set()
        self._seen_assets: set[str] = set()
        self._fetched: dict[str, FetchResult] = {}
        self._failed_assets: set[str] = set()
        self._in_progress_css: set[str] = set()
        self._deny = tuple(re.compile(pattern, re.I) for pattern in options.deny_patterns)
        self._allow = tuple(re.compile(pattern, re.I) for pattern in options.allow_patterns)
        self._renderer = None
        self.bytes_written = 0
        self._written_sizes: dict[str, int] = {}
        self._started = time.monotonic()
        self._on_event = on_event
        self._progress_emitted_at = 0.0
        self._finished_report: MirrorReport | None = None

    # -- progress / control ----------------------------------------------
    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def snapshot(self) -> dict:
        """Counters the GUI needs to animate a percentage."""
        pages_done = len(self.pages)
        assets_done = len(self.assets)
        pages_known = min(max(len(self._seen_pages), pages_done), max(1, self.opt.max_pages))
        assets_known = min(
            max(len(self._seen_assets), assets_done), max(1, self.opt.max_assets)
        )
        total = pages_known + assets_known
        done = pages_done + assets_done
        elapsed = max(time.monotonic() - self._started, 0.001)
        with self._transfer_lock:
            self._trim_transfer_samples(time.monotonic())
            downloaded = self._downloaded_bytes
            rate = int(self._recent_transfer_bytes / min(elapsed, 5.0))
        percent = min(100.0, done / total * 100.0) if total else 0.0
        return {
            "percent": round(max(1.0, percent) if pages_done else percent, 1),
            "pages_done": pages_done,
            "pages_known": pages_known,
            "assets_done": assets_done,
            "assets_known": assets_known,
            "queue": len(self._queue),
            "errors": len(self.errors),
            "bytes": self.bytes_written,
            "downloaded_bytes": downloaded,
            "elapsed": round(elapsed, 1),
            "bytes_per_second": rate,
            "cancelled": self.cancelled,
        }

    def _trim_transfer_samples(self, now: float) -> None:
        while self._transfer_samples and now - self._transfer_samples[0][0] > 5.0:
            _, amount = self._transfer_samples.popleft()
            self._recent_transfer_bytes -= amount

    def _on_transfer(self, amount: int) -> None:
        now = time.monotonic()
        with self._transfer_lock:
            self._downloaded_bytes += amount
            self._transfer_samples.append((now, amount))
            self._recent_transfer_bytes += amount
            self._trim_transfer_samples(now)
        self._emit_progress()

    def _emit(self, kind: str, **payload) -> None:
        if self._on_event is None:
            return
        payload["kind"] = kind
        try:
            self._on_event(payload)
        except Exception:  # a broken UI callback must never stop the crawl
            pass

    def _emit_progress(self, force: bool = False) -> None:
        now = time.monotonic()
        with self._progress_lock:
            if not force and now - self._progress_emitted_at < 0.12:
                return
            self._progress_emitted_at = now
        self._emit("progress", **self.snapshot())

    # -- public API ------------------------------------------------------
    def run(self) -> MirrorReport:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if self.opt.render:
            self._renderer = self._create_renderer()

        self._emit("start", url=self.opt.start_url, output=str(self.output_dir))
        try:
            self._queue_page(self.opt.start_url, 0)
            while (
                self._queue
                and len(self.pages) < self.opt.max_pages
                and not self.fetcher.exhausted
                and not self.cancelled
            ):
                url, depth = self._queue.popleft()
                self._mirror_page(url, depth)
            if len(self.pages) >= self.opt.max_pages and self._queue:
                self._record_error(
                    self.opt.start_url, "page-limit",
                    f"Sayfa sınırına ulaşıldı ({self.opt.max_pages}); "
                    f"{len(self._queue)} sayfa sırada kaldı",
                )
            if self.fetcher.exhausted and self._queue:
                self._record_error(
                    self.opt.start_url,
                    "budget",
                    f"time budget ({self.opt.time_budget}s) used up; "
                    f"{len(self._queue)} page(s) left in the queue",
                )
            if self.cancelled:
                self._record_error(
                    self.opt.start_url,
                    "cancelled",
                    "user cancelled; the partial copy was kept",
                )
        finally:
            self.fetcher.close()
            if self._renderer is not None:
                self._renderer.close()

        report = self._write_report()
        self._emit_progress(force=True)
        self._emit(
            "finish" if not self.cancelled else "cancelled",
            **report.as_dict(),
        )
        return report

    def _create_renderer(self):
        from .render import Renderer, RendererUnavailable

        try:
            return Renderer(
                self.opt.user_agent,
                timeout_ms=max(5000, int(self.opt.timeout * 1500)),
            )
        except RendererUnavailable as exc:
            raise RuntimeError(str(exc)) from exc

    # -- crawling --------------------------------------------------------
    def _mirror_page(self, url: str, depth: int) -> None:
        key = url_key(url)
        relative = self.map.lookup(url)
        if relative is None:
            relative = self.map.assign(url, is_page=True)
        started = time.monotonic()
        if self.cancelled:
            return

        try:
            html = None
            final_url = url
            status: int | None = None

            if self._renderer is not None and self.opt.render:
                try:
                    html = self._renderer.html(url)
                except Exception as exc:
                    self._record_error(url, "render", str(exc))

            if html is None:
                result = self._get(url)
                if result is None or not result.ok:
                    reason = _describe_failure(result)
                    self._record_error(url, "page", reason)
                    self._write_failure_stub(relative, url, reason)
                    self.pages[key] = self._page_record(
                        url, relative, depth, None, reason, started
                    )
                    return
                status = result.status
                final_url = result.final_url or url
                if not _is_html(result):
                    self._store_binary_page(url, relative, result, depth, started)
                    return
                html = decode_text(result)

            static_soup = BeautifulSoup(html, "html.parser")
            static_shell = is_unrendered_shell(static_soup)
            if self.opt.auto_render and not self.opt.render and is_render_candidate(static_soup):
                if self._renderer is None:
                    try:
                        self._renderer = self._create_renderer()
                    except Exception as exc:
                        self._record_error(url, "render", str(exc))
                if self._renderer is not None:
                    try:
                        rendered_html = self._renderer.html(url)
                        rendered_soup = BeautifulSoup(rendered_html, "html.parser")
                        if static_shell or _content_score(rendered_soup) > _content_score(static_soup) * 1.15 + 150:
                            html = rendered_html
                            self._emit("render", url=url)
                    except Exception as exc:
                        self._record_error(url, "render", str(exc))
            if is_unrendered_shell(BeautifulSoup(html, "html.parser")):
                reason = "Dinamik içerik tarayıcıda yüklenemedi; boş HTML kabuğu kopyalanmadı"
                self._record_error(url, "empty-shell", reason)
                self._write_failure_stub(relative, url, reason)
                self.pages[key] = self._page_record(url, relative, depth, status, reason, started)
                return

            if final_url != url and self.map.lookup(final_url) is None:
                self.map.add_alias(final_url, relative)

            self._process_html(url, relative, html, final_url, depth, status, started)
        finally:
            # Page bodies are large; keep only what the current page needs in memory.
            self._fetched.pop(key, None)

    def _process_html(
        self,
        url: str,
        relative: str,
        html: str,
        final_url: str,
        depth: int,
        status: int | None,
        started: float,
    ) -> None:
        soup = BeautifulSoup(html, "html.parser")

        page_base = final_url
        for tag in soup.find_all("base"):
            href = tag.get("href")
            if href and isinstance(href, str) and page_base == final_url:
                page_base = urljoin(final_url, href)
            tag.decompose()

        assets, pages = self._collect_refs(soup, page_base, url)
        self._write_preview_snapshot(soup, page_base, relative, url, depth, status, started)
        self._prefetch(assets)

        if depth < self.opt.max_depth:
            for target in pages:
                self._queue_page(target, depth + 1)

        rewrite_document(soup, LinkResolver(self.map, page_base, relative))
        self._write_text(relative, soup.decode(formatter=None))
        self.pages[url_key(url)] = self._page_record(url, relative, depth, status, None, started)
        self._emit("page", url=url, local=relative, depth=depth)
        self._emit_progress()

        if self.opt.verbose:
            print(f"  page  {relative}", flush=True)

    def _write_preview_snapshot(
        self, soup: BeautifulSoup, page_base: str, relative: str,
        url: str, depth: int, status: int | None, started: float,
    ) -> None:
        """Expose visible page content while its offline assets are still downloading."""
        preview = BeautifulSoup(soup.decode(formatter=None), "html.parser")
        for script in preview.find_all("script"):
            script.decompose()
        head = preview.head
        if head is None:
            head = preview.new_tag("head")
            if preview.html is not None:
                preview.html.insert(0, head)
            else:
                preview.insert(0, head)
        head.insert(0, preview.new_tag("base", href=page_base))
        self._write_text(relative, preview.decode(formatter=None))
        self.pages[url_key(url)] = self._page_record(url, relative, depth, status, None, started)
        self._emit("preview", url=url, local=relative, output=str(self.output_dir.resolve()))
        self._emit_progress(force=True)

    # -- link discovery --------------------------------------------------
    def _collect_refs(
        self, soup: BeautifulSoup, page_base: str, source_url: str
    ) -> tuple[list[str], list[str]]:
        assets: list[str] = []
        pages: list[str] = []
        seen_assets: set[str] = set()
        seen_pages: set[str] = set()
        source_key = url_key(source_url)

        def add_asset(raw: str) -> None:
            absolute = absolutize(page_base, raw)
            if absolute is None or not self._asset_in_scope(absolute):
                return
            key = url_key(absolute)
            if key in seen_assets or key in self._failed_assets:
                return
            if self.map.lookup(absolute) is not None:
                return
            seen_assets.add(key)
            assets.append(absolute)

        def add_page(raw: str) -> None:
            if self.opt.max_depth == 0:
                return
            absolute = absolutize(page_base, raw)
            if absolute is None:
                return
            key = url_key(absolute)
            if key == source_key or key in seen_pages:
                return
            if not self._page_allowed(absolute):
                return
            seen_pages.add(key)
            pages.append(absolute)

        for element in soup.find_all(True):
            name = element.name

            if name in ("a", "area"):
                href = element.get("href")
                if isinstance(href, str) and href.strip():
                    absolute = absolutize(page_base, href)
                    if absolute is None:
                        continue
                    if classify_anchor(absolute) == "asset":
                        add_asset(href)
                    else:
                        add_page(href)
                continue

            if name == "link":
                href = element.get("href")
                if not isinstance(href, str) or not href.strip() or not link_is_asset(element):
                    continue
                absolute = absolutize(page_base, href)
                if absolute is None or posixpath.splitext(urlsplit(absolute).path)[1].lower() in PAGE_EXTENSIONS:
                    continue
                tokens = _tokens(element.get("rel"))
                if tokens & {"preconnect", "dns-prefetch", "prerender"}:
                    continue
                add_asset(href)
                continue

            if name in ("iframe", "frame"):
                src = element.get("src")
                if self.opt.max_depth > 0 and isinstance(src, str) and src.strip():
                    absolute = absolutize(page_base, src)
                    if absolute is not None and self._page_allowed(absolute):
                        add_page(src)
                continue

            for attribute in URL_ATTR_RULES.get(name, ()):
                value = element.get(attribute)
                if not isinstance(value, str) or not value.strip():
                    continue
                if attribute in ("srcset", "data-srcset"):
                    for raw in _iter_srcset_urls(value):
                        add_asset(raw)
                else:
                    add_asset(value)

        return assets, pages

    # -- asset download --------------------------------------------------
    def _prefetch(self, urls: list[str]) -> None:
        """Download and save assets as each response arrives."""
        pending: list[str] = []
        seen: set[str] = set()
        for url in urls:
            key = url_key(url)
            if key in seen or key in self._fetched or key in self._failed_assets:
                continue
            if len(self.assets) + len(pending) >= self.opt.max_assets:
                break
            seen.add(key)
            self._seen_assets.add(key)
            pending.append(url)
        if not pending:
            return
        self._emit_progress(force=True)
        batch_size = max(self.opt.jobs * 2, 2)
        for start in range(0, len(pending), batch_size):
            chunk = pending[start : start + batch_size]
            if self.fetcher.exhausted or self.cancelled:
                break
            # These are resources already referenced by a page we are allowed
            # to copy. Browsers load them as resources, even when a CDN's
            # crawler rules exclude its asset paths.
            for url, result in self.fetcher.iter_fetch_many(chunk, check_robots=False):
                if self.cancelled:
                    continue
                key = url_key(url)
                if self.map.lookup(url) is not None or key in self._failed_assets:
                    continue
                self._fetched[key] = result
                self._ensure_asset(url)

    def _get(self, url: str, *, check_robots: bool = True) -> FetchResult | None:
        key = url_key(url)
        if key in self._fetched:
            return self._fetched[key]
        result = self.fetcher.fetch(url, check_robots=check_robots)
        self._fetched[key] = result
        return result

    def _ensure_asset(self, absolute: str) -> str | None:
        key = url_key(absolute)
        self._seen_assets.add(key)
        if self.cancelled:
            return None
        existing = self.map.lookup(absolute)
        if existing is not None:
            return existing
        if key in self._failed_assets or len(self.assets) >= self.opt.max_assets:
            return None

        result = self._get(absolute, check_robots=False)
        if result is None or not result.ok:
            reason = _describe_failure(result)
            self._failed_assets.add(key)
            self._fetched.pop(key, None)
            self._record_error(absolute, "asset", reason)
            self.assets[key] = {
                "url": absolute,
                "local": None,
                "status": None if result is None else result.status,
                "content_type": None,
                "bytes": 0,
                "error": reason,
            }
            self._emit_progress()
            return None

        if _is_css(result, absolute):
            relative = self.map.assign(key, is_page=False, content_type="text/css")
            if key in self._in_progress_css:
                return relative
            self._in_progress_css.add(key)
            try:
                text = decode_text(result).replace("\r\n", "\n")
                resolver = LinkResolver(self.map, result.final_url or absolute, relative)
                nested = [
                    target
                    for target in (resolver.absolute(raw) for raw in iter_css_refs(text))
                    if target is not None and self._asset_in_scope(target)
                ]
                for target in dict.fromkeys(nested):
                    self._ensure_asset(target)
                self._write_text(relative, rewrite_css(text, resolver))
            finally:
                self._in_progress_css.discard(key)
        else:
            relative = self.map.assign(
                key, is_page=False, content_type=result.content_type
            )
            self._write_bytes(relative, result.content)

        self.assets[key] = {
            "url": absolute,
            "local": relative,
            "status": result.status,
            "content_type": result.content_type or None,
            "bytes": len(result.content),
            "error": None,
        }
        self._fetched.pop(key, None)
        self._emit(
            "asset",
            url=absolute,
            local=relative,
            bytes=len(result.content),
            content_type=result.content_type or "",
        )
        self._emit_progress()
        return relative

    # -- output ----------------------------------------------------------
    def _store_binary_page(
        self, url: str, page_relative: str, result: FetchResult, depth: int, started: float
    ) -> None:
        extension = guess_extension(result.content_type)
        binary_relative = posixpath.splitext(page_relative)[0] + extension
        self._write_bytes(binary_relative, result.content)
        self._write_stub(
            page_relative,
            binary_relative,
            f"{posixpath.basename(binary_relative)} (mirrored as a file, not a page)",
        )
        self.assets[url_key(url)] = {
            "url": url,
            "local": binary_relative,
            "status": result.status,
            "content_type": result.content_type or None,
            "bytes": len(result.content),
            "error": None,
        }
        self.pages[url_key(url)] = self._page_record(
            url, page_relative, depth, result.status, "not-html", started
        )

    def _write_stub(self, relative: str, target: str, message: str) -> None:
        href = self.map.relative(relative, target)
        title = message.split(" (")[0]
        html = (
            "<!DOCTYPE html>\n"
            '<html lang="en"><head><meta charset="utf-8">'
            f"<title>{_html_escape(title)}</title>"
            f'<meta http-equiv="refresh" content="0; url={href}">'
            "</head><body>"
            f"<h1>{_html_escape(title)}</h1>"
            f"<p>{_html_escape(message)}</p>"
            f'<p><a href="{href}">Open</a> &middot; '
            f'<a href="{_html_escape(target)}">{_html_escape(target)}</a></p>'
            "</body></html>"
        )
        self._write_text(relative, html)

    def _write_failure_stub(self, relative: str, url: str, reason: str) -> None:
        html = (
            "<!DOCTYPE html>\n"
            '<html lang="en"><head><meta charset="utf-8">'
            f"<title>Not mirrored: {_html_escape(url)}</title></head><body>"
            f"<h1>Not mirrored</h1><p>{_html_escape(reason)}</p>"
            f'<p><a href="{_html_escape(url)}">{_html_escape(url)}</a></p>'
            "</body></html>"
        )
        self._write_text(relative, html)

    def _write_text(self, relative: str, text: str) -> None:
        self._write_bytes(relative, text.encode("utf-8"))

    def _write_bytes(self, relative: str, data: bytes) -> None:
        path = self.output_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        previous_size = self._written_sizes.get(relative, 0)
        path.write_bytes(data)
        self._written_sizes[relative] = len(data)
        self.bytes_written += len(data) - previous_size

    # -- reports ---------------------------------------------------------
    def _page_record(
        self,
        url: str,
        relative: str,
        depth: int,
        status: int | None,
        error: str | None,
        started: float,
    ) -> dict:
        return {
            "url": url,
            "local": relative,
            "depth": depth,
            "status": status,
            "error": error,
            "elapsed_seconds": round(time.monotonic() - started, 2),
        }

    def _record_error(self, url: str, kind: str, message: str) -> None:
        self.errors.append({"url": url, "kind": kind, "message": message})
        self._emit("error", url=url, error=message, error_kind=kind)
        if self.opt.verbose:
            print(f"  fail  {url}  ({message})", flush=True)

    def _write_report(self) -> MirrorReport:
        start_record = self.pages.get(url_key(self.opt.start_url))
        entry = self.map.lookup(self.opt.start_url) if start_record and not start_record.get("error") else None
        report = MirrorReport(
            start_url=self.opt.start_url,
            output_dir=str(self.output_dir.resolve()),
            entry_file=entry,
            pages=list(self.pages.values()),
            assets=list(self.assets.values()),
            errors=self.errors,
            bytes_written=self.bytes_written,
            duration_seconds=time.monotonic() - self._started,
            report_path="",
        )
        report_path = self.output_dir / REPORT_NAME
        report.report_path = str(report_path)
        report_path.write_text(
            json.dumps(report.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return report

    # -- scope rules -----------------------------------------------------
    def _same_site(self, url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        return host in self._site_hosts

    def _asset_in_scope(self, absolute: str) -> bool:
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https"):
            return False
        host = (parts.hostname or "").lower()
        if any(host == blocked or host.endswith("." + blocked) for blocked in BLOCKED_ASSET_HOSTS):
            return False
        if self._same_site(absolute):
            return True
        return self.opt.external_assets

    def _page_allowed(self, absolute: str) -> bool:
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https"):
            return False
        if self.opt.stay_on_host and not self._same_site(absolute):
            return False
        if any(pattern.search(absolute) for pattern in self._deny):
            return False
        if self._allow and not any(pattern.search(absolute) for pattern in self._allow):
            return False
        extension = posixpath.splitext(parts.path)[1].lower()
        if extension in ASSET_EXTENSIONS:
            return False
        return True

    def _queue_page(self, url: str, depth: int) -> None:
        key = url_key(url)
        if key in self._seen_pages:
            return
        if len(self._seen_pages) >= self.opt.max_pages * 3:
            return
        if not self._page_allowed(url):
            return
        self._seen_pages.add(key)
        self.map.assign(url, is_page=True)
        self._queue.append((url, depth))


# -- module helpers ------------------------------------------------------
def _content_score(soup: BeautifulSoup) -> int:
    """Prefer a rendered DOM only when it adds meaningful visible content."""
    body = soup.body or soup
    return (
        len(body.get_text(" ", strip=True))
        + 20 * len(body.find_all("a"))
        + 25 * len(body.find_all("img"))
    )


def _tokens(value) -> set[str]:
    if not value:
        return set()
    if isinstance(value, str):
        values = [value]
    else:
        values = list(value)
    return {str(item).strip().lower() for item in values}


def _iter_srcset_urls(value: str) -> list[str]:
    urls: list[str] = []
    for part in value.split(","):
        part = part.strip()
        if part:
            urls.append(part.split()[0])
    return urls


def _is_html(result: FetchResult) -> bool:
    content_type = result.content_type
    if content_type in ("text/html", "application/xhtml+xml"):
        return True
    if content_type in ("", "text/plain", "application/octet-stream"):
        head = result.content[:2048].lstrip().lower()
        return head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<head" in head[:1024]
    return False


def _is_css(result: FetchResult, url: str) -> bool:
    if result.content_type in CSS_TYPES:
        return True
    return posixpath.splitext(urlsplit(url).path)[1].lower() == ".css"


def _describe_failure(result: FetchResult | None) -> str:
    if result is None:
        return "no-response"
    if result.error:
        return result.error
    return f"http-{result.status}"


def _html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# Re-exported for the CLI.
__all__ = [
    "MirrorOptions",
    "MirrorReport",
    "SiteMirror",
    "DEFAULT_USER_AGENT",
    "REPORT_NAME",
]
