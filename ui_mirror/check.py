"""Pre-flight feasibility check: can this page be mirrored, and why?

The report is built from observable facts only (HTTP status, robots.txt rules,
response headers, parsed markup, probe requests for real assets), so the answer
to "is this page copyable?" is backed by evidence instead of a guess.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from .fetcher import DEFAULT_USER_AGENT
from .http_session import OriginScopedSession
from .rewrite import URL_ATTR_RULES, absolutize, classify_anchor, link_is_asset

MAX_BODY_BYTES = 3 * 1024 * 1024
PROBE_LIMIT = 12

RENDER_MOUNT_IDS = ("root", "app", "__next", "___gatsby", "react-root", "app-root")
RENDER_MARKERS = (
    "__NEXT_DATA__",
    "window.__NUXT__",
    "data-reactroot",
    "ng-version",
    "__SVELTE__",
    "astro-island",
)
CHALLENGE_MARKERS = (
    "just a moment",
    "attention required",
    "checking your browser",
    "enable javascript and cookies to continue",
    "ddos protection",
    "cf-browser-verification",
    "are you a robot",
)
MEDIA_EXTENSIONS = (".m3u8", ".mpd", ".mp4", ".webm", ".mp3", ".m4a", ".ogg")

VERDICT_TEXT = {
    "mirrorable": "Kopyalanabilir",
    "conditional": "Koşullu kopyalanabilir",
    "blocked": "Kopyalanamaz",
}


@dataclass
class CheckItem:
    status: str  # pass | warn | fail | info
    title: str
    detail: str = ""
    penalty: int = 0


@dataclass
class FeasibilityReport:
    url: str
    final_url: str = ""
    http_status: int | None = None
    server: str = ""
    content_type: str = ""
    score: int = 100
    verdict: str = "blocked"
    blocked: bool = False
    headline: str = ""
    items: list[CheckItem] = field(default_factory=list)
    page_links: int = 0
    asset_links: int = 0
    probed_assets: int = 0
    reachable_assets: int = 0
    known_bytes: int = 0
    render_needed: bool = False
    empty_shell: bool = False
    login_wall: bool = False
    robots_allowed: bool = True
    suggested_flags: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    body: bytes = field(default=b"", repr=False, compare=False)
    response_headers: dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    @property
    def ok(self) -> bool:
        return not self.blocked

    @property
    def verdict_text(self) -> str:
        return VERDICT_TEXT.get(self.verdict, self.verdict)

    def add(self, item: CheckItem) -> None:
        self.items.append(item)

    def as_dict(self) -> dict:
        return {
            "url": self.url,
            "final_url": self.final_url,
            "http_status": self.http_status,
            "server": self.server,
            "content_type": self.content_type,
            "score": self.score,
            "verdict": self.verdict,
            "verdict_text": self.verdict_text,
            "blocked": self.blocked,
            "headline": self.headline,
            "page_links": self.page_links,
            "asset_links": self.asset_links,
            "probed_assets": self.probed_assets,
            "reachable_assets": self.reachable_assets,
            "known_bytes": self.known_bytes,
            "render_needed": self.render_needed,
            "empty_shell": self.empty_shell,
            "login_wall": self.login_wall,
            "robots_allowed": self.robots_allowed,
            "suggested_flags": list(self.suggested_flags),
            "duration_seconds": round(self.duration_seconds, 2),
            "items": [
                {
                    "status": item.status,
                    "title": item.title,
                    "detail": item.detail,
                    "penalty": item.penalty,
                }
                for item in self.items
            ],
        }


def analyze(
    url: str,
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: float = 15.0,
    cookies: str | None = None,
    extra_headers: dict[str, str] | None = None,
    probe_limit: int = PROBE_LIMIT,
) -> FeasibilityReport:
    """Run every check we can observe from the outside and score the result."""
    started = time.monotonic()
    report = FeasibilityReport(url=url)
    session = OriginScopedSession(url, extra_headers=extra_headers, cookies=cookies)
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,tr;q=0.8",
        }
    )

    try:
        _check_http(session, report, timeout)
        if _is_html(report):
            _check_robots(session, report, report.final_url or url, user_agent, timeout)
            soup = BeautifulSoup(_decode(report.body), "html.parser") if report.body else None
            if soup is not None:
                _check_markup(report, soup)
                if not report.blocked:
                    _check_targets(report, soup, session, timeout, probe_limit)
        else:
            if report.http_status is not None:
                report.add(
                    CheckItem(
                        "fail",
                        "HTML sayfası değil",
                        f"Content-Type: {report.content_type or 'bilinmiyor'}",
                        penalty=50,
                    )
                )
                report.blocked = True
    finally:
        session.close()

    report.score = max(0, min(100, 100 - sum(item.penalty for item in report.items)))
    if report.empty_shell:
        report.score = min(report.score, 65)
    report.verdict = _verdict(report)
    report.headline = _headline(report)
    report.suggested_flags = _suggest_flags(report)
    report.duration_seconds = time.monotonic() - started
    return report


# -- individual checks ---------------------------------------------------
def _check_http(session: requests.Session, report: FeasibilityReport, timeout: float) -> None:
    try:
        response = session.get(report.url, timeout=timeout, allow_redirects=True, stream=True)
    except requests.RequestException as exc:
        report.add(
            CheckItem("fail", "Bağlantı kurulamadı", f"{type(exc).__name__}: {exc}", penalty=100)
        )
        report.blocked = True
        report.http_status = None
        return

    try:
        report.http_status = response.status_code
        report.final_url = response.url
        report.server = (response.headers.get("server") or "").strip()
        report.content_type = (
            (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        )
        report.response_headers = {key.lower(): value for key, value in response.headers.items()}
        _read_body(response, report)
    finally:
        response.close()

    status = report.http_status
    if status is not None and 200 <= status < 300:
        report.add(CheckItem("pass", f"HTTP {status} · sayfa açılıyor"))
    elif status is not None and 300 <= status < 400:
        report.add(CheckItem("info", f"HTTP {status} · yönlendirme izlendi", report.final_url))
    elif status in (401, 403):
        report.add(
            CheckItem(
                "fail",
                f"HTTP {status} · erişim reddedildi",
                "Oturum çerezi/başlık gerekebilir (--cookies)",
                penalty=45,
            )
        )
        report.blocked = True
    elif status == 404:
        report.add(CheckItem("fail", "HTTP 404 · sayfa bulunamadı", penalty=60))
        report.blocked = True
    elif status == 429:
        report.add(CheckItem("warn", "HTTP 429 · hız sınırı", "İstek aralığını artırın", penalty=20))
    elif status is not None and status >= 500:
        report.add(CheckItem("fail", f"HTTP {status} · sunucu hatası", penalty=50))

    if (report.response_headers.get("cf-mitigated") or "").lower() == "challenge":
        report.add(
            CheckItem(
                "fail",
                "Cloudflare doğrulaması",
                "Tarayıcı meydan okuması var; statik indirme engellenir",
                penalty=55,
            )
        )
        report.blocked = True

    robots_header = report.response_headers.get("x-robots-tag") or ""
    if "noindex" in robots_header.lower():
        report.add(
            CheckItem(
                "warn", "X-Robots-Tag: noindex", "Sahibi indekslenmesini istemiyor", penalty=8
            )
        )


def _read_body(response: requests.Response, report: FeasibilityReport) -> None:
    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in response.iter_content(65536):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total >= MAX_BODY_BYTES:
                break
    except requests.RequestException:
        pass
    report.body = b"".join(chunks)


def _decode(body: bytes) -> str:
    for encoding in ("utf-8", "windows-1254", "latin-1"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _is_html(report: FeasibilityReport) -> bool:
    if report.content_type in ("text/html", "application/xhtml+xml"):
        return True
    head = (report.body or b"").lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<head" in head[:2048]


def _check_robots(
    session: requests.Session,
    report: FeasibilityReport,
    url: str,
    user_agent: str,
    timeout: float,
) -> None:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    parser: RobotFileParser | None = None
    robots_text = ""
    try:
        response = session.get(origin + "/robots.txt", timeout=timeout)
        if response.ok and response.text:
            robots_text = response.text
            parser = RobotFileParser()
            parser.parse(robots_text.splitlines())
    except requests.RequestException:
        parser = None

    if parser is None:
        report.robots_allowed = True
        report.add(CheckItem("pass", "robots.txt yok", "Kısıt tanımlanmamış"))
        return

    allowed = parser.can_fetch(user_agent, url)
    report.robots_allowed = allowed
    has_sitemap = "sitemap" in robots_text.lower()
    if allowed:
        report.add(
            CheckItem(
                "pass",
                "robots.txt sayfayı engellemiyor",
                "Sitemap tanımlı" if has_sitemap else "Sitemap tanımlı değil",
            )
        )
    else:
        report.add(
            CheckItem(
                "warn",
                "robots.txt bu URL'yi kısıtlıyor",
                "Sayfa sahibi otomatik kopyalamayı istemiyor; --no-robots yalnızca kendi sitende",
                penalty=25,
            )
        )


def is_unrendered_shell(soup: BeautifulSoup) -> bool:
    """Detect a near-empty JavaScript shell without rejecting sparse static pages."""
    body = soup.body or soup
    visible_text = body.get_text(" ", strip=True)
    if len(visible_text) >= 100 or body.find("form") or body.find("input"):
        return False
    scripts = len(soup.find_all("script"))
    empty_mount = any(
        (mount := soup.find(id=element_id)) is not None
        and not mount.get_text(" ", strip=True)
        for element_id in RENDER_MOUNT_IDS
    )
    return empty_mount or scripts >= 3


def is_render_candidate(soup: BeautifulSoup) -> bool:
    """Catch pages whose static chrome hides a JavaScript-populated main area."""
    if is_unrendered_shell(soup):
        return True
    body = soup.body or soup
    return len(body.get_text(" ", strip=True)) < 3500 and len(soup.find_all("script")) >= 8


def _check_markup(report: FeasibilityReport, soup: BeautifulSoup) -> None:
    text = soup.get_text(" ", strip=True)
    scripts = len(soup.find_all("script"))
    markers = sum(1 for marker in RENDER_MARKERS if marker in str(soup))
    empty_mount = any(
        (soup.find(id=element_id) is not None)
        and len(soup.find(id=element_id).get_text(strip=True)) < 40
        for element_id in RENDER_MOUNT_IDS
    )
    report.render_needed = (
        empty_mount or markers >= 2 or (len(text) < 900 and scripts >= 3)
        or is_render_candidate(soup)
    )
    report.empty_shell = is_unrendered_shell(soup)
    if report.render_needed:
        report.add(
            CheckItem(
                "warn",
                "İstemci tarafında render ediliyor (SPA)",
                "Tarayıcıda yükleme gerekir; masaüstü uygulaması bunu otomatik dener",
                penalty=15,
            )
        )
    if report.empty_shell:
        report.add(CheckItem(
            "warn", "Sayfa içeriği HTML yanıtında yok",
            "Tarayıcıda JavaScript çalışmadan anlamlı bir kopya oluşmaz",
            penalty=20,
        ))

    password_inputs = soup.find_all("input", {"type": "password"})
    login_forms = [
        form
        for form in soup.find_all("form")
        if any(
            word in (form.get("action") or "").lower()
            for word in ("login", "signin", "sign-in", "auth")
        )
    ]
    route_segments = urlsplit(report.final_url or report.url).path.lower().strip("/").split("/")
    login_route = any(part in {"login", "signin", "sign-in", "log-in"} for part in route_segments)
    report.login_wall = bool(password_inputs) or bool(login_forms) or login_route
    if report.login_wall:
        report.add(
            CheckItem(
                "warn",
                "Oturum girişi sayfası",
                "Görünüm kaydedilebilir; gerçek oturum açma çevrimdışı çalışmaz",
                penalty=12,
            )
        )

    title = soup.title.get_text().lower() if soup.title else ""
    challenge_text = f"{title} {text.lower()}"
    if report.http_status in (403, 503) and any(
        marker in challenge_text for marker in CHALLENGE_MARKERS
    ):
        report.add(
            CheckItem(
                "fail",
                "Anti-bot koruması",
                "Cloudflare/benzeri doğrulama sayfası döndü",
                penalty=55,
            )
        )
        report.blocked = True

    meta_robots = soup.find("meta", attrs={"name": "robots"})
    if meta_robots and "noindex" in (meta_robots.get("content") or "").lower():
        report.add(
            CheckItem("warn", "meta robots: noindex", "İndekslemeye kapalı sayfa", penalty=8)
        )

    if soup.find("canvas") is not None:
        report.add(CheckItem("info", "canvas/WebGL içeriği var", "Bu içerik kopyalanmaz"))

    inline_scripts = " ".join(
        script.get_text() for script in soup.find_all("script") if script.get_text()
    )
    if "WebSocket(" in inline_scripts:
        report.add(
            CheckItem("info", "WebSocket kullanılıyor", "Canlı veri akışı çevrimdışı çalışmaz")
        )

    base = report.final_url or report.url
    media_hits = [
        absolute
        for absolute in _absolute_targets(soup, base)
        if absolute.lower().endswith(MEDIA_EXTENSIONS)
    ]
    if media_hits or soup.find("video") is not None:
        report.add(CheckItem("info", "Video/ses akışı var", "Büyük medya dosyaları atlanabilir"))


def _check_targets(
    report: FeasibilityReport,
    soup: BeautifulSoup,
    session: requests.Session,
    timeout: float,
    probe_limit: int,
) -> None:
    base = report.final_url or report.url
    assets = _asset_targets(soup, base)
    pages = _page_targets(soup, base, report.url)
    report.asset_links = len(assets)
    report.page_links = len(pages)

    report.add(
        CheckItem(
            "info",
            f"{report.page_links} sayfa bağlantısı · {report.asset_links} varlık",
            "Kopyalanacak ilk sayfanın içeriği",
        )
    )

    if not assets and report.render_needed:
        report.add(CheckItem("warn", "HTML içinde varlık bulunamadı", "Varlıklar JavaScript ile yükleniyor olabilir"))
        return
    if not assets:
        report.add(CheckItem("pass", "Harici varlık yok", "Sayfa kendi kendine yeterli"))
        return

    probed = assets[: max(1, probe_limit)]
    reachable = 0
    total_bytes = 0
    for absolute in probed:
        status, size = _probe_asset(session, absolute, timeout)
        if status is not None and 200 <= status < 400:
            reachable += 1
            total_bytes += size
    report.probed_assets = len(probed)
    report.reachable_assets = reachable
    report.known_bytes = total_bytes

    ratio = reachable / len(probed) if probed else 0.0
    if ratio >= 0.9:
        report.add(
            CheckItem(
                "pass",
                f"Varlık erişimi {reachable}/{len(probed)}",
                f"Örneklenen varlıklar indirilebiliyor (~{_human(total_bytes)})",
            )
        )
    elif ratio >= 0.5:
        report.add(
            CheckItem(
                "warn",
                f"Varlık erişimi {reachable}/{len(probed)}",
                "Bazı varlıklar 403/404 döndü; görsel bütünlük bozulabilir",
                penalty=10,
            )
        )
    else:
        report.add(
            CheckItem(
                "fail",
                f"Varlık erişimi {reachable}/{len(probed)}",
                "Varlıkların çoğu indirilemiyor (CDN/hotlink koruması)",
                penalty=25,
            )
        )


# -- helpers -------------------------------------------------------------
def _asset_targets(soup: BeautifulSoup, base: str) -> list[str]:
    host = _host(base)
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        absolute = absolutize(base, raw)
        if absolute is None or _host(absolute) != host:
            return
        if absolute.casefold() in seen:
            return
        seen.add(absolute.casefold())
        found.append(absolute)

    for element in soup.find_all(True):
        name = element.name
        if name in ("a", "area", "form"):
            continue
        if name == "link":
            href = element.get("href")
            if isinstance(href, str) and link_is_asset(element):
                add(href)
            continue
        for attribute in URL_ATTR_RULES.get(name, ()):
            value = element.get(attribute)
            if not isinstance(value, str) or not value.strip():
                continue
            if attribute in ("srcset", "data-srcset"):
                for part in value.split(","):
                    part = part.strip()
                    if part:
                        add(part.split()[0])
            else:
                add(value)
    return found


def _page_targets(soup: BeautifulSoup, base: str, source: str) -> list[str]:
    host = _host(base)
    source_host = _host(source)
    found: list[str] = []
    seen: set[str] = set()
    for element in soup.find_all(["a", "area", "iframe"]):
        value = element.get("href") or element.get("src")
        if not isinstance(value, str) or not value.strip():
            continue
        absolute = absolutize(base, value)
        if absolute is None:
            continue
        if element.name != "iframe" and classify_anchor(absolute) == "asset":
            continue
        target_host = _host(absolute)
        if target_host not in (host, source_host):
            continue
        if absolute.casefold() in seen:
            continue
        seen.add(absolute.casefold())
        found.append(absolute)
    return found


def _absolute_targets(soup: BeautifulSoup, base: str) -> list[str]:
    values: list[str] = []
    for element in soup.find_all(True):
        for attribute in URL_ATTR_RULES.get(element.name, ()):
            value = element.get(attribute)
            if not isinstance(value, str) or not value.strip():
                continue
            if attribute in ("srcset", "data-srcset"):
                for part in value.split(","):
                    part = part.strip()
                    if part:
                        absolute = absolutize(base, part.split()[0])
                        if absolute:
                            values.append(absolute)
                continue
            absolute = absolutize(base, value)
            if absolute:
                values.append(absolute)
    return values


def _probe_asset(session: requests.Session, url: str, timeout: float) -> tuple[int | None, int]:
    try:
        response = session.head(url, timeout=timeout, allow_redirects=True)
        status = response.status_code
        declared = response.headers.get("content-length")
        size = int(declared) if declared and declared.isdigit() else 0
        response.close()
        if status in (403, 405, 501):
            response = session.get(url, timeout=timeout, allow_redirects=True, stream=True)
            try:
                status = response.status_code
                declared = response.headers.get("content-length")
                size = int(declared) if declared and declared.isdigit() else 0
            finally:
                response.close()
        return status, size
    except requests.RequestException:
        return None, 0


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _human(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _verdict(report: FeasibilityReport) -> str:
    if report.blocked:
        return "blocked"
    if report.render_needed or report.login_wall:
        return "conditional"
    if report.score >= 80:
        return "mirrorable"
    if report.score >= 55:
        return "conditional"
    return "blocked"


def _headline(report: FeasibilityReport) -> str:
    if report.verdict == "mirrorable":
        return "Sayfa kopyalanabilir görünüyor."
    if report.verdict == "conditional":
        return "Kopyalanabilir, ancak koşullar var."
    return "Bu sayfa olduğu gibi kopyalanamaz."


def _suggest_flags(report: FeasibilityReport) -> list[str]:
    flags: list[str] = []
    if report.render_needed and not report.blocked:
        flags.append("--render")
    if report.login_wall and report.http_status in (401, 403):
        flags.append('--cookies "ad=deger"')
    if not report.robots_allowed and not report.blocked:
        flags.append("--no-robots")
    return flags


def format_report(report: FeasibilityReport) -> str:
    """Human readable confirmation text for the terminal."""
    score_line = f"{report.score}/100 · {report.verdict_text}"
    lines = [
        f"Teyit: {report.url}",
        f"Sonuç: {score_line} - {report.headline}",
        f"HTTP {report.http_status if report.http_status is not None else '-'} · "
        f"{report.content_type or 'bilinmiyor'} · {report.server or 'sunucu bilgisi yok'}",
        "",
    ]
    marks = {"pass": "[OK]  ", "warn": "[!]   ", "fail": "[X]   ", "info": "[i]   "}
    for item in report.items:
        lines.append(f"{marks.get(item.status, '[i]   ')}{item.title}")
        if item.detail:
            lines.append(f"        {item.detail}")
    lines.append("")
    lines.append(
        f"Keşfedilen: {report.page_links} sayfa bağlantısı · "
        f"{report.asset_links} varlık · örneklenen {report.probed_assets} "
        f"(erişilebilir {report.reachable_assets}, ~{_human(report.known_bytes)})"
    )
    if report.suggested_flags:
        lines.append("Önerilen: " + " ".join(report.suggested_flags))
    lines.append(f"Süre: {report.duration_seconds:.2f}s")
    return "\n".join(lines)
