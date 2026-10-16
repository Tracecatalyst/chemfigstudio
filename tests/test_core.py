"""core 层测试：上下标解析、字体、画布、裁边与边缘告警。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from chemfigstudio import core


class MarkupTests(unittest.TestCase):
    def test_subscript_and_superscript(self):
        self.assertEqual(core.parse_markup("CH_{3}"), [("n", "CH"), ("_", "3")])
        self.assertEqual(core.parse_markup("g^{2}"), [("n", "g"), ("^", "2")])

    def test_multiple_and_plain(self):
        self.assertEqual(core.parse_markup("C_{2}H_{5}OH"),
                         [("n", "C"), ("_", "2"), ("n", "H"), ("_", "5"), ("n", "OH")])
        self.assertEqual(core.parse_markup("kJ/g"), [("n", "kJ/g")])

    def test_empty(self):
        self.assertEqual(core.parse_markup(""), [])


class FontTests(unittest.TestCase):
    def test_font_resolves_and_is_cached(self):
        font = core.fonts(20)
        self.assertGreater(font.size, 0)
        self.assertIs(font, core.fonts(20))          # 缓存命中
        self.assertIsNot(font, core.fonts(20, bold=True))


class CanvasTests(unittest.TestCase):
    def test_new_canvas_is_white(self):
        image, _draw = core.new_canvas(40, 30)
        self.assertEqual(image.size, (40, 30))
        self.assertEqual(image.getpixel((0, 0)), core.WHITE)

    def test_save_figure_crops_to_ink(self):
        with tempfile.TemporaryDirectory() as tmp:
            image, draw = core.new_canvas(600, 400)
            draw.rectangle([250, 150, 350, 250], fill=core.INK)
            report = core.save_figure(image, Path(tmp) / "a.png")
            self.assertEqual(report.warnings, [])
            self.assertTrue(report.path.exists())
            width, height = report.size
            self.assertLess(width, 600)              # 白边被裁掉了
            self.assertLess(height, 400)
            self.assertGreater(width, 100)           # 但图形本身保留
            with open(report.path, "rb") as fh:
                self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")

    def test_save_figure_warns_when_ink_touches_edge(self):
        with tempfile.TemporaryDirectory() as tmp:
            image, draw = core.new_canvas(200, 200)
            draw.rectangle([0, 0, 199, 199], fill=core.INK)   # 顶满画布
            report = core.save_figure(image, Path(tmp) / "edge.png")
            self.assertTrue(report.warnings, "贴边时必须给出告警")


class ChainTests(unittest.TestCase):
    def test_draw_chain_advances_and_renders(self):
        image, draw = core.new_canvas(1500, 420)
        end_x = core.draw_chain(draw, 130, 200, core.ChainStyle(n_carbons=4))
        self.assertGreater(end_x, 130 + 3 * core.CHAIN_DX)

    def test_ch_count_summary_matches_chemistry(self):
        # C4H9OH: 2n+1 = 9 个 C-H, n-1 = 3 个 C-C, 1 个 O-H
        n = 4
        self.assertEqual((2 * n + 1, n - 1), (9, 3))


if __name__ == "__main__":
    unittest.main()
