"""Validated, per-user desktop copy settings."""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppSettings:
    max_pages: int = 60
    max_assets: int = 2500
    jobs: int = 4
    delay: float = 0.15
    timeout: float = 20.0
    retries: int = 1
    time_budget: int = 600
    render_dynamic: bool = True

    def validate(self) -> None:
        if not isinstance(self.render_dynamic, bool):
            raise ValueError("render_dynamic: doğru/yanlış bekleniyor")
        limits = {
            "max_pages": (1, 1000),
            "max_assets": (1, 10000),
            "jobs": (1, 16),
            "delay": (0, 5),
            "timeout": (3, 120),
            "retries": (0, 5),
            "time_budget": (30, 7200),
        }
        for name, (lower, upper) in limits.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name}: sayı bekleniyor")
            if name in {"max_pages", "max_assets", "jobs", "retries", "time_budget"} and not isinstance(value, int):
                raise ValueError(f"{name}: tam sayı bekleniyor")
            if not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError(f"{name}: {lower}–{upper} aralığında olmalı")


def settings_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".config")))
    return base / "Blackwood UI Mirror" / "settings.json"


def load_settings(path: Path | None = None) -> AppSettings:
    source = path or settings_path()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
        defaults = asdict(AppSettings())
        values = {name: raw.get(name, default) for name, default in defaults.items()}
        settings = AppSettings(**values)
        settings.validate()
        return settings
    except (OSError, ValueError, TypeError, AttributeError):
        return AppSettings()


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    settings.validate()
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    temporary.replace(target)
