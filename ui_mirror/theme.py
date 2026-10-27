"""Shared Blackwood interface palette and color helpers."""

from __future__ import annotations

# Neutral surfaces keep the interface calm; red is reserved for actions and identity.
BG = "#090D11"
SURFACE = "#11191F"
SURFACE_HI = "#19232B"
SURFACE_2 = "#23313A"
BORDER = "#2B3942"
LOG_BG = "#0D1419"
TRACK = "#243039"

TEXT = "#EAEDEA"
MUTED = "#A4B2B1"
DIM = "#71858A"

ACCENT = "#E04B55"
ACCENT_2 = "#E2A878"
ACCENT_INK = "#180D10"

OK = "#80CCA7"
WARN = "#E4B36E"
BAD = "#ED7075"

INTRO_BG = "#080C10"
INTRO_DIM = "#45515B"
INTRO_GRID = "#182229"


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, int(round(channel)))):02x}" for channel in rgb)


def lerp_color(start: str, end: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    a, b = hex_to_rgb(start), hex_to_rgb(end)
    return rgb_to_hex(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))
