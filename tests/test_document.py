"""文档模型测试（契约 §5.1 / §8）：版本规则、未知字段、模式、图层、草稿。"""

from __future__ import annotations

import _bootstrap  # noqa: F401  （测试隔离：见 tests/_bootstrap.py）
import json
import os
import tempfile
import unittest
from pathlib import Path

from chemfigstudio import document as D
from chemfigstudio.errors import RenderError


class DocumentTestCase(unittest.TestCase):
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


class RoundTripTests(DocumentTestCase):
    def test_new_document_defaults(self):
        doc = D.Document.new("series", {"show_delta_h": True})
        self.assertEqual(doc.mode, "figure")
        self.assertEqual(doc.render_target(), ("series", {"show_delta_h": True}))
        self.assertEqual(len(doc.layers), 1)
        self.assertTrue(doc.layers[0]["visible"])

    def test_save_and_load_roundtrip(self):
        doc = D.Document.new("alcohol", {"smiles": "CCCCO"})
        doc.molecules.append({"id": "m1", "smiles": "CCCCO", "style": "condensed"})
        doc.style["scale"] = 1.5
        doc.export["defaultFormat"] = "svg"
        path = doc.save(self.tmp / "a.cfgd.json")
        again = D.Document.load(path)
        self.assertEqual(again.render_target(), ("alcohol", {"smiles": "CCCCO"}))
        self.assertEqual(again.molecules[0]["id"], "m1")
        self.assertEqual(again.style["scale"], 1.5)
        self.assertEqual(again.export["defaultFormat"], "svg")
        self.assertEqual(again.as_dict()["formatVersion"], D.FORMAT_VERSION)

    def test_unknown_fields_are_preserved(self):
        raw = {"formatVersion": 1, "mode": "figure",
               "figure": {"kind": "alcohol", "params": {}},
               "futureThing": {"nested": [1, 2]}}
        (self.tmp / "u.cfgd.json").write_text(json.dumps(raw), encoding="utf-8")
        doc = D.Document.load(self.tmp / "u.cfgd.json")
        self.assertEqual(doc.extra["futureThing"], {"nested": [1, 2]})
        doc.set_figure("chcount", {"n_carbons": 5})
        saved = json.loads(doc.save(self.tmp / "u2.cfgd.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["futureThing"], {"nested": [1, 2]})

    def test_missing_style_and_export_get_defaults(self):
        raw = {"formatVersion": 1, "figure": {"kind": "alcohol", "params": {}}}
        (self.tmp / "d.cfgd.json").write_text(json.dumps(raw), encoding="utf-8")
        doc = D.Document.load(self.tmp / "d.cfgd.json")
        self.assertEqual(doc.style["oxygenColour"], D.DEFAULT_STYLE["oxygenColour"])
        self.assertEqual(doc.export["dpi"], D.DEFAULT_EXPORT["dpi"])


class VersionTests(DocumentTestCase):
    def test_newer_format_version_is_rejected(self):
        raw = {"formatVersion": D.FORMAT_VERSION + 5, "figure": {"kind": "alcohol"}}
        (self.tmp / "v.cfgd.json").write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(RenderError) as ctx:
            D.Document.load(self.tmp / "v.cfgd.json")
        self.assertIn(str(D.FORMAT_VERSION + 5), str(ctx.exception))

    def test_corrupt_document_is_rejected_not_crashed(self):
        (self.tmp / "bad.cfgd.json").write_text("{ not json", encoding="utf-8")
        with self.assertRaises(RenderError):
            D.Document.load(self.tmp / "bad.cfgd.json")

    def test_missing_file_is_rejected(self):
        with self.assertRaises(RenderError):
            D.Document.load(self.tmp / "nope.cfgd.json")


class ModeAndLayerTests(DocumentTestCase):
    def test_molecule_and_figure_modes(self):
        single = D.Document.new("alcohol", {}, mode="molecule")
        self.assertEqual(single.mode, "molecule")
        figure = D.Document.new("series", {}, mode="figure")
        self.assertEqual(figure.mode, "figure")

    def test_apply_only_changes_document(self):
        """「应用」不落盘：调用后磁盘上没有新文件（/2 的模型层保证）。"""
        doc = D.Document.new("alcohol", {"smiles": "CCO"})
        before = set(self.tmp.iterdir())
        doc.set_figure("series", {})
        self.assertEqual(set(self.tmp.iterdir()), before)
        self.assertEqual(doc.render_target()[0], "series")

    def test_layers_toggle_and_visibility(self):
        doc = D.Document.new()
        doc.add_layer("labels")
        self.assertEqual(len(doc.layers), 2)
        doc.toggle_layer("L1", False)
        self.assertEqual([l["id"] for l in doc.visible_layers()], ["L2"])
        doc.toggle_layer("L1")
        self.assertTrue(doc.layers[0]["visible"])


class DraftTests(DocumentTestCase):
    def test_save_draft_goes_to_config_dir_not_output_dir(self):
        output_dir = self.tmp / "out"
        output_dir.mkdir()
        doc = D.Document.new("alcohol", {"smiles": "CCCCO"})
        path = doc.save_draft()
        self.assertTrue(path.exists())
        self.assertEqual(path.parent, D.drafts_dir())
        self.assertEqual(list(output_dir.iterdir()), [], "草稿不该写进输出目录")

    def test_list_and_load_drafts(self):
        first = D.Document.new("alcohol", {"smiles": "CCCCO"}).save_draft()
        second = D.Document.new("series", {}).save_draft()
        items = D.list_drafts()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["id"], second.name)          # 最新在前
        loaded = D.load_draft(first.name)
        self.assertEqual(loaded.render_target()[0], "alcohol")

    def test_load_draft_rejects_path_traversal(self):
        with self.assertRaises(RenderError):
            D.load_draft("../../etc/passwd")

    def test_list_drafts_skips_broken_files(self):
        D.Document.new("alcohol", {}).save_draft()
        (D.drafts_dir() / "broken.cfgd.json").write_text("{oops", encoding="utf-8")
        self.assertEqual(len(D.list_drafts()), 1)


if __name__ == "__main__":
    unittest.main()
