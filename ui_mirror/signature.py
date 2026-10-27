"""Opening skull frames and the live image-shaped ASCII signature."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from PIL import Image


PANEL_COLUMNS = 34
PANEL_ROWS = 27
# Each group has similar visual weight, so characters can change without
# breaking the skull silhouette or turning the eye sockets into noise.
_GLYPH_GROUPS = (
    " ", ".,", ":;", ";i", "rs", "xX", "A25", "3hH", "HGS", "S#9", "#B@",
)


@lru_cache(maxsize=1)
def _frames() -> dict:
    path = Path(__file__).resolve().parent / "assets" / "deadgpt_skull_frames.json"
    return json.loads(path.read_text(encoding="utf-8"))


def skull_frame(tick: int, mood: str = "idle", size: str = "compact") -> tuple[str, str, list[list[int]]]:
    """Return the original skull animation frame, reaction, and red-eye spans."""
    expression = {
        "idle": "neutral",
        "typing": "surprised",
        "scan": "neutral",
        "copy": "grin",
        "done": "grin",
        "error": "angry",
        "wait": "surprised",
        "laugh": "laugh",
    }.get(mood, "neutral")
    if mood == "idle" and tick % 130 >= 112:
        expression = "laugh"

    asset = _frames()
    frames = asset["full" if size == "full" else "compact"][expression]
    step = tick if expression == "laugh" else tick // 3
    frame = frames[step % len(frames)]
    if size == "full":
        art, hot = frame["text"], frame["hot"]
    else:
        art, hot = frame, []

    reaction = {
        "typing": "Adresi dinliyor...",
        "scan": "Siteyi inceliyor...",
        "copy": "Dosyaları topluyor...",
        "done": "Kopya hazır.",
        "error": "Bir şey ters gitti.",
        "wait": "Durduruluyor...",
    }.get(mood, "Bir adres bekliyor.")
    if expression == "laugh":
        reaction = "HA HA HA!"
    return art, reaction, hot


def render_skull(tick: int, mood: str = "idle") -> tuple[str, str]:
    """Sample a skull portrait into changing letters, digits, and symbols."""
    _, reaction, _ = skull_frame(tick, mood)
    phase = tick // (2 if mood in {"scan", "copy", "laugh"} else 3)
    rows = []
    for y, values in enumerate(_panel_luminance()):
        line = []
        for x, value in enumerate(values):
            level = min(10, round((value / 255) ** 0.85 * 10))
            choices = _GLYPH_GROUPS[level]
            line.append(choices[(x * 37 + y * 19 + (x * y) % 13 + phase) % len(choices)])
        rows.append("".join(line))
    return "\n".join(rows), reaction


@lru_cache(maxsize=1)
def panel_skull_source() -> Image.Image:
    path = Path(__file__).resolve().parent / "assets" / "ascii_skull_source_v2.png"
    image = Image.open(path).convert("L")
    bounds = image.point(lambda value: 255 if value > 25 else 0).getbbox()
    if bounds is None:
        raise ValueError("ASCII skull source is empty")
    left, top, right, bottom = bounds
    return image.crop((max(0, left - 40), max(0, top - 40),
                       min(image.width, right + 40), min(image.height, bottom + 40)))


@lru_cache(maxsize=1)
def _panel_luminance() -> tuple[tuple[int, ...], ...]:
    sampled = panel_skull_source().resize((PANEL_COLUMNS, PANEL_ROWS), Image.Resampling.LANCZOS)
    return tuple(tuple(sampled.getpixel((x, y)) for x in range(PANEL_COLUMNS)) for y in range(PANEL_ROWS))


def play_laugh() -> None:
    """Play the original opening laugh without blocking Tk's animation loop."""
    try:
        import winsound

        path = Path(__file__).resolve().parent / "assets" / "deadgpt_laugh.wav"
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except (ImportError, OSError, RuntimeError):
        pass


def stop_laugh() -> None:
    try:
        import winsound

        winsound.PlaySound(None, winsound.SND_ASYNC)
    except (ImportError, OSError, RuntimeError):
        pass
