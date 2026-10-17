"""CLI 测试：旧命令必须继续可用，新参数与错误码也要正确。"""

from __future__ import annotations

import _bootstrap  # noqa: F401  （测试隔离：见 tests/_bootstrap.py）
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from chemfigstudio import cli, i18n
from chemfigstudio import render3d as render3d_mod
from chemfigstudio import settings as settings_mod
from chemfigstudio.smiles import resolve_name, resolve_smiles


REPO = Path(__file__).resolve().parents[1]

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


class Render3DCommandTests(CliTestCase):
    """``render3d`` 子命令：出的是几何数据（molblock），不是位图。

    RDKit 是可选依赖，所以这里 mock 掉 :func:`render3d.embed3d`：
    测的是 CLI 自己的契约（退出码、写盘、json、参数透传）。
    """

    @staticmethod
    def _fake_conformer(smiles="CCO"):
        return render3d_mod.Conformer3D(
            smiles=smiles, molblock="BLOCK-LINE-1\nM  END\n", atoms=9, heavy_atoms=3,
            formula="C2H6O", mw=46.07, energy=-1.337, method="ETKDGv3 + MMFF94")

    def test_writes_sdf_file(self):
        target = self.tmp / "out" / "ethanol.sdf"          # 父目录不存在，应被自动创建
        with mock.patch.object(render3d_mod, "embed3d", return_value=self._fake_conformer()):
            code, out, _err = self.run_cli("render3d", "--smiles", "CCO", "-o", str(target))
        self.assertEqual(code, 0, out)
        self.assertTrue(target.exists())
        self.assertIn("M  END", target.read_text(encoding="utf-8"))
        self.assertIn(str(target), out)
        self.assertIn("C2H6O", out)                        # 摘要行带分子式

    def test_json_goes_to_stdout_without_writing_file(self):
        with mock.patch.object(render3d_mod, "embed3d", return_value=self._fake_conformer()):
            code, out, _err = self.run_cli("render3d", "--smiles", "CCO", "--json")
        self.assertEqual(code, 0, out)
        data = json.loads(out)
        self.assertEqual(data["format"], "sdf")
        self.assertEqual(data["molblock"].strip(), "BLOCK-LINE-1\nM  END".strip())
        self.assertEqual(data["energy"], -1.337)
        # --json 是给脚本吃的，不该在输出目录留下任何文件
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_missing_rdkit_is_environment_error(self):
        from chemfigstudio.errors import ConfigError
        with mock.patch.object(render3d_mod, "embed3d",
                               side_effect=ConfigError(render3d_mod.RDKIT_3D_HINT)):
            code, _out, err = self.run_cli("render3d", "--smiles", "CCO")
        self.assertEqual(code, 3)                          # 3 = 环境错误（不是用户参数错）
        self.assertIn("rdkit", err.lower())

    def test_bad_smiles_is_param_error(self):
        from chemfigstudio.errors import RenderError
        with mock.patch.object(render3d_mod, "embed3d",
                               side_effect=RenderError("RDKit 无法解析")):
            code, _out, err = self.run_cli("render3d", "--smiles", "!!!")
        self.assertEqual(code, 2)                          # 2 = 参数错误
        self.assertIn("RDKit", err)

    def test_flags_are_passed_through(self):
        seen = {}

        def fake_embed(smiles, **kwargs):
            seen["smiles"] = smiles
            seen.update(kwargs)
            return self._fake_conformer()

        with mock.patch.object(render3d_mod, "embed3d", side_effect=fake_embed):
            code, _out, _err = self.run_cli("render3d", "--smiles", "CCCCO",
                                            "--seed", "42", "--conformers", "3",
                                            "--no-optimise", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(seen["smiles"], "CCCCO")
        self.assertEqual(seen["seed"], 42)
        self.assertEqual(seen["conformers"], 3)
        self.assertFalse(seen["optimise"])

    def test_default_output_name_uses_formula(self):
        """不给 -o 时落到设置里的输出目录，文件名用分子式（可读、稳定）。"""
        target = self.tmp / "outdir"
        self.run_cli("settings", "--output-dir", str(target))
        with mock.patch.object(render3d_mod, "embed3d", return_value=self._fake_conformer()):
            code, out, _err = self.run_cli("render3d", "--smiles", "CCO")
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(p.name for p in target.iterdir()), ["C2H6O.sdf"])

    def test_doctor_reports_3d_capability(self):
        code, out, _err = self.run_cli("doctor")
        self.assertEqual(code, 0)
        self.assertIn(i18n.t("cli.doctor.rdkit3d"), out)


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

    def test_missing_oxygen_hint_is_install_independent(self):
        """「不是醇」的提示必须与装没装 RDKit 无关。

        踩过：内置路径会提示「甲醇的 SMILES 是 CO」，RDKit 路径却只报「0 个氧」——
        用户装了 RDKit 反而看到更差的提示。两条路径现在共用同一道前检。
        """
        from chemfigstudio import smiles as smiles_mod
        from chemfigstudio.errors import RenderError
        expected = smiles_mod._resolve_builtin
        with self.assertRaises(RenderError) as builtin_ctx:
            expected("C")
        hint = "甲醇的 SMILES 是 CO"
        self.assertIn(hint, str(builtin_ctx.exception))
        # RDKit 路径（装了才测）必须给出同一句提示，而不是只报原子数
        if smiles_mod.rdkit_available():
            with self.assertRaises(RenderError) as rdkit_ctx:
                smiles_mod._resolve_rdkit("C")
            self.assertIn(hint, str(rdkit_ctx.exception))


class LanguageTests(CliTestCase):
    """验收 16：语言切换必须覆盖 CLI 提示与错误信息。"""

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
    """settings 子命令（验收 15）。"""

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


class ConvertCommandTests(unittest.TestCase):
    """`chemfigstudio convert`：骨架 → molblock / SMILES / InChI（ADR-015）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def run_cli(self, *argv):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO / "src")
        return subprocess.run([sys.executable, "-m", "chemfigstudio.cli", "convert"]
                              + list(argv),
                              capture_output=True, text=True, env=env, timeout=180)

    def test_infers_format_from_the_extension(self):
        target = self.dir / "out.mol"
        result = self.run_cli("--skeleton", "CCO", "-o", str(target))
        self.assertEqual(result.returncode, 0, result.stderr)
        text = target.read_text(encoding="utf-8")
        self.assertIn("V2000", text)
        self.assertIn("M  END", text)

    def test_writes_canonical_smiles(self):
        target = self.dir / "out.smi"
        result = self.run_cli("--skeleton", "OCC", "-o", str(target))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_text(encoding="utf-8").strip(), "CCO",
                         "规范 SMILES 应当把 OCC 归一成 CCO")

    def test_writes_inchi(self):
        target = self.dir / "out.inchi"
        result = self.run_cli("--skeleton", "CCO", "-o", str(target))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_text(encoding="utf-8").strip(),
                         "InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3")

    def test_reads_molblock_from_a_file(self):
        """往返：导出 molblock → 再从文件读回来 → 导出 InChI。"""
        block = self.dir / "in.mol"
        first = self.run_cli("--skeleton", "c1ccccc1", "-o", str(block))
        self.assertEqual(first.returncode, 0, first.stderr)

        inchi = self.dir / "out.inchi"
        second = self.run_cli("--in", str(block), "-o", str(inchi))
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("InChI=1S/C6H6", inchi.read_text(encoding="utf-8"))

    def test_prints_to_stdout_without_an_output_file(self):
        result = self.run_cli("--skeleton", "CCO", "--format", "smiles")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "CCO")

    def test_without_input_is_a_usage_error(self):
        result = self.run_cli("-o", str(self.dir / "x.mol"))
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)

    def test_both_inputs_at_once_is_a_usage_error(self):
        result = self.run_cli("--in", str(self.dir / "a.mol"),
                              "--skeleton", "CCO")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)

    def test_unknown_extension_is_rejected(self):
        result = self.run_cli("--skeleton", "CCO", "-o", str(self.dir / "x.xyz"))
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)

    def test_broken_valence_gives_a_readable_error(self):
        """超价分子导出 InChI 时，错误要说清"先修价键"，而不是甩一个 None。"""
        from chemfigstudio.skeleton import Skeleton

        bad = Skeleton()
        carbon = bad.add_atom("C", 0.0, 0.0)
        for index in range(5):
            hydrogen = bad.add_atom("H", 80.0 * (index + 1), 0.0)
            bad.add_bond(carbon.id, hydrogen.id)
        result = self.run_cli("--skeleton", bad.to_json(),
                              "-o", str(self.dir / "x.inchi"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("价键", result.stderr)
