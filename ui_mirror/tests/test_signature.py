"""Regression checks for the live ASCII signature."""

import unittest

from ui_mirror.signature import PANEL_COLUMNS, PANEL_ROWS, render_skull


class SignatureTest(unittest.TestCase):
    def test_reactions_keep_the_skull_silhouette_and_closed_jaw(self) -> None:
        idle, _ = render_skull(6, "idle")
        laugh, _ = render_skull(6, "laugh")
        next_frame, _ = render_skull(9, "idle")

        lines = idle.splitlines()
        self.assertEqual(len(lines), PANEL_ROWS)
        self.assertTrue(all(len(line) == PANEL_COLUMNS for line in lines))
        self.assertNotEqual(idle, next_frame)
        self.assertEqual(
            tuple(char == " " for char in idle),
            tuple(char == " " for char in laugh),
        )


if __name__ == "__main__":
    unittest.main()
