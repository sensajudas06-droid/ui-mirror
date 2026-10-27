"""Compact, app-colored window controls for the Windows desktop shell."""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from ctypes import wintypes
from typing import Callable

from .theme import ACCENT, BG, BORDER, MUTED, SURFACE, SURFACE_HI, TEXT


class _Control(tk.Canvas):
    def __init__(self, parent: tk.Misc, kind: str, command: Callable[[], None]) -> None:
        super().__init__(parent, width=46, height=42, bg=SURFACE,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.kind = kind
        self.command = command
        self._hover = False
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", lambda _event: self.command())
        self.draw()

    def _enter(self, _event: tk.Event) -> None:
        self._hover = True
        self.draw()

    def _leave(self, _event: tk.Event) -> None:
        self._hover = False
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        fill = ACCENT if self._hover and self.kind == "close" else (
            SURFACE_HI if self._hover else SURFACE
        )
        self.configure(bg=fill)
        color = TEXT if self._hover else MUTED
        if self.kind == "min":
            self.create_line(18, 23, 28, 23, fill=color, width=1.7)
        elif self.kind == "max":
            self.create_rectangle(18, 15, 28, 25, outline=color, width=1.5)
        elif self.kind == "restore":
            self.create_line(20, 16, 27, 16, 27, 23, fill=color, width=1.4)
            self.create_rectangle(17, 19, 25, 27, outline=color, width=1.4)
        else:
            self.create_line(18, 17, 28, 27, fill=color, width=1.6)
            self.create_line(28, 17, 18, 27, fill=color, width=1.6)


class WindowChrome:
    def __init__(self, root: tk.Tk, icon: tk.PhotoImage | None,
                 on_close: Callable[[], None]) -> None:
        self.root = root
        self._maximized = False
        self._restore_geometry = ""
        self._drag: tuple[int, int, int, int] | None = None
        self._resize: tuple[int, int, int, int] | None = None
        root.overrideredirect(True)

        bar = tk.Frame(root, bg=SURFACE, height=43)
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(1, weight=1)
        self.bar = bar

        brand = tk.Frame(bar, bg=SURFACE)
        brand.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=(13, 0))
        if icon is not None:
            mark = tk.Label(brand, image=icon, bg=SURFACE, bd=0)
        else:
            mark = tk.Label(brand, text="◆", bg=SURFACE, fg=ACCENT,
                            font=("Segoe UI", 15), bd=0)
        mark.pack(side="left", padx=(0, 9))
        name = tk.Label(brand, text="UI MIRROR", bg=SURFACE, fg=TEXT,
                        font=("Segoe UI", 10, "bold"), bd=0)
        name.pack(side="left")
        divider = tk.Label(brand, text="/", bg=SURFACE, fg=BORDER,
                           font=("Segoe UI", 10), bd=0)
        divider.pack(side="left", padx=9)
        maker = tk.Label(brand, text="BLACKWOOD", bg=SURFACE, fg=MUTED,
                         font=("Segoe UI", 9, "bold"), bd=0)
        maker.pack(side="left")
        for surface in (bar, brand, mark, name, divider, maker):
            surface.bind("<ButtonPress-1>", self._begin_move)
            surface.bind("<B1-Motion>", self._move)
            surface.bind("<Double-Button-1>", lambda _event: self.toggle_maximize())

        _Control(bar, "min", self.minimize).grid(row=0, column=2, sticky="ns")
        self.max_button = _Control(bar, "max", self.toggle_maximize)
        self.max_button.grid(row=0, column=3, sticky="ns")
        _Control(bar, "close", on_close).grid(row=0, column=4, sticky="ns")
        tk.Frame(root, bg=BORDER, height=1).grid(row=1, column=0, sticky="ew")

        self.grip = tk.Canvas(root, width=18, height=18, bg=BG,
                              highlightthickness=0, cursor="size_nw_se")
        for shift in (0, 5, 10):
            self.grip.create_line(15-shift, 16, 16, 15-shift,
                                  fill=BORDER, width=1)
        self.grip.place(relx=1, rely=1, anchor="se")
        self.grip.bind("<ButtonPress-1>", self._begin_resize)
        self.grip.bind("<B1-Motion>", self._resize_window)

        root.bind("<Alt-F4>", lambda _event: on_close(), add="+")
        root.bind("<Map>", self._on_map, add="+")

    def activate(self) -> None:
        """Keep the borderless root visible in the Windows taskbar."""
        self.root.update_idletasks()
        if sys.platform != "win32":
            return
        try:
            user32 = ctypes.windll.user32
            user32.GetParent.argtypes = [wintypes.HWND]
            user32.GetParent.restype = wintypes.HWND
            user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.GetWindowLongW.restype = ctypes.c_long
            user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
            user32.SetWindowLongW.restype = ctypes.c_long
            user32.SetWindowPos.argtypes = [
                wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_uint,
            ]
            user32.SetWindowPos.restype = wintypes.BOOL
            hwnd = user32.GetParent(self.root.winfo_id())
            exstyle = user32.GetWindowLongW(hwnd, -20)
            user32.SetWindowLongW(hwnd, -20, (exstyle | 0x00040000) & ~0x00000080)
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)
        except (AttributeError, OSError):
            pass

    def _on_map(self, event: tk.Event) -> None:
        if event.widget is not self.root:
            return
        self.root.after(20, self._restore_override)

    def _restore_override(self) -> None:
        if self.root.winfo_exists() and self.root.state() == "normal":
            self.root.overrideredirect(True)
            self.activate()

    def minimize(self) -> None:
        # Tk cannot iconify an override-redirect window. Restore the native
        # style only while minimized, then reinstate our chrome on Map.
        self.root.overrideredirect(False)
        self.root.iconify()

    def toggle_maximize(self) -> None:
        if self._maximized:
            self.root.geometry(self._restore_geometry)
            self._maximized = False
            self.grip.place(relx=1, rely=1, anchor="se")
            self.max_button.kind = "max"
        else:
            self._restore_geometry = self.root.geometry()
            left, top, right, bottom = self._work_area()
            self.root.geometry(f"{right-left}x{bottom-top}+{left}+{top}")
            self._maximized = True
            self.grip.place_forget()
            self.max_button.kind = "restore"
        self.max_button.draw()

    def _work_area(self) -> tuple[int, int, int, int]:
        if sys.platform == "win32":
            try:
                rect = wintypes.RECT()
                if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0,
                                                               ctypes.byref(rect), 0):
                    return rect.left, rect.top, rect.right, rect.bottom
            except (AttributeError, OSError):
                pass
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _begin_move(self, event: tk.Event) -> None:
        self._drag = (event.x_root, event.y_root,
                      self.root.winfo_x(), self.root.winfo_y())

    def _move(self, event: tk.Event) -> None:
        if self._drag is None or self._maximized:
            return
        sx, sy, x, y = self._drag
        self.root.geometry(f"+{x + event.x_root - sx}+{y + event.y_root - sy}")

    def _begin_resize(self, event: tk.Event) -> None:
        if not self._maximized:
            self._resize = (event.x_root, event.y_root,
                            self.root.winfo_width(), self.root.winfo_height())

    def _resize_window(self, event: tk.Event) -> None:
        if self._resize is None or self._maximized:
            return
        sx, sy, width, height = self._resize
        self.root.geometry(
            f"{max(1160, width + event.x_root - sx)}x"
            f"{max(820, height + event.y_root - sy)}"
        )
