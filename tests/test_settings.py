"""settings 测试：位置、默认值、未知字段保留、损坏自愈、原子写入、校验。"""

from __future__ import annotations

import _bootstrap  # noqa: F401  （测试隔离：见 tests/_bootstrap.py）
import json
import os
import tempfile
import unittest
from pathlib import Path

from chemfigstudio import settings as S


class SettingsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self._old = os.environ.get("CHEMFIG_CONFIG_DIR")
        os.environ["CHEMFIG_CONFIG_DIR"] = str(self.dir)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("CHEMFIG_CONFIG_DIR", None)
        else:
            os.environ["CHEMFIG_CONFIG_DIR"] = self._old
        self._tmp.cleanup()

    def write(self, payload):
        path = S.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path


class LocationTests(SettingsTestCase):
    def test_path_uses_env_override(self):
        self.assertEqual(S.settings_path(), self.dir / "settings.json")
        self.assertEqual(S.config_dir(), self.dir)

    def test_load_without_file_gives_defaults(self):
        obj = S.Settings.load()
        self.assertFalse(obj.loaded_from_disk)
        self.assertEqual(obj.get("defaultFormat"), "png")
        self.assertEqual(obj.get("defaultDpi"), 300)
        self.assertEqual(obj.get("language"), "auto")


class PersistenceTests(SettingsTestCase):
    def test_save_then_load_roundtrip(self):
        obj = S.Settings.load()
        obj.set("language", "en")
        obj.set("outputDir", str(self.dir / "out"))
        obj.set("transparent", True)
        path = obj.save()
        self.assertTrue(path.exists())

        again = S.Settings.load()
        self.assertTrue(again.loaded_from_disk)
        self.assertEqual(again.get("language"), "en")
        self.assertEqual(again.get("outputDir"), str(self.dir / "out"))
        self.assertIs(again.get("transparent"), True)

    def test_update_returns_changed_keys_and_saves_on_demand(self):
        obj = S.Settings.load()
        changed = obj.update({"defaultFormat": "svg", "defaultDpi": 300})
        self.assertEqual(changed, ["defaultFormat"])       # 值没变的键不算改动
        self.assertFalse(S.settings_path().exists())       # save=False 不落盘
        obj.update({"defaultDpi": 600}, save=True)
        self.assertEqual(S.Settings.load().get("defaultDpi"), 600)

    def test_unknown_fields_are_preserved(self):
        self.write({"language": "en", "futureFeature": {"a": 1}, "another": [1, 2]})
        obj = S.Settings.load()
        self.assertEqual(obj.get("language"), "en")
        self.assertEqual(obj.extra["futureFeature"], {"a": 1})
        obj.set("defaultDpi", 150)
        obj.save()
        raw = json.loads(S.settings_path().read_text(encoding="utf-8"))
        self.assertEqual(raw["futureFeature"], {"a": 1})   # 未知字段没被删
        self.assertEqual(raw["another"], [1, 2])
        self.assertEqual(raw["defaultDpi"], 150)

    def test_no_temp_files_left_behind(self):
        obj = S.Settings.load()
        obj.set("defaultDpi", 200)
        obj.save()
        leftovers = [p.name for p in self.dir.iterdir() if p.name.startswith(".settings-")]
        self.assertEqual(leftovers, [])


class RepairTests(SettingsTestCase):
    def test_corrupt_file_is_backed_up_and_defaulted(self):
        path = self.write({"language": "en"})
        path.write_text("{ this is not json", encoding="utf-8")
        obj = S.Settings.load()
        self.assertTrue(obj.repaired)
        self.assertEqual(obj.get("language"), "auto")      # 回默认值
        self.assertTrue(path.with_suffix(".json.bak").exists())

    def test_non_object_json_is_repaired(self):
        self.write([1, 2, 3])
        obj = S.Settings.load()
        self.assertTrue(obj.repaired)


class SanitizeTests(SettingsTestCase):
    def test_invalid_values_are_normalised(self):
        self.write({"defaultFormat": "TIFF", "defaultDpi": 99999,
                    "language": "klingon", "transparent": "yes",
                    "recentDocuments": "oops", "windowState": 5})
        obj = S.Settings.load()
        self.assertEqual(obj.get("defaultFormat"), "png")
        self.assertEqual(obj.get("defaultDpi"), 1200)      # 上限
        self.assertEqual(obj.get("language"), "auto")
        self.assertIs(obj.get("transparent"), True)
        self.assertEqual(obj.get("recentDocuments"), [])
        self.assertEqual(obj.get("windowState"), {})

    def test_dpi_lower_bound(self):
        self.write({"defaultDpi": 10})
        self.assertEqual(S.Settings.load().get("defaultDpi"), 72)


class ResolvedPathTests(SettingsTestCase):
    def test_output_dir_default_and_override(self):
        obj = S.Settings.load()
        self.assertEqual(obj.output_dir(), Path.home() / "Documents" / "chemfigstudio")
        custom = self.dir / "我的图"
        obj.set("outputDir", str(custom))
        self.assertEqual(obj.output_dir(), custom)

    def test_plugins_dir_default_and_override(self):
        obj = S.Settings.load()
        self.assertEqual(obj.plugins_dir(), self.dir / "plugins")
        obj.set("pluginsDir", str(self.dir / "myplugins"))
        self.assertEqual(obj.plugins_dir(), self.dir / "myplugins")

    def test_as_dict_exposes_resolved_paths(self):
        data = S.Settings.load().as_dict()
        self.assertIn("_path", data)
        self.assertIn("outputDir", data["_resolved"])


class RecentTests(SettingsTestCase):
    def test_add_recent_dedupes_and_limits(self):
        obj = S.Settings.load()
        for i in range(15):
            obj.add_recent("/doc-%d.cfgd.json" % i)
        obj.add_recent("/doc-5.cfgd.json")                 # 重复项应被提到最前
        items = obj.get("recentDocuments")
        self.assertEqual(len(items), 10)
        self.assertEqual(items[0], "/doc-5.cfgd.json")


if __name__ == "__main__":
    unittest.main()


class PlatformConfigDirTests(unittest.TestCase):
    """配置目录要跟着平台走 —— Windows 用户不该去 C:\\Users\\X\\.config 里找。"""

    def test_windows_uses_localappdata(self):
        import importlib
        import os
        from unittest import mock

        from chemfigstudio import settings as settings_mod

        with mock.patch.object(settings_mod.sys, "platform", "win32"):
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\x\AppData\Local"}):
                with mock.patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("CHEMFIG_CONFIG_DIR", None)
                    got = settings_mod.config_dir()
        self.assertIn("AppData", str(got))
        self.assertIn("chemfigstudio", str(got))
        self.assertNotIn(".config", str(got))

    def test_linux_keeps_xdg_location(self):
        from unittest import mock

        from chemfigstudio import settings as settings_mod

        with mock.patch.object(settings_mod.sys, "platform", "linux"):
            with mock.patch.dict("os.environ", {}, clear=False):
                import os

                os.environ.pop("CHEMFIG_CONFIG_DIR", None)
                got = settings_mod.config_dir()
        self.assertIn(".config", str(got))

    def test_override_wins_everywhere(self):
        from unittest import mock

        from chemfigstudio import settings as settings_mod

        with mock.patch.dict("os.environ", {"CHEMFIG_CONFIG_DIR": "/tmp/xyz"}):
            self.assertEqual(str(settings_mod.config_dir()), "/tmp/xyz")
