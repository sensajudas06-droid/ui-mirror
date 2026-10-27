from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ui_mirror.settings import AppSettings, load_settings, save_settings


class SettingsTest(unittest.TestCase):
    def test_round_trip_and_invalid_file_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            settings = AppSettings(max_pages=120, jobs=6, delay=0.3, render_dynamic=False)
            save_settings(settings, path)
            self.assertEqual(load_settings(path), settings)
            path.write_text('{"jobs": 999}', encoding="utf-8")
            self.assertEqual(load_settings(path), AppSettings())


if __name__ == "__main__":
    unittest.main()
