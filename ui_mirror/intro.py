"""Minimal Blackwood opening: a fading logo and two resolving wordmarks."""

from __future__ import annotations

import random
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path

from PIL import Image, ImageTk

from .theme import ACCENT, INTRO_BG, MUTED, TEXT, lerp_color


GLYPHS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#%&@+<>/"


def _ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


class IntroSplash(tk.Toplevel):
    """A short, borderless intro that hands control back to the main window."""

    WIDTH = 800
    HEIGHT = 560
    DURATION = 3.75

    def __init__(self, master: tk.Misc, on_finish=None) -> None:
        super().__init__(master, bg=INTRO_BG)
        self._on_finish = on_finish
        self._done = False
        self._rng = random.Random(0xB1AC)

        self.overrideredirect(True)
        self.resizable(False, False)
        try:
            self.attributes("-topmost", True)
        except tk.TclError:
            pass

        x = max(0, (self.winfo_screenwidth() - self.WIDTH) // 2)
        y = max(0, (self.winfo_screenheight() - self.HEIGHT) // 2)
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

        self._canvas = tk.Canvas(
            self, width=self.WIDTH, height=self.HEIGHT,
            bg=INTRO_BG, bd=0, highlightthickness=0,
        )
        self._canvas.pack(fill="both", expand=True)

        families = set(tkfont.families(self))
        mono = "Cascadia Code" if "Cascadia Code" in families else (
            "Consolas" if "Consolas" in families else "Courier New"
        )
        self._title_font = tkfont.Font(family=mono, size=44, weight="bold")
        self._credit_font = tkfont.Font(family=mono, size=20, weight="bold")

        self._logo_frames = self._load_logo_frames()
        if self._logo_frames:
            self._logo_item = self._canvas.create_image(
                self.WIDTH // 2, 216, image=self._logo_frames[0],
            )
        else:
            self._logo_item = self._canvas.create_text(
                self.WIDTH // 2, 216, text="B", fill=INTRO_BG,
                font=self._title_font,
            )

        self._title = self._make_line("UI Mirror", self._title_font, 416)
        self._credit = self._make_line("Created By Blackwood", self._credit_font, 478)

        self.bind("<Button-1>", lambda _event: self._finish())
        self.bind("<Escape>", lambda _event: self._finish())
        self.focus_force()
        self._started = time.monotonic()
        self._animation_job = self.after(16, self._animate)

    def _load_logo_frames(self) -> list[ImageTk.PhotoImage]:
        path = Path(__file__).resolve().parent / "assets" / "blackwood_mark_generated.png"
        try:
            with Image.open(path) as source:
                mark = source.convert("RGBA").resize(
                    (300, 300), Image.Resampling.LANCZOS,
                )
        except (OSError, ValueError):
            return []

        alpha = mark.getchannel("A")
        frames: list[ImageTk.PhotoImage] = []
        for step in range(25):
            frame = mark.copy()
            strength = _ease(step / 24)
            frame.putalpha(alpha.point(lambda value: round(value * strength)))
            frames.append(ImageTk.PhotoImage(frame, master=self))
        return frames

    def _make_line(
        self, phrase: str, font: tkfont.Font, y: int,
    ) -> tuple[str, list[int | None]]:
        char_width = font.measure("M")
        start_x = (self.WIDTH - char_width * len(phrase)) / 2 + char_width / 2
        items: list[int | None] = []
        for index, character in enumerate(phrase):
            if character == " ":
                items.append(None)
                continue
            items.append(self._canvas.create_text(
                start_x + index * char_width, y, text="",
                fill=INTRO_BG, font=font,
            ))
        return phrase, items

    def _animate_line(
        self, line: tuple[str, list[int | None]], elapsed: float,
        start: float, duration: float, final_color: str,
    ) -> None:
        phrase, items = line
        if elapsed < start:
            return
        progress = _ease((elapsed - start) / duration)
        locked = round(progress * len(phrase))
        fade = _ease((elapsed - start) / 0.28)
        for index, (character, item) in enumerate(zip(phrase, items)):
            if item is None:
                continue
            if index < locked:
                glyph = character
                color = lerp_color(INTRO_BG, final_color, fade)
            else:
                glyph = self._rng.choice(GLYPHS)
                color = lerp_color(INTRO_BG, ACCENT if index % 5 == 0 else MUTED, fade * 0.72)
            self._canvas.itemconfigure(item, text=glyph, fill=color)

    def _animate(self) -> None:
        if self._done:
            return
        try:
            elapsed = time.monotonic() - self._started
            if self._logo_frames:
                logo_progress = _ease((elapsed - 0.08) / 1.20)
                frame = round(logo_progress * (len(self._logo_frames) - 1))
                self._canvas.itemconfigure(self._logo_item, image=self._logo_frames[frame])
            else:
                self._canvas.itemconfigure(
                    self._logo_item,
                    fill=lerp_color(INTRO_BG, TEXT, _ease(elapsed / 1.20)),
                )

            self._animate_line(self._title, elapsed, 0.68, 1.28, TEXT)
            self._animate_line(self._credit, elapsed, 1.36, 1.32, MUTED)

            if elapsed >= self.DURATION:
                self._finish()
            else:
                self._animation_job = self.after(33, self._animate)
        except tk.TclError:
            return

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        if hasattr(self, "_animation_job"):
            self.after_cancel(self._animation_job)
        try:
            self.destroy()
        except tk.TclError:
            pass
        if self._on_finish is not None:
            self._on_finish()
