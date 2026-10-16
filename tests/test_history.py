"""编辑会话测试：多标签、撤销/重做、图层进历史（、5、6）。"""

from __future__ import annotations
import unittest

from chemfigstudio.document import Document
from chemfigstudio.history import DocumentSession, HISTORY_LIMIT


class TabTests(unittest.TestCase):
    def test_new_session_has_active_tab(self):
        session = DocumentSession()
        tab_id = session.new_tab(Document.new("alcohol", {"smiles": "CCO"}))
        self.assertEqual(session.active_id, tab_id)
        self.assertEqual(session.active.document.render_target()[0], "alcohol")

    def test_多个标签互不影响(self):
        session = DocumentSession()
        first = session.new_tab(Document.new("alcohol", {"smiles": "CCO"}), title="A")
        second = session.new_tab(Document.new("series", {}), title="B")
        session.set_figure("chcount", {"n_carbons": 6}, tab_id=first)
        self.assertEqual(session.tab(first).document.render_target()[0], "chcount")
        self.assertEqual(session.tab(second).document.render_target()[0], "series")

    def test_close_tab_selects_neighbour(self):
        session = DocumentSession()
        first = session.new_tab(Document.new("alcohol", {}))
        second = session.new_tab(Document.new("series", {}))
        self.assertTrue(session.close_tab(second))
        self.assertEqual(session.active_id, first)
        self.assertTrue(session.close_tab(first))
        self.assertIsNone(session.active_id)
        self.assertFalse(session.close_tab("nope"))

    def test_select_unknown_tab_is_rejected(self):
        session = DocumentSession()
        session.new_tab()
        self.assertFalse(session.select("missing"))


class UndoRedoTests(unittest.TestCase):
    def setUp(self):
        self.session = DocumentSession()
        self.tab = self.session.new_tab(Document.new("alcohol", {"smiles": "CCO"}))

    def test_undo_redo_single_step(self):
        self.session.set_figure("series", {})
        self.assertTrue(self.session.can_undo(self.tab))
        self.assertTrue(self.session.undo(self.tab))
        self.assertEqual(self.session.tab(self.tab).document.render_target()[0], "alcohol")
        self.assertTrue(self.session.can_redo(self.tab))
        self.assertTrue(self.session.redo(self.tab))
        self.assertEqual(self.session.tab(self.tab).document.render_target()[0], "series")

    def test_at_least_50_undo_steps(self):
        """至少 50 步撤销。这里做 60 步。"""
        for index in range(60):
            self.session.set_figure("chcount", {"n_carbons": 1 + index % 12})
        self.assertGreaterEqual(self.session.history_depth(self.tab), 51)
        for _ in range(60):
            self.session.undo(self.tab)
        self.assertEqual(self.session.tab(self.tab).document.render_target()[0], "alcohol")
        for _ in range(60):
            self.session.redo(self.tab)
        # 末次编辑 index=59 → n_carbons = 1 + 59 % 12 = 12
        self.assertEqual(self.session.tab(self.tab).document.figure["params"]["n_carbons"], 12)

    def test_undo_at_bottom_is_noop(self):
        self.assertFalse(self.session.can_undo(self.tab))
        self.assertFalse(self.session.undo(self.tab))
        self.assertTrue(self.session.can_redo(self.tab) is False)

    def test_new_edit_after_undo_drops_redo_branch(self):
        self.session.set_figure("series", {})
        self.session.set_figure("chcount", {})
        self.session.undo(self.tab)
        self.session.set_figure("reaction", {})
        self.assertFalse(self.session.can_redo(self.tab))
        self.assertEqual(self.session.tab(self.tab).document.render_target()[0], "reaction")

    def test_history_is_capped(self):
        for index in range(HISTORY_LIMIT + 20):
            self.session.set_figure("alcohol", {"n_carbons": 1 + index % 12})
        self.assertLessEqual(self.session.history_depth(self.tab), HISTORY_LIMIT)


class LayerHistoryTests(unittest.TestCase):
    """图层操作（增/隐藏）必须可撤销。"""

    def setUp(self):
        self.session = DocumentSession()
        self.tab = self.session.new_tab()

    def test_add_layer_is_undoable(self):
        self.session.add_layer("labels", tab_id=self.tab)
        self.assertEqual(len(self.session.tab(self.tab).document.layers), 2)
        self.session.undo(self.tab)
        self.assertEqual(len(self.session.tab(self.tab).document.layers), 1)
        self.session.redo(self.tab)
        self.assertEqual(len(self.session.tab(self.tab).document.layers), 2)

    def test_hide_layer_is_undoable_and_affects_visible(self):
        self.session.add_layer("labels", tab_id=self.tab)
        self.session.toggle_layer("L2", False, tab_id=self.tab)
        document = self.session.tab(self.tab).document
        self.assertEqual([l["id"] for l in document.visible_layers()], ["L1"])
        self.session.undo(self.tab)
        self.assertEqual(len(self.session.tab(self.tab).document.visible_layers()), 2)


class SnapshotTests(unittest.TestCase):
    """ 的崩溃恢复地基：会话可序列化并原样恢复。"""

    def test_snapshot_restore_roundtrip(self):
        session = DocumentSession()
        first = session.new_tab(Document.new("alcohol", {"smiles": "CCCCO"}), title="A")
        session.set_figure("series", {}, tab_id=first)
        session.new_tab(Document.new("chcount", {"n_carbons": 5}), title="B")

        restored = DocumentSession.restore(session.snapshot())
        self.assertEqual(len(restored.tabs), 2)
        self.assertEqual(restored.active_id, session.active_id)
        self.assertEqual(restored.tab(first).document.render_target()[0], "series")
        self.assertTrue(restored.can_undo(first))
        self.assertTrue(restored.undo(first))
        self.assertEqual(restored.tab(first).document.render_target()[0], "alcohol")

    def test_restore_of_empty_state_is_safe(self):
        session = DocumentSession.restore({})
        self.assertEqual(session.tabs, [])
        self.assertIsNone(session.active)
        self.assertIsNone(session.active_id)

    def test_as_dict_reports_tabs(self):
        session = DocumentSession()
        session.new_tab(Document.new("alcohol", {}), title="Hello")
        payload = session.as_dict()
        self.assertEqual(len(payload["tabs"]), 1)
        self.assertEqual(payload["tabs"][0]["label"], "Hello")
        self.assertFalse(payload["tabs"][0]["canUndo"])


if __name__ == "__main__":
    unittest.main()


class RenameTabTests(unittest.TestCase):
    """标签改名：只影响显示，不进历史栈。"""

    def setUp(self):
        self.session = DocumentSession()
        self.tab = self.session.new_tab(Document.new("alcohol", {}), title="A")

    def test_rename_changes_label_only(self):
        depth = self.session.history_depth(self.tab)
        self.assertTrue(self.session.rename_tab(self.tab, "丁醇 插图"))
        self.assertEqual(self.session.tab(self.tab).label(), "丁醇 插图")
        self.assertEqual(self.session.history_depth(self.tab), depth, "改名不该进历史")

    def test_rename_trims_and_truncates(self):
        self.session.rename_tab(self.tab, "  " + "x" * 80 + "  ")
        self.assertEqual(len(self.session.tab(self.tab).title), 60)

    def test_empty_title_falls_back_to_kind(self):
        self.session.rename_tab(self.tab, "   ")
        self.assertEqual(self.session.tab(self.tab).label(), "alcohol")

    def test_unknown_tab_is_rejected(self):
        self.assertFalse(self.session.rename_tab("nope", "x"))

    def test_rename_survives_snapshot_restore(self):
        self.session.rename_tab(self.tab, "名字")
        restored = DocumentSession.restore(self.session.snapshot())
        self.assertEqual(restored.tab(self.tab).title, "名字")
