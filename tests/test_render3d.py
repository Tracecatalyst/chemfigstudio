"""render3d 的单元测试。

**重要约定**：仓库里**没有** RDKit（它是可选依赖，见 ADR-006/ADR-012），
所以本文件用 mock 验证 :func:`embed3d` 的**契约**（参数、选优、兜底、报错），
这样 ``make test`` 在任何机器上都全绿。

真构象（ETKDGv3 是否真给出非平面几何）用一条**端到端探针**验证，不进主闸门：

    CHEMFIG_PYLIBS=/path/to/rdkit-site PYTHONPATH=src python3 -c "
    from chemfigstudio import render3d
    c = render3d.embed3d('CC(O)CC')
    print(c.formula, render3d.molblock_has_3d(c.molblock))"

预期：``C4H10O True``（丁-2-醇的手性中心给出真三维坐标）。
"""

from __future__ import annotations

import unittest
from unittest import mock

import _bootstrap  # noqa: F401  （隔离用户设置，必须在其它 import 之前）

from chemfigstudio import render3d
from chemfigstudio.errors import ConfigError, RenderError

# 真实 molblock 片段（由 RDKit 2025.9.2 的 ETKDGv3 + MMFF 实际生成，不是手编）
# 注意第一行是**空行**（RDKit 不写分子名）—— 计数行固定在第 4 行，别"清理"掉它。
ETHANOL_BLOCK = """
     RDKit          3D

  3  2  0  0  0  0  0  0  0  0999 V2000
    0.8716    0.1993    0.1351 C   0  0  0  0  0  0  0  0  0  0  0  0
   -0.4606   -0.5156    0.0446 C   0  0  0  0  0  0  0  0  0  0  0  0
   -1.3264    0.1817   -0.8391 O   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0
  2  3  1  0
M  END
"""

#: 水：三个原子**本来就共面** —— z 全为 0，这是正确的几何，不是 bug
WATER_BLOCK = """
     RDKit          3D

  3  2  0  0  0  0  0  0  0  0999 V2000
    0.0010    0.3978    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0
   -0.7640   -0.1970    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0
    0.7630   -0.2008    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0
  1  3  1  0
M  END
"""


class MolblockHelpersTests(unittest.TestCase):
    """不依赖 RDKit 的纯文本解析（molblock 格式是公开标准）。"""

    def test_detects_nonzero_z(self):
        self.assertTrue(render3d.molblock_has_3d(ETHANOL_BLOCK))

    def test_planar_molecule_reports_no_z(self):
        """水共面 → z 全零，必须如实报 False（别把它当"3D 失败"）。"""
        self.assertFalse(render3d.molblock_has_3d(WATER_BLOCK))

    def test_counts_atoms_and_bonds(self):
        self.assertEqual(render3d.molblock_counts(ETHANOL_BLOCK), {"atoms": 3, "bonds": 2})
        self.assertEqual(render3d.molblock_counts(WATER_BLOCK), {"atoms": 3, "bonds": 2})

    def test_malformed_block_is_survivable(self):
        for bad in ("", "abc", "x\ny\nz\n", "   \n\n\nnot-a-counts-line\n"):
            with self.subTest(block=bad):
                self.assertIn(render3d.molblock_has_3d(bad), (True, False))
                self.assertEqual(render3d.molblock_counts(bad), {"atoms": 0, "bonds": 0})

    def test_format_name_matches_frontend(self):
        """前端用 addModel(text, format) —— 两边必须是同一个字符串。"""
        self.assertEqual(render3d.MOLBLOCK_FORMAT, "sdf")


class Conformer3DDataTests(unittest.TestCase):
    def test_as_dict_round_trip_shape(self):
        conf = render3d.Conformer3D(smiles="CCO", molblock=ETHANOL_BLOCK, atoms=9,
                                    heavy_atoms=3, formula="C2H6O", mw=46.07,
                                    energy=-1.34, method="ETKDGv3 + MMFF94")
        data = conf.as_dict()
        self.assertEqual(data["smiles"], "CCO")
        self.assertEqual(data["format"], "sdf")
        self.assertEqual(data["molblock"], ETHANOL_BLOCK)
        self.assertEqual(data["energy_unit"], "kcal/mol")
        self.assertEqual(data["source"], "rdkit")
        # 键名是前端契约，改名会静默破坏界面
        for key in ("molblock", "format", "atoms", "heavy_atoms", "formula", "mw",
                    "energy", "energy_unit", "method", "notes"):
            self.assertIn(key, data)

    def test_notes_default_is_independent_per_instance(self):
        first = render3d.Conformer3D(smiles="C", molblock=ETHANOL_BLOCK, atoms=5,
                                     heavy_atoms=1)
        second = render3d.Conformer3D(smiles="C", molblock=ETHANOL_BLOCK, atoms=5,
                                      heavy_atoms=1)
        first.notes.append("x")
        self.assertEqual(second.notes, [])


class MissingRdkitTests(unittest.TestCase):
    """没装 RDKit 时必须给**可执行的**指引，而不是一句"失败"。"""

    def test_embed3d_raises_config_error_with_install_hint(self):
        with mock.patch.object(render3d, "rdkit3d_available", return_value=False):
            with self.assertRaises(ConfigError) as ctx:
                render3d.embed3d("CCO")
        message = str(ctx.exception)
        self.assertIn("pip install", message)      # 用户能照着做
        self.assertIn("rdkit", message)

    def test_hint_mentions_target_dir(self):
        """提示里要给出 --target .pylibs（本项目既有的依赖安装姿势）。"""
        self.assertIn("--target .pylibs", render3d.RDKIT_3D_HINT)


# --------------------------------------------------------------------- 伪 RDKit

class _FakeConformer(object):
    pass


class _FakeMol(object):
    """够用的假分子：记录调用，返回可预期的能量/构象。"""

    def __init__(self, num_atoms=3, num_heavy=3):
        self._atoms = num_atoms
        self._heavy = num_heavy
        self.embeds = []

    def GetNumAtoms(self):
        return self._atoms

    def GetNumHeavyAtoms(self):
        return self._heavy

    def GetConformer(self, conf_id=0):
        self.last_conf_id = conf_id
        return _FakeConformer()


def _fake_rdkit_modules(energies=(1.0, -5.0, 3.0), embed_ids=(0, 1, 2),
                        embed_fail=False, single_fallback=0, optimize_raises=False):
    """造一整套假的 ``rdkit.*`` 模块，用来精确控制 embed3d 的分支。"""
    import sys
    import types

    calls = {"embed_multiple": [], "embed_single": [], "optimize": [], "molblock": []}

    chem = types.ModuleType("rdkit.Chem")

    def MolFromSmiles(text):
        calls["smiles"] = text
        return _FakeMol() if text and text != "BAD" else None

    def AddHs(mol):
        calls["addhs"] = True
        return mol

    def MolToMolBlock(mol, confId=0, kekulize=True):
        calls["molblock"].append({"confId": confId, "kekulize": kekulize})
        return ETHANOL_BLOCK

    chem.MolFromSmiles = MolFromSmiles
    chem.AddHs = AddHs
    chem.MolToMolBlock = MolToMolBlock

    allchem = types.ModuleType("rdkit.Chem.AllChem")

    class _Params(object):
        def __init__(self):
            self.randomSeed = None
            self.pruneRmsThresh = None
            self.numThreads = None

    def ETKDGv3():
        return _Params()

    def EmbedMultipleConfs(mol, numConfs=1, params=None):
        calls["embed_multiple"].append({"numConfs": numConfs,
                                        "seed": getattr(params, "randomSeed", None),
                                        "prune": getattr(params, "pruneRmsThresh", None),
                                        "threads": getattr(params, "numThreads", None)})
        return [] if embed_fail else list(embed_ids)

    def EmbedMolecule(mol, params=None):
        calls["embed_single"].append(True)
        return single_fallback

    def MMFFOptimizeMoleculeConfs(mol, numThreads=0):
        calls["optimize"].append(numThreads)
        if optimize_raises:
            raise RuntimeError("no MMFF parameters")
        return [(0, energy) for energy in energies]

    allchem.ETKDGv3 = ETKDGv3
    allchem.EmbedMultipleConfs = EmbedMultipleConfs
    allchem.EmbedMolecule = EmbedMolecule
    allchem.MMFFOptimizeMoleculeConfs = MMFFOptimizeMoleculeConfs

    chem.AllChem = allchem

    descriptors = types.ModuleType("rdkit.Chem.Descriptors")
    descriptors.MolWt = lambda mol: 46.07

    rdmol = types.ModuleType("rdkit.Chem.rdMolDescriptors")
    rdmol.CalcMolFormula = lambda mol: "C2H6O"

    rdkit = types.ModuleType("rdkit")
    saved = {}
    for name, module in (("rdkit", rdkit), ("rdkit.Chem", chem),
                         ("rdkit.Chem.AllChem", allchem),
                         ("rdkit.Chem.Descriptors", descriptors),
                         ("rdkit.Chem.rdMolDescriptors", rdmol)):
        saved[name] = sys.modules.get(name)
        sys.modules[name] = module
    return calls, saved


class Embed3DContractTests(unittest.TestCase):
    """用假 RDKit 验证 embed3d 的分支与参数，不依赖真实 RDKit。"""

    def setUp(self):
        self._patches = []

    def tearDown(self):
        for patcher in self._patches:
            patcher.stop()

    def _install(self, **kwargs):
        import sys
        calls, saved = _fake_rdkit_modules(**kwargs)
        self.addCleanup(lambda: [sys.modules.__setitem__(k, v) if v is not None
                                 else sys.modules.pop(k, None) for k, v in saved.items()])
        patcher = mock.patch.object(render3d, "rdkit3d_available", return_value=True)
        patcher.start()
        self._patches.append(patcher)
        return calls

    def test_picks_lowest_mmff_energy_conformer(self):
        """energies 与 conf_ids 同序：最低能量是 -5.0，对应 conf_id=1。"""
        calls = self._install(energies=(1.0, -5.0, 3.0), embed_ids=(0, 1, 2))
        conf = render3d.embed3d("CCO")
        self.assertEqual(conf.energy, -5.0)
        self.assertEqual(calls["molblock"][0]["confId"], 1)     # 选的正是最优那个
        self.assertTrue(calls["molblock"][0]["kekulize"])

    def test_seed_and_prune_are_passed_through(self):
        calls = self._install()
        render3d.embed3d("CCO", seed=999, prune_rms=0.25)
        record = calls["embed_multiple"][0]
        self.assertEqual(record["seed"], 999)
        self.assertEqual(record["prune"], 0.25)
        self.assertEqual(record["threads"], 0)                  # 0 = 全部核心

    def test_conformer_count_is_respected(self):
        calls = self._install()
        render3d.embed3d("CCO", conformers=3)
        self.assertEqual(calls["embed_multiple"][0]["numConfs"], 3)

    def test_falls_back_to_single_conformer(self):
        """多构象失败时退回单构象，并如实记一条 note。"""
        calls = self._install(embed_fail=True, single_fallback=7)
        conf = render3d.embed3d("CCO")
        self.assertTrue(calls["embed_single"])
        self.assertEqual(calls["molblock"][0]["confId"], 7)
        self.assertTrue(any("单构象" in note for note in conf.notes))

    def test_raises_when_even_single_conformer_fails(self):
        self._install(embed_fail=True, single_fallback=-1)
        with self.assertRaises(RenderError) as ctx:
            render3d.embed3d("CCO")
        self.assertIn("3D 构象", str(ctx.exception))

    def test_bad_smiles_raises_render_error(self):
        self._install()
        with self.assertRaises(RenderError):
            render3d.embed3d("BAD")

    def test_empty_smiles_raises_render_error(self):
        self._install()
        with self.assertRaises(RenderError):
            render3d.embed3d("   ")

    def test_survives_missing_force_field(self):
        """MMFF 不可用时应退回未优化构象，而不是整个功能失败。"""
        self._install(optimize_raises=True)
        conf = render3d.embed3d("CCO")
        self.assertEqual(conf.method, "ETKDGv3")                # 没有 + MMFF94
        self.assertTrue(any("MMFF" in note for note in conf.notes))

    def test_method_name_reports_optimization(self):
        self._install()
        self.assertEqual(render3d.embed3d("CCO").method, "ETKDGv3 + MMFF94")

    def test_optimise_can_be_disabled(self):
        calls = self._install()
        conf = render3d.embed3d("CCO", optimise=False)
        self.assertEqual(calls["optimize"], [])                 # 没调 MMFF
        self.assertEqual(conf.method, "ETKDGv3")
        self.assertEqual(conf.energy, 0.0)

    def test_notes_mention_conformer_count_when_several(self):
        self._install(embed_ids=(0, 1, 2))
        conf = render3d.embed3d("CCO")
        self.assertTrue(any("3 个构象" in note for note in conf.notes))

    def test_metadata_is_carried_through(self):
        self._install()
        conf = render3d.embed3d("CCO")
        self.assertEqual(conf.formula, "C2H6O")
        self.assertEqual(conf.mw, 46.07)
        self.assertEqual(conf.source, "rdkit")
        self.assertEqual(conf.molblock, ETHANOL_BLOCK)


class AvailabilityTests(unittest.TestCase):
    def test_availability_is_a_bool_not_an_exception(self):
        """可选依赖探测绝不能把异常抛给调用方。"""
        self.assertIsInstance(render3d.rdkit3d_available(), bool)

    def test_lib_candidates_include_vendor(self):
        """原生 .app 把 rdkit/numpy 内嵌在 Resources/vendor → 必须在候选里。"""
        from chemfigstudio import smiles
        names = [path.name for path in smiles._lib_candidates()]
        self.assertIn("vendor", names)
        self.assertIn(".pylibs", names)


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
