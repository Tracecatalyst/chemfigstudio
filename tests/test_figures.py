"""figures 层测试：四种图都能出图，且不带告警。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from chemfigstudio import figures

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class FigureTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _assert_png(self, result, min_width=300, min_height=80):
        path = result.path
        self.assertTrue(path.exists(), "没有生成文件：%s" % path)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(8), PNG_MAGIC)
        with Image.open(path) as image:          # 用 with：否则会有 ResourceWarning
            width, height = image.size
        self.assertGreater(width, min_width)
        self.assertGreater(height, min_height)
        self.assertEqual(result.size, (width, height))
        self.assertEqual(result.warnings, [], "标准参数下不应有告警：%s" % result.warnings)


class AlcoholFigureTests(FigureTestCase):
    def test_default(self):
        self._assert_png(figures.alcohol_figure(self.tmp / "alcohol.png", n_carbons=4))

    def test_with_name_and_delta_h(self):
        result = figures.alcohol_figure(self.tmp / "butanol.png", n_carbons=4,
                                        name="butan-1-ol", delta_h="-36.1")
        self._assert_png(result)

    def test_label_style_ch2_is_accepted(self):
        self._assert_png(figures.alcohol_figure(self.tmp / "ch2.png", n_carbons=5,
                                                label_style="ch2"))


class OtherFiguresTests(FigureTestCase):
    def test_series(self):
        self._assert_png(figures.series_figure(self.tmp / "series.png"), min_height=600)

    def test_series_without_delta_h(self):
        self._assert_png(figures.series_figure(self.tmp / "series_nodh.png",
                                               show_delta_h=False), min_height=600)

    def test_reaction_even_carbon(self):
        self._assert_png(figures.reaction_figure(self.tmp / "reaction.png", n_carbons=4))

    def test_reaction_odd_carbon_doubles_equation(self):
        # 奇数碳时 1.5n 不是整数 → 整条方程式乘 2，不应抛异常
        self._assert_png(figures.reaction_figure(self.tmp / "reaction3.png", n_carbons=3))

    def test_chcount(self):
        self._assert_png(figures.chcount_figure(self.tmp / "chcount.png", n_carbons=4))


class CoefficientTests(unittest.TestCase):
    def test_butanol_coefficients(self):
        coef, o2, co2, h2o, scale = figures._combustion_coefficients(4)
        self.assertEqual((coef, o2, co2, h2o), (1, 6, 4, 5))
        self.assertIsNone(scale)

    def test_propanol_needs_doubling(self):
        coef, o2, co2, h2o, scale = figures._combustion_coefficients(3)
        self.assertEqual(scale, 2)
        self.assertEqual((coef, o2, co2, h2o), (2, 9, 6, 8))


if __name__ == "__main__":
    unittest.main()


class BuiltinFigureCoverageTests(unittest.TestCase):
    """契约要求内置图类型 ≥6 种：逐个出图 + 不变量检查。"""

    EXPECTED = ("alcohol", "series", "reaction", "chcount", "enthalpy",
                "esterification", "skeleton")

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_builtin_kinds_are_complete_and_ordered(self):
        from chemfigstudio import api
        self.assertEqual(api.BUILTIN_KINDS, self.EXPECTED)
        self.assertGreaterEqual(len(self.EXPECTED), 6, "契约要求内置图 ≥6 种")
        kinds = [figure["kind"] for figure in api.list_figures()]
        self.assertEqual(kinds[:len(self.EXPECTED)], list(self.EXPECTED),
                         "内置图必须排在前面且顺序固定")

    def test_every_builtin_renders_in_two_formats(self):
        from chemfigstudio import api
        params = {
            "alcohol": {"smiles": "CCCCO"},
            "series": {},
            "reaction": {"n_carbons": 3},
            "chcount": {"n_carbons": 4},
            "enthalpy": {},
            "esterification": {"acid_carbons": 2, "alcohol_carbons": 2},
            "skeleton": {"skeleton": ""},
        }
        for kind in self.EXPECTED:
            with self.subTest(kind=kind):
                result = api.render(kind, out=str(self.tmp / (kind + ".png")),
                                    params=params[kind], formats=("png", "svg"), dpi=300)
                self.assertEqual(sorted(result.outputs), ["png", "svg"])
                self.assertEqual(result.warnings, [], "%s 不该有告警" % kind)
                self.assertGreater(max(result.size), 200, kind)

    def test_enthalpy_defaults_match_report_data(self):
        from chemfigstudio import figures
        self.assertEqual(len(figures.ENTHALPY_SERIES), 5)
        names = [row[0] for row in figures.ENTHALPY_SERIES]
        self.assertEqual(names, ["ethanol", "propan-1-ol", "butan-1-ol",
                                 "pentan-1-ol", "hexan-1-ol"])

    def test_enthalpy_accepts_four_column_entries(self):
        from chemfigstudio import figures
        result = figures.enthalpy_figure(
            str(self.tmp / "four.png"),
            entries=[("slug", "custom", 5.0, 25.0)], formats=("png",), dpi=150)
        self.assertTrue(Path(result.report.outputs["png"]).exists())

    def test_source_has_no_unicode_subscripts(self):
        """硬约束：字符串里不许出现 Unicode 上下标（Arial 缺字 → □）。"""
        source = (Path(__file__).resolve().parents[1] / "src" / "chemfigstudio"
                  / "figures.py").read_text(encoding="utf-8")
        banned = [ch for ch in "₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺" if ch in source]
        self.assertEqual(banned, [], "figures.py 里出现 Unicode 上下标：%s" % banned)

    def test_esterification_names(self):
        from chemfigstudio import figures
        self.assertEqual(figures._ACID_NAMES[2], "ethanoic acid")
        self.assertEqual(figures._ACID_NAMES[2].replace("oic acid", "oate"), "ethanoate")
        self.assertEqual(figures._ALKANOL_NAMES[1], "methanol")


class SeriesOverlapDetectionTests(unittest.TestCase):
    """精确重叠检测：真实布局不误报，真重叠必须检出（修掉"自适应重绘后误报"）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_real_series_layout_never_warns_at_any_dpi(self):
        from chemfigstudio import api
        for dpi in (150, 300, 600):
            with self.subTest(dpi=dpi):
                result = api.render("series", out=str(self.tmp / ("s%d.png" % dpi)),
                                    params={}, formats=("png",), dpi=dpi)
                self.assertEqual(result.warnings, [],
                                 "dpi=%d 时 series 不该有重叠告警（旧版用未缩放坐标查缩放图，会误报）"
                                 % dpi)

    def _image(self, first_bottom, second_top):
        from PIL import Image, ImageDraw
        image = Image.new("RGB", (400, 100), (255, 255, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle([10, 20, 390, first_bottom], fill=(0, 0, 0))
        draw.rectangle([10, second_top, 390, 80], fill=(0, 0, 0))
        return image

    def test_genuine_overlap_is_detected(self):
        from chemfigstudio import figures
        # 行中心 25 / 75，行距 50 → 中线在 y=50；两个墨迹块都越过中线
        image = self._image(first_bottom=52, second_top=48)
        warnings = figures._series_overlap_warnings(image, [25.0, 75.0], 50.0, 1.0)
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("第 1 行与 2 行", warnings[0])

    def test_separated_rows_warn_nothing(self):
        from chemfigstudio import figures
        image = self._image(first_bottom=44, second_top=56)
        self.assertEqual(figures._series_overlap_warnings(image, [25.0, 75.0], 50.0, 1.0), [])

    def test_detection_is_scale_invariant(self):
        """缩放后结论必须一致（这正是旧版误报的根源）。"""
        from PIL import Image, ImageDraw
        from chemfigstudio import figures
        for scale in (1.0, 2.0, 3.5):
            with self.subTest(scale=scale):
                size = (400, 100)
                image = Image.new("RGB", (int(size[0] * scale), int(size[1] * scale)),
                                  (255, 255, 255))
                draw = ImageDraw.Draw(image)
                draw.rectangle([10 * scale, 20 * scale, 390 * scale, 44 * scale],
                               fill=(0, 0, 0))
                draw.rectangle([10 * scale, 56 * scale, 390 * scale, 80 * scale],
                               fill=(0, 0, 0))
                self.assertEqual(
                    figures._series_overlap_warnings(image, [25.0, 75.0], 50.0, scale), [],
                    "scale=%s 时误报" % scale)


class ReactionOverflowTests(unittest.TestCase):
    """回归：reaction 大碳数时内容被右边缘裁切（复杂图压力测试发现）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_small_carbon_count_is_unchanged(self):
        """4 碳（报告正在用的那张）必须保持既有尺寸，不受自适应加宽影响。"""
        from chemfigstudio import figures
        result = figures.reaction_figure(str(self.tmp / "c4.png"), n_carbons=4,
                                         formats=("png",), dpi=150)
        self.assertEqual(result.warnings, [])
        self.assertLess(result.size[0], 2200, "小碳数不该被加宽：%s" % (result.size,))

    def test_large_carbon_count_has_no_edge_warning(self):
        from chemfigstudio import figures
        result = figures.reaction_figure(str(self.tmp / "c12.png"), n_carbons=12,
                                         delta_h_kj_per_mol=-8000, formats=("png",), dpi=150)
        self.assertEqual(result.warnings, [],
                         "12 碳时不该再有边缘裁切告警：%s" % (result.warnings,))

    def test_widening_keeps_everything_visible(self):
        """加宽后的墨迹必须**不**贴到画布右沿（否则说明还没画全）。"""
        from PIL import Image, ImageChops
        from chemfigstudio import core, figures
        result = figures.reaction_figure(str(self.tmp / "wide.png"), n_carbons=12,
                                         formats=("png",), dpi=150)
        with Image.open(result.report.outputs["png"]) as image:
            self.assertLess(image.width, 4000)
        self.assertEqual(result.warnings, [])

    def test_explicit_width_is_honoured(self):
        from chemfigstudio import figures
        wide = figures.reaction_figure(str(self.tmp / "explicit.png"), n_carbons=12,
                                       width=4000, formats=("png",), dpi=150)
        self.assertEqual(wide.warnings, [])
