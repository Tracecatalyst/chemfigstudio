"""阶段④ 插件机制：Python 插件 / JSON 预置 / 坏插件不影响程序 / 内置不可覆盖。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from chemfigstudio import api, plugins


class PluginTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.plugins_dir = self.root / "plugins"
        self.plugins_dir.mkdir()
        self._old_cfg = os.environ.get("CHEMFIG_CONFIG_DIR")
        os.environ["CHEMFIG_CONFIG_DIR"] = str(self.root / "cfg")
        self._figures = dict(api.FIGURES)          # 快照，测试间互不影响
        self._old_plugins = os.environ.get("CHEMFIG_PLUGINS")
        os.environ["CHEMFIG_PLUGINS"] = str(self.plugins_dir)

    def tearDown(self):
        api.FIGURES.clear()
        api.FIGURES.update(self._figures)
        plugins._STATE["loaded"] = False
        plugins._STATE["infos"] = []
        if self._old_cfg is None:
            os.environ.pop("CHEMFIG_CONFIG_DIR", None)
        else:
            os.environ["CHEMFIG_CONFIG_DIR"] = self._old_cfg
        if self._old_plugins is None:
            os.environ.pop("CHEMFIG_PLUGINS", None)
        else:
            os.environ["CHEMFIG_PLUGINS"] = self._old_plugins
        self._tmp.cleanup()

    def write_plugin(self, name: str, body: str) -> Path:
        path = self.plugins_dir / name
        path.write_text(body, encoding="utf-8")
        return path

    def reload(self):
        return {info.name: info for info in plugins.load_all(force=True)}


class PythonPluginTests(PluginTestCase):
    BODY = '''
def register(api):
    def runner(out, n_carbons=3, formats=("png",), dpi=None, transparent=False, **_k):
        width, height = 300, 100
        surface = api.surface(width, height, scale=api.core.dpi_scale(width, height, dpi),
                              svg="svg" in formats)
        surface.text((10, 30), "C%d" % n_carbons, font=api.core.fonts(20, True),
                     fill=api.core.INK)
        report = api.core.save_figure(surface, out, formats=formats, dpi=dpi,
                                      transparent=transparent)
        return api.figures.FigureResult("demo", report)

    api.add_figure("demo_bar", runner, title="Demo", description="demo plugin",
                   params=[api.int_param("n_carbons", label="C", default=3, minimum=1,
                                         maximum=9)])
'''

    def test_python_plugin_registers_and_renders(self):
        self.write_plugin("demo.py", self.BODY)
        infos = self.reload()
        self.assertTrue(infos["demo.py"].ok, infos["demo.py"].error)
        self.assertEqual(infos["demo.py"].kinds, ["demo_bar"])
        self.assertIn("demo_bar", [f["kind"] for f in api.list_figures()])
        result = api.render("demo_bar", out=str(self.root / "demo.png"),
                            params={"n_carbons": 5}, formats=("png", "svg"), dpi=150)
        self.assertEqual(sorted(result.outputs), ["png", "svg"])

    def test_plugin_without_register_is_reported(self):
        self.write_plugin("bad.py", "x = 1\n")
        infos = self.reload()
        self.assertFalse(infos["bad.py"].ok)
        self.assertIn("register", infos["bad.py"].error)

    def test_broken_plugin_does_not_break_others(self):
        self.write_plugin("broken.py", "def register(api):\n    raise ValueError('boom')\n")
        self.write_plugin("demo.py", self.BODY)
        infos = self.reload()
        self.assertFalse(infos["broken.py"].ok)
        self.assertIn("boom", infos["broken.py"].error)
        self.assertTrue(infos["demo.py"].ok, "坏插件不该影响其它插件")
        self.assertIn("demo_bar", api.FIGURES)

    def test_syntax_error_is_caught(self):
        self.write_plugin("syntax.py", "def register(api)\n    pass\n")
        infos = self.reload()
        self.assertFalse(infos["syntax.py"].ok)

    def test_builtin_kind_cannot_be_overridden(self):
        self.write_plugin("evil.py", '''
def register(api):
    api.add_figure("alcohol", lambda out, **k: None, title="hijack")
''')
        infos = self.reload()
        self.assertFalse(infos["evil.py"].ok)
        self.assertIn("built-in", infos["evil.py"].error)
        # 内置图仍然正常
        result = api.render("alcohol", out=str(self.root / "a.png"), params={"n_carbons": 2},
                            formats=("png",), dpi=150)
        self.assertTrue(Path(result.outputs["png"]).exists())

    def test_duplicate_kind_between_plugins_is_rejected(self):
        self.write_plugin("one.py", self.BODY)
        self.write_plugin("two.py", self.BODY.replace("demo_bar", "demo_bar"))
        infos = self.reload()
        self.assertTrue(infos["one.py"].ok)
        self.assertFalse(infos["two.py"].ok)
        self.assertIn("already registered", infos["two.py"].error)

    def test_builtin_figures_come_first(self):
        self.write_plugin("demo.py", self.BODY)
        self.reload()
        kinds = [f["kind"] for f in api.list_figures()]
        self.assertEqual(kinds[:4], ["alcohol", "series", "reaction", "chcount"])
        self.assertIn("demo_bar", kinds[4:])


class JsonPresetTests(PluginTestCase):
    def test_preset_renders_with_defaults(self):
        (self.plugins_dir / "preset.figure.json").write_text(json.dumps({
            "kind": "my_preset", "base": "chcount", "title": "My preset",
            "params": {"n_carbons": 6},
        }), encoding="utf-8")
        infos = self.reload()
        self.assertTrue(infos["preset.figure.json"].ok, infos["preset.figure.json"].error)
        result = api.render("my_preset", out=str(self.root / "p.png"), params={},
                            formats=("png",), dpi=150)
        self.assertTrue(Path(result.outputs["png"]).exists())

    def test_unknown_base_is_rejected(self):
        (self.plugins_dir / "bad.figure.json").write_text(json.dumps(
            {"kind": "x", "base": "nope"}), encoding="utf-8")
        infos = self.reload()
        self.assertFalse(infos["bad.figure.json"].ok)
        self.assertIn("unknown", infos["bad.figure.json"].error)

    def test_invalid_json_is_rejected(self):
        (self.plugins_dir / "junk.figure.json").write_text("{not json", encoding="utf-8")
        infos = self.reload()
        self.assertFalse(infos["junk.figure.json"].ok)

    def test_missing_kind_or_base_is_rejected(self):
        (self.plugins_dir / "half.figure.json").write_text(json.dumps({"kind": "x"}),
                                                           encoding="utf-8")
        infos = self.reload()
        self.assertFalse(infos["half.figure.json"].ok)
        self.assertIn("base", infos["half.figure.json"].error)


class PluginDiscoveryTests(PluginTestCase):
    def test_plugin_report_shape(self):
        self.write_plugin("demo.py", PythonPluginTests.BODY)
        report = plugins.plugin_report()
        self.assertIn("dirs", report)
        self.assertTrue(any(str(self.plugins_dir) in d for d in report["dirs"]))
        self.assertIn("demo_bar", report["kinds"])
        self.assertEqual(report["errors"], [])

    def test_non_plugin_files_are_ignored(self):
        (self.plugins_dir / "notes.txt").write_text("hello", encoding="utf-8")
        (self.plugins_dir / "__pycache__").mkdir()
        infos = self.reload()
        self.assertNotIn("notes.txt", infos)
        self.assertNotIn("__pycache__", infos)

    def test_load_all_is_idempotent(self):
        self.write_plugin("demo.py", PythonPluginTests.BODY)
        first = plugins.load_all(force=True)
        second = plugins.load_all()
        self.assertEqual(len(first), len(second))


if __name__ == "__main__":
    unittest.main()
