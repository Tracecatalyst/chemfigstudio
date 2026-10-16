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
        skeleton = Skeleton.chain(2)
        skeleton.add_bond(skeleton.atoms[0].id, skeleton.atoms[2].id, order=2)
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
