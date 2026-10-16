"""CLI 测试：旧命令必须继续可用，新参数与错误码也要正确。"""

from __future__ import annotations

import _bootstrap  # noqa: F401  （测试隔离：见 tests/_bootstrap.py）
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from chemfigstudio import cli, i18n
from chemfigstudio import settings as settings_mod
from chemfigstudio.smiles import resolve_name, resolve_smiles


CJK = range(0x4E00, 0x9FFF)


def has_cjk(text: str) -> bool:
    return any(ord(ch) in CJK for ch in text or "")


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *argv):
        """跑一次 CLI，返回 (退出码, stdout, stderr)。"""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class BackwardCompatibilityTests(CliTestCase):
    """README 里记录的老命令，一个字都不能改。"""

    def test_legacy_alcohol(self):
        target = self.tmp / "butanol.png"
        code, out, _err = self.run_cli("alcohol", "--n", "4", "--name", "butan-1-ol",
                                       "--dh", "-36.1", "-o", str(target),
                                       "--vertex-labels", "h2")
        self.assertEqual(code, 0, out)
        self.assertTrue(target.exists())
        self.assertIn(str(target), out)          # 不断言语言，只断言路径

    def test_legacy_series_reaction_chcount(self):
        for cmd, name in (("series", "series.png"), ("reaction", "reaction.png"),
                          ("chcount", "chcount.png")):
            target = self.tmp / name
            code, out, _err = self.run_cli(cmd, "-o", str(target))
            self.assertEqual(code, 0, "%s 失败：%s" % (cmd, out))
            self.assertTrue(target.exists(), cmd)


class NewFeatureTests(CliTestCase):
    def test_smiles_sets_carbon_count(self):
        target = self.tmp / "by_smiles.png"
        code, out, _err = self.run_cli("alcohol", "--smiles", "CCCCO", "-o", str(target))
        self.assertEqual(code, 0, out)
        self.assertTrue(target.exists())

    def test_json_output(self):
        target = self.tmp / "json.png"
        code, out, _err = self.run_cli("alcohol", "--n", "3", "-o", str(target), "--json")
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertEqual(payload["kind"], "alcohol")
        self.assertTrue(Path(payload["path"]).exists())
        self.assertEqual(payload["warnings"], [])

    def test_outdir_default_filename(self):
        code, out, _err = self.run_cli("series", "--outdir", str(self.tmp))
        self.assertEqual(code, 0, out)
        self.assertTrue(any(p.suffix == ".png" for p in self.tmp.iterdir()))

    def test_doctor_runs(self):
        code, out, _err = self.run_cli("doctor")
        self.assertEqual(code, 0)
        self.assertIn("Pillow", out)
        self.assertIn("RDKit", out)


class ErrorHandlingTests(CliTestCase):
    def test_unknown_kind(self):
        code, _out, err = self.run_cli("nope", "-o", str(self.tmp / "x.png"))
        self.assertNotEqual(code, 0)

    def test_carbon_count_out_of_range(self):
        code, _out, err = self.run_cli("alcohol", "--n", "0", "-o", str(self.tmp / "x.png"))
        self.assertEqual(code, 2)
        self.assertIn("碳原子数", err)

    def test_smiles_not_an_alcohol(self):
        code, _out, err = self.run_cli("alcohol", "--smiles", "CCC(=O)O",
                                       "-o", str(self.tmp / "x.png"))
        self.assertEqual(code, 2)
        self.assertIn("SMILES", err)

    def test_unknown_param_via_api(self):
        from chemfigstudio import api
        with self.assertRaises(api.RenderError):
            api.render("alcohol", out=str(self.tmp / "x.png"), params={"bogus": 1})


class SmilesTests(unittest.TestCase):
    def test_linear_alcohol_builtin(self):
        info = resolve_smiles("CCCCO")
        self.assertEqual(info.n_carbons, 4)
        self.assertIn(info.name, ("butan-1-ol", ""))

    def test_name_lookup(self):
        self.assertEqual(resolve_name("butan-1-ol"), 4)
        self.assertEqual(resolve_name("1-butanol"), 4)
        self.assertEqual(resolve_name("Butanol"), 4)
        self.assertIsNone(resolve_name("unobtainium"))

    def test_rejects_two_oxygens(self):
        from chemfigstudio.errors import RenderError
        with self.assertRaises(RenderError):
            resolve_smiles("CCC(=O)O")


class LanguageTests(CliTestCase):
    """语言切换必须覆盖 CLI 提示与错误信息。"""

    def tearDown(self):
        i18n.set_language("auto")

    def test_english_help_has_no_chinese(self):
        code, out, err = self.run_cli("--lang", "en", "--help")
        self.assertEqual(code, 0)
        self.assertFalse(has_cjk(out + err), "英文 help 里还有中文")

    def test_chinese_help_is_chinese(self):
        code, out, _err = self.run_cli("--lang", "zh-CN", "--help")
        self.assertEqual(code, 0)
        self.assertTrue(has_cjk(out))

    def test_result_message_follows_language(self):
        code, out, _err = self.run_cli("--lang", "en", "alcohol", "--n", "3",
                                       "-o", str(self.tmp / "en.png"))
        self.assertEqual(code, 0)
        self.assertIn("Wrote", out)
        self.assertFalse(has_cjk(out))

    def test_error_message_follows_language(self):
        code, _out, err = self.run_cli("--lang", "en", "alcohol", "--n", "0",
                                       "-o", str(self.tmp / "x.png"))
        self.assertEqual(code, 2)
        self.assertIn("at least", err)
        self.assertFalse(has_cjk(err))

    def test_subcommand_language_flag(self):
        code, out, _err = self.run_cli("alcohol", "--lang", "en", "--n", "2",
                                       "-o", str(self.tmp / "en2.png"))
        self.assertEqual(code, 0)
        self.assertIn("Wrote", out)


class SettingsCommandTests(CliTestCase):
    """settings 子命令。"""

    def setUp(self):
        super().setUp()
        self._old_cfg = os.environ.get("CHEMFIG_CONFIG_DIR")
        os.environ["CHEMFIG_CONFIG_DIR"] = str(self.tmp / "cfg")

    def tearDown(self):
        if self._old_cfg is None:
            os.environ.pop("CHEMFIG_CONFIG_DIR", None)
        else:
            os.environ["CHEMFIG_CONFIG_DIR"] = self._old_cfg
        i18n.set_language("auto")
        super().tearDown()

    def test_change_and_read_back(self):
        code, out, _err = self.run_cli("settings", "--format", "svg", "--dpi", "450", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["defaultFormat"], "svg")
        self.assertEqual(payload["defaultDpi"], 450)
        self.assertTrue(Path(payload["_path"]).exists())

        again = settings_mod.Settings.load()
        self.assertEqual(again.get("defaultFormat"), "svg")

    def test_settings_text_output_is_localised(self):
        code, out, _err = self.run_cli("--lang", "en", "settings")
        self.assertEqual(code, 0)
        self.assertIn("Current settings", out)
        self.assertFalse(has_cjk(out))

    def test_reset_restores_defaults(self):
        self.run_cli("settings", "--format", "pdf")
        code, out, _err = self.run_cli("settings", "--reset", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["defaultFormat"], "png")

    def test_default_outdir_comes_from_settings(self):
        target = self.tmp / "from-settings"
        self.run_cli("settings", "--output-dir", str(target))
        code, out, _err = self.run_cli("alcohol", "--n", "3", "--json")
        self.assertEqual(code, 0, out)
        self.assertEqual(Path(json.loads(out)["path"]).parent, target)

    def test_unwritable_settings_dir_is_human_readable(self):
        """错误路径（契约 §9）：写不了设置文件 → 可读错误 + 退出码 3，不抛 traceback。"""
        os.environ["CHEMFIG_CONFIG_DIR"] = "/System/definitely-not-writable-chemfig"
        code, _out, err = self.run_cli("settings", "--format", "svg")
        self.assertEqual(code, 3)
        self.assertIn("settings.json", err)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
