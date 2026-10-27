from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ui_mirror.gui import MirrorApp


class _Var:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value


class OutputLocationTest(unittest.TestCase):
    def test_empty_chosen_folder_is_used_and_existing_copy_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "selected"
            folder.mkdir()
            app = SimpleNamespace(output_var=_Var(str(folder)))
            self.assertEqual(MirrorApp._new_output_dir(app, "https://example.com"), folder)
            (folder / "existing.txt").write_text("keep", encoding="utf-8")
            new_folder = MirrorApp._new_output_dir(app, "https://example.com")
            self.assertEqual(new_folder.parent, folder)
            self.assertTrue(new_folder.name.startswith("mirror_example.com-"))
            self.assertFalse(new_folder.exists())
            self.assertEqual((folder / "existing.txt").read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
