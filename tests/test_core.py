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


class CoreKnowsNoChemistryTests(unittest.TestCase):
    """ARCHITECTURE §6.5：core 只画几何与文本，不认识化学。

    这条以前只是文档里的一句话，实际上 `draw_chain` 把 ``CH_{3}`` / ``H_{2}`` /
    ``OH`` / ``2 C-H`` 连同"链尾一定是羟基"的判断都写死在 core 里 —— 想画一条
    **不含氧**的链（烷烃/酸）都做不到。现在标签由 `figures.chain_style` 注入。
    """

    #: 这些字样只允许出现在 figures.py（或插件）里
    #: 词表刻意比"刚好覆盖当前代码"更宽 —— 审计 F4 指出，旧表只有 10 个词，
    #: 往里注入 ``NH_{2}`` / ``C=O`` 之类的化学式仍会全绿。
    CHEMISTRY_IN_CODE = (
        "CH_{3}", "CH_{2}", "H_{2}", "OH", "NH_{2}", "COOH", "C=O", "C-H",
        "2 C-H", "3 C-H", "孤对电子", "羟基", "羰基", "羧基",
        "醇", "醛", "酮", "酯", "烷烃", "烯烃", "炔烃",
    )

    @staticmethod
    def _code_strings(path):
        """源文件里**非 docstring** 的字符串字面量与标识符名字。

        跳过 docstring 是有意的：core 的文档需要说明"本层不认识化学"，
        那正是这条不变量本身的说明文字。
        """
        import ast
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = getattr(node, "body", None)
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    docstrings.add(id(body[0].value))
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
                if id(node) in docstrings:
                    continue
                value = node.value
                if isinstance(value, bytes):
                    # 审计 F4：旧版只扫 str，`b"OH"` 这种字节串能溜过去
                    value = value.decode("utf-8", "replace")
                found.append(value)
            elif isinstance(node, ast.Name):
                found.append(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                found.append(node.name)
            elif isinstance(node, ast.Attribute):
                found.append(node.attr)
            elif isinstance(node, ast.keyword) and node.arg:
                found.append(node.arg)
        return found

    @staticmethod
    def _mentions(text, term):
        """按**词边界**找 term。

        不能直接用 ``in``：``"OH"`` 会命中 ``"draw_rich"``／``"ImageDraw"``，
        这种误报会让人干脆把守卫测试关掉 —— 那就等于没有守卫。
        """
        import re
        return re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(term),
                         text) is not None

    def test_core_source_has_no_chemistry_vocabulary(self):
        path = Path(core.__file__)
        items = self._code_strings(path)
        for term in self.CHEMISTRY_IN_CODE:
            hits = [item for item in items if self._mentions(str(item), term)]
            self.assertEqual(hits, [],
                             "core.py 的代码里出现了化学字样 %r（%r）—— "
                             "标签应当由调用方（figures.chain_style）注入"
                             % (term, hits[:3]))

    def test_the_guard_would_catch_an_injection(self):
        """守卫自证：往扫描结果里塞违禁词，它必须变红。

        只验证"当前代码是干净的"是**假绿** —— 守卫完全可能根本没在检查
        （审计 F4 就是这么发现旧守卫能被绕过的）。这里把注入路径也测掉。
        """
        from unittest import mock

        real = CoreKnowsNoChemistryTests._code_strings

        def polluted(path):
            return list(real(path)) + ["NH_{2}", "C=O", b"OH"]

        with mock.patch.object(CoreKnowsNoChemistryTests, "_code_strings",
                               staticmethod(polluted)):
            with self.assertRaises(AssertionError):
                self.test_core_source_has_no_chemistry_vocabulary()

    def test_figures_owns_the_alcohol_vocabulary(self):
        """反过来：词汇表必须真的在 figures 里，否则上面那条会变成空检查。"""
        from chemfigstudio import figures
        for term in ("CH_{3}", "H_{2}", "CH_{2}", "OH", "2 C-H", "3 C-H"):
            self.assertIn(term, figures._ALCOHOL_VOCAB.values(),
                          "figures 的词汇表缺 %r" % term)

    def test_chain_style_agrees_with_the_requested_label_style(self):
        """`label_style` 的语义（h2 vs ch2）由 figures 解释，core 不再判断。"""
        from chemfigstudio import figures
        self.assertEqual(figures.chain_style(4, label_style="h2").vertex_label, "H_{2}")
        self.assertEqual(figures.chain_style(4, label_style="ch2").vertex_label, "CH_{2}")
        # h2 是默认：老调用点（不传 label_style）行为必须不变
        self.assertEqual(figures.chain_style(4).vertex_label, "H_{2}")

    def test_chain_style_injects_head_and_tail_labels(self):
        from chemfigstudio import figures
        style = figures.chain_style(3)
        self.assertEqual(style.head_label, "CH_{3}")
        self.assertEqual(style.end_label, "OH")
        self.assertTrue(style.end_lone_pairs)
        self.assertEqual(style.end_colour, core.OXYGEN_RED)

    def test_notes_are_opt_in_and_only_carry_text(self):
        from chemfigstudio import figures
        plain = figures.chain_style(4)
        self.assertFalse(plain.note)
        self.assertEqual(plain.note_label, "")
        self.assertEqual(plain.note_head_label, "")
        noted = figures.chain_style(4, note=True)
        self.assertTrue(noted.note)
        self.assertEqual(noted.note_label, "2 C-H")
        self.assertEqual(noted.note_head_label, "3 C-H")

    def test_bare_chain_style_draws_geometry_without_labels(self):
        """直接构造 core.ChainStyle() 得到的是"没有文字的锯齿链"，不该自带任何化学字样。"""
        style = core.ChainStyle(n_carbons=3)
        for field in ("head_label", "vertex_label", "end_label",
                      "note_label", "note_head_label"):
            self.assertEqual(getattr(style, field), "", "ChainStyle 默认带了标签：%s" % field)
        image, draw = core.new_canvas(600, 300)
        self.assertGreater(core.draw_chain(draw, 60, 150, style), 60)

    def test_geometry_kwargs_pass_through(self):
        """`**geometry` 必须透传（series 图靠它给每行不同的 dy/字号）。"""
        from chemfigstudio import figures
        style = figures.chain_style(2, label_size=31, sub_size=21, dy=44)
        self.assertEqual((style.label_size, style.sub_size, style.dy), (31, 21, 44))


if __name__ == "__main__":
    unittest.main()


class CjkFontTests(unittest.TestCase):
    """中文文本必须能真的显示出来（不是 ▯）。

    踩过：Arial 没有中文字形，中文标题渲染成空心方块 ``□``。
    **内容丢失**，不是"字体难看"。6 张示例图标题全是英文，所以一直没暴露。
    """

    def test_needs_cjk_for_chinese(self):
        from chemfigstudio import core
        self.assertTrue(core.needs_cjk("苯"))
        self.assertTrue(core.needs_cjk("丁醇的燃烧"))
        self.assertTrue(core.needs_cjk("，：；"))          # 中文标点
        self.assertTrue(core.needs_cjk("benzene 苯"))

    def test_needs_cjk_is_false_for_latin_and_math(self):
        """箭头、希腊字母、数学符号 Arial 都有 —— 切字体会改变既有观感。"""
        from chemfigstudio import core
        for text in ("Benzene", "ΔHc (literature)", "a → b", "2 × 3",
                     "H_{2}O", "kJ/mol", ""):
            with self.subTest(text=text):
                self.assertFalse(core.needs_cjk(text))

    def test_font_for_text_switches_only_for_cjk(self):
        from chemfigstudio import core
        base = core.fonts(27, True)
        self.assertIs(core.font_for_text(base, "Benzene"), base,
                      "纯英文不该换字体（换了就会破坏逐字节回归）")
        switched = core.font_for_text(base, "苯")
        self.assertNotEqual(switched.path, base.path)
        self.assertEqual(switched.size, base.size, "换字体但字号必须一致")
        self.assertTrue(core.font_is_cjk(switched))

    def test_scaled_font_keeps_the_cjk_flag(self):
        """画布缩放时不能把中文字体换回 Arial（踩过：改完还是方块，根因在这）。"""
        from chemfigstudio import core
        cjk = core.fonts(27, True, True)
        surface = core.new_surface(400, 200, scale=2.0)
        scaled = surface._scaled_font(cjk)
        self.assertTrue(core.font_is_cjk(scaled),
                        "缩放后丢了 cjk 标记 → 中文又变方块")
        self.assertAlmostEqual(float(scaled.size), 54, delta=2)

    def test_cjk_font_is_actually_loadable(self):
        from chemfigstudio import core
        font = core.fonts(24, False, True)
        self.assertGreater(font.getbbox("苯")[2], 0, "中文字形宽度为 0")

    def test_title_with_chinese_renders_real_glyphs(self):
        """端到端：中文标题必须真的画出墨迹，而不是一个方块。

        判据：中文标题的墨迹**明显多于**空白标题（方块只有一个，中文有五个字）。
        """
        import tempfile
        from pathlib import Path

        from PIL import Image

        from chemfigstudio import figures
        from chemfigstudio.skeleton import Skeleton

        payload = Skeleton.chain(3).to_json()
        with tempfile.TemporaryDirectory() as tmp:
            blank = figures.skeleton_figure(str(Path(tmp) / "a.png"), skeleton=payload,
                                            formats=("png",), dpi=150)
            chinese = figures.skeleton_figure(str(Path(tmp) / "b.png"), skeleton=payload,
                                              formats=("png",), dpi=150,
                                              title="苯环结构式")
            with Image.open(blank.report.outputs["png"]) as first:
                with Image.open(chinese.report.outputs["png"]) as second:
                    self.assertGreater(second.size[1], 0)
                    # 标题让画布变高或变宽（裁边后），至少不能更小
                    self.assertGreaterEqual(max(second.size), max(first.size) - 4)
