"""阶段⑤ 骨架模型：编辑操作、命中测试、序列化、渲染（可编辑画布的数据层）。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from chemfigstudio import figures
from chemfigstudio.errors import RenderError
from chemfigstudio.skeleton import Skeleton


class SkeletonTestCase(unittest.TestCase):
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


class EditTests(SkeletonTestCase):
    def test_add_move_remove_atom(self):
        skeleton = Skeleton()
        carbon = skeleton.add_atom("C", 10, 20)
        oxygen = skeleton.add_atom("O", 100, 20, label="OH")
        self.assertEqual(len(skeleton.atoms), 2)
        skeleton.move_atom(carbon.id, 55, 66)
        self.assertEqual((carbon.x, carbon.y), (55, 66))
        self.assertTrue(skeleton.remove_atom(oxygen.id))
        self.assertFalse(skeleton.remove_atom("nope"))
        with self.assertRaises(RenderError):
            skeleton.move_atom("nope", 0, 0)

    def test_unsupported_element_is_rejected(self):
        with self.assertRaises(RenderError) as ctx:
            Skeleton().add_atom("Xx", 0, 0)
        self.assertIn("unsupported element", str(ctx.exception))

    def test_bonds_and_order(self):
        skeleton = Skeleton()
        a = skeleton.add_atom("C", 0, 0)
        b = skeleton.add_atom("O", 90, 0)
        skeleton.add_bond(a.id, b.id, order=2)
        self.assertEqual(skeleton.find_bond(a.id, b.id).order, 2)
        with self.assertRaises(RenderError):
            skeleton.add_bond(a.id, b.id)                     # 重复键
        with self.assertRaises(RenderError):
            skeleton.add_bond(a.id, a.id)                     # 自环
        with self.assertRaises(RenderError):
            skeleton.add_bond(a.id, "missing")
        self.assertTrue(skeleton.remove_bond(a.id, b.id))
        self.assertFalse(skeleton.remove_bond(a.id, b.id))

    def test_removing_atom_drops_its_bonds(self):
        skeleton = Skeleton()
        a = skeleton.add_atom("C", 0, 0)
        b = skeleton.add_atom("C", 90, 0)
        skeleton.add_bond(a.id, b.id)
        skeleton.remove_atom(a.id)
        self.assertEqual(skeleton.bonds, [], "删原子必须连带删键，不能留悬空键")

    def test_hit_test_picks_nearest_within_radius(self):
        skeleton = Skeleton()
        near = skeleton.add_atom("C", 100, 100)
        skeleton.add_atom("C", 400, 400)
        self.assertEqual(skeleton.hit_test(105, 104).id, near.id)
        self.assertIsNone(skeleton.hit_test(250, 250), "超出半径不该命中")
        self.assertIsNone(skeleton.hit_test(105, 104, radius=3.0))

    def test_bounds_follow_atoms(self):
        skeleton = Skeleton()
        skeleton.add_atom("C", 10, 20)
        skeleton.add_atom("O", 110, 220)
        self.assertEqual(skeleton.bounds(), (10.0, 20.0, 110.0, 220.0))


class SerialisationTests(SkeletonTestCase):
    def test_roundtrip(self):
        skeleton = Skeleton.chain(3)
        skeleton.move_atom(skeleton.atoms[1].id, 250, 240)
        again = Skeleton.from_json(skeleton.to_json())
        self.assertEqual(len(again.atoms), len(skeleton.atoms))
        self.assertEqual(len(again.bonds), len(skeleton.bonds))
        self.assertEqual((again.atoms[1].x, again.atoms[1].y), (250.0, 240.0))

    def test_chain_defaults_to_alcohol_layout(self):
        skeleton = Skeleton.chain(4)
        self.assertEqual(len(skeleton.atoms), 5)               # 4 个碳 + 1 个氧
        self.assertEqual(skeleton.atoms[-1].element, "O")
        self.assertEqual(skeleton.atoms[-1].label, "OH")
        self.assertEqual(len(skeleton.bonds), 4)

    def test_dangling_bonds_in_json_are_dropped(self):
        raw = {"atoms": [{"id": "a", "element": "C", "x": 0, "y": 0}],
               "bonds": [{"a": "a", "b": "ghost"}]}
        skeleton = Skeleton.from_dict(raw)
        self.assertEqual(skeleton.bonds, [], "指向不存在原子的键应被忽略")

    def test_invalid_json_is_rejected(self):
        with self.assertRaises(RenderError):
            Skeleton.from_json("{not json")

    def test_empty_json_is_empty_skeleton(self):
        self.assertEqual(Skeleton.from_json("").atoms, [])


class RenderTests(SkeletonTestCase):
    def render(self, skeleton, name="sk.png"):
        return figures.skeleton_figure(str(self.tmp / name), skeleton=skeleton,
                                       formats=("png",), dpi=150)

    def test_renders_default_chain(self):
        result = self.render("")
        self.assertTrue(Path(result.report.outputs["png"]).exists())
        self.assertEqual(result.warnings, [])

    def test_moving_an_atom_changes_the_picture(self):
        """真实行为检查：挪动一个原子必须改变输出像素（而不只是改了数据）。"""
        skeleton = Skeleton.chain(3)
        first = self.render(skeleton.to_json(), "before.png")
        skeleton.move_atom(skeleton.atoms[1].id, skeleton.atoms[1].x, 420)
        second = self.render(skeleton.to_json(), "after.png")
        before_bytes = Path(first.report.outputs["png"]).read_bytes()
        after_bytes = Path(second.report.outputs["png"]).read_bytes()
        self.assertNotEqual(before_bytes, after_bytes, "挪原子后画面竟然没变")

    def test_double_bond_renders(self):
        """双键要能画出来，而且**不能有价键告警**。

        这里曾经写的是"chain(2) 再补一根 C=O 双键"—— 那会做出一个氧有 3 根键的
        分子（C–O 单键 + C=O 双键），化学上是错的；价键检查上线后立刻被抓到。
        改成把已有的 C–C 键升为双键：C1 键级和 2、C2 是 3、O 是 1，全部合法。
        """
        skeleton = Skeleton.chain(2)
        skeleton.set_bond_order(skeleton.atoms[0].id, skeleton.atoms[1].id, 2)
        result = self.render(skeleton.to_json(), "double.png")
        self.assertEqual(result.warnings, [])

    def test_svg_is_vector_text(self):
        result = figures.skeleton_figure(str(self.tmp / "sk.svg"), skeleton="",
                                         formats=("svg",), dpi=150)
        text = Path(result.report.outputs["svg"]).read_text(encoding="utf-8")
        self.assertIn("<text", text)
        self.assertNotIn("<image", text)


if __name__ == "__main__":
    unittest.main()


class SnappingTests(SkeletonTestCase):
    """阶段⑤ 进阶：键角 30° 吸附、键长吸到 92 的倍数。"""

    def setUp(self):
        super().setUp()
        self.skeleton = Skeleton()
        self.carbon = self.skeleton.add_atom("C", 100.0, 100.0)

    def test_near_horizontal_snaps_to_zero_degrees_and_one_bond(self):
        x, y = self.skeleton.snap_position(200, 105, reference=self.carbon.id)
        self.assertEqual((x, y), (192.0, 100.0))          # 100 + 92*cos0

    def test_thirty_degrees_and_two_bond_lengths(self):
        import math
        x, y = self.skeleton.snap_position(280, 190, reference=self.carbon.id)
        self.assertAlmostEqual(x, 100 + 184 * math.cos(math.radians(30)), places=6)
        self.assertAlmostEqual(y, 100 + 184 * math.sin(math.radians(30)), places=6)

    def test_no_reference_returns_input_unchanged(self):
        self.assertEqual(self.skeleton.snap_position(500, 500), (500.0, 500.0))

    def test_reference_is_auto_picked_when_not_given(self):
        x, y = self.skeleton.snap_position(195, 102)      # 附近有碳
        self.assertEqual((x, y), (192.0, 100.0))

    def test_snapped_position_is_idempotent(self):
        first = self.skeleton.snap_position(280, 190, reference=self.carbon.id)
        second = self.skeleton.snap_position(first[0], first[1], reference=self.carbon.id)
        self.assertEqual(tuple(round(v, 6) for v in first), tuple(round(v, 6) for v in second))


class BondHitAndOrderTests(SkeletonTestCase):
    def setUp(self):
        super().setUp()
        self.skeleton = Skeleton.chain(3)
        self.bond = self.skeleton.bonds[0]
        self.first = self.skeleton.atom(self.bond.a)
        self.second = self.skeleton.atom(self.bond.b)

    def midpoint(self):
        return ((self.first.x + self.second.x) / 2, (self.first.y + self.second.y) / 2)

    def test_hit_test_bond_finds_segment(self):
        x, y = self.midpoint()
        self.assertIs(self.skeleton.hit_test_bond(x, y), self.bond)
        self.assertIsNone(self.skeleton.hit_test_bond(900, 900))

    def test_hit_test_bond_respects_tolerance(self):
        x, y = self.midpoint()
        self.assertIsNone(self.skeleton.hit_test_bond(x, y + 40, tolerance=12.0))

    def test_bond_hit_uses_segment_not_infinite_line(self):
        """延长线方向上的点不该命中（否则点到线的判定就写错了）。"""
        dx, dy = self.second.x - self.first.x, self.second.y - self.first.y
        self.assertIsNone(self.skeleton.hit_test_bond(self.second.x + dx, self.second.y + dy))

    def test_set_and_cycle_bond_order(self):
        self.assertEqual(self.bond.order, 1)
        self.assertEqual([self.skeleton.cycle_bond_order(self.bond.a, self.bond.b)
                          for _ in range(4)], [2, 3, 1, 2])
        self.skeleton.set_bond_order(self.bond.a, self.bond.b, 3)
        self.assertEqual(self.bond.order, 3)
        with self.assertRaises(RenderError):
            self.skeleton.set_bond_order(self.bond.a, "ghost", 2)

    def test_triple_bond_changes_the_picture(self):
        double = figures.skeleton_figure(str(self.tmp / "d.png"),
                                        skeleton=self.skeleton.to_json(), formats=("png",),
                                        dpi=150)
        self.skeleton.set_bond_order(self.bond.a, self.bond.b, 3)
        triple = figures.skeleton_figure(str(self.tmp / "t.png"),
                                         skeleton=self.skeleton.to_json(), formats=("png",),
                                         dpi=150)
        self.assertNotEqual(Path(double.report.outputs["png"]).read_bytes(),
                            Path(triple.report.outputs["png"]).read_bytes(),
                            "三键与双键的画面必须不同")


class SceneTests(SkeletonTestCase):
    """阶段⑤ 收尾：多分子（图层）——模型 + 渲染 + 旧格式兼容。"""

    def test_single_scene_roundtrip(self):
        from chemfigstudio.skeleton import Scene
        scene = Scene.single(Skeleton.chain(3), name="A")
        again = Scene.from_json(scene.to_json())
        self.assertEqual(len(again.molecules), 1)
        self.assertEqual(again.names[0], "A")

    def test_old_bare_skeleton_json_still_parses(self):
        from chemfigstudio.skeleton import Scene
        scene = Scene.from_json(Skeleton.chain(2).to_json())      # 旧格式：裸骨架
        self.assertEqual(len(scene.molecules), 1)
        self.assertEqual(scene.current().atoms[-1].element, "O")

    def test_add_select_remove(self):
        from chemfigstudio.skeleton import Scene
        scene = Scene.single(Skeleton.chain(2), name="A")
        scene.add(Skeleton.chain(4), name="B")
        self.assertEqual(len(scene.molecules), 2)
        self.assertEqual(scene.active, 1)                          # 新建的成为当前
        self.assertTrue(scene.select(0))
        self.assertFalse(scene.select(9))
        self.assertEqual(scene.current().atoms[-1].label, "OH")
        self.assertTrue(scene.remove(1))
        self.assertFalse(scene.remove(0), "至少要留一个分子")

    def test_offsets_stack_vertically_without_overlap(self):
        from chemfigstudio.skeleton import MOLECULE_GAP, Scene
        scene = Scene.single(Skeleton.chain(3), name="A")
        scene.add(Skeleton.chain(3), name="B")
        (_dx, first_y), (_dx2, second_y) = scene.offsets()
        first = scene.molecules[0].bounds()
        second = scene.molecules[1].bounds()
        self.assertGreaterEqual((second[1] + second_y) - (first[3] + first_y),
                                MOLECULE_GAP - 1e-6, "两个分子之间要留出间距")
        self.assertLess(first_y, second_y)

    def test_multi_molecule_render_is_taller(self):
        from chemfigstudio.skeleton import Scene
        one = Scene.single(Skeleton.chain(3))
        two = Scene.single(Skeleton.chain(3), name="A")
        two.add(Skeleton.chain(3), name="B")
        single = figures.skeleton_figure(str(self.tmp / "one.png"), skeleton=one.to_json(),
                                        formats=("png",), dpi=150)
        pair = figures.skeleton_figure(str(self.tmp / "two.png"), skeleton=two.to_json(),
                                       formats=("png", "svg"), dpi=150)
        self.assertGreater(pair.size[1], single.size[1])
        self.assertEqual(pair.warnings, [])
        svg = Path(pair.report.outputs["svg"]).read_text(encoding="utf-8")
        self.assertIn("<text", svg)
        self.assertNotIn("<image", svg)

    def test_removing_a_molecule_changes_the_picture(self):
        from chemfigstudio.skeleton import Scene
        scene = Scene.single(Skeleton.chain(3), name="A")
        scene.add(Skeleton.chain(4), name="B")
        before = figures.skeleton_figure(str(self.tmp / "b.png"), skeleton=scene.to_json(),
                                        formats=("png",), dpi=150)
        scene.remove(1)
        after = figures.skeleton_figure(str(self.tmp / "a.png"), skeleton=scene.to_json(),
                                       formats=("png",), dpi=150)
        self.assertNotEqual(Path(before.report.outputs["png"]).read_bytes(),
                            Path(after.report.outputs["png"]).read_bytes())


class ElementSwitchingTests(SkeletonTestCase):
    """阶段⑤ 收尾：切换原子元素（默认标签跟着换，自定义标签不被覆盖）。"""

    def setUp(self):
        super().setUp()
        self.skeleton = Skeleton.chain(2)
        self.carbon = self.skeleton.atoms[0]

    def test_default_label_follows_the_element(self):
        self.assertEqual(self.carbon.label, "CH_{3}")
        self.skeleton.change_element(self.carbon.id, "O")
        self.assertEqual((self.carbon.element, self.carbon.label), ("O", "OH"))

    def test_custom_label_is_not_overwritten(self):
        self.skeleton.change_element(self.carbon.id, "N", label="NHR")
        self.skeleton.change_element(self.carbon.id, "S")
        self.assertEqual((self.carbon.element, self.carbon.label), ("S", "NHR"),
                         "用户自定义标签不该被元素切换覆盖")

    def test_explicit_label_wins(self):
        self.skeleton.change_element(self.carbon.id, "O", label="O^{-}")
        self.assertEqual(self.carbon.label, "O^{-}")

    def test_unsupported_element_and_atom_are_rejected(self):
        with self.assertRaises(RenderError):
            self.skeleton.change_element(self.carbon.id, "Xx")
        with self.assertRaises(RenderError):
            self.skeleton.change_element("ghost", "O")

    def test_switching_element_changes_the_picture(self):
        before = figures.skeleton_figure(str(self.tmp / "c.png"),
                                        skeleton=self.skeleton.to_json(), formats=("png",),
                                        dpi=150)
        self.skeleton.change_element(self.carbon.id, "O")
        after = figures.skeleton_figure(str(self.tmp / "o.png"),
                                        skeleton=self.skeleton.to_json(), formats=("png",),
                                        dpi=150)
        self.assertNotEqual(Path(before.report.outputs["png"]).read_bytes(),
                            Path(after.report.outputs["png"]).read_bytes(),
                            "换成氧原子后画面必须不同（颜色与标签都变了）")

    def test_element_labels_cover_all_supported_elements(self):
        from chemfigstudio.skeleton import ELEMENTS, ELEMENT_LABELS
        self.assertEqual(set(ELEMENTS), set(ELEMENT_LABELS))


class ValenceTests(unittest.TestCase):
    """价键检查（ROADMAP 金牌项 ③）：**超价要报，欠价不报**。

    为什么欠价不报：结构式里氢是隐含的 —— 一个只连 1 根键的碳其实是 CH₃，
    完全合法。如果按"必须正好 4 根键"去查，每个骨架碳都会误报。

    为什么不在 VALENCE 表里的元素不检查：配位化合物、自由基、非标准价态都真实存在，
    **宁可漏报也不误报**（误报会让人把这个功能关掉，然后它等于不存在）。
    """

    @staticmethod
    def star(n_bonds, element="C", order=1):
        """建一个中心原子连 n 根键的骨架，返回 (骨架, 中心原子)。"""
        from chemfigstudio.skeleton import Skeleton

        skeleton = Skeleton()
        centre = skeleton.add_atom(element, 0.0, 0.0)
        for index in range(n_bonds):
            other = skeleton.add_atom("H", 80.0 * (index + 1), 0.0)
            skeleton.add_bond(centre.id, other.id, order)
        return skeleton, centre

    # ------------------------------------------------------------- 正常分子

    def test_default_chain_is_clean(self):
        from chemfigstudio.skeleton import Skeleton
        self.assertEqual(Skeleton.chain(4).valence_errors(), [])

    def test_benzene_is_clean(self):
        from chemfigstudio.structure import from_smiles
        result = from_smiles("c1ccccc1")
        self.assertEqual(result.skeleton.valence_errors(), [])

    def test_aspirin_is_clean(self):
        """真实分子必须是干净的 —— 否则这功能只会制造噪声。"""
        from chemfigstudio import smiles as smiles_mod
        if not smiles_mod.rdkit_available():
            self.skipTest("没装 RDKit（可选依赖）")
        from chemfigstudio.structure import from_smiles
        result = from_smiles("CC(=O)Oc1ccccc1C(=O)O")
        self.assertEqual(result.skeleton.valence_errors(), [])

    # --------------------------------------------------------------- 超价

    def test_five_bonds_on_carbon_is_reported(self):
        skeleton, carbon = self.star(5)
        issues = skeleton.valence_errors()
        self.assertEqual(len(issues), 1, issues)
        issue = issues[0]
        self.assertEqual(issue.atom_id, carbon.id)
        self.assertEqual(issue.element, "C")
        self.assertEqual(issue.actual, 5)
        self.assertEqual(issue.allowed, 4)

    def test_four_bonds_on_carbon_is_fine(self):
        skeleton, _ = self.star(4)
        self.assertEqual(skeleton.valence_errors(), [])

    def test_bond_order_counts_towards_valence(self):
        """按**键级之和**算，不是按键数：三键(3) + 单键(1) + 单键(1) = 5，超价。

        （注意对手原子要选碳：给 H 连三键的话，H 自己就超价了 —— 那是测试写错，
          不是被测代码的问题。）
        """
        from chemfigstudio.skeleton import Skeleton

        skeleton = Skeleton()
        centre = skeleton.add_atom("C", 0.0, 0.0)
        for index, order in enumerate((3, 1, 1)):
            other = skeleton.add_atom("C", 80.0 * (index + 1), 0.0)
            skeleton.add_bond(centre.id, other.id, order)
        issues = skeleton.valence_errors()
        self.assertEqual(len(issues), 1, issues)
        self.assertEqual(issues[0].actual, 5)
        self.assertEqual(issues[0].element, "C")

    def test_two_double_bonds_on_carbon_is_reported(self):
        """=C= （两个双键）是 4，合法；再加一根单键就是 5，要报。"""
        from chemfigstudio.skeleton import Skeleton

        skeleton = Skeleton()
        centre = skeleton.add_atom("C", 0.0, 0.0)
        first = skeleton.add_atom("C", 80.0, 0.0)
        second = skeleton.add_atom("C", -80.0, 0.0)
        skeleton.add_bond(centre.id, first.id, 2)
        skeleton.add_bond(centre.id, second.id, 2)
        self.assertEqual(skeleton.valence_errors(), [], "两个双键正好 4，不该报")

        third = skeleton.add_atom("H", 0.0, 80.0)
        skeleton.add_bond(centre.id, third.id, 1)
        self.assertEqual(len(skeleton.valence_errors()), 1, "再加一根就 5 了")

    def test_oxygen_with_three_bonds_is_reported(self):
        skeleton, oxygen = self.star(3, element="O")
        issues = skeleton.valence_errors()
        self.assertEqual(len(issues), 1, issues)
        self.assertEqual(issues[0].allowed, 2)

    # --------------------------------------------------------------- 电荷

    def test_positive_charge_raises_the_limit(self):
        """铵根 NH₄⁺：N 带 4 根键是**合法**的。"""
        skeleton, nitrogen = self.star(4, element="N")
        self.assertEqual(len(skeleton.valence_errors()), 1,
                         "不带电的 N 有 4 根键应当报")
        nitrogen.charge = 1
        self.assertEqual(skeleton.valence_errors(), [],
                         "N⁺ 有 4 根键是铵根，合法")

    def test_negative_charge_lowers_the_limit(self):
        """O⁻ 只允许 1 根键（羟基负离子）。"""
        skeleton, oxygen = self.star(2, element="O")
        self.assertEqual(skeleton.valence_errors(), [], "中性 O 两根键合法")
        oxygen.charge = -1
        self.assertEqual(len(skeleton.valence_errors()), 1, "O⁻ 两根键超价")

    # ----------------------------------------------------- 不检查 / 不误报

    def test_unknown_element_is_not_checked(self):
        """不在 VALENCE 表里的元素一律不检查（配位化合物可能合法）。"""
        from chemfigstudio.skeleton import Atom, Bond, Skeleton

        atoms = [Atom(id="fe", element="Fe")]
        bonds = []
        for index in range(8):
            atoms.append(Atom(id="l%d" % index, element="N",
                              x=80.0 * (index + 1)))
            bonds.append(Bond(a="fe", b="l%d" % index))
        skeleton = Skeleton(atoms=atoms, bonds=bonds)
        self.assertEqual(skeleton.valence_errors(), [])

    def test_isolated_atom_is_fine(self):
        """孤立原子（0 根键）不报 —— 欠价不是错误。"""
        from chemfigstudio.skeleton import Skeleton

        skeleton = Skeleton()
        skeleton.add_atom("C", 0.0, 0.0)
        self.assertEqual(skeleton.valence_errors(), [])

    def test_issue_has_a_human_readable_message(self):
        skeleton, _ = self.star(6)
        issue = skeleton.valence_errors()[0]
        text = issue.text()
        self.assertIn("6", text)
        self.assertIn("4", text)
        self.assertIn("C", text)

    # ------------------------------------------------------------ 接线

    def test_import_note_mentions_valence_problem(self):
        """导入一个超价分子时，结果要有提示（不能静默画出化学错误）。"""
        from chemfigstudio.structure import from_molblock

        # 甲烷 + 5 根 H 键（V2000：1 个 C + 5 个 H = 6 原子，5 根单键）
        block = "\n".join([
            "overvalent", "     chemfigstudio-test", "",
            "  6  5  0  0  0  0  0  0  0  0999 V2000",
            "    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0",
            "    1.0000    0.0000    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0",
            "   -1.0000    0.0000    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0",
            "    0.0000    1.0000    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0",
            "    0.0000   -1.0000    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0",
            "    0.7000    0.7000    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0",
            "  1  2  1  0", "  1  3  1  0", "  1  4  1  0", "  1  5  1  0", "  1  6  1  0",
            "M  END", ""])
        result = from_molblock(block, keep_hydrogens=True)
        self.assertTrue(result.skeleton.valence_errors())
        self.assertTrue(any("价" in note for note in result.notes),
                        "导入结果的 notes 里应当提到价键问题：%s" % result.notes)

    def test_render_warns_about_valence_problem(self):
        """出图时也要警告 —— GUI/CLI 都靠 SaveReport.warnings 看到问题。"""
        import tempfile
        from pathlib import Path

        from chemfigstudio import figures

        skeleton, _ = self.star(5)
        with tempfile.TemporaryDirectory() as tmp:
            result = figures.skeleton_figure(
                str(Path(tmp) / "bad.png"), skeleton=skeleton.to_json(),
                formats=("png",), dpi=150)
        self.assertTrue(any("价" in warning for warning in result.warnings),
                        "出图时应当警告价键问题：%s" % result.warnings)

    def test_clean_molecule_still_has_no_warnings(self):
        """反向确认：干净分子不能被这套检查弄出告警来。"""
        import tempfile
        from pathlib import Path

        from chemfigstudio import figures
        from chemfigstudio.skeleton import Skeleton

        with tempfile.TemporaryDirectory() as tmp:
            result = figures.skeleton_figure(
                str(Path(tmp) / "ok.png"), skeleton=Skeleton.chain(5).to_json(),
                formats=("png",), dpi=150)
        self.assertEqual(result.warnings, [])


class AromaticRingTests(unittest.TestCase):
    """芳香环圆圈（可选，默认关闭）。

    判定是**拓扑启发式**（零依赖）：环长 5–7，且环内每个原子至少参与 1 根双键
    （环内或环外都算 —— 否则萘只能有半个环满足）。
    """

    @classmethod
    def setUpClass(cls):
        from chemfigstudio import smiles as smiles_mod
        cls.available = smiles_mod.rdkit_available()

    def setUp(self):
        if not self.available:
            self.skipTest("没装 RDKit（要从 SMILES 造分子）")

    @staticmethod
    def rings_of(smiles):
        from chemfigstudio.structure import from_smiles
        return from_smiles(smiles).skeleton.aromatic_rings()

    # ------------------------------------------------------------ 判定

    def test_benzene_has_one_ring(self):
        self.assertEqual(len(self.rings_of("c1ccccc1")), 1)

    def test_pyridine_has_one_ring(self):
        self.assertEqual(len(self.rings_of("c1ccncc1")), 1)

    def test_naphthalene_has_two_rings(self):
        """萘的两个环只有一个是完全交替的，但两个都该画 —— 见 aromatic_rings 的说明。"""
        self.assertEqual(len(self.rings_of("c1ccc2ccccc2c1")), 2)

    def test_cyclopentadiene_is_not_aromatic(self):
        """五元环但有一个 sp3 碳不参与双键 → 不是芳香环 → 不画。"""
        self.assertEqual(self.rings_of("C1=CC=CC1"), [])

    def test_pyrrole_stays_kekule(self):
        """吡咯的五元环里杂原子不带双键 → 不画圆圈（课本也多用 Kekulé 式）。"""
        self.assertEqual(self.rings_of("c1cc[nH]c1"), [])

    def test_cyclooctatetraene_is_not_drawn(self):
        """8 元环超出窗口，而且它本来就是盆式非芳香。"""
        self.assertEqual(self.rings_of("C1=CC=CC=CC=C1"), [])

    def test_cyclohexane_and_cyclohexadiene_are_not_aromatic(self):
        for smiles in ("C1CCCCC1", "C1=CC=CCC1"):
            with self.subTest(smiles=smiles):
                self.assertEqual(self.rings_of(smiles), [])

    def test_aspirin_ring_is_found(self):
        self.assertEqual(len(self.rings_of("CC(=O)Oc1ccccc1C(=O)O")), 1)

    # ------------------------------------------------------------ 渲染

    def _render(self, skeleton, name, **kwargs):
        import tempfile
        from pathlib import Path

        from chemfigstudio import figures

        target = Path(self.__dict__.setdefault("_dir", tempfile.mkdtemp())) / name
        return figures.skeleton_figure(str(target), skeleton=skeleton,
                                       formats=("png",), dpi=150, **kwargs)

    def test_rings_off_by_default(self):
        """默认必须保持 Kekulé —— 这是既有输出风格，也是逐字节回归的基线。"""
        from chemfigstudio.structure import from_smiles
        from chemfigstudio import figures
        import tempfile
        from pathlib import Path

        payload = from_smiles("c1ccccc1").skeleton.to_json()
        with tempfile.TemporaryDirectory() as tmp:
            default = figures.skeleton_figure(str(Path(tmp) / "a.png"),
                                              skeleton=payload, formats=("png",), dpi=150)
            off = figures.skeleton_figure(str(Path(tmp) / "b.png"),
                                          skeleton=payload, formats=("png",), dpi=150,
                                          rings=False)
            # 必须在临时目录还活着时读 —— 否则文件已经没了（这个测试踩过）
            self.assertEqual(Path(default.report.outputs["png"]).read_bytes(),
                             Path(off.report.outputs["png"]).read_bytes())

    def test_rings_actually_change_the_picture(self):
        import tempfile
        from pathlib import Path

        from chemfigstudio import figures
        from chemfigstudio.structure import from_smiles

        payload = from_smiles("c1ccccc1").skeleton.to_json()
        with tempfile.TemporaryDirectory() as tmp:
            plain = figures.skeleton_figure(str(Path(tmp) / "a.png"), skeleton=payload,
                                            formats=("png",), dpi=150)
            circled = figures.skeleton_figure(str(Path(tmp) / "b.png"), skeleton=payload,
                                              formats=("png",), dpi=150, rings=True)
            self.assertNotEqual(Path(plain.report.outputs["png"]).read_bytes(),
                                Path(circled.report.outputs["png"]).read_bytes())

    def test_ring_bonds_are_drawn_as_single(self):
        """开圆圈时环内不能再画双键 —— 那等于把芳香性说了两遍（化学上错误）。

        用白盒断言（探针 _draw_bond）而不是比对像素：想验的是"传下去的是单键"
        这个行为，像素差异会被圆圈本身干扰，说不清。
        """
        import tempfile
        from pathlib import Path
        from unittest import mock

        from chemfigstudio import figures
        from chemfigstudio.structure import from_smiles

        payload = from_smiles("c1ccccc1").skeleton.to_json()
        seen = []
        original = figures._draw_bond

        def spy(draw, first, second, bond):
            seen.append(bond.order)
            return original(draw, first, second, bond)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(figures, "_draw_bond", spy):
                figures.skeleton_figure(str(Path(tmp) / "c.png"), skeleton=payload,
                                        formats=("png",), dpi=150, rings=True)

        self.assertNotIn(2, seen, "开了圆圈却还在画双键：%s" % seen)
        self.assertEqual(seen, [1] * 6, "苯环的 6 根键都该按单键画：%s" % seen)

    def test_api_exposes_the_rings_parameter(self):
        from chemfigstudio import api
        spec = api.FIGURES["skeleton"]
        names = [param.name for param in spec.params]
        self.assertIn("rings", names)


class LabelLayoutTests(unittest.TestCase):
    """标签排版：有标签的原子**不画圆点**，键缩到标签边缘。

    早期版本是"把圆点撑大到能包住文字" —— 看起来像气泡里写字，不像课本结构式。
    """

    @staticmethod
    def _surface(width=600, height=400):
        from chemfigstudio import core
        return core.new_surface(width, height)

    def _atom(self, element, label="", charge=0):
        from chemfigstudio.skeleton import Atom
        atom = Atom(id="a", element=element, label=label, x=100.0, y=100.0)
        atom.charge = charge
        return atom

    # ----------------------------------------------------------- 占位判断

    def test_bare_carbon_has_no_label_extent(self):
        from chemfigstudio import figures
        self.assertIsNone(figures._label_extent(self._surface(), self._atom("C")))

    def test_labelled_atom_has_an_extent(self):
        from chemfigstudio import figures
        extent = figures._label_extent(self._surface(), self._atom("O", "OH"))
        self.assertIsNotNone(extent)
        half_w, top, bottom = extent
        self.assertGreater(half_w, 0)
        self.assertLess(top, 0, "上边距应当是负数（在中心以上）")
        self.assertGreater(bottom, 0)

    def test_vertical_extent_is_not_symmetric(self):
        """文字被上移了 12 px，所以"上方要留的空白"比下方多。

        对称处理会让竖着的键穿到字里（踩过：``OH`` 上方那根线）。
        """
        from chemfigstudio import figures
        _half_w, top, bottom = figures._label_extent(self._surface(),
                                                     self._atom("O", "OH"))
        self.assertGreater(abs(top), bottom,
                           "上方留白 %s 应当大于下方 %s（文字上移过）" % (abs(top), bottom))

    # ------------------------------------------------------------- 缩键

    def test_shrink_moves_the_endpoint_towards_the_other_atom(self):
        from chemfigstudio import figures
        extent = (20.0, -14.0, 9.0)
        start = figures._shrink_to_label((100.0, 100.0), (300.0, 100.0), extent)
        self.assertGreater(start[0], 100.0, "应当朝另一个原子移动")
        self.assertAlmostEqual(start[1], 100.0, delta=0.01, msg="水平键不该改变 y")
        self.assertAlmostEqual(start[0], 120.0, delta=0.5,
                               msg="沿键方向正好走 half_w=20")

    def test_shrink_never_eats_the_whole_bond(self):
        """两个标签离得很近时，键不能被缩成反方向（那会画歪）。"""
        from chemfigstudio import figures
        extent = (1000.0, -1000.0, 1000.0)
        start = figures._shrink_to_label((100.0, 100.0), (110.0, 100.0), extent)
        self.assertLessEqual(start[0], 105.0 + 1e-6, "最多缩到一半")

    def test_shrink_uses_the_right_side_for_vertical_bonds(self):
        """向上的键用"上边距"，向下的键用"下边距" —— 不对称就是为这个。"""
        from chemfigstudio import figures
        extent = (20.0, -14.0, 9.0)
        up = figures._shrink_to_label((100.0, 100.0), (100.0, 0.0), extent)
        down = figures._shrink_to_label((100.0, 100.0), (100.0, 200.0), extent)
        self.assertAlmostEqual(100.0 - up[1], 14.0, delta=0.5, msg="向上缩 14")
        self.assertAlmostEqual(down[1] - 100.0, 9.0, delta=0.5, msg="向下缩 9")

    def test_no_extent_means_no_shrink(self):
        from chemfigstudio import figures
        self.assertEqual(figures._shrink_to_label((10.0, 20.0), (100.0, 20.0), None),
                         (10.0, 20.0))

    # ------------------------------------------------------------- 端到端

    def test_labelled_molecule_renders_clean(self):
        import tempfile
        from pathlib import Path

        from chemfigstudio import figures
        from chemfigstudio.skeleton import Skeleton

        skeleton = Skeleton()
        first = skeleton.add_atom("C", 100.0, 100.0)
        second = skeleton.add_atom("C", 200.0, 160.0)
        oxygen = skeleton.add_atom("O", 300.0, 100.0)
        skeleton.add_bond(first.id, second.id)
        skeleton.add_bond(second.id, oxygen.id)
        skeleton.atoms[2].label = "OH"

        with tempfile.TemporaryDirectory() as tmp:
            result = figures.skeleton_figure(str(Path(tmp) / "x.png"),
                                             skeleton=skeleton.to_json(),
                                             formats=("png",), dpi=150)
        self.assertEqual(result.warnings, [])
