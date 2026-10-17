"""导出测试：PNG / SVG / PDF 三格式（契约验收 8–11）。

要点：
* SVG 必须是**真文本**（验收 9），且与 PNG 取景一致（验收 11）
* PDF 页面尺寸与 PNG 长宽误差 < 1%（验收 10）
* DPI 影响实际像素（验收 8）：300 dpi 时输出长边 ≥ 2400 px（见 rev3 修订）
* 透明背景：四角 alpha = 0
"""

from __future__ import annotations

import _bootstrap  # noqa: F401  （测试隔离：见 tests/_bootstrap.py）
import os
import re
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from chemfigstudio import api, figures

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def pdf_mediabox(path: Path):
    """不依赖第三方库，从 PDF 里抠出 MediaBox（点为单位）。"""
    raw = path.read_bytes()
    match = re.search(rb"/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", raw)
    if not match:
        return None
    x0, y0, x1, y1 = (float(v) for v in match.groups())
    return (x1 - x0, y1 - y0)


class ExportFormatTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def render(self, kind="alcohol", **kwargs):
        return api.render(kind, out=str(self.tmp / (kind + ".png")),
                          params=kwargs.pop("params", {"smiles": "CCCCO"}),
                          formats=("png", "svg", "pdf"), dpi=kwargs.pop("dpi", 300),
                          transparent=kwargs.pop("transparent", False))

    # ---------------------------------------------------------------- 基本
    def test_all_three_formats_are_written(self):
        result = self.render()
        self.assertEqual(sorted(result.outputs), ["pdf", "png", "svg"])
        self.assertEqual(PNG_MAGIC, Path(result.outputs["png"]).read_bytes()[:8])
        self.assertTrue(Path(result.outputs["svg"]).read_text(encoding="utf-8").startswith("<?xml"))
        self.assertEqual(b"%PDF-", Path(result.outputs["pdf"]).read_bytes()[:5])

    def test_main_path_is_a_file_that_exists(self):
        result = self.render()
        self.assertTrue(Path(result.path).exists(), result.path)

    def test_only_svg_requested_yields_svg_as_main_path(self):
        """只要求 svg 时，报告路径必须是那个 .svg（不能指向不存在的 .png）。"""
        result = figures.alcohol_figure(self.tmp / "only.png", n_carbons=3,
                                        formats=("svg",), dpi=300)
        self.assertEqual(Path(result.path).suffix, ".svg")
        self.assertTrue(Path(result.path).exists())
        self.assertEqual(sorted(result.outputs), ["svg"])

    # ------------------------------------------------------- SVG 是真矢量
    def test_svg_has_real_text_and_no_bitmap(self):
        svg = Path(self.render().outputs["svg"]).read_text(encoding="utf-8")
        self.assertIn("<text", svg, "SVG 必须包含可选中的文本")
        self.assertNotIn("<image", svg, "SVG 不应内嵌位图")
        self.assertNotIn("data:image", svg)
        self.assertIn("font-family", svg)

    def test_svg_viewbox_matches_png_size(self):
        """验收 11：SVG 与 PNG 取景一致（误差 < 1%）。"""
        result = self.render()
        svg = Path(result.outputs["svg"]).read_text(encoding="utf-8")
        view_box = [float(v) for v in re.search(r'viewBox="([^"]+)"', svg).group(1).split()]
        png_w, png_h = Image.open(result.outputs["png"]).size
        self.assertAlmostEqual(view_box[2], png_w, delta=max(2, png_w * 0.01))
        self.assertAlmostEqual(view_box[3], png_h, delta=max(2, png_h * 0.01))

    # ------------------------------------------------------------------ PDF
    def test_pdf_page_size_matches_png(self):
        """验收 10：PDF 页面与 PNG 长宽误差 < 1%（PDF 单位是点 = 1/72 inch）。"""
        result = self.render(dpi=300)
        box = pdf_mediabox(Path(result.outputs["pdf"]))
        self.assertIsNotNone(box, "找不到 MediaBox")
        png_w, png_h = Image.open(result.outputs["png"]).size
        for got, want in zip(box, (png_w, png_h)):
            # PNG 像素 → 点：/dpi*72
            expected_pt = want / 300.0 * 72.0
            self.assertLess(abs(got - expected_pt) / expected_pt, 0.01)

    # ---------------------------------------------------------------- DPI
    def test_dpi_controls_pixel_size(self):
        """验收 8（rev3）：300 dpi 时输出长边 ≥ 2400 px；150 dpi 明显更小。"""
        # 用公开入口（内部自适应重绘只在这里生效）
        big = api.render("alcohol", out=str(self.tmp / "big.png"),
                         params={"smiles": "CCCCO"}, formats=("png",), dpi=300)
        small = api.render("alcohol", out=str(self.tmp / "small.png"),
                           params={"smiles": "CCCCO"}, formats=("png",), dpi=150)
        self.assertGreaterEqual(max(big.size), 2400)
        self.assertLess(max(small.size), max(big.size))
        self.assertGreaterEqual(max(small.size), 800)

    # -------------------------------------------------------------- 透明背景
    def test_transparent_png_has_alpha_zero_corners(self):
        result = self.render(transparent=True)
        with Image.open(result.outputs["png"]) as image:
            self.assertEqual(image.mode, "RGBA")
            corners = [image.getpixel((0, 0)), image.getpixel((image.width - 1, 0)),
                       image.getpixel((0, image.height - 1)),
                       image.getpixel((image.width - 1, image.height - 1))]
        for pixel in corners:
            self.assertEqual(pixel[3], 0, "透明导出时四角 alpha 应为 0：%s" % (pixel,))

    def test_opaque_png_stays_rgb(self):
        result = self.render(transparent=False)
        with Image.open(result.outputs["png"]) as image:
            self.assertEqual(image.mode, "RGB")


class FigureMatrixTests(unittest.TestCase):
    """四种图 × 三格式都得能出，且 SVG 一律是真文本。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_all_kinds_produce_vector_text(self):
        cases = (("alcohol", figures.alcohol_figure, dict(n_carbons=4, name="butan-1-ol")),
                 ("series", figures.series_figure, {}),
                 ("reaction", figures.reaction_figure, {}),
                 ("chcount", figures.chcount_figure, {}),
                 ("enthalpy", figures.enthalpy_figure, {}),
                 ("esterification", figures.esterification_figure,
                  dict(acid_carbons=2, alcohol_carbons=2)),
                 ("skeleton", figures.skeleton_figure, {}))
        for kind, builder, kwargs in cases:
            with self.subTest(kind=kind):
                result = builder(self.tmp / (kind + ".png"), formats=("png", "svg", "pdf"),
                                 dpi=300, **kwargs)
                self.assertEqual(sorted(result.outputs), ["pdf", "png", "svg"])
                svg = Path(result.outputs["svg"]).read_text(encoding="utf-8")
                self.assertIn("<text", svg)
                self.assertNotIn("<image", svg)
                self.assertEqual(result.warnings, [], "%s 不该有告警" % kind)



class FormatDocumentationTests(unittest.TestCase):
    """文档不能说"只出 PNG"：SVG/PDF 早就实现了（ADR-014 取代了 ADR-004）。

    踩过：`ARCHITECTURE.md §11`、§7.4、ADR-004、README FAQ 四处同时声称只做 PNG，
    而代码三种格式都能出 —— 读者会以为矢量输出是个"待办"，而不是已有功能。
    """

    ROOT = Path(__file__).resolve().parents[1]
    #: 整份文件豁免：ADR 原文是历史记录，措辞必须保持原样
    HISTORY_FILES = ("docs/DECISIONS.md",)
    #: 只豁免"历史段"的文件：CHANGELOG 的旧发布说明要留档，
    #: 但 Unreleased 段落不该再引用旧说法（引用式地"说明它已被取代"会让守卫误报，
    #: 而误报最终会让人把它关掉 —— 直接指向 ADR-014 即可）。
    PARTIAL_FILES = ("CHANGELOG.md",)

    PATTERNS = ("只出 PNG", "只输出 PNG", "只做 PNG", "PNG only")

    @classmethod
    def _scanned_text(cls, path, rel):
        """返回本文档里"现役"的部分（CHANGELOG 只取第一个**已发布**版本之前）。

        不要写死版本号：私有仓库是 0.2.x、公开仓库是 0.4.x，写死会让它在
        另一边永远匹配不到 —— 而这种情况曾被重复类名静默掩盖过。
        """
        text = path.read_text(encoding="utf-8")
        if rel in cls.PARTIAL_FILES:
            first_release = re.search(r"^## \[\d", text, re.MULTILINE)
            if first_release:
                return text[:first_release.start()]
        return text

    def test_docs_do_not_claim_png_only(self):
        checked = 0
        for path in sorted(self.ROOT.glob("*.md")) + sorted((self.ROOT / "docs").glob("*.md")):
            rel = path.relative_to(self.ROOT).as_posix()
            if rel in self.HISTORY_FILES:
                continue                      # ADR 原文保留历史措辞
            text = self._scanned_text(path, rel)
            checked += 1
            for pattern in self.PATTERNS:
                self.assertNotIn(pattern, text,
                                 "%s 的现役部分仍说 %r —— 现在 PNG/SVG/PDF 都支持（ADR-014）"
                                 % (rel, pattern))
        self.assertGreater(checked, 0, "没扫到任何文档，断言等于空转")

    def test_changelog_exemption_stops_at_the_current_release(self):
        """反向确认：CHANGELOG 只豁免历史段，本次发布段仍受约束。"""
        path = self.ROOT / "CHANGELOG.md"
        full = path.read_text(encoding="utf-8")
        scanned = self._scanned_text(path, "CHANGELOG.md")
        self.assertLess(len(scanned), len(full), "CHANGELOG 的历史段没有被排除")
        self.assertIn("## [Unreleased]", scanned)

    def test_formats_are_actually_three(self):
        from chemfigstudio import settings
        self.assertEqual(tuple(settings.VALID_FORMATS), ("png", "svg", "pdf"))


if __name__ == "__main__":
    unittest.main()


class PreviewSizeTests(unittest.TestCase):
    """预览必须"够看且不拖慢"：1:1 绘制并封顶 1200 px（不写用户输出目录）。"""

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

    def test_preview_is_capped_for_all_kinds(self):
        from chemfigstudio import api
        cases = (("alcohol", {"smiles": "CCCCO"}), ("series", {}),
                 ("reaction", {}), ("chcount", {}))
        for kind, params in cases:
            with self.subTest(kind=kind):
                result = api.preview(kind, params)
                longest = max(result.size)
                self.assertLessEqual(longest, api.PREVIEW_MAX_LONGEST,
                                     "%s 预览过大：%s" % (kind, result.size))
                self.assertGreaterEqual(longest, 200, "%s 预览过小：%s" % (kind, result.size))
                self.assertTrue(Path(result.path).exists())

    def test_preview_does_not_grow_with_dpi(self):
        from chemfigstudio import api
        small = api.preview("alcohol", {"smiles": "CCCCO"}, dpi=0)
        self.assertLessEqual(max(small.size), api.PREVIEW_MAX_LONGEST)

    def test_preview_leaves_output_dir_clean(self):
        from chemfigstudio import api
        output = self.tmp / "out"
        output.mkdir()
        before = {p.name for p in output.iterdir()}
        api.preview("series", {})
        self.assertEqual({p.name for p in output.iterdir()}, before)
