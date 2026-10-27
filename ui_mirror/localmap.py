"""Map remote URLs onto stable, filesystem-safe local paths.

Every resource of the mirrored site gets exactly one deterministic local path.
Pages become ``*.html`` files, assets keep their extension (inferred from the
``Content-Type`` header when the URL has none). Collisions are resolved with a
short hash of the URL, so two runs against the same site produce the same tree.
"""

from __future__ import annotations

import hashlib
import mimetypes
import posixpath
import re
from urllib.parse import unquote, urlsplit, urlunsplit

PAGE_EXTENSIONS = frozenset(
    {
        ".html",
        ".htm",
        ".xhtml",
        ".shtml",
        ".php",
        ".php3",
        ".php4",
        ".php5",
        ".phtml",
        ".asp",
        ".aspx",
        ".jsp",
        ".jspx",
        ".cfm",
        ".cgi",
        ".pl",
        ".do",
        ".action",
    }
)

ASSET_EXTENSIONS = frozenset(
    {
        ".css",
        ".js",
        ".mjs",
        ".cjs",
        ".json",
        ".map",
        ".xml",
        ".txt",
        ".csv",
        ".webmanifest",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".avif",
        ".svg",
        ".ico",
        ".bmp",
        ".cur",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".mp4",
        ".webm",
        ".ogv",
        ".mp3",
        ".wav",
        ".ogg",
        ".m4a",
        ".vtt",
        ".pdf",
        ".zip",
        ".wasm",
    }
)

CONTENT_TYPE_EXT = {
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "text/css": ".css",
    "text/javascript": ".js",
    "application/javascript": ".js",
    "application/x-javascript": ".js",
    "application/json": ".json",
    "application/ld+json": ".json",
    "application/manifest+json": ".webmanifest",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/svg+xml": ".svg",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    "image/bmp": ".bmp",
    "font/woff": ".woff",
    "font/woff2": ".woff2",
    "font/ttf": ".ttf",
    "font/otf": ".otf",
    "application/font-woff": ".woff",
    "application/font-woff2": ".woff2",
    "application/x-font-ttf": ".ttf",
    "application/vnd.ms-fontobject": ".eot",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/ogg": ".ogv",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "application/pdf": ".pdf",
    "application/wasm": ".wasm",
    "text/plain": ".txt",
    "text/vtt": ".vtt",
}

_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_SEGMENT = 96


def url_key(url: str) -> str:
    """Canonical identity of a resource: no fragment, no default port."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def guess_extension(content_type: str | None) -> str:
    """Pick a file extension for a response that has none in its URL."""
    if not content_type:
        return ".bin"
    content_type = content_type.split(";")[0].strip().lower()
    if content_type in CONTENT_TYPE_EXT:
        return CONTENT_TYPE_EXT[content_type]
    guessed = mimetypes.guess_extension(content_type) or ""
    if guessed in ("", ".jpe", ".bin"):
        return ".bin"
    return guessed


def _sanitize_segment(segment: str) -> str:
    segment = _ILLEGAL_CHARS.sub("_", segment).strip(" .")
    if not segment:
        return "_"
    stem = segment.split(".")[0].upper()
    if stem in _RESERVED_NAMES:
        segment = "_" + segment
    if len(segment) > _MAX_SEGMENT:
        digest = hashlib.sha1(segment.encode("utf-8")).hexdigest()[:8]
        base, ext = posixpath.splitext(segment)
        segment = f"{base[: _MAX_SEGMENT - 9].rstrip(' .') or '_'}__{digest}{ext[:8]}"
    return segment


class LocalMap:
    """Owns the URL -> relative path table for one mirror run."""

    def __init__(self, output_dir: str) -> None:
        self.output_dir = output_dir
        self._paths: dict[str, str] = {}
        self._taken: set[str] = set()

    # -- lookups ---------------------------------------------------------
    def lookup(self, url: str) -> str | None:
        return self._paths.get(url_key(url))

    def __contains__(self, url: str) -> bool:
        return url_key(url) in self._paths

    # -- assignment ------------------------------------------------------
    def assign(
        self,
        url: str,
        *,
        is_page: bool,
        content_type: str | None = None,
    ) -> str:
        """Return the local path for ``url``, assigning one if needed."""
        key = url_key(url)
        existing = self._paths.get(key)
        if existing is not None:
            return existing
        relative = self._build(key, is_page=is_page, content_type=content_type)
        relative = self._dedupe(relative, key)
        self._paths[key] = relative
        self._taken.add(relative.casefold())
        return relative

    def add_alias(self, url: str, relative: str) -> None:
        """Point another URL key at an already assigned file (e.g. after redirect)."""
        key = url_key(url)
        if key in self._paths:
            return
        self._paths[key] = relative
        self._taken.add(relative.casefold())

    def forget(self, url: str) -> None:
        relative = self._paths.pop(url_key(url), None)
        if relative is not None:
            self._taken.discard(relative.casefold())

    # -- helpers ---------------------------------------------------------
    def relative(self, from_relative: str, to_relative: str) -> str:
        """Path of ``to_relative`` as seen from the file ``from_relative``."""
        start = posixpath.dirname(from_relative) or "."
        return posixpath.relpath(to_relative, start)

    def _build(self, key: str, *, is_page: bool, content_type: str | None) -> str:
        parts = urlsplit(key)
        segments = [
            _sanitize_segment(segment)
            for segment in unquote(parts.path).split("/")
            if segment not in ("", ".", "..")
        ]
        if not segments:
            segments = ["index"]

        last = segments[-1]
        stem, ext = posixpath.splitext(last)
        ext = ext.lower()

        if is_page:
            last = (stem if ext in PAGE_EXTENSIONS else last) + ".html"
            segments[-1] = last
        elif ext == "" or len(ext) > 8:
            segments[-1] = last + guess_extension(content_type)

        return "/".join(segments)

    def _dedupe(self, relative: str, key: str) -> str:
        if relative.casefold() not in self._taken:
            return relative
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
        directory, name = posixpath.split(relative)
        stem, ext = posixpath.splitext(name)
        candidate = posixpath.join(directory, f"{stem}__{digest}{ext}")
        counter = 2
        while candidate.casefold() in self._taken:
            candidate = posixpath.join(directory, f"{stem}__{digest}-{counter}{ext}")
            counter += 1
        return candidate
