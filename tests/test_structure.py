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
