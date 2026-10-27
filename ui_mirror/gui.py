"""Blackwood masaüstü arayüzü: tek akışta analiz ve kopyalama."""

from __future__ import annotations

import argparse
import math
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk

from .check import FeasibilityReport, analyze as analyze_site
from .cli import default_output_dir, force_utf8_console, human_size, normalize_url
from .intro import IntroSplash
from .mirror import MirrorOptions, SiteMirror
from .preview import PreviewServer
from .settings import AppSettings, load_settings, save_settings
from .signature import panel_skull_source, play_laugh, render_skull
from .theme import (
    ACCENT,
    ACCENT_2,
    ACCENT_INK,
    BAD,
    BG,
    BORDER,
    DIM,
    LOG_BG,
    MUTED,
    OK,
    SURFACE,
    SURFACE_2,
    SURFACE_HI,
    TEXT,
    TRACK,
    WARN,
    lerp_color,
)
from .windowchrome import WindowChrome

PHASES = ("analiz", "kopyalama", "bitti")
PHASE_LABELS = {"analiz": "Analiz", "kopyalama": "Kopyalama", "bitti": "Bitti"}


def rounded_rect_points(x1, y1, x2, y2, radius, steps=6) -> list[float]:
    radius = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points: list[float] = []
    corners = (
        (x1 + radius, y1 + radius, 180),
        (x2 - radius, y1 + radius, 270),
        (x2 - radius, y2 - radius, 0),
        (x1 + radius, y2 - radius, 90),
    )
    for cx, cy, start in corners:
        for step in range(steps + 1):
            angle = math.radians(start + step * 90 / steps)
            points.extend((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points


def draw_icon(canvas: tk.Canvas, name: str, color: str) -> None:
    canvas.delete("all")
    background = canvas["bg"]
    if name == "pages":
        canvas.create_polygon(
            rounded_rect_points(6, 3, 20, 17, 3), fill="", outline=color, width=1.4
        )
        canvas.create_polygon(
            rounded_rect_points(3, 8, 17, 22, 3), fill=background, outline=color, width=1.4
        )
    elif name == "assets":
        canvas.create_polygon(rounded_rect_points(4, 4, 16, 16, 3), fill="", outline=color, width=1.4)
        canvas.create_polygon(
            rounded_rect_points(10, 10, 22, 22, 3), fill=background, outline=color, width=1.4
        )
    elif name == "size":
        for index, height in enumerate((8, 13, 18)):
            canvas.create_polygon(
                rounded_rect_points(4 + index * 7, 24 - height, 9 + index * 7, 22, 1.6),
                fill=color,
                outline=color,
            )
    elif name == "speed":
        canvas.create_arc(3, 5, 23, 25, start=30, extent=120, style=tk.ARC, outline=color, width=1.6)
        canvas.create_line(13, 20, 18, 11, fill=color, width=1.8, capstyle=tk.ROUND)
        canvas.create_oval(11.4, 18.4, 14.6, 21.6, fill=color, outline=color)
    elif name == "check":
        canvas.create_line(4, 13, 10, 19, 22, 6, fill=color, width=2.4, capstyle=tk.ROUND,
                           joinstyle=tk.ROUND)


class RoundedButton(tk.Canvas):
    """Yuvarlatılmış köşeli, hover animasyonlu buton."""

    def __init__(
        self,
        parent,
        text: str,
        command=None,
        *,
        width: int = 132,
        height: int = 38,
        kind: str = "ghost",
        radius: int = 6,
        bg: str = BG,
        font=None,
        selected: bool = False,
        enabled: bool = True,
    ) -> None:
        super().__init__(
            parent, width=width, height=height, bg=bg, highlightthickness=0, bd=0,
            takefocus=1,
        )
        self._text = text
        self._command = command
        self._kind = kind
        self._radius = radius
        self._btn_w = width
        self._btn_h = height
        self._font = font
        self._selected = selected
        self._enabled = enabled
        self._hover = False
        self._pressed = False
        self._focused = False
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", self._on_focus)
        self.bind("<FocusOut>", self._on_blur)
        self.bind("<Return>", self._on_keyboard)
        self.bind("<space>", self._on_keyboard)
        self._draw()

    # -- public ---------------------------------------------------------
    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._draw()

    def set_text(self, text: str) -> None:
        self._text = text
        self._draw()

    # -- drawing --------------------------------------------------------
    def _colors(self) -> tuple[str, str, str]:
        if not self._enabled:
            return SURFACE, DIM, BORDER
        if self._kind == "primary":
            base = ACCENT if not self._hover else lerp_color(ACCENT, "#ffffff", 0.16)
            return base, ACCENT_INK, base
        if self._kind == "danger":
            base = SURFACE_HI if not self._hover else SURFACE_2
            return base, BAD, BORDER
        if self._kind == "seg":
            if self._selected:
                return ACCENT, ACCENT_INK, ACCENT
            return (SURFACE_HI if not self._hover else SURFACE_2), TEXT, BORDER
        fill = SURFACE_HI if not self._hover else SURFACE_2
        return fill, TEXT, BORDER

    def _draw(self) -> None:
        self.delete("all")
        fill, fg, border = self._colors()
        if self._pressed:
            fill = lerp_color(fill, "#000000", 0.18)
        points = rounded_rect_points(1, 1, self._btn_w - 1, self._btn_h - 1, self._radius)
        self.create_polygon(points, fill=fill, outline=ACCENT if self._focused else border,
                            width=1, smooth=False)
        self.create_text(
            self._btn_w / 2,
            self._btn_h / 2,
            text=self._text,
            fill=fg,
            font=self._font,
        )
        self.configure(cursor="hand2" if self._enabled else "arrow")

    # -- events ---------------------------------------------------------
    def _on_enter(self, _event=None) -> None:
        self._hover = True
        self._draw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._pressed = False
        self._draw()

    def _on_press(self, _event=None) -> None:
        if not self._enabled:
            return
        self.focus_set()
        self._pressed = True
        self._draw()

    def _on_release(self, event=None) -> None:
        was_pressed = self._pressed
        self._pressed = False
        self._draw()
        inside = event is None or (0 <= event.x < self._btn_w and 0 <= event.y < self._btn_h)
        if was_pressed and inside and self._enabled and self._command is not None:
            self._command()

    def _on_focus(self, _event=None) -> None:
        self._focused = True
        self._draw()

    def _on_blur(self, _event=None) -> None:
        self._focused = False
        self._pressed = False
        self._draw()

    def _on_keyboard(self, _event=None) -> None:
        if self._enabled and self._command is not None:
            self._command()


class RingCanvas(tk.Canvas):
    """Antialiased, time-based progress gauge."""

    def __init__(self, parent, size: int = 258, family: str = "Segoe UI") -> None:
        super().__init__(parent, width=size, height=size, bg=SURFACE, highlightthickness=0, bd=0)
        self.size = size
        self.scale = 3
        self.display = 0.0
        self.target = 0.0
        self.mode = "idle"
        self.sweep = 0.0
        self._last_frame = time.monotonic()
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "seguisb.ttf"
        fallback = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arialbd.ttf"
        font_name = str(font_path if font_path.is_file() else fallback) if fallback.is_file() or font_path.is_file() else "DejaVuSans.ttf"
        self._number_font = ImageFont.truetype(font_name, 42 * self.scale)
        self._label_font = ImageFont.truetype(font_name, 9 * self.scale)
        self._caption_font = ImageFont.truetype(font_name, 11 * self.scale)
        self._image_item = self.create_image(size // 2, size // 2)
        self._photo = None
        self.render()

    def set_state(self, *, target: float | None = None, mode: str | None = None) -> None:
        if target is not None:
            self.target = max(0.0, min(100.0, target))
        if mode is not None:
            self.mode = mode

    def animate(self) -> None:
        now = time.monotonic()
        dt = min(0.08, max(0.0, now - self._last_frame))
        self._last_frame = now
        self.sweep = (self.sweep + 104 * dt) % 360
        delta = self.target - self.display
        self.display = self.target if abs(delta) < 0.08 else self.display + delta * (1 - math.exp(-8 * dt))
        self.render()

    def render(self) -> None:
        scale = self.scale
        extent = self.size * scale
        center = extent / 2
        radius = (self.size / 2 - 21) * scale
        width = 12 * scale
        bounds = (center - radius, center - radius, center + radius, center + radius)
        image = Image.new("RGB", (extent, extent), SURFACE)
        draw = ImageDraw.Draw(image)
        draw.arc(bounds, -90, 270, fill=TRACK, width=width)
        if self.mode in ("analyzing", "preparing"):
            start = self.sweep - 90
            for index in range(56):
                angle = start + index * 1.3
                fade = math.sin(math.pi * (index + 0.5) / 56) ** 1.4
                color = lerp_color(TRACK, ACCENT, 0.7 * fade)
                draw.arc(bounds, angle, angle + 1.9, fill=color, width=width)
        elif self.mode != "idle" and self.display >= 0.5:
            arc_length = 360 * self.display / 100
            segments = max(1, int(arc_length / 2))
            for index in range(segments):
                begin = -90 + arc_length * index / segments
                end = -90 + arc_length * (index + 1) / segments
                color = lerp_color(ACCENT, OK if self.mode == "done" else ACCENT_2,
                                   index / max(1, segments - 1))
                draw.arc(bounds, begin, end + 0.8, fill=color, width=width)
        color = OK if self.mode == "done" else BAD if self.mode == "error" else TEXT
        draw.text((center, center - 48 * scale), "İLERLEME", font=self._label_font,
                  fill=DIM, anchor="mm")
        number = "···" if self.mode in ("analyzing", "preparing") else f"{int(round(self.display))}%"
        draw.text((center, center - 5 * scale), number, font=self._number_font,
                  fill=color, anchor="mm")
        caption = {
            "idle": "Bekliyor",
            "analyzing": "Analiz",
            "preparing": "Sayfa alınıyor",
            "running": "Kopyalanıyor",
            "done": "Tamamlandı",
            "cancelled": "Durduruldu",
            "error": "Hata",
        }.get(self.mode, "Bekliyor")
        draw.text((center, center + 42 * scale), caption, font=self._caption_font,
                  fill=MUTED, anchor="mm")
        image = image.resize((self.size, self.size), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(image, master=self)
        self.itemconfigure(self._image_item, image=self._photo)

class SignatureCanvas(tk.Canvas):
    """Animated skull composed only of image-toned ASCII glyphs."""

    def __init__(self, parent) -> None:
        super().__init__(parent, bg=BG, highlightthickness=0, bd=0)
        self.art = render_skull(0)[0]
        self.mood = "idle"
        self._photo = None
        self._image_item = self.create_image(0, 0, anchor="center")
        self._fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self._texture_cache: dict[tuple[int, int, str], Image.Image] = {}
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "consolab.ttf"
        self._font_path = str(font_path) if font_path.is_file() else "DejaVuSansMono-Bold.ttf"
        self.bind("<Configure>", lambda _event: self.render())

    def set_frame(self, art: str, mood: str) -> None:
        if art == self.art and mood == self.mood:
            return
        self.art = art
        self.mood = mood
        self.render()

    def render(self) -> None:
        width, height = self.winfo_width(), self.winfo_height()
        if width < 50 or height < 50:
            return
        lines = self.art.splitlines()
        longest = max(map(len, lines), default=34)
        render_scale = 3
        stretch = 1.2
        for font_size in range(25, 8, -1):
            font = self._font(font_size * render_scale)
            line_height = int(font_size * 1.18 * render_scale)
            art_width = int(font.getlength("W" * longest) * stretch)
            if art_width <= (width - 28) * render_scale and line_height * len(lines) <= (height - 24) * render_scale:
                break
        else:
            font_size = 9
            font = self._font(font_size * render_scale)
            line_height = int(font_size * 1.18 * render_scale)
        raw_width = int(font.getlength("W" * longest) + 12 * render_scale)
        raw_height = line_height * len(lines) + 12 * render_scale
        glyph_mask = Image.new("L", (raw_width, raw_height))
        painter = ImageDraw.Draw(glyph_mask)
        for index, line in enumerate(lines):
            painter.text((6 * render_scale, 6 * render_scale + index * line_height),
                         line, font=font, fill=255, anchor="lt")
        color_mood = self.mood if self.mood in {"laugh", "error"} else "normal"
        key = raw_width, raw_height, color_mood
        if key not in self._texture_cache:
            source = panel_skull_source().resize((raw_width, raw_height), Image.Resampling.LANCZOS)
            dark, light = {
                "normal": ("#763842", "#FFE0C7"),
                "laugh": ("#81502D", "#FFE2A9"),
                "error": ("#7D2938", "#FFA9AB"),
            }[color_mood]
            self._texture_cache[key] = ImageOps.colorize(source, dark, light)
            if len(self._texture_cache) > 12:
                self._texture_cache = {key: self._texture_cache[key]}
        artwork = Image.composite(self._texture_cache[key], Image.new("RGB", (raw_width, raw_height), BG), glyph_mask)
        artwork = artwork.resize((int(artwork.width * stretch / render_scale),
                                  int(artwork.height / render_scale)), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(artwork, master=self)
        self.coords(self._image_item, width / 2, height / 2)
        self.itemconfigure(self._image_item, image=self._photo)

    def _font(self, size: int) -> ImageFont.FreeTypeFont:
        if size not in self._fonts:
            self._fonts[size] = ImageFont.truetype(self._font_path, size)
        return self._fonts[size]


class MirrorApp(tk.Tk):
    def __init__(
        self,
        initial_url: str | None = None,
        initial_output: str | None = None,
        splash: bool = True,
        auto_analyze: bool = False,
    ) -> None:
        super().__init__()
        self.withdraw()
        self.configure(bg=BG)
        self.title("UI MIRROR  /  BLACKWOOD")
        self.geometry("1360x940")
        self.minsize(1160, 820)
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"1360x940+{max(0, (screen_w - 1360) // 2)}+{max(10, (screen_h - 990) // 2)}")
        self._setup_fonts()
        self._setup_icon()
        self._chrome = WindowChrome(self, self._chrome_icon, self._close)

        self.url_var = tk.StringVar(value=initial_url or "")
        self.output_var = tk.StringVar(value=initial_output or "")
        self.scope_var = tk.StringVar(value="page")
        self.settings = load_settings()

        self.queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.engine: SiteMirror | None = None
        self.cancel_event = threading.Event()
        self.check_report: FeasibilityReport | None = None
        self.target_pct = 0.0
        self.last_dir: Path | None = None
        self.last_entry: str | None = None
        self._active_output_dir: Path | None = None
        self._preview: PreviewServer | None = None
        self._phase = "analiz"
        self._auto_copy = False
        self._analysis_serial = 0
        self._phase_labels: dict[str, tk.Label] = {}
        self._stat_values: dict[str, tk.Label] = {}
        self._activity = ""
        self._last_live_poll = 0.0
        self._skull_tick = 0
        self._skull_mood = "idle"
        self._skull_until = 0.0
        self._build()
        self._tick_job = self.after(33, self._tick)
        self._skull_job = self.after(90, self._animate_skull)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._chrome.activate()

        if splash:
            self.after(60, lambda: IntroSplash(self, on_finish=self._after_intro))
        else:
            self.deiconify()
            if auto_analyze and (initial_url or "").strip():
                self.after(300, self.start_run)

    # -- açılış animasyonu ----------------------------------------------
    def _after_intro(self) -> None:
        self.deiconify()
        self._chrome.activate()
        self.lift()
        self.focus_force()
        self._log("arayüz hazır · blackwood", "ok")
        if (self.url_var.get() or "").strip():
            self.after(250, self.start_run)

    # -- kurulum ---------------------------------------------------------
    def _setup_fonts(self) -> None:
        families = set(tkfont.families(self))
        family = "Segoe UI" if "Segoe UI" in families else "TkDefaultFont"
        mono = next((name for name in ("Cascadia Code", "Consolas", "Courier New")
                     if name in families), "TkFixedFont")
        self.family = family
        self.mono = mono
        self.f_title = (family, 23, "bold")
        self.f_sub = (family, 10)
        self.f_label = (family, 10, "bold")
        self.f_body = (family, 11)
        self.f_small = (family, 9)
        self.f_button = (family, 10, "bold")
        self.f_seg = (mono, 9, "bold")
        self.f_stat = (family, 19, "bold")
        self.f_verdict = (family, 19, "bold")
        self.f_pill = (family, 9, "bold")
        self.f_mono = (mono, 9)

    def _setup_icon(self) -> None:
        try:
            path = Path(__file__).resolve().parent / "assets" / "blackwood_mark_generated.png"
            with Image.open(path) as source:
                mark = source.convert("RGBA")
                self._brand_icon = ImageTk.PhotoImage(
                    mark.resize((60, 60), Image.Resampling.LANCZOS), master=self
                )
                self._chrome_icon = ImageTk.PhotoImage(
                    mark.resize((28, 28), Image.Resampling.LANCZOS), master=self
                )
            self.iconphoto(True, self._brand_icon)
        except (tk.TclError, OSError, ValueError):
            self._brand_icon = None
            self._chrome_icon = None

    # -- yerleşim --------------------------------------------------------
    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        content = tk.Frame(self, bg=BG)
        content.grid(row=2, column=0, sticky="nsew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(2, weight=1)

        header = tk.Frame(content, bg=BG)
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 13))
        header.grid_columnconfigure(1, weight=1)

        self.logo = tk.Canvas(header, width=64, height=64, bg=BG, highlightthickness=0)
        self.logo.grid(row=0, column=0, rowspan=3, sticky="w")
        self._draw_logo()

        tk.Label(
            header, text="Blackwood", bg=BG, fg=ACCENT,
            font=self.f_label,
        ).grid(row=0, column=1, sticky="w", padx=(14, 0))
        tk.Label(
            header, text="UI Mirror", bg=BG, fg=TEXT, font=self.f_title
        ).grid(row=1, column=1, sticky="w", padx=(14, 0))
        tk.Label(
            header,
            text="Tek sayfa veya tam siteyi yerel olarak yakalayın.",
            bg=BG,
            fg=MUTED,
            font=self.f_sub,
        ).grid(row=2, column=1, sticky="w", padx=(14, 0))

        self.settings_button = RoundedButton(
            header, "Ayarlar", command=self.open_settings, width=92, height=34,
            kind="ghost", bg=BG, font=self.f_button,
        )
        self.settings_button.grid(row=0, column=2, rowspan=3, sticky="e", padx=(0, 12))

        self.status = tk.Label(
            header,
            text="●  Hazır",
            bg=SURFACE,
            fg=OK,
            font=self.f_pill,
            padx=18,
            pady=10,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        self.status.grid(row=0, column=3, rowspan=3, sticky="e")

        self.accent_bar = tk.Canvas(content, height=1, bg=BG, highlightthickness=0)
        self.accent_bar.grid(row=1, column=0, sticky="ew", padx=24)
        self.accent_bar.bind("<Configure>", self._draw_accent_bar)

        body = tk.Frame(content, bg=BG)
        body.grid(row=2, column=0, sticky="nsew", padx=24, pady=(18, 16))
        body.grid_columnconfigure(0, weight=5, minsize=430)
        body.grid_columnconfigure(1, weight=7, minsize=620)
        body.grid_rowconfigure(1, weight=1)

        self._build_input_bar(body)
        self._build_check_card(body)
        self._build_center(body)
        self._build_footer(body)

    def _draw_accent_bar(self, event=None) -> None:
        self.accent_bar.delete("all")
        width = max(1, self.accent_bar.winfo_width())
        self.accent_bar.create_line(0, 0, width, 0, fill=BORDER)
        self.accent_bar.create_line(0, 0, min(width, 152), 0, fill=ACCENT)

    def _draw_logo(self) -> None:
        self.logo.delete("all")
        if self._brand_icon is not None:
            self.logo.create_image(32, 32, image=self._brand_icon)
            return
        self.logo.create_rectangle(5, 5, 59, 59, outline=ACCENT, width=2)
        self.logo.create_text(32, 32, text="BW", fill=TEXT, font=(self.mono, 17, "bold"))

    def _build_input_bar(self, parent) -> None:
        bar = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        inner = tk.Frame(bar, bg=SURFACE)
        inner.pack(fill="x", padx=18, pady=(13, 14))

        meta = tk.Frame(inner, bg=SURFACE)
        meta.pack(fill="x", pady=(0, 10))
        tk.Label(meta, text="KOPYALAMA HEDEFİ", bg=SURFACE, fg=ACCENT,
                 font=self.f_label).pack(side="left")
        tk.Label(meta, text="Analiz ve kopyalama tek akışta", bg=SURFACE, fg=DIM,
                 font=self.f_small).pack(side="right")

        fields = tk.Frame(inner, bg=SURFACE)
        fields.pack(fill="x")

        tk.Label(fields, text="Adres", bg=SURFACE, fg=MUTED, font=self.f_label).pack(
            side="left", padx=(0, 10)
        )
        entry = tk.Entry(
            fields,
            textvariable=self.url_var,
            bg=LOG_BG,
            fg=TEXT,
            insertbackground=ACCENT,
            relief="flat",
            font=self.f_body,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        entry.pack(side="left", fill="x", expand=True, ipady=9, padx=(0, 15))
        entry.bind("<Return>", lambda _event: self.start_run())
        entry.bind("<KeyRelease>", lambda _event: self._react_skull("typing", 1.8))
        entry.bind("<FocusIn>", lambda _event: self._react_skull("typing", 1.8))

        self.copy_button = RoundedButton(
            fields, "Başlat", command=self.start_run, width=122, height=38,
            kind="primary", bg=SURFACE, font=self.f_button,
        )
        self.copy_button.pack(side="left", padx=(0, 8))
        self.copy_button.bind("<Enter>", lambda _event: self._react_skull("copy", 1.3), add="+")

        self.stop_button = RoundedButton(
            fields, "Durdur", command=self.stop_copy, width=86, height=38,
            kind="danger", bg=SURFACE, font=self.f_button, enabled=False,
        )
        self.stop_button.pack(side="left")

        scope = tk.Frame(inner, bg=SURFACE)
        scope.pack(fill="x", pady=(13, 0))
        tk.Label(scope, text="Kapsam", bg=SURFACE, fg=MUTED,
                 font=self.f_label).pack(side="left", padx=(0, 12))
        self.scope_buttons: dict[str, RoundedButton] = {}
        for mode, title in (("page", "Tek sayfa"), ("site", "Tam site")):
            button = RoundedButton(
                scope, title, command=lambda value=mode: self._set_scope(value),
                width=136, height=38, kind="seg", bg=SURFACE,
                font=self.f_button, selected=mode == "page",
            )
            button.pack(side="left", padx=(0, 7))
            self.scope_buttons[mode] = button
        self.scope_hint = tk.Label(
            scope, text="Yalnızca bu adres; bağlı sayfalar alınmaz.",
            bg=SURFACE, fg=MUTED, font=self.f_small, anchor="w",
        )
        self.scope_hint.pack(side="left", fill="x", expand=True, padx=(13, 0))

    def _build_check_card(self, parent) -> None:
        panel = tk.Frame(parent, bg=BG)
        panel.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        panel.grid_rowconfigure(0, weight=1)
        panel.grid_columnconfigure(0, weight=1)
        self.signature = SignatureCanvas(panel)
        self.signature.grid(row=0, column=0, sticky="nsew")
        for widget in (panel, self.signature):
            widget.configure(cursor="hand2")
            widget.bind("<Button-1>", lambda _event: (self._react_skull("laugh", 2.5), play_laugh()))
            widget.bind("<Enter>", lambda _event: self._react_skull("laugh", 1.4))

    def _build_center(self, parent) -> None:
        center = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        center.grid(row=1, column=1, sticky="nsew")
        center.grid_columnconfigure(0, weight=1)
        center.grid_rowconfigure(3, weight=1)

        head = tk.Frame(center, bg=SURFACE)
        head.grid(row=0, column=0, sticky="ew", padx=19, pady=(17, 0))
        tk.Label(head, text="02  Kopyalama ilerlemesi", bg=SURFACE, fg=TEXT,
                 font=self.f_label).pack(side="left")
        self.progress_scope = tk.Label(head, text="TEK SAYFA", bg=SURFACE, fg=ACCENT,
                                       font=self.f_pill)
        self.progress_scope.pack(side="right")

        overview = tk.Frame(center, bg=SURFACE)
        overview.grid(row=1, column=0, sticky="ew", padx=14, pady=(3, 0))
        overview.grid_columnconfigure(1, weight=1)

        self.ring = RingCanvas(overview, size=214, family=self.mono)
        self.ring.grid(row=0, column=0, rowspan=2, padx=(6, 18), pady=(0, 2))

        stats = tk.Frame(overview, bg=SURFACE)
        stats.grid(row=0, column=1, sticky="nsew", padx=(0, 4), pady=(8, 0))
        for column in range(2):
            stats.grid_columnconfigure(column, weight=1)
        self._stat_values["pages"] = self._stat_card(stats, "pages", "Sayfa", 0, 0)
        self._stat_values["assets"] = self._stat_card(stats, "assets", "Varlık", 0, 1)
        self._stat_values["size"] = self._stat_card(stats, "size", "Boyut", 1, 0)
        self._stat_values["speed"] = self._stat_card(stats, "speed", "Hız", 1, 1)

        self.activity_label = tk.Label(
            overview, text="Hazır. Hedefi ve kapsamı seçerek başlayın.",
            bg=SURFACE, fg=MUTED, font=self.f_small,
            anchor="w", justify="left", wraplength=420,
        )
        self.activity_label.grid(row=1, column=1, sticky="sw", padx=(4, 0), pady=(4, 12))

        phases = tk.Frame(center, bg=SURFACE)
        phases.grid(row=2, column=0, sticky="w", padx=22, pady=(0, 3))
        self.phases = phases
        for phase in PHASES:
            label = tk.Label(
                phases,
                text=PHASE_LABELS[phase],
                bg=SURFACE_HI,
                fg=DIM,
                font=self.f_pill,
                padx=10,
                pady=7,
            )
            label.pack(side="left", padx=3)
            self._phase_labels[phase] = label

        self._build_log_card(center)
        center.bind("<Configure>", self._on_center_resize)

    def _on_center_resize(self, event) -> None:
        compact = event.height < 450
        if compact:
            self.phases.grid_remove()
            self.activity_label.grid_remove()
        else:
            self.phases.grid()
            self.activity_label.grid()

    def _stat_card(self, parent, icon: str, caption: str, row: int, column: int) -> tk.Label:
        card = tk.Frame(parent, bg=SURFACE_HI, highlightbackground=BORDER, highlightthickness=1)
        card.grid(row=row, column=column, sticky="ew", padx=4, pady=4)
        icon_canvas = tk.Canvas(card, width=26, height=26, bg=SURFACE_HI, highlightthickness=0)
        draw_icon(icon_canvas, icon, ACCENT)
        icon_canvas.grid(row=0, column=0, rowspan=2, padx=(10, 7), pady=8)
        value = tk.Label(card, text="0", bg=SURFACE_HI, fg=TEXT, font=self.f_stat, anchor="w")
        value.grid(row=0, column=1, sticky="w", pady=(5, 0))
        tk.Label(card, text=caption, bg=SURFACE_HI, fg=MUTED, font=self.f_small).grid(
            row=1, column=1, sticky="w", pady=(0, 5)
        )
        return value

    def _build_log_card(self, parent) -> None:
        card = tk.Frame(parent, bg=LOG_BG, highlightbackground=BORDER, highlightthickness=1)
        card.grid(row=3, column=0, sticky="nsew", padx=16, pady=(8, 14))
        card.grid_rowconfigure(1, weight=1)
        card.grid_columnconfigure(0, weight=1)

        head = tk.Frame(card, bg=LOG_BG)
        head.grid(row=0, column=0, sticky="ew", padx=15, pady=(12, 8))
        tk.Label(head, text="03  İşlem günlüğü", bg=LOG_BG, fg=TEXT,
                 font=self.f_label).pack(side="left")
        self.log_state_label = tk.Label(head, text="●  Hazır", bg=LOG_BG, fg=DIM,
                                        font=self.f_small)
        self.log_state_label.pack(side="right")

        self.log_text = tk.Text(
            card,
            bg=LOG_BG,
            fg=MUTED,
            font=self.f_mono,
            relief="flat",
            bd=0,
            highlightthickness=0,
            wrap="word",
            width=1,
            height=8,
            cursor="arrow",
        )
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 10))
        self.log_text.tag_configure("info", foreground=MUTED)
        self.log_text.tag_configure("page", foreground=TEXT)
        self.log_text.tag_configure("asset", foreground=ACCENT)
        self.log_text.tag_configure("error", foreground=BAD)
        self.log_text.tag_configure("ok", foreground=OK)
        self.log_text.tag_configure("warn", foreground=WARN)
        self.log_text.configure(state="disabled")
        self.log_text.bind(
            "<MouseWheel>", lambda event: self.log_text.yview_scroll(int(-event.delta / 120), "units")
        )
        self._log("BLACKWOOD / OTURUM HAZIR", "ok")
        self._log("Hedefi ve kapsamı seçip Başlat'a basın.", "info")

    def _build_footer(self, parent) -> None:
        footer = tk.Frame(parent, bg=BG)
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        footer.grid_columnconfigure(0, weight=1)

        self.output_label = tk.Label(
            footer,
            text="Çıktı klasörü: Henüz oluşturulmadı",
            bg=BG,
            fg=MUTED,
            font=self.f_small,
            anchor="w",
            justify="left",
            wraplength=660,
        )
        self.output_label.grid(row=0, column=0, sticky="w")

        buttons = tk.Frame(footer, bg=BG)
        buttons.grid(row=0, column=1, sticky="e")
        self.choose_output_button = RoundedButton(
            buttons, "Kayıt yeri", command=self.choose_output, width=108, height=34,
            kind="ghost", bg=BG, font=self.f_button,
        )
        self.choose_output_button.pack(side="left", padx=(0, 8))
        self.open_folder_button = RoundedButton(
            buttons, "Klasörü Aç", command=self.open_folder, width=116, height=34,
            kind="ghost", bg=BG, font=self.f_button, enabled=False,
        )
        self.open_folder_button.pack(side="left", padx=(0, 8))
        self.open_browser_button = RoundedButton(
            buttons, "Tarayıcıda Aç", command=self.open_in_browser, width=128, height=34,
            kind="ghost", bg=BG, font=self.f_button, enabled=False,
        )
        self.open_browser_button.pack(side="left", padx=(0, 8))
        self.open_report_button = RoundedButton(
            buttons, "Rapor", command=self.open_report, width=78, height=34,
            kind="ghost", bg=BG, font=self.f_button, enabled=False,
        )
        self.open_report_button.pack(side="left")

        self.help_label = tk.Label(
            footer,
            text="Yalnızca kopyalama izniniz olan siteleri kullanın.",
            bg=BG,
            fg=DIM,
            font=self.f_small,
            anchor="w",
        )
        self.help_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

    # -- küçük yardımcılar ----------------------------------------------
    def _set_scope(self, mode: str) -> None:
        if mode not in ("page", "site") or self._busy():
            return
        self.scope_var.set(mode)
        for name, button in self.scope_buttons.items():
            button.set_selected(name == mode)
        if mode == "site":
            self.scope_hint.configure(
                text=f"Aynı site içindeki bağlantılar · en fazla {self.settings.max_pages} sayfa",
                fg=TEXT,
            )
            self.progress_scope.configure(text="TAM SİTE", fg=ACCENT)
        else:
            self.scope_hint.configure(
                text="Yalnızca bu adres; bağlı sayfalar alınmaz.", fg=MUTED,
            )
            self.progress_scope.configure(text="TEK SAYFA", fg=ACCENT)

    def _set_status(self, text: str, color: str) -> None:
        self.status.configure(text=f"●  {text}", fg=color)

    def _set_phase(self, phase: str) -> None:
        self._phase = phase
        for name, label in self._phase_labels.items():
            if name == phase:
                label.configure(bg=ACCENT, fg=ACCENT_INK)
            else:
                label.configure(bg=SURFACE_HI, fg=DIM)

    def _busy(self) -> bool:
        return self.worker is not None

    def _set_busy(self, busy: bool) -> None:
        self.copy_button.set_enabled(not busy)
        self.choose_output_button.set_enabled(not busy)
        self.settings_button.set_enabled(not busy)
        self.stop_button.set_enabled(busy)
        for button in self.scope_buttons.values():
            button.set_enabled(not busy)
        self.log_state_label.configure(
            text="●  Çalışıyor" if busy else "●  Hazır",
            fg=OK if busy else DIM,
        )

    def _react_skull(self, mood: str, seconds: float | None = None) -> None:
        self._skull_mood = mood
        self._skull_tick = 0
        self._skull_until = time.monotonic() + seconds if seconds is not None else 0.0

    def _animate_skull(self) -> None:
        try:
            if self._skull_until and time.monotonic() >= self._skull_until:
                self._skull_mood = "idle"
                self._skull_until = 0.0
            self._skull_tick += 1
            art, _reaction = render_skull(self._skull_tick, self._skull_mood)
            self.signature.set_frame(art, self._skull_mood)
            self._skull_job = self.after(90, self._animate_skull)
        except tk.TclError:
            return

    # -- olay akışı ------------------------------------------------------
    def _tick(self) -> None:
        try:
            self.ring.animate()
            self._drain_queue()
            now = time.monotonic()
            if (self._phase == "kopyalama" and self.worker is not None
                    and self.engine is not None and now - self._last_live_poll >= 0.5):
                self._last_live_poll = now
                self._apply_progress(self.engine.snapshot())
        finally:
            self._tick_job = self.after(33, self._tick)

    def _drain_queue(self) -> None:
        processed = 0
        while processed < 120:
            try:
                event = self.queue.get_nowait()
            except queue.Empty:
                break
            processed += 1
            try:
                self._on_event(event)
            except Exception as exc:  # UI hataları uygulamayı kilitlemesin
                self._log(f"arayüz hatası: {exc}", "error")

    def _on_event(self, event: dict) -> None:
        kind = event.get("kind")
        if "analysis_serial" in event and event["analysis_serial"] != self._analysis_serial:
            return
        if kind == "start":
            self._set_status("Kopyalanıyor", ACCENT)
            self._set_phase("kopyalama")
            self.ring.set_state(mode="preparing", target=0.0)
            self._react_skull("copy")
            self._set_activity("Analiz tamamlandı · sayfa alınıyor, ardından varlıklar indirilecek")
            self._log(f"başladı · {event.get('url', '')}", "info")
            self._log(f"hedef · {event.get('output', '')}", "info")
        elif kind == "render":
            self._set_activity("Tarayıcı içeriği yüklendi · önizleme hazırlanıyor")
            self._log("dinamik içerik tarayıcıdan alındı", "info")
        elif kind == "preview":
            self.last_dir = Path(event["output"])
            self.last_entry = event["local"]
            self.open_folder_button.set_enabled(True)
            self.open_browser_button.set_enabled(True)
            self._set_activity("İlk önizleme hazır · varlıklar indiriliyor")
            self._log("önizleme hazır · Tarayıcıda Aç kullanılabilir", "ok")
        elif kind == "page":
            self._log(f"sayfa  {event.get('local', '')}", "page")
            self._set_activity(f"Sayfa yazıldı: {event.get('local', '')}")
        elif kind == "asset":
            size = human_size(int(event.get("bytes", 0)))
            self._log(f"varlık {event.get('local', '')}  ({size})", "asset")
            self._set_activity(f"Varlık indirildi: {event.get('local', '')}")
        elif kind == "error":
            self._log(
                f"hata   {event.get('url', '')}  ·  {event.get('error', '')}", "error"
            )
        elif kind == "progress":
            self._apply_progress(event)
        elif kind == "finish":
            self._on_finish(event)
        elif kind == "cancelled":
            self._on_cancelled(event)
        elif kind == "check":
            self._on_check(event["report"])
        elif kind == "fatal":
            self._on_fatal(event)

    def _apply_progress(self, event: dict) -> None:
        self.target_pct = max(self.target_pct, float(event.get("percent", 0.0)))
        has_page = int(event.get("pages_done", 0)) > 0
        self.ring.set_state(target=self.target_pct, mode="running" if has_page else "preparing")
        self._stat_values["pages"].configure(
            text=f"{event.get('pages_done', 0)}/{event.get('pages_known', 0)}"
        )
        self._stat_values["assets"].configure(
            text=f"{event.get('assets_done', 0)}/{event.get('assets_known', 0)}"
        )
        self._stat_values["size"].configure(text=human_size(int(event.get("bytes", 0))))
        rate = int(event.get("bytes_per_second", 0))
        downloaded = int(event.get("downloaded_bytes", 0))
        speed_label = f"{human_size(rate)}/s" if rate > 0 else (
            "Bekliyor" if downloaded == 0 else "Durakladı"
        )
        self._stat_values["speed"].configure(text=speed_label)
        queue_len = int(event.get("queue", 0))
        errors = int(event.get("errors", 0))
        self.output_label.configure(
            text=f"Çıktı: {self._output_dir()}  ·  kuyruk {queue_len}  ·  hata {errors}"
        )

    def _set_activity(self, text: str) -> None:
        self._activity = text
        self.activity_label.configure(text=text)

    def _log(self, text: str, tag: str = "info") -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n", tag)
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > 600:
            self.log_text.delete("1.0", f"{line_count - 600}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _output_dir(self) -> Path:
        if self._active_output_dir is not None:
            return self._active_output_dir
        if self.output_var.get().strip():
            return Path(self.output_var.get().strip()).expanduser()
        return Path.home() / "Documents" / "UI Mirror"

    def choose_output(self) -> None:
        selected = filedialog.askdirectory(
            parent=self, title="Kopyanın kaydedileceği klasörü seçin",
            initialdir=str(self._output_dir() if self._output_dir().exists() else Path.home()),
        )
        if selected:
            self.output_var.set(selected)
            self.output_label.configure(text=f"Kayıt yeri: {selected}")

    def open_settings(self) -> None:
        self.update_idletasks()
        dialog = tk.Toplevel(self, bg=BG)
        dialog.title("Kopyalama ayarları")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.geometry(f"580x570+{self.winfo_rootx() + 150}+{self.winfo_rooty() + 90}")
        dialog.configure(padx=24, pady=22)
        dialog.grid_columnconfigure(0, weight=1)

        heading = tk.Frame(dialog, bg=BG)
        heading.grid(row=0, column=0, sticky="ew")
        tk.Label(heading, text="TERCİHLER / KOPYALAMA", bg=BG, fg=ACCENT,
                 font=self.f_label).pack(anchor="w")
        tk.Label(heading, text="Kopyalama ayarları", bg=BG, fg=TEXT,
                 font=self.f_verdict).pack(anchor="w", pady=(7, 2))
        tk.Label(heading, text="Sınırları ve tarayıcı davranışını sonraki işlem için düzenleyin.",
                 bg=BG, fg=MUTED, font=self.f_small).pack(anchor="w")
        tk.Frame(dialog, bg=BORDER, height=1).grid(row=1, column=0, sticky="ew", pady=(19, 16))

        fields = (
            ("max_pages", "En fazla sayfa", "1–1000", int),
            ("max_assets", "En fazla varlık", "1–10000", int),
            ("jobs", "Eşzamanlı istek", "1–16", int),
            ("delay", "İstek aralığı (sn)", "0–5", float),
            ("timeout", "Zaman aşımı (sn)", "3–120", float),
            ("retries", "Yeniden deneme", "0–5", int),
            ("time_budget", "Toplam süre (sn)", "30–7200", int),
        )
        variables: dict[str, tk.StringVar] = {}
        settings_grid = tk.Frame(dialog, bg=BG)
        settings_grid.grid(row=2, column=0, sticky="ew")
        settings_grid.grid_columnconfigure((0, 1), weight=1, uniform="settings")
        for index, (name, label, hint, _cast) in enumerate(fields):
            tile = tk.Frame(settings_grid, bg=SURFACE, highlightbackground=BORDER,
                            highlightthickness=1, padx=13, pady=9)
            tile.grid(row=index // 2, column=index % 2, sticky="ew",
                      padx=(0, 7) if index % 2 == 0 else (7, 0), pady=(0, 10))
            tile.grid_columnconfigure(0, weight=1)
            tk.Label(tile, text=label, bg=SURFACE, fg=TEXT, font=self.f_small,
                     anchor="w").grid(row=0, column=0, sticky="w")
            tk.Label(tile, text=hint, bg=SURFACE, fg=DIM, font=self.f_small,
                     anchor="e").grid(row=0, column=1, sticky="e")
            variable = tk.StringVar(value=str(getattr(self.settings, name)))
            variables[name] = variable
            tk.Entry(tile, textvariable=variable, width=12, bg=LOG_BG, fg=TEXT,
                     insertbackground=ACCENT, font=self.f_mono, relief="flat",
                     highlightthickness=1, highlightbackground=BORDER,
                     highlightcolor=ACCENT).grid(row=1, column=0, columnspan=2,
                                                 sticky="ew", pady=(7, 0), ipady=4)

        render_var = tk.BooleanVar(value=self.settings.render_dynamic)
        render_tile = tk.Frame(settings_grid, bg=SURFACE, highlightbackground=BORDER,
                               highlightthickness=1, padx=11, pady=10)
        render_tile.grid(row=3, column=1, sticky="ew", padx=(7, 0), pady=(0, 10))
        tk.Checkbutton(render_tile, text="Tarayıcıyla yükle",
                       variable=render_var, bg=SURFACE, fg=TEXT, selectcolor=LOG_BG,
                       activebackground=SURFACE, activeforeground=TEXT,
                       font=self.f_small).pack(anchor="w")
        tk.Label(render_tile, text="Dinamik sayfalarda Chrome / Edge.",
                 bg=SURFACE, fg=MUTED, font=self.f_small).pack(anchor="w", padx=(24, 0))

        def apply() -> None:
            try:
                values = {
                    name: cast(variables[name].get().strip().replace(",", "."))
                    for name, _label, _hint, cast in fields
                }
                updated = AppSettings(**values, render_dynamic=render_var.get())
                updated.validate()
                save_settings(updated)
            except (ValueError, OSError) as exc:
                messagebox.showerror("Ayarlar", f"Ayarlar kaydedilemedi: {exc}", parent=dialog)
                return
            self.settings = updated
            self._set_scope(self.scope_var.get())
            self._log("kopyalama ayarları kaydedildi", "ok")
            dialog.destroy()

        buttons = tk.Frame(dialog, bg=BG)
        buttons.grid(row=3, column=0, sticky="e", pady=(18, 0))
        RoundedButton(buttons, "Vazgeç", command=dialog.destroy, width=95, height=36,
                      kind="ghost", bg=BG, font=self.f_button).pack(side="left", padx=(0, 8))
        RoundedButton(buttons, "Kaydet", command=apply, width=95, height=36,
                      kind="primary", bg=BG, font=self.f_button).pack(side="left")
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.wait_visibility()
        dialog.lift(self)
        dialog.grab_set()
        dialog.focus_force()

    def _new_output_dir(self, url: str) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"{default_output_dir(url).name}-{stamp}"
        chosen = self.output_var.get().strip()
        if chosen:
            target = Path(chosen).expanduser()
            if target.exists():
                if not target.is_dir():
                    raise ValueError("Çıktı yolu bir klasör olmalı")
                if not any(target.iterdir()):
                    return target
                target = target / name
        else:
            target = Path.home() / "Documents" / "UI Mirror" / name
        candidate = target
        index = 2
        while candidate.exists():
            candidate = target.with_name(f"{target.name}-{index}")
            index += 1
        return candidate

    # -- analiz ----------------------------------------------------------
    def start_run(self) -> None:
        if self._busy():
            return
        self._auto_copy = True
        self.cancel_event.clear()
        self._clear_log()
        self.start_analysis()

    def start_analysis(self) -> None:
        if self._busy():
            self._log("bir işlem sürüyor; önce bitmesini bekle", "warn")
            return
        try:
            url = normalize_url(self.url_var.get())
        except ValueError as exc:
            self._auto_copy = False
            self._log(f"geçersiz adres · {exc}", "error")
            self._set_status("Geçersiz adres", BAD)
            self.ring.set_state(mode="error", target=0.0)
            return
        self.url_var.set(url)
        self.check_report = None
        self._reset_for_run()
        self._set_status("Analiz ediliyor", WARN)
        self._set_phase("analiz")
        self._react_skull("scan")
        self.ring.set_state(mode="analyzing", target=0.0)
        self._set_activity("Hedef inceleniyor: " + url)
        self._log(f"analiz başladı · {url}", "info")
        self._analysis_serial += 1
        self.worker = threading.Thread(
            target=self._analysis_worker,
            args=(url, self.queue, self._analysis_serial),
            daemon=True,
        )
        self.worker.start()
        self._set_busy(True)

    @staticmethod
    def _analysis_worker(url: str, events: queue.Queue, serial: int) -> None:
        try:
            report = analyze_site(url)
            events.put({"kind": "check", "report": report, "analysis_serial": serial})
        except Exception as exc:  # pragma: no cover - savunma amaçlı
            events.put({"kind": "fatal", "error": f"{type(exc).__name__}: {exc}",
                        "analysis_serial": serial})

    def _on_check(self, report: FeasibilityReport) -> None:
        self.check_report = report
        self.worker = None
        color = {"mirrorable": OK, "conditional": WARN, "blocked": BAD}.get(report.verdict, MUTED)
        tag = "ok" if report.verdict == "mirrorable" else "warn"
        self._log(f"analiz sonucu · {report.verdict_text} · {report.score}/100", tag)
        self._log(report.headline, "info")
        for item in report.items:
            item_tag = {"pass": "ok", "warn": "warn", "fail": "error", "info": "info"}.get(item.status, "info")
            self._log(f"  {item.title}", item_tag)
            if item.detail:
                self._log(f"    {item.detail}", "info")
        if report.suggested_flags:
            self._log("öneri · " + " ".join(report.suggested_flags), "warn")
        self._set_status("Analiz tamamlandı", color)
        self._react_skull("laugh" if report.verdict == "mirrorable" else "error", 2.7)
        if self.cancel_event.is_set():
            self._auto_copy = False
            self._set_busy(False)
            self.ring.set_state(mode="cancelled", target=0.0)
            self._set_status("Durduruldu", WARN)
            self._log("analizden sonra durduruldu · kopyalama başlatılmadı", "warn")
            return
        self._set_busy(False)
        if self._auto_copy:
            self._set_activity("Analiz tamamlandı · kopyalama otomatik başlıyor")
            self._log("──── ANALİZ TAMAMLANDI · KOPYALAMA BAŞLIYOR ────", "ok")
            self.start_copy()
            self._auto_copy = False

    # -- kopyalama -------------------------------------------------------
    def start_copy(self) -> None:
        if self._busy():
            self._log("bir işlem sürüyor; önce bitmesini bekle", "warn")
            return
        try:
            url = normalize_url(self.url_var.get())
        except ValueError as exc:
            self._log(f"geçersiz adres: {exc}", "error")
            return
        self.url_var.set(url)
        try:
            output_dir = self._new_output_dir(url)
        except (OSError, ValueError) as exc:
            self._log(f"çıktı klasörü seçilemedi: {exc}", "error")
            self._set_status("Çıktı hatası", BAD)
            return
        self._active_output_dir = output_dir
        self.output_label.configure(text=f"Çıktı: {output_dir}")

        scope_mode = self.scope_var.get()
        options = MirrorOptions(
            start_url=url,
            output_dir=output_dir,
            max_depth=self.settings.max_pages if scope_mode == "site" else 0,
            max_pages=self.settings.max_pages,
            max_assets=self.settings.max_assets,
            jobs=self.settings.jobs,
            delay=self.settings.delay,
            timeout=self.settings.timeout,
            retries=self.settings.retries,
            time_budget=self.settings.time_budget,
            auto_render=self.settings.render_dynamic,
            verbose=False,
        )
        self._reset_for_run()
        if not self._auto_copy:
            self._clear_log()
        self.cancel_event.clear()
        self.engine = SiteMirror(
            options,
            on_event=self.queue.put,
            cancel_event=self.cancel_event,
        )
        self._set_status("Kopyalanıyor", ACCENT)
        self._set_phase("kopyalama")
        self._react_skull("copy")
        self._set_busy(True)
        self.ring.set_state(mode="preparing", target=0.0)
        self._stat_values["speed"].configure(text="Bekliyor")
        self._set_activity("Sayfa alınıyor · içerik ve varlıklar sırayla kaydedilecek")
        self._log(
            f"{'tam site' if scope_mode == 'site' else 'tek sayfa'} kopyalanıyor · {url}",
            "info",
        )
        self.worker = threading.Thread(
            target=self._copy_worker, args=(self.engine, self.queue), daemon=True
        )
        self.worker.start()

    @staticmethod
    def _copy_worker(engine: SiteMirror, events: queue.Queue) -> None:
        try:
            engine.run()
        except Exception as exc:  # pragma: no cover - savunma amaçlı
            events.put({"kind": "fatal", "error": f"{type(exc).__name__}: {exc}"})

    def _on_finish(self, event: dict) -> None:
        stats = event.get("stats") or {}
        successful_pages = int(stats.get("successful_pages", stats.get("pages", 0)))
        error_count = int(stats.get("errors", 0))
        self.worker = None
        self._auto_copy = False
        self._set_busy(False)
        self.target_pct = 100.0 if successful_pages else self.target_pct
        self.ring.set_state(
            target=100.0 if successful_pages else self.ring.display,
            mode="done" if successful_pages else "error",
        )
        self._set_phase("bitti")
        if not successful_pages:
            self._set_status("Kopya oluşturulamadı", BAD)
            self._react_skull("error", 4.0)
        elif error_count:
            self._set_status("Eksik tamamlandı", WARN)
            self._react_skull("done", 4.0)
        else:
            self._set_status("Tamamlandı", OK)
            self._react_skull("laugh", 4.0)
        self._stat_values["pages"].configure(
            text=f"{stats.get('pages', 0)}"
        )
        self._stat_values["assets"].configure(text=f"{stats.get('assets', 0)}")
        self._stat_values["size"].configure(text=human_size(int(stats.get("bytes_written", 0))))
        result_label = (
            "Kopya oluşturulamadı" if not successful_pages else
            "Eksik kopya" if error_count else "Kopya hazır"
        )
        self.activity_label.configure(
            text=f"{result_label} · {stats.get('pages', 0)} sayfa · "
            f"{stats.get('assets', 0)} varlık · {stats.get('duration_seconds', 0)}s"
        )
        self._log(
            f"bitti · {stats.get('pages', 0)} sayfa · {stats.get('assets', 0)} varlık · "
            f"{human_size(int(stats.get('bytes_written', 0)))}",
            "ok" if successful_pages and not error_count else "warn",
        )
        errors = event.get("errors") or []
        if errors:
            self._log(f"{len(errors)} istek başarısız (rapor dosyasında)", "warn")

        entry = event.get("entry_file")
        output_dir = event.get("output_dir")
        if output_dir:
            self.last_dir = Path(output_dir)
            self.last_entry = entry
            self.open_folder_button.set_enabled(True)
            self.open_report_button.set_enabled(True)
            self.open_browser_button.set_enabled(bool(entry))
            self.output_label.configure(text=f"Çıktı: {self.last_dir}")

    def _on_cancelled(self, event: dict) -> None:
        self.worker = None
        self._auto_copy = False
        self._set_busy(False)
        self.ring.set_state(mode="cancelled", target=self.ring.display)
        self._set_status("Durduruldu", WARN)
        self._set_phase("bitti")
        self._react_skull("idle")
        self._log("kullanıcı durdurdu · kısmi kopya korundu", "warn")
        if event.get("output_dir"):
            self.last_dir = Path(event["output_dir"])
            self.last_entry = event.get("entry_file")
            self.open_folder_button.set_enabled(True)
            self.open_browser_button.set_enabled(bool(self.last_entry))
            self.open_report_button.set_enabled(
                (self.last_dir / "mirror-report.json").exists()
            )

    def _on_fatal(self, event: dict) -> None:
        self.worker = None
        self._auto_copy = False
        self._set_busy(False)
        self._set_status("Hata", BAD)
        self._react_skull("error", 4.0)
        self.ring.set_state(mode="error", target=self.ring.display)
        self._log(f"kritik hata · {event.get('error', '')}", "error")
        self.activity_label.configure(text=f"Hata: {event.get('error', '')}")

    def stop_copy(self) -> None:
        if not self._busy():
            return
        self.cancel_event.set()
        if self._phase == "analiz":
            self._analysis_serial += 1
            self.worker = None
            self._auto_copy = False
            self._set_busy(False)
            self.ring.set_state(mode="cancelled", target=0.0)
            self._set_status("Durduruldu", WARN)
            self._react_skull("idle")
            self._log("analiz durduruldu · kopyalama başlatılmadı", "warn")
            return
        self._set_status("Durduruluyor", WARN)
        self._react_skull("wait")
        self.stop_button.set_enabled(False)
        self._log("durdurma isteği gönderildi…", "warn")

    def _reset_for_run(self) -> None:
        self.target_pct = 0.0
        self.ring.display = 0.0
        self._stat_values["pages"].configure(text="0/0")
        self._stat_values["assets"].configure(text="0/0")
        self._stat_values["size"].configure(text="0 B")
        self._stat_values["speed"].configure(text="—")
        self.open_folder_button.set_enabled(False)
        self.open_browser_button.set_enabled(False)
        self.open_report_button.set_enabled(False)

    # -- dışa açma -------------------------------------------------------
    def open_folder(self) -> None:
        if not self.last_dir or not self.last_dir.exists():
            return
        if sys.platform.startswith("win"):
            os.startfile(self.last_dir)  # noqa: S606 - standart Windows davranışı
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(self.last_dir)])
        else:
            subprocess.Popen(["xdg-open", str(self.last_dir)])

    def open_in_browser(self) -> None:
        if not self.last_dir or not self.last_entry:
            return
        try:
            if self._preview is None or self._preview.directory != self.last_dir.resolve():
                if self._preview is not None:
                    self._preview.close()
                    self._preview = None
                self._preview = PreviewServer(self.last_dir)
            url = self._preview.url_for(self.last_entry)
            webbrowser.open(url)
            self._log(f"yerel önizleme · {url}", "ok")
        except (OSError, ValueError) as exc:
            self._log(f"önizleme açılamadı: {exc}", "error")

    def open_report(self) -> None:
        if not self.last_dir:
            return
        report_path = self.last_dir / "mirror-report.json"
        if not report_path.exists():
            return
        if sys.platform.startswith("win"):
            os.startfile(report_path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(report_path)])
        else:
            subprocess.Popen(["xdg-open", str(report_path)])

    def _close(self) -> None:
        self.cancel_event.set()
        for job in (self._tick_job, self._skull_job):
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
        if self._preview is not None:
            self._preview.close()
        self.ring._photo = None
        self.signature._photo = None
        self._brand_icon = None
        for name in ("url_var", "output_var", "scope_var"):
            setattr(self, name, None)
        self.destroy()


def main(argv: list[str] | None = None) -> int:
    force_utf8_console()
    parser = argparse.ArgumentParser(prog="ui-mirror-gui", add_help=True)
    parser.add_argument("url", nargs="?", help="başlangıç adresi")
    parser.add_argument("-o", "--output", help="çıktı klasörü")
    parser.add_argument(
        "--no-splash", action="store_true", help="açılış animasyonunu atla"
    )
    args = parser.parse_args(argv)

    app = MirrorApp(
        initial_url=args.url,
        initial_output=args.output,
        splash=not args.no_splash,
        auto_analyze=bool(args.url),
    )
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
