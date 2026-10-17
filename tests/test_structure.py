"""structure 的单元测试：把真实分子导进来。

分两半：

* **molblock 解析**是纯文本处理 → 不需要 RDKit，用固定夹具断言（任何机器都跑）。
* **SMILES 导入**走 RDKit（可选依赖）→ 没装就跳过，装了才测。

⚠️ 这里最容易犯的错是**看起来对、化学错**（标签写成 CH_{3}、楔形指反）。所以断言尽量
落在"化学含义"上（键级、楔形朝向、有没有多余的字），而不是"有没有出图"。

⚠️ molblock 规定标题行是第 1 行、计数行是第 4 行。夹具一律用 :func:`_mol` 构造
（显式拼行），**不要**直接用三引号贴 —— 缩进或空行一错，
计数行就移位，测试会在错误的行上解析。
"""

from __future__ import annotations

import unittest

import _bootstrap  # noqa: F401  （测试隔离：见 tests/_bootstrap.py）

from chemfigstudio import structure
from chemfigstudio.errors import ConfigError, RenderError
from chemfigstudio.skeleton import WEDGE_DOWN, WEDGE_NONE, WEDGE_UP


def _mol(title: str, counts: str, atoms, bonds, extra="") -> str:
    """拼一个 V2000 molblock。

    标准布局：第 1 行标题、第 2 行程序、**第 3 行空行**、第 4 行计数。
    第 3 行必须是空行 —— 少一行整个解析就错位（踩过）。
    """
    lines = [title, "     chemfigstudio-test", "", counts]
    lines.extend(atoms)
    lines.extend(bonds)
    if extra:
        lines.append(extra)
    lines.append("M  END")
    return "\n".join(lines) + "\n"


#: 乙醇（三个重原子；两端都是甲基，所以两个碳都该写 CH_{3}）
ETHANOL_BLOCK = _mol(
    "ethanol", "  3  2  0  0  0  0  0  0  0  0999 V2000",
    ["    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0",
     "    1.2990    0.7500    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0",
     "    2.5980    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0"],
    ["  1  2  1  0", "  2  3  1  0"])

#: 带形式电荷（N+）与实心楔形键的最小夹具
CHARGED_BLOCK = _mol(
    "charged", "  3  2  0  0  0  0  0  0  0  0999 V2000",
    ["    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0",
     "    1.2990    0.7500    0.0000 N   0  0  0  0  0  0  0  0  0  0  0  0",
     "    2.5980    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0"],
    ["  1  2  1  1", "  2  3  1  0"], extra="M  CHG  1   2   1")

#: 丁烷：中间两个碳各连 2 个碳 —— 真正的"骨架碳"（不该写字）
BUTANE_BLOCK = _mol(
    "butane", "  4  3  0  0  0  0  0  0  0  0999 V2000",
    ["    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0",
     "    1.2990    0.7500    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0",
     "    2.5980    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0",
     "    3.8970    0.7500    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0"],
    ["  1  2  1  0", "  2  3  1  0", "  3  4  1  0"])

#: 楔形对照：C1(甲基,度数1) — C2(羧基碳,度数2) — O1(羰基氧,度数1)，
#: 楔形画在 C2—O1 上且是虚线（molblock 里 6 = hash）
WEDGE_BLOCK = _mol(
    "wedge", "  3  2  0  0  0  0  0  0  0  0999 V2000",
    ["    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0",
     "    1.2990    0.7500    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0",
     "    2.5980    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0"],
    ["  1  2  1  0", "  2  3  1  6"])


class MolblockParsingTests(unittest.TestCase):
    """纯文本解析：不需要 RDKit。"""

    def test_fixtures_follow_the_v2000_layout(self):
        """夹具本身要合规：计数行必须在第 4 行（前 3 行是标题/程序/空行）。"""
        for name in ("ETHANOL_BLOCK", "CHARGED_BLOCK", "BUTANE_BLOCK", "WEDGE_BLOCK"):
            with self.subTest(fixture=name):
                lines = globals()[name].splitlines()
                self.assertEqual(lines[2], "", "%s 的第 3 行应当是空行" % name)
                self.assertIn("V2000", lines[3])
                int(lines[3][0:3])                      # 计数行可解析

    def test_parses_atoms_bonds_and_title(self):
        result = structure.from_molblock(ETHANOL_BLOCK)
        skeleton = result.skeleton
        self.assertEqual(len(skeleton.atoms), 3)
        self.assertEqual(len(skeleton.bonds), 2)
        self.assertEqual([a.element for a in skeleton.atoms], ["C", "C", "O"])

    def test_coordinates_are_scaled_to_our_bond_length(self):
        """molblock 用 Å，画布用像素 —— 不缩放的话分子会小得看不见。"""
        import math
        from chemfigstudio.structure import DEFAULT_BOND_PX

        skeleton = structure.from_molblock(ETHANOL_BLOCK).skeleton
        first, second = skeleton.atoms[0], skeleton.atoms[1]
        length = math.dist((first.x, first.y), (second.x, second.y))
        self.assertAlmostEqual(length, DEFAULT_BOND_PX, delta=1.0)

    def test_y_axis_is_flipped(self):
        """molblock 的 y 向上为正，画布向下为正 —— 不翻就是上下镜像（楔形会指反）。

        夹具里 C1 在 y=0、C2 在 y=0.75，翻转后 C2 应当在**下面**（y 更小）。
        """
        skeleton = structure.from_molblock(ETHANOL_BLOCK).skeleton
        self.assertGreater(skeleton.atoms[0].y, skeleton.atoms[1].y)

    def test_wedge_and_order_are_read(self):
        skeleton = structure.from_molblock(CHARGED_BLOCK).skeleton
        first = skeleton.bonds[0]
        self.assertEqual(first.order, 1)
        self.assertIn(first.wedge, (WEDGE_UP, WEDGE_DOWN))

    def test_formal_charge_is_read(self):
        skeleton = structure.from_molblock(CHARGED_BLOCK).skeleton
        nitrogen = [a for a in skeleton.atoms if a.element == "N"][0]
        self.assertEqual(nitrogen.charge, 1)
        self.assertIn("^{+}", nitrogen.label)      # 标签要带上电荷

    def test_double_bond_oxygen_is_not_labelled_OH(self):
        """羧基的双键氧必须写 O 而不是 OH —— 这是"看起来对、化学错"的典型。"""
        block = ETHANOL_BLOCK.replace("  2  3  1  0", "  2  3  2  0")
        skeleton = structure.from_molblock(block).skeleton
        oxygen = [a for a in skeleton.atoms if a.element == "O"][0]
        self.assertEqual(oxygen.label, "O")

    def test_skeletal_carbon_has_no_label(self):
        """骨架碳不写字（结构式里碳是顶点）——写 CH_{3} 会满图都是甲基。"""
        skeleton = structure.from_molblock(BUTANE_BLOCK).skeleton
        degree = {a.id: sum(1 for b in skeleton.bonds if a.id in (b.a, b.b))
                  for a in skeleton.atoms}
        inner = [a for a in skeleton.atoms if degree[a.id] >= 2]
        self.assertEqual(len(inner), 2, "丁烷中间应当有两个骨架碳")
        for atom in inner:
            self.assertEqual(atom.label, "", "骨架碳不该带 CH_{3} 之类的字")

    def test_terminal_carbon_gets_methyl_label(self):
        skeleton = structure.from_molblock(ETHANOL_BLOCK).skeleton
        terminal = [a for a in skeleton.atoms
                    if a.element == "C" and sum(1 for b in skeleton.bonds
                                                if a.id in (b.a, b.b)) == 1]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0].label, "CH_{3}")

    @staticmethod
    def _with_hydrogen():
        """在乙醇夹具上挂一个显式氢（原子数 3 → 4）。"""
        return _mol(
            "ethanol+h", "  4  2  0  0  0  0  0  0  0  0999 V2000",
            ["    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0",
             "    1.2990    0.7500    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0",
             "    2.5980    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0",
             "    0.0000    1.0000    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0"],
            ["  1  2  1  0", "  2  3  1  0"])

    def test_hydrogens_are_dropped_by_default(self):
        result = structure.from_molblock(self._with_hydrogen())
        self.assertEqual([a.element for a in result.skeleton.atoms], ["C", "C", "O"])
        self.assertTrue(any("氢" in note for note in result.notes))

    def test_keep_hydrogens_option(self):
        result = structure.from_molblock(self._with_hydrogen(), keep_hydrogens=True)
        self.assertEqual(len(result.skeleton.atoms), 4)

    def test_sdf_uses_first_record_only(self):
        text = ETHANOL_BLOCK + "$$$$\n" + BUTANE_BLOCK
        result = structure.from_molblock(text)
        self.assertEqual(len(result.skeleton.atoms), 3)

    # ------------------------------------------------------------ 坏输入

    def test_rejects_empty_input(self):
        for bad in ("", "   \n"):
            with self.subTest(value=bad):
                with self.assertRaises(RenderError):
                    structure.from_molblock(bad)

    def test_rejects_v3000_with_actionable_message(self):
        block = ETHANOL_BLOCK.replace("V2000", "V3000")
        with self.assertRaises(RenderError) as ctx:
            structure.from_molblock(block)
        self.assertIn("V3000", str(ctx.exception))

    def test_rejects_truncated_atom_block(self):
        truncated = "\n".join(ETHANOL_BLOCK.splitlines()[:6])
        with self.assertRaises(RenderError):
            structure.from_molblock(truncated)

    def test_rejects_bond_pointing_at_missing_atom(self):
        block = ETHANOL_BLOCK.replace("  2  3  1  0", "  2  9  1  0")
        with self.assertRaises(RenderError) as ctx:
            structure.from_molblock(block)
        self.assertIn("不存在的原子", str(ctx.exception))

    def test_unknown_element_becomes_carbon_with_a_note(self):
        block = ETHANOL_BLOCK.replace("    0.0000    0.0000    0.0000 C",
                                      "    0.0000    0.0000    0.0000 Xx")
        result = structure.from_molblock(block)
        self.assertTrue(result.skipped_elements)
        self.assertTrue(any("不认识" in note for note in result.notes))


class WedgeConventionTests(unittest.TestCase):
    """楔形朝向是化学含义，不是画法偏好。"""

    def test_degree_heuristic_puts_the_narrow_end_at_the_more_substituted_atom(self):
        """没有权威手性中心时用度数启发式：取代基多的那个当窄端。"""
        skeleton = structure.from_molblock(WEDGE_BLOCK).skeleton
        wedge = [b for b in skeleton.bonds if b.wedge != WEDGE_NONE][0]
        self.assertEqual(wedge.a, "m2", "羧基碳（度数 2）应当是窄端")
        self.assertEqual(wedge.b, "m3")
        self.assertEqual(wedge.wedge, WEDGE_DOWN)

    def test_known_stereo_centre_wins_over_the_heuristic(self):
        """给了权威手性中心时必须听它的，而不是听度数 —— 并同时翻转楔形种类。

        （现实里 O 不是手性中心；这里只是把两条判定路径摆成互相矛盾，验证优先级。）
        """
        told = structure.molblock_to_skeleton(WEDGE_BLOCK, known_stereo=[3]).skeleton
        wedge = [b for b in told.bonds if b.wedge != WEDGE_NONE][0]
        self.assertEqual(wedge.a, "m3", "应服从权威手性中心")
        self.assertEqual(wedge.b, "m2")
        self.assertEqual(wedge.wedge, WEDGE_UP, "交换两端时必须翻转楔形种类")

    def test_equal_degree_ends_are_left_alone(self):
        """两端度数相同时不要猜：保持原样（宁可不动，也不制造对映体）。"""
        block = _mol("oo", "  2  1  0  0  0  0  0  0  0  0999 V2000",
                     ["    0.0000    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0",
                      "    1.2990    0.7500    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0"],
                     ["  1  2  1  6"])
        skeleton = structure.from_molblock(block).skeleton
        wedge = [b for b in skeleton.bonds if b.wedge != WEDGE_NONE][0]
        self.assertEqual((wedge.a, wedge.b), ("m1", "m2"))
        self.assertEqual(wedge.wedge, WEDGE_DOWN)


class HeteroatomLabelTests(unittest.TestCase):
    """杂原子标签要**按价键环境**推，不能按元素查表。

    踩过：一律套 ``ELEMENT_LABELS``，于是咖啡因的三个氮全被写成 ``NH_{2}``
    —— 而它们是 N-甲基（连 2 个重原子）与环内 =N-（无氢）。专业工具都是看
    "连了几个重原子、有几根重键"来定标签的。
    """

    @classmethod
    def setUpClass(cls):
        from chemfigstudio import smiles as smiles_mod
        cls.available = smiles_mod.rdkit_available()

    def setUp(self):
        if not self.available:
            self.skipTest("没装 RDKit（可选依赖）")

    @staticmethod
    def _labels(smiles, element):
        result = structure.from_smiles(smiles)
        return sorted({a.label for a in result.skeleton.atoms if a.element == element})

    def test_caffeine_nitrogens_have_no_hydrogen(self):
        """咖啡因的四个氮：两个 N-甲基、两个环内 =N-，**都不带氢**。"""
        self.assertEqual(self._labels("CN1C=NC2=C1C(=O)N(C)C(=O)N2C", "N"), ["N"])

    def test_pyrrole_has_exactly_one_NH(self):
        """吡咯：环内那个 N 带 1 个氢（画成 NH），不是 N 也不是 NH_{2}。"""
        self.assertEqual(self._labels("c1cc[nH]c1", "N"), ["NH"])

    def test_pyridine_nitrogen_bare(self):
        self.assertEqual(self._labels("c1ccncc1", "N"), ["N"])

    def test_amine_and_alcohol(self):
        self.assertEqual(self._labels("NCCO", "N"), ["NH_{2}"])
        self.assertEqual(self._labels("NCCO", "O"), ["OH"])

    def test_carbonyl_oxygen_is_bare_and_hydroxyl_keeps_its_hydrogen(self):
        labels = self._labels("CC(=O)O", "O")
        self.assertEqual(labels, ["O", "OH"])

    def test_phenol_hydroxyl_is_kept(self):
        self.assertEqual(self._labels("O=C(O)c1ccccc1O", "O"), ["O", "OH"])

    def test_terminal_carbon_is_methyl_but_ring_carbon_is_bare(self):
        result = structure.from_smiles("Cc1ccccc1")          # 甲苯
        carbons = [a for a in result.skeleton.atoms if a.element == "C"]
        labelled = [a for a in carbons if a.label]
        self.assertEqual(len(labelled), 1, "只有甲基那个碳该写字")
        self.assertEqual(labelled[0].label, "CH_{3}")

    def test_charged_atom_keeps_its_sign(self):
        """带电原子要在标签后带上标（铵根 NH_{3}^{+}）。"""
        result = structure.from_smiles("C[NH3+]")
        labels = {a.label for a in result.skeleton.atoms if a.element == "N"}
        self.assertTrue(any("^{+}" in label for label in labels), labels)


class WedgeRenderingTests(unittest.TestCase):
    """楔形必须真的被画出来 —— 模型里存了不等于画得出来。

    （踩过：`_draw_hashed_wedge` 漏移植时全部单测仍是绿的，因为没有一条测试
      真的走"虚线楔形"这条渲染分支。所以这里对**三种键**都出图比对。）
    """

    @staticmethod
    def _render(tmp, wedge_code, name):
        """出图并返回 (字节, 墨迹像素数)。"""
        from pathlib import Path

        from PIL import Image
        from chemfigstudio import figures

        block = ETHANOL_BLOCK.replace("  2  3  1  0", "  2  3  1  %s" % wedge_code)
        target = Path(tmp) / ("%s.png" % name)
        result = figures.skeleton_figure(str(target), skeleton=block,
                                         formats=("png",), dpi=150)
        assert result.warnings == [], result.warnings
        data = target.read_bytes()
        with Image.open(target) as image:
            rgb = image.convert("RGB")
            ink = sum(1 for pixel in rgb.getdata() if pixel != (255, 255, 255))
        return data, ink

    def test_all_three_bond_kinds_render_and_differ(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            plain, plain_ink = self._render(tmp, "0", "plain")
            wedge, wedge_ink = self._render(tmp, "1", "wedge")
            hashed, hashed_ink = self._render(tmp, "6", "hashed")

        # 三张图必须互不相同：说明三种键型都真的画了不同东西
        self.assertNotEqual(plain, wedge, "实心楔形与普通线画出来一样")
        self.assertNotEqual(plain, hashed, "虚线楔形与普通线画出来一样")
        self.assertNotEqual(wedge, hashed, "实心与虚线楔形画出来一样")
        # 三种都会画出墨迹（非空图）——但**不要**断言"谁用墨更多"：
        # 虚线是 7 段**渐长**的短线，总和未必比一条粗实线多（实测更少），
        # 那种断言只是把猜测写进测试。
        for name, ink in (("plain", plain_ink), ("wedge", wedge_ink),
                          ("hashed", hashed_ink)):
            self.assertGreater(ink, 0, "%s 渲染出来是空白" % name)

    def test_hashed_wedge_from_a_real_chiral_molecule(self):
        """端到端：手性分子的楔形要能画出来（这是渲染分支的真实入口）。"""
        from chemfigstudio import smiles as smiles_mod

        if not smiles_mod.rdkit_available():
            self.skipTest("没装 RDKit（可选依赖）")

        import tempfile
        from pathlib import Path

        from chemfigstudio import api

        result = structure.from_smiles("C[C@H](N)C(=O)O")
        self.assertTrue([b for b in result.skeleton.bonds if b.wedge != WEDGE_NONE])
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "alanine.png"
            rendered = api.render("skeleton", out=str(target), params={
                "skeleton": result.skeleton.to_json(), "title": "L-alanine"})
            self.assertEqual(rendered.warnings, [])
            self.assertTrue(target.exists())


class RoundTripTests(unittest.TestCase):
    """导入 → 导出 JSON → 再读回来，不能丢信息。"""

    def test_json_round_trip_keeps_charge_and_wedge(self):
        from chemfigstudio.skeleton import Skeleton

        skeleton = structure.from_molblock(CHARGED_BLOCK).skeleton
        again = Skeleton.from_json(skeleton.to_json())
        self.assertEqual([a.charge for a in again.atoms], [a.charge for a in skeleton.atoms])
        self.assertEqual([b.wedge for b in again.bonds], [b.wedge for b in skeleton.bonds])

    def test_as_dict_omits_defaults_but_keeps_real_values(self):
        from chemfigstudio.skeleton import Atom, Bond

        self.assertNotIn("charge", Atom(id="a", element="C").as_dict())
        self.assertNotIn("wedge", Bond(a="a", b="b").as_dict())
        self.assertEqual(Bond(a="a", b="b", wedge=WEDGE_UP).as_dict()["wedge"], WEDGE_UP)

    def test_unknown_wedge_value_falls_back_to_none(self):
        from chemfigstudio.skeleton import Bond

        self.assertEqual(Bond.from_dict({"a": "a", "b": "b", "wedge": "bogus"}).wedge,
                         WEDGE_NONE)


class SkeletonInputTests(unittest.TestCase):
    """`skeleton` 参数接受三种输入 —— 这是"导入"真正被接线的地方。"""

    def test_json_passes_through(self):
        from chemfigstudio.skeleton import Skeleton, parse_skeleton_input

        original = Skeleton.chain(5)
        parsed = parse_skeleton_input(original.to_json())
        self.assertEqual(len(parsed.atoms), len(original.atoms))
        self.assertEqual(len(parsed.bonds), len(original.bonds))

    def test_empty_gives_default_chain(self):
        from chemfigstudio.skeleton import parse_skeleton_input

        self.assertTrue(parse_skeleton_input("").atoms)
        self.assertTrue(parse_skeleton_input("   ").atoms)

    def test_molblock_text_is_detected(self):
        from chemfigstudio.skeleton import parse_skeleton_input

        parsed = parse_skeleton_input(ETHANOL_BLOCK)
        self.assertEqual([a.element for a in parsed.atoms], ["C", "C", "O"])

    def test_sdf_sniffed_by_dollar_marker(self):
        from chemfigstudio.skeleton import parse_skeleton_input

        parsed = parse_skeleton_input(ETHANOL_BLOCK + "$$$$\n")
        self.assertEqual(len(parsed.atoms), 3)

    def test_plain_text_is_treated_as_smiles(self):
        """不是 JSON 也不是 molblock → 当 SMILES（需要 RDKit，没装就跳过）。"""
        from chemfigstudio import smiles as smiles_mod
        from chemfigstudio.skeleton import parse_skeleton_input

        if not smiles_mod.rdkit_available():
            self.skipTest("没装 RDKit（可选依赖）")
        parsed = parse_skeleton_input("c1ccccc1")
        self.assertEqual(len(parsed.atoms), 6)

    def test_render_accepts_json_and_molblock(self):
        """端到端：不同输入都必须能出图（否则"导入"只活在库里）。"""
        import tempfile
        from pathlib import Path

        from chemfigstudio import api
        from chemfigstudio.skeleton import Skeleton

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "x.png"
            first = api.render("skeleton", out=str(target),
                               params={"skeleton": Skeleton.chain(3).to_json()})
            self.assertTrue(Path(first.path).exists())
            second = api.render("skeleton", out=str(target),
                                params={"skeleton": ETHANOL_BLOCK, "title": "from molblock"})
            self.assertTrue(Path(second.path).exists())

    def test_multi_molecule_scene_json_is_not_downgraded(self):
        """多分子场景 JSON 顶层是 {active, molecules}，不能被降级成单分子（踩过）。"""
        import tempfile
        from pathlib import Path

        from chemfigstudio import figures
        from chemfigstudio.skeleton import Scene, Skeleton

        scene = Scene.single(Skeleton.chain(3), name="A")
        scene.add(Skeleton.chain(3), name="B")
        with tempfile.TemporaryDirectory() as tmp:
            single = figures.skeleton_figure(str(Path(tmp) / "one.png"),
                                            skeleton=Scene.single(
                                                Skeleton.chain(3)).to_json(),
                                            formats=("png",), dpi=150)
            pair = figures.skeleton_figure(str(Path(tmp) / "two.png"),
                                           skeleton=scene.to_json(),
                                           formats=("png",), dpi=150)
            self.assertGreater(pair.size[1], single.size[1],
                               "两个分子应当比一个分子高")


class SmilesImportTests(unittest.TestCase):
    """SMILES 那条路要 RDKit（可选依赖）—— 没装就跳过，别让主闸门变红。"""

    @classmethod
    def setUpClass(cls):
        from chemfigstudio import smiles as smiles_mod
        cls.available = smiles_mod.rdkit_available()

    def setUp(self):
        if not self.available:
            self.skipTest("没装 RDKit（可选依赖）")

    @staticmethod
    def _labels(result):
        return [(a.element, a.label) for a in result.skeleton.atoms if a.label]

    def test_benzene_is_a_label_free_ring(self):
        """苯环：六个碳都不写字，六个键交替单双（Kekulé 式）。"""
        result = structure.from_smiles("c1ccccc1")
        self.assertEqual(len(result.skeleton.atoms), 6)
        self.assertEqual(self._labels(result), [])
        orders = sorted(b.order for b in result.skeleton.bonds)
        self.assertEqual(orders, [1, 1, 1, 2, 2, 2])
        self.assertEqual(result.formula, "C6H6")

    def test_carboxylic_acid_distinguishes_the_two_oxygens(self):
        result = structure.from_smiles("CC(=O)O")
        self.assertEqual(self._labels(result),
                         [("C", "CH_{3}"), ("O", "O"), ("O", "OH")])

    def test_stereocentre_gets_a_wedge_from_the_centre(self):
        """L-丙氨酸：楔形的窄端必须是那个手性碳，不能是甲基。"""
        result = structure.from_smiles("C[C@H](N)C(=O)O")
        skeleton = result.skeleton
        wedges = [b for b in skeleton.bonds if b.wedge != WEDGE_NONE]
        self.assertEqual(len(wedges), 1, "手性中心应该恰好画出一根楔形键")
        degree = {a.id: sum(1 for b in skeleton.bonds if a.id in (b.a, b.b))
                  for a in skeleton.atoms}
        self.assertGreaterEqual(degree[wedges[0].a], degree[wedges[0].b])

    def test_metadata_is_filled(self):
        result = structure.from_smiles("CC(=O)Oc1ccccc1C(=O)O")   # 阿司匹林
        self.assertEqual(result.formula, "C9H8O4")
        self.assertAlmostEqual(result.mw, 180.16, delta=0.1)
        self.assertEqual(result.source, "smiles")

    def test_hydrogens_are_implicit_by_default(self):
        result = structure.from_smiles("CCO")
        self.assertEqual([a.element for a in result.skeleton.atoms], ["C", "C", "O"])

    def test_bad_smiles_raises_render_error(self):
        with self.assertRaises(RenderError):
            structure.from_smiles("!!!not-a-smiles")

    def test_empty_smiles_raises_render_error(self):
        with self.assertRaises(RenderError):
            structure.from_smiles("   ")


class NoStderrLeakTests(unittest.TestCase):
    """解析失败时**不能有 stderr 输出**（AGENTS §4：库代码不许 print）。

    踩过：把依赖搜索深度从 4 调到 8 之后，本机 RDKit 突然"可见"了，于是
    `smiles.resolve_smiles("not-real")` 走 RDKit 路径、RDKit 把
    `SMILES Parse Error` 直接写到 stderr —— 全量测试输出里突然多出一段英文日志。
    根本原因是 `_resolve_rdkit` 漏了 `quiet_rdkit()`（render3d/structure 包了，它没包）。

    ⚠️ 必须**起子进程**来抓：RDKit 的 C++ 日志直接写文件描述符 2，
    Python 的 ``contextlib.redirect_stderr`` 只重定向 ``sys.stderr``，抓不到 ——
    第一版就是这么写的，测试永远绿（假绿），实际终端里照样喷日志。
    """

    @classmethod
    def setUpClass(cls):
        from chemfigstudio import smiles as smiles_mod
        cls.available = smiles_mod.rdkit_available()

    def setUp(self):
        if not self.available:
            self.skipTest("没装 RDKit（可选依赖）")

    def _stderr_of(self, body):
        """在子进程里跑一段代码，返回它的 stderr（能看到 fd 2）。"""
        import os
        import subprocess
        import sys

        env = dict(os.environ)
        env["PYTHONPATH"] = "src" if os.path.isdir("src") else ""
        result = subprocess.run([sys.executable, "-c", body],
                                capture_output=True, text=True, env=env, timeout=60)
        return result.stderr

    def test_smiles_parse_failure_is_silent(self):
        stderr = self._stderr_of(
            "from chemfigstudio import smiles\n"
            "try:\n"
            "    smiles.resolve_smiles('not-real')\n"
            "except Exception:\n"
            "    pass\n")
        self.assertEqual(stderr, "", "resolve_smiles 把 RDKit 日志漏到了 stderr")

    def test_structure_import_failure_is_silent(self):
        stderr = self._stderr_of(
            "from chemfigstudio import structure\n"
            "try:\n"
            "    structure.from_smiles('not-real')\n"
            "except Exception:\n"
            "    pass\n")
        self.assertEqual(stderr, "", "from_smiles 把 RDKit 日志漏到了 stderr")

    def test_render3d_failure_is_silent(self):
        stderr = self._stderr_of(
            "from chemfigstudio import render3d\n"
            "try:\n"
            "    render3d.embed3d('not-real')\n"
            "except Exception:\n"
            "    pass\n")
        self.assertEqual(stderr, "", "embed3d 把 RDKit 日志漏到了 stderr")


class MissingRdkitTests(unittest.TestCase):
    def test_hint_names_both_ways_out(self):
        """没装 RDKit 时，提示要同时给出"装它"和"改用 molblock"两条路。"""
        self.assertIn("pip install", structure.RDKIT_2D_HINT)
        self.assertIn("molblock", structure.RDKIT_2D_HINT)

    def test_molblock_import_needs_no_rdkit(self):
        """molblock 那条路**不能**依赖 RDKit —— 这是它存在的意义。"""
        from unittest import mock
        with mock.patch.object(structure, "ensure_rdkit_path", return_value=[]):
            with mock.patch.dict("sys.modules", {"rdkit": None, "rdkit.Chem": None}):
                result = structure.from_molblock(ETHANOL_BLOCK)
        self.assertEqual(len(result.skeleton.atoms), 3)

    def test_from_smiles_without_rdkit_is_config_error(self):
        from unittest import mock
        with mock.patch.object(structure, "ensure_rdkit_path", return_value=[]):
            with mock.patch.dict("sys.modules", {"rdkit": None, "rdkit.Chem": None}):
                with self.assertRaises(ConfigError):
                    structure.from_smiles("CCO")


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()


class MolblockExportTests(unittest.TestCase):
    """`Skeleton → molblock`（ADR-015）：**零依赖**那条路必须自己走通。

    为什么强调零依赖：如果只有"InChI 导出"，没装 RDKit 的用户就完全交不出文件；
    而 molblock 是事实标准，纯文本就能拼，没有理由不做。
    """

    def test_exports_v2000_header_and_end(self):
        from chemfigstudio import structure
        from chemfigstudio.skeleton import Skeleton

        block = structure.to_molblock(Skeleton.chain(3), title="propane")
        lines = block.splitlines()
        self.assertEqual(lines[0], "propane", "第一行是标题")
        self.assertIn("V2000", lines[3], "第四行是计数行（第 3 行必须是空行）")
        self.assertEqual(lines[2], "")
        self.assertEqual(block.rstrip().splitlines()[-1], "M  END")

    def test_atom_and_bond_counts_match(self):
        from chemfigstudio import structure
        from chemfigstudio.skeleton import Skeleton

        skeleton = Skeleton.chain(4)
        block = structure.to_molblock(skeleton)
        counts = block.splitlines()[3]
        self.assertEqual(int(counts[0:3]), len(skeleton.atoms))
        self.assertEqual(int(counts[3:6]), len(skeleton.bonds))

    def test_round_trip_keeps_elements_and_orders(self):
        """导出再导入，原子/键/键级都得回来（坐标允许有几像素误差）。"""
        from chemfigstudio import structure
        from chemfigstudio.skeleton import Skeleton

        original = Skeleton.chain(4)
        original.set_bond_order(original.atoms[1].id, original.atoms[2].id, 2)
        again = structure.from_molblock(structure.to_molblock(original)).skeleton

        self.assertEqual([a.element for a in again.atoms],
                         [a.element for a in original.atoms])
        self.assertEqual(sorted(b.order for b in again.bonds),
                         sorted(b.order for b in original.bonds))

    def test_round_trip_preserves_the_shape(self):
        """往返**不保证坐标数值相同**，但必须保证形状相同。

        为什么数值会变：导出时把分子归一化到标准键长 1.5 Å（否则别的软件读进去
        会发现键长 0.65 Å 或 3.3 Å，那不是化学），导入时又归一化到 ``bond_px``。
        所以往返是一次**等比缩放** —— 形状不变，尺寸变。
        """
        import math

        from chemfigstudio import structure
        from chemfigstudio.skeleton import Skeleton
        from chemfigstudio.structure import DEFAULT_BOND_PX

        original = Skeleton.chain(5)
        again = structure.from_molblock(structure.to_molblock(original)).skeleton

        # 键长应当统一到 bond_px
        for bond in again.bonds:
            first, second = again.atom(bond.a), again.atom(bond.b)
            length = math.dist((first.x, first.y), (second.x, second.y))
            self.assertAlmostEqual(length, DEFAULT_BOND_PX, delta=1.0)

        # 每个原子相对第一个原子的位移方向一致（等比缩放）
        first_before, first_after = original.atoms[0], again.atoms[0]
        scale = None
        for before, after in zip(original.atoms[1:], again.atoms[1:]):
            dx_before = before.x - first_before.x
            dx_after = after.x - first_after.x
            if abs(dx_before) > 10.0:
                ratio = dx_after / dx_before
                if scale is None:
                    scale = ratio
                self.assertAlmostEqual(ratio, scale, delta=0.02)

    def test_exported_bond_lengths_are_chemical(self):
        """导出的键长必须是**真实的化学尺度**（1.5 Å），不能是任意数字。"""
        import math

        from chemfigstudio import structure
        from chemfigstudio.skeleton import Skeleton

        # 故意造一个"画得很大"的分子：键长 300 像素
        skeleton = Skeleton()
        first = skeleton.add_atom("C", 0.0, 0.0)
        second = skeleton.add_atom("C", 300.0, 0.0)
        skeleton.add_bond(first.id, second.id)

        block = structure.to_molblock(skeleton)
        atom_lines = block.splitlines()[4:6]
        x0 = float(atom_lines[0][0:10])
        x1 = float(atom_lines[1][0:10])
        self.assertAlmostEqual(abs(x1 - x0), 1.5, delta=0.01)

    def test_round_trip_keeps_charge(self):
        from chemfigstudio import structure
        from chemfigstudio.skeleton import Skeleton

        skeleton = Skeleton.chain(2)
        skeleton.atoms[-1].charge = -1
        block = structure.to_molblock(skeleton)
        self.assertIn("M  CHG", block)
        again = structure.from_molblock(block).skeleton
        self.assertEqual(again.atoms[-1].charge, -1)

    def test_wedges_are_written_as_bond_stereo(self):
        """楔形写成 molblock 的 bond stereo：1=实心（朝外），6=虚线（朝里）(ADR-015)。

        ⚠️ 不要断言"读回来 wedge 一模一样"：导入时会按化学正确性
        **重新判定窄端在哪一端**（`_normalise_wedges`），所以 up/down 可能互换、
        端点可能对调 —— 那是同一个立体构型的两种写法，不是丢信息。
        真正该验证的"化学等价"由 `InchiExportTests` 用手性分子来回测。
        """
        from chemfigstudio import structure
        from chemfigstudio.skeleton import WEDGE_DOWN, WEDGE_UP, Skeleton

        for wedge, code in ((WEDGE_UP, "1"), (WEDGE_DOWN, "6")):
            with self.subTest(wedge=wedge):
                skeleton = Skeleton.chain(3)
                skeleton.bonds[0].wedge = wedge
                block = structure.to_molblock(skeleton)
                bond_line = block.splitlines()[4 + len(skeleton.atoms)]
                # 行格式：a b order stereo ...
                fields = bond_line.split()
                self.assertEqual(fields[3], code)

    def test_no_explicit_hydrogens_are_written(self):
        """氢是隐含的 —— 导出时不写（ADR-015：结构式里氢不画）。"""
        from chemfigstudio import structure
        from chemfigstudio.skeleton import Skeleton

        block = structure.to_molblock(Skeleton.chain(4))
        self.assertNotIn(" H ", block)

    def test_title_defaults_to_skeleton_title(self):
        from chemfigstudio import structure
        from chemfigstudio.skeleton import Skeleton

        skeleton = Skeleton.chain(3)
        skeleton.title = "my molecule"
        self.assertTrue(structure.to_molblock(skeleton).startswith("my molecule"))

    def test_exported_block_is_reimportable_without_rdkit(self):
        """零依赖保证：导出结果能被我们自己的解析器读回（那条路不用 RDKit）。"""
        from unittest import mock

        from chemfigstudio import structure
        from chemfigstudio.skeleton import Skeleton

        block = structure.to_molblock(Skeleton.chain(4))
        with mock.patch.object(structure, "ensure_rdkit_path", return_value=[]):
            with mock.patch.dict("sys.modules", {"rdkit": None, "rdkit.Chem": None}):
                again = structure.from_molblock(block)
        self.assertEqual(len(again.skeleton.atoms), len(Skeleton.chain(4).atoms))


class InchiExportTests(unittest.TestCase):
    """InChI / 规范 SMILES（需要 RDKit；没装就跳过）。"""

    @classmethod
    def setUpClass(cls):
        from chemfigstudio import smiles as smiles_mod
        cls.available = smiles_mod.rdkit_available()

    def setUp(self):
        if not self.available:
            self.skipTest("没装 RDKit（可选依赖）")

    def test_ethanol_inchi_is_known(self):
        from chemfigstudio import structure

        result = structure.from_smiles("CCO")
        self.assertEqual(structure.to_inchi(result.skeleton),
                         "InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3")

    def test_benzene_inchi_is_known(self):
        """芳香性必须正确还原 —— Kekulé 写进去、芳香性读出来。"""
        from chemfigstudio import structure

        result = structure.from_smiles("c1ccccc1")
        self.assertEqual(structure.to_inchi(result.skeleton),
                         "InChI=1S/C6H6/c1-2-4-6-5-3-1/h1-6H")

    def test_aspirin_inchi_matches_itself(self):
        """同一个分子经"导出 → 导入"后，InChI 必须不变（否则导出的不是同一个分子）。"""
        from chemfigstudio import structure

        original = structure.from_smiles("CC(=O)Oc1ccccc1C(=O)O")
        block = structure.to_molblock(original.skeleton)
        again = structure.from_molblock(block)
        self.assertEqual(structure.to_inchi(again.skeleton),
                         structure.to_inchi(original.skeleton))

    def test_canonical_smiles(self):
        from chemfigstudio import structure

        result = structure.from_smiles("CCO")
        self.assertEqual(structure.to_smiles(result.skeleton), "CCO")

    def test_stereochemistry_survives_the_round_trip(self):
        """L-丙氨酸的 InChI 要带上手性层（/t2-）。"""
        from chemfigstudio import structure

        original = structure.from_smiles("C[C@H](N)C(=O)O")
        block = structure.to_molblock(original.skeleton)
        again = structure.from_molblock(block)
        inchi = structure.to_inchi(again.skeleton)
        self.assertIn("/t2-", inchi, "手性层丢了：%s" % inchi)

    def test_inchi_needs_rdkit(self):
        from unittest import mock

        from chemfigstudio import structure
        from chemfigstudio.errors import ConfigError
        from chemfigstudio.skeleton import Skeleton

        with mock.patch.object(structure, "ensure_rdkit_path", return_value=[]):
            with mock.patch.dict("sys.modules", {"rdkit": None, "rdkit.Chem": None}):
                with self.assertRaises(ConfigError):
                    structure.to_inchi(Skeleton.chain(4))

    def test_molblock_still_works_without_rdkit(self):
        """反向确认：molblock 导出**不能**因为 InChI 需要 RDKit 而跟着要 RDKit。"""
        from unittest import mock

        from chemfigstudio import structure
        from chemfigstudio.skeleton import Skeleton

        with mock.patch.object(structure, "ensure_rdkit_path", return_value=[]):
            with mock.patch.dict("sys.modules", {"rdkit": None, "rdkit.Chem": None}):
                block = structure.to_molblock(Skeleton.chain(4))
        self.assertIn("M  END", block)


class EmptyTitleMolblockTests(unittest.TestCase):
    """标题为空的 molblock 必须能读回来。

    踩过：`parse_skeleton_input` 里有个 `.strip()`，它把**空标题行**吃掉了，
    于是"计数行在第 4 行"的整体错位，读自己刚导出的文件会报
    "molblock 的计数行读不出来（第 4 行）"。

    molblock 规范里标题行可以为空 —— 这是合法文件，不是畸形输入。
    """

    def test_exported_empty_title_starts_with_a_blank_line(self):
        from chemfigstudio import structure
        from chemfigstudio.skeleton import Skeleton

        block = structure.to_molblock(Skeleton.chain(3), title="")
        self.assertTrue(block.startswith("\n"),
                        "标题为空时第一行就该是空行：%r" % block[:40])

    def test_from_molblock_accepts_empty_title(self):
        from chemfigstudio import structure
        from chemfigstudio.skeleton import Skeleton

        block = structure.to_molblock(Skeleton.chain(3), title="")
        again = structure.from_molblock(block)
        self.assertEqual(len(again.skeleton.atoms), len(Skeleton.chain(3).atoms))

    def test_parse_skeleton_input_must_not_strip_a_molblock(self):
        """这个断言直接盯着 strip 这个坑：首行空行必须保留下来。"""
        from chemfigstudio import structure
        from chemfigstudio.skeleton import Skeleton, parse_skeleton_input

        block = structure.to_molblock(Skeleton.chain(3), title="")
        parsed = parse_skeleton_input(block)
        self.assertEqual(len(parsed.atoms), len(Skeleton.chain(3).atoms))

    def test_looks_like_molblock_rejects_json(self):
        """JSON 骨架里有 `M  END` 这种字样也不能被当成 molblock。"""
        from chemfigstudio import structure
        from chemfigstudio.skeleton import Skeleton

        payload = Skeleton.chain(3).to_json()
        self.assertFalse(structure.looks_like_molblock(payload))

    def test_looks_like_molblock_accepts_our_own_output(self):
        from chemfigstudio import structure
        from chemfigstudio.skeleton import Skeleton

        self.assertTrue(structure.looks_like_molblock(
            structure.to_molblock(Skeleton.chain(3))))
