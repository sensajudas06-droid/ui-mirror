"""Polite HTTP client: shared headers, rate limiting, retries and robots.txt."""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Iterator
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests

from .http_session import OriginScopedSession

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 UiMirror/1.0"
)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
DEFAULT_MAX_BODY_BYTES = 25 * 1024 * 1024
_READ_CHUNK = 65536
_CHARSET_HEADER = re.compile(r"charset\s*=\s*[\"']?([\w.:-]+)", re.I)
_CHARSET_META = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w.:-]+)""", re.I)


@dataclass
class FetchResult:
    url: str
    final_url: str = ""
    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes = b""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None and 200 <= self.status < 300

    @property
    def content_type(self) -> str:
        return (self.headers.get("content-type") or "").split(";")[0].strip().lower()


def decode_text(result: FetchResult) -> str:
    """Decode a response body honouring HTTP charset, meta charset, then UTF-8."""
    if not result.content:
        return ""
    candidates: list[str] = []
    match = _CHARSET_HEADER.search(result.headers.get("content-type", ""))
    if match:
        candidates.append(match.group(1))
    meta = _CHARSET_META.search(result.content[:4096])
    if meta:
        candidates.append(meta.group(1).decode("ascii", "ignore"))
    candidates.append("utf-8")
    for encoding in candidates:
        try:
            return result.content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return result.content.decode("utf-8", errors="replace")


class Fetcher:
    """Thread-safe HTTP client with a global request spacing (politeness)."""

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 20.0,
        delay: float = 0.2,
        retries: int = 2,
        extra_headers: dict[str, str] | None = None,
        cookies: str | None = None,
        origin_url: str | None = None,
        jobs: int = 4,
        respect_robots: bool = True,
        max_bytes: int = DEFAULT_MAX_BODY_BYTES,
        time_budget: float | None = None,
        per_response_deadline: float = 120.0,
        cancel_event: threading.Event | None = None,
        on_transfer: Callable[[int], None] | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.delay = max(0.0, delay)
        self.retries = max(0, retries)
        self.jobs = max(1, jobs)
        self.respect_robots = respect_robots
        self.extra_headers = dict(extra_headers or {})
        self.max_bytes = max(1024, max_bytes)
        self.per_response_deadline = max(5.0, per_response_deadline)
        self._cancel = cancel_event if cancel_event is not None else threading.Event()
        self._on_transfer = on_transfer
        self._deadline = (
            None if time_budget is None else time.monotonic() + max(1.0, time_budget)
        )

        self._base_headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,tr;q=0.8",
            "Accept-Encoding": "gzip, deflate",
        }
        self._origin_url = origin_url
        self._cookies = cookies
        if (cookies or self.extra_headers) and not origin_url:
            raise ValueError("Başlık ve çerez kullanımı için hedef URL gerekli")

        self._local = threading.local()
        self._sessions: list[requests.Session] = []
        self._sessions_lock = threading.Lock()
        self._limiter_lock = threading.Lock()
        self._next_allowed = 0.0
        self._robots: dict[str, RobotFileParser | None] = {}
        self._robots_lock = threading.Lock()
        self.request_count = 0

    # -- public API ------------------------------------------------------
    def close(self) -> None:
        with self._sessions_lock:
            sessions = list(self._sessions)
            self._sessions.clear()
        for session in sessions:
            try:
                session.close()
            except Exception:  # pragma: no cover - closing must never raise
                pass

    @property
    def exhausted(self) -> bool:
        """True once the overall time budget is used up."""
        return self._deadline is not None and time.monotonic() > self._deadline

    def fetch(
        self,
        url: str,
        *,
        check_robots: bool = True,
        retries: int | None = None,
    ) -> FetchResult:
        if self._cancel.is_set():
            return FetchResult(url=url, final_url=url, error="cancelled")
        if check_robots and not self.allowed(url):
            return FetchResult(url=url, final_url=url, error="blocked-by-robots")
        if self.exhausted:
            return FetchResult(url=url, final_url=url, error="time-budget-exhausted")

        attempts = (self.retries if retries is None else retries) + 1
        last_error = "unknown-error"
        for attempt in range(attempts):
            if self._cancel.is_set() or not self._wait_turn():
                return FetchResult(url=url, final_url=url, error="cancelled")
            try:
                response = self._session().get(
                    url, timeout=self.timeout, allow_redirects=True, stream=True
                )
            except requests.RequestException as exc:
                last_error = f"network: {type(exc).__name__}: {exc}"
                if attempt + 1 < attempts:
                    if self._cancel.wait(min(2.0 * (attempt + 1), 6.0)):
                        return FetchResult(url=url, final_url=url, error="cancelled")
                continue

            try:
                headers = {key.lower(): value for key, value in response.headers.items()}
                status = response.status_code
                if not response.ok:
                    result = FetchResult(
                        url=url,
                        final_url=response.url,
                        status=status,
                        headers=headers,
                        error=f"http-{status}",
                    )
                    if status in RETRY_STATUSES and attempt + 1 < attempts:
                        if self._cancel.wait(min(2.0 * (attempt + 1), 6.0)):
                            return FetchResult(url=url, final_url=url, error="cancelled")
                        last_error = result.error or last_error
                        continue
                    return result

                declared = headers.get("content-length", "")
                if declared.isdigit() and int(declared) > self.max_bytes:
                    return FetchResult(
                        url=url,
                        final_url=response.url,
                        status=status,
                        headers=headers,
                        error=f"too-large ({int(declared)} bytes)",
                    )

                body, body_error = self._read_body(response)
                return FetchResult(
                    url=url,
                    final_url=response.url,
                    status=status,
                    headers=headers,
                    content=body,
                    error=body_error,
                )
            finally:
                response.close()

        return FetchResult(url=url, final_url=url, error=last_error)

    def fetch_many(
        self, urls: list[str], *, check_robots: bool = True,
    ) -> dict[str, FetchResult]:
        """Fetch several URLs concurrently, keyed by URL."""
        return dict(self.iter_fetch_many(urls, check_robots=check_robots))

    def iter_fetch_many(
        self, urls: list[str], *, check_robots: bool = True,
    ) -> Iterator[tuple[str, FetchResult]]:
        """Yield responses as they finish so the UI can advance during downloads."""
        if not urls:
            return
        if len(urls) == 1:
            yield urls[0], self.fetch(urls[0], check_robots=check_robots)
            return
        with ThreadPoolExecutor(max_workers=self.jobs) as pool:
            futures = {
                pool.submit(self.fetch, url, check_robots=check_robots): url
                for url in urls
            }
            for future in as_completed(futures):
                url = futures[future]
                try:
                    yield url, future.result()
                except Exception as exc:  # pragma: no cover - defensive
                    yield url, FetchResult(url=url, final_url=url, error=str(exc))

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            return True
        origin = f"{parts.scheme}://{parts.netloc}"
        parser = self._robots_for(origin)
        if parser is None:
            return True
        return parser.can_fetch(self.user_agent, url)

    # -- internals -------------------------------------------------------
    def _read_body(self, response: requests.Response) -> tuple[bytes, str | None]:
        """Read a response body with a hard size cap and a wall-clock deadline."""
        chunks: list[bytes] = []
        total = 0
        started = time.monotonic()
        try:
            for chunk in response.iter_content(chunk_size=_READ_CHUNK):
                if self._cancel.is_set():
                    return b"", "cancelled"
                if not chunk:
                    continue
                total += len(chunk)
                if total > self.max_bytes:
                    return b"", f"too-large (> {self.max_bytes} bytes)"
                if time.monotonic() - started > self.per_response_deadline:
                    return b"", "stalled (response slower than the read deadline)"
                chunks.append(chunk)
                if self._on_transfer is not None:
                    self._on_transfer(len(chunk))
        except requests.RequestException as exc:
            return b"", f"network: {type(exc).__name__}: {exc}"
        return b"".join(chunks), None

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = OriginScopedSession(
                self._origin_url, extra_headers=self.extra_headers, cookies=self._cookies
            )
            session.headers.update(self._base_headers)
            self._local.session = session
            with self._sessions_lock:
                self._sessions.append(session)
        return session

    def _wait_turn(self) -> bool:
        if self.delay <= 0:
            return not self._cancel.is_set()
        with self._limiter_lock:
            wait = self._next_allowed - time.monotonic()
            if wait > 0 and self._cancel.wait(wait):
                return False
            if self._cancel.is_set():
                return False
            self._next_allowed = time.monotonic() + self.delay
            self.request_count += 1
        return True

    def _robots_for(self, origin: str) -> RobotFileParser | None:
        with self._robots_lock:
            if origin in self._robots:
                return self._robots[origin]
        parser: RobotFileParser | None = None
        try:
            result = self.fetch(urljoin(origin, "/robots.txt"), check_robots=False, retries=0)
            if result.ok and result.content:
                parser = RobotFileParser()
                parser.parse(result.content.decode("utf-8", "replace").splitlines())
        except Exception:  # pragma: no cover - network stack already logs errors
            parser = None
        with self._robots_lock:
            self._robots[origin] = parser
        return parser

