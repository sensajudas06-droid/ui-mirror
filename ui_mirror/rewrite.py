"""Rewrite URLs inside CSS and HTML so the copy works from the local disk."""

from __future__ import annotations

import posixpath
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .localmap import ASSET_EXTENSIONS, PAGE_EXTENSIONS

SKIP_PREFIXES = (
    "#",
    "data:",
    "javascript:",
    "mailto:",
    "tel:",
    "sms:",
    "geo:",
    "blob:",
    "about:",
    "file:",
    "ftp:",
    "chrome:",
)

# href/source attributes per element that need rewriting.
URL_ATTR_RULES: dict[str, tuple[str, ...]] = {
    "a": ("href",),
    "area": ("href",),
    "link": ("href",),
    "script": ("src",),
    "img": ("src", "srcset", "data-src", "data-srcset", "data-original", "data-lazy-src"),
    "source": ("src", "srcset"),
    "video": ("src", "poster"),
    "audio": ("src",),
    "track": ("src",),
    "embed": ("src",),
    "object": ("data",),
    "iframe": ("src",),
    "frame": ("src",),
    "input": ("src",),
    "use": ("href", "xlink:href"),
    "image": ("href", "xlink:href"),
    "body": ("background",),
    "table": ("background",),
    "td": ("background",),
}

SRCSET_ATTRS = frozenset({"srcset", "data-srcset"})

# <link rel="..."> values that point at a downloadable asset.
LINK_ASSET_RELS = frozenset(
    {
        "stylesheet",
        "icon",
        "shortcut icon",
        "apple-touch-icon",
        "apple-touch-icon-precomposed",
        "mask-icon",
        "manifest",
        "preload",
        "modulepreload",
        "prefetch",
        "preconnect",
        "dns-prefetch",
    }
)

_CSS_REF = re.compile(
    r"""(?P<import>@import\s+(?P<import_quote>["'])(?P<import_url>[^"']+)(?P=import_quote))"""
    r"""|(?P<url>url\(\s*(?P<quote>["']?)(?P<url_value>[^"')\s]+)(?P=quote)\s*\))""",
    re.IGNORECASE,
)
_CSS_CHARSET = re.compile(r"""^\s*@charset\s+["'][^"']*["']\s*;?""", re.MULTILINE)


def is_skippable(raw: str) -> bool:
    value = raw.strip().lower()
    return not value or value.startswith(SKIP_PREFIXES)


def absolutize(base_url: str, raw: str) -> str | None:
    """Resolve ``raw`` against ``base_url``; ``None`` for non-HTTP references."""
    if is_skippable(raw):
        return None
    absolute = urljoin(base_url, raw.strip())
    parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https"):
        return None
    return absolute


class LinkResolver:
    """Turn a remote reference into a path relative to the file containing it."""

    def __init__(self, local_map, base_url: str, source_relative: str) -> None:
        self.local_map = local_map
        self.base_url = base_url
        self.source_relative = source_relative

    def absolute(self, raw: str) -> str | None:
        return absolutize(self.base_url, raw)

    def __call__(self, raw: str) -> str | None:
        """Local relative path when mirrored, live absolute URL otherwise.

        Unmirrored targets (pages outside the depth budget, failed assets) are
        pointed at the real site, because a root-relative ``/x`` would resolve
        to the drive root under ``file://`` and simply break.
        """
        absolute = self.absolute(raw)
        if absolute is None:
            return None
        target = self.local_map.lookup(absolute)
        if target is None:
            return absolute
        fragment = urlsplit(absolute).fragment
        local = self.local_map.relative(self.source_relative, target)
        return f"{local}#{fragment}" if fragment else local


def iter_css_refs(text: str) -> list[str]:
    """Every raw URL referenced by ``url(...)`` or a quoted ``@import``."""
    references: list[str] = []
    for match in _CSS_REF.finditer(text):
        value = match.group("import_url") or match.group("url_value") or ""
        if not is_skippable(value):
            references.append(value.strip())
    return references


def rewrite_css(text: str, resolver: LinkResolver) -> str:
    """Rewrite every ``url(...)`` and quoted ``@import`` inside a stylesheet."""

    def replace(match: re.Match[str]) -> str:
        if match.group("import"):
            new = resolver(match.group("import_url"))
            return f'@import url("{new}");' if new else match.group(0)
        new = resolver(match.group("url_value"))
        return f'url("{new}")' if new else match.group(0)

    text = _CSS_CHARSET.sub('@charset "utf-8";', text, count=1)
    return _CSS_REF.sub(replace, text)


def rewrite_srcset(value: str, resolver: LinkResolver) -> str:
    rewritten: list[str] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split()
        new = resolver(bits[0])
        if new:
            bits[0] = new
        rewritten.append(" ".join(bits))
    return ", ".join(rewritten)


def classify_anchor(absolute: str) -> str:
    """Return ``"page"`` or ``"asset"`` for an ``<a href>`` target."""
    path = urlsplit(absolute).path
    extension = posixpath.splitext(path)[1].lower()
    if extension in ASSET_EXTENSIONS:
        return "asset"
    if extension in PAGE_EXTENSIONS or extension == "":
        return "page"
    return "asset"


def link_is_asset(element) -> bool:
    rel = element.get("rel") or []
    if isinstance(rel, str):
        rel = [rel]
    tokens = {token.strip().lower() for token in rel}
    if "stylesheet" in tokens:
        return True
    return bool(tokens & LINK_ASSET_RELS)


def rewrite_document(soup: BeautifulSoup, resolver: LinkResolver) -> None:
    """Rewrite URLs in place: attributes, inline styles, <style> blocks, meta charset."""
    for element in soup.find_all(True):
        name = element.name
        for attribute in URL_ATTR_RULES.get(name, ()):
            value = element.get(attribute)
            if not value or not isinstance(value, str):
                continue
            if attribute in SRCSET_ATTRS:
                element[attribute] = rewrite_srcset(value, resolver)
                continue
            new = resolver(value)
            if new:
                element[attribute] = new

        style = element.get("style")
        if style:
            element["style"] = rewrite_css(style, resolver)

    for style_tag in soup.find_all("style"):
        text = style_tag.get_text()
        if text:
            style_tag.clear()
            style_tag.append(rewrite_css(text, resolver))

    normalize_encoding_tags(soup)
    strip_security_headers(soup)


def normalize_encoding_tags(soup: BeautifulSoup) -> None:
    """The copy is written as UTF-8, so every charset declaration must say so."""
    declared = False
    for meta in soup.find_all("meta"):
        if meta.get("charset"):
            meta["charset"] = "utf-8"
            declared = True
        elif (meta.get("http-equiv") or "").strip().lower() == "content-type":
            meta["content"] = "text/html; charset=utf-8"
            declared = True
    if declared:
        return
    head = soup.find("head")
    if head is None:
        return
    tag = soup.new_tag("meta", charset="utf-8")
    head.insert(0, tag)


def strip_security_headers(soup: BeautifulSoup) -> None:
    """A mirrored copy under file:// must not carry the original CSP."""
    for meta in soup.find_all("meta"):
        if (meta.get("http-equiv") or "").strip().lower() == "content-security-policy":
            meta.decompose()
