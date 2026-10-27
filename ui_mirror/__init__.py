"""ui_mirror - download a website's interface into a browsable offline copy."""

from __future__ import annotations

from .check import FeasibilityReport, analyze
from .fetcher import DEFAULT_USER_AGENT, Fetcher, FetchResult, decode_text
from .localmap import LocalMap, url_key
from .mirror import MirrorOptions, MirrorReport, SiteMirror

__version__ = "1.9.11"

__all__ = [
    "DEFAULT_USER_AGENT",
    "FeasibilityReport",
    "Fetcher",
    "FetchResult",
    "LocalMap",
    "MirrorOptions",
    "MirrorReport",
    "SiteMirror",
    "analyze",
    "decode_text",
    "url_key",
    "__version__",
]
