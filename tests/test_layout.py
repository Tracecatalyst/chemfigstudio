"""P3 布局层：声明式版式（figures.py 只声明结构，坐标由 layout 给）。"""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from chemfigstudio import figures, layout


class RowLayoutMathTests(unittest.TestCase):
    def test_row_y_and_centres(self):
        grid = layout.series_layout()
        self.assertEqual(grid.row_y(0), 130.0)
        self.assertEqual(grid.row_y(4), 130.0 + 4 * 168.0)
        self.assertEqual(grid.centres(3), [130.0, 298.0, 466.0])

    def test_height_matches_the_previous_hardcoded_formula(self):
        """高度公式必须与重构前的 `130 + pitch * rows + 90` 完全一致（否则视觉会漂）。"""
        grid = layout.series_layout()
        for rows in (1, 3, 5, 8):
            self.assertEqual(grid.height(rows), 130 + 168 * rows + 90)

    def test_slot_lookup_and_error(self):
        grid = layout.series_layout()
        self.assertEqual(grid.slot("structure").x, 90)
        with self.assertRaises(KeyError) as ctx:
            grid.slot("nope")
        self.assertIn("no slot", str(ctx.exception))

    def test_layout_is_immutable(self):
        grid = layout.series_layout()
        with self.assertRaises(Exception):
            grid.pitch = 1.0                                     # frozen dataclass

    def test_series_layout_numbers_are_declared_here(self):
        """这些数字以前硬编码在 figures.series_figure 里，现在只应存在于 layout.py。"""
        grid = layout.series_layout()
        self.assertEqual(grid.width, 1800)
        self.assertEqual(grid.pitch, 168.0)
        self.assertEqual(grid.chain_dy, 36.0)
        self.assertEqual(grid.slot("name").x, 760)
        self.assertEqual(grid.slot("name").dy, -34)
        self.assertEqual(grid.slot("formula").dy, 2)
        self.assertEqual(grid.slot("delta_h").x, 1040)
        self.assertEqual(grid.slot("delta_h").dy, -18)
        self.assertEqual(grid.slot("delta_h").ink(), layout.DELTA_RED)

    def test_figures_source_has_no_hardcoded_column_positions(self):
        """figures.py 里不该再出现 760 / 1040 这类列位字面量。"""
        source = (Path(__file__).resolve().parents[1] / "src" / "chemfigstudio"
                  / "figures.py").read_text(encoding="utf-8")
        series = source[source.index("def series_figure"):source.index("def reaction_figure")]
        for number in ("760", "1040", "1800", "168"):
            self.assertNotIn(number, series,
                             "series_figure 里还有硬编码版式数字 %s（应来自 layout）" % number)

    def test_banner_layout(self):
        banner = layout.banner_layout(1500, 470)
        self.assertEqual((banner.width, banner.height), (1500, 470))
        self.assertEqual(banner.content_width(), 1500 - 2 * banner.margin)


class LayoutDrivesTheRenderTests(unittest.TestCase):
    """把布局换掉，画面必须跟着变（证明 figures 真的在消费布局，而不是巧合一致）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._old = os.environ.get("CHEMFIG_CONFIG_DIR")
        os.environ["CHEMFIG_CONFIG_DIR"] = str(self.tmp / "cfg")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("CHEMFIG_CONFIG_DIR", None)
        else:
            os.environ["CHEMFIG_CONFIG_DIR"] = self._old
        self._tmp.cleanup()

    def test_custom_pitch_changes_output_height(self):
        default = figures.series_figure(str(self.tmp / "default.png"), formats=("png",), dpi=150)
        wide = replace(layout.series_layout(), pitch=240.0)
        custom = figures.series_figure(str(self.tmp / "custom.png"), layout=wide,
                                       formats=("png",), dpi=150)
        self.assertGreater(custom.size[1], default.size[1],
                           "换布局后行距变大，输出应更高：%s vs %s" % (custom.size, default.size))

    def test_custom_columns_move_content(self):
        grid = layout.series_layout()
        moved = replace(grid, slots=dict(grid.slots, name=replace(grid.slot("name"), x=1200)))
        result = figures.series_figure(str(self.tmp / "moved.png"), layout=moved,
                                       formats=("png",), dpi=150)
        self.assertEqual(result.warnings, [])
        self.assertGreater(result.size[0], 0)

    def test_default_series_still_renders_without_warnings(self):
        for dpi in (150, 300):
            with self.subTest(dpi=dpi):
                result = figures.series_figure(str(self.tmp / ("s%d.png" % dpi)),
                                               formats=("png", "svg"), dpi=dpi)
                self.assertEqual(result.warnings, [])
                self.assertEqual(sorted(result.outputs), ["png", "svg"])


if __name__ == "__main__":
    unittest.main()


class BannerLayoutTests(unittest.TestCase):
    """一次性图（alcohol/reaction/chcount/enthalpy/esterification）的版式也归 layout 管。"""

    EXPECTED = {
        "alcohol": (1500, 420, 34.0),
        "reaction": (2100, 560, 26.0),
        "chcount": (1700, 520, 28.0),
        "enthalpy": (1500, 660, 60.0),
        "esterification": (1500, 470, 46.0),
    }

    def test_banners_are_declared_here(self):
        for kind, (width, height, title_y) in self.EXPECTED.items():
            with self.subTest(kind=kind):
                banner = layout.figure_layout(kind)
                self.assertEqual((banner.width, banner.height), (width, height))
                self.assertEqual(banner.title_y, title_y)

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(KeyError) as ctx:
            layout.figure_layout("nope")
        self.assertIn("no banner layout", str(ctx.exception))

    def test_figures_source_has_no_hardcoded_canvas_sizes(self):
        """这些画布尺寸以前硬编码在 figures.py，现在只应存在于 layout.py。"""
        source = (Path(__file__).resolve().parents[1] / "src" / "chemfigstudio"
                  / "figures.py").read_text(encoding="utf-8")
        for width, height, _y in self.EXPECTED.values():
            self.assertNotIn("width, height = %d, %d" % (width, height), source,
                             "figures.py 里还有硬编码画布 %dx%d（应来自 layout）"
                             % (width, height))

    def test_every_banner_figure_uses_the_layout(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "chemfigstudio"
                  / "figures.py").read_text(encoding="utf-8")
        for kind in self.EXPECTED:
            self.assertIn('figure_layout("%s")' % kind, source,
                          "%s 没有使用 figure_layout" % kind)
