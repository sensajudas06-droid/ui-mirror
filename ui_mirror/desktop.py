"""Windows executable entry point for the desktop application."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _self_test() -> int:
    import bs4  # noqa: F401 - packaged dependency probe
    import requests  # noqa: F401 - packaged dependency probe

    from ui_mirror.gui import MirrorApp
    from ui_mirror.signature import PANEL_COLUMNS, PANEL_ROWS, render_skull, skull_frame

    art, _, _ = skull_frame(0, size="full")
    if len(art.splitlines()) != 44:
        raise RuntimeError("ASCII imza pakette bulunamadı")
    panel_art, _ = render_skull(0)
    if len(panel_art.splitlines()) != PANEL_ROWS or max(map(len, panel_art.splitlines())) != PANEL_COLUMNS:
        raise RuntimeError("Panel ASCII kafatası pakette bulunamadı")
    app = MirrorApp(splash=False)
    app.withdraw()
    app.update()
    if app._brand_icon is None:
        raise RuntimeError("Blackwood logo pakette bulunamadı")
    app._close()
    return 0


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return _self_test()
    from ui_mirror.gui import main as gui_main

    return gui_main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        log_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Blackwood UI Mirror"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "startup-error.log"
            log_path.write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            log_path = None
        if "--self-test" not in sys.argv[1:]:
            try:
                import ctypes

                detail = f"Ayrıntılar: {log_path}" if log_path else "Ayrıntılar kaydedilemedi."
                ctypes.windll.user32.MessageBoxW(
                    None, f"UI Mirror açılamadı.\n{detail}", "Blackwood UI Mirror", 0x10,
                )
            except Exception:
                pass
        raise SystemExit(1)
