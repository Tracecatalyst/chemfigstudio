"""chemfigstudio.render3d — SMILES → 真 3D 构象（molblock）。

设计要点（与 :mod:`chemfigstudio.smiles` 同一套路子）：

* **不自己算 3D 几何**。构象生成是 RDKit 的强项（ETKDGv3 + MMFF），
  本项目只做"薄封装 + 友好报错 + 可复现的随机种子"，绝不手写距离几何/力场。
* **RDKit 是可选依赖**。装了就能出 3D；没装就抛 :class:`ConfigError` 并在
  消息里给出安装命令（``python3 -m pip install --target .pylibs rdkit numpy``）。
  原生 .app 会把 rdkit/numpy 内嵌进 ``Resources/vendor``，由
  :func:`smiles.ensure_rdkit_path` 自动发现。
* **只吐 molblock 文本**，不碰文件、不碰 HTTP、不打印 —— 渲染交给前端
  （3Dmol.js）或调用方，符合"库代码不做 I/O"的约定。

输出格式用 V2000 molblock：体积小、3Dmol.js 可直接 ``addModel(text, "sdf")``，
且是行业通用的交换格式（后续要接别的查看器也不用改）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from .errors import ConfigError, RenderError
from .smiles import ensure_rdkit_path

__all__ = ["Conformer3D", "RDKIT_3D_HINT", "rdkit3d_available", "embed3d", "MOLBLOCK_FORMAT"]

#: molblock 的格式名（前端 ``addModel`` 用同一个字符串）
MOLBLOCK_FORMAT = "sdf"

RDKIT_3D_HINT = ("需要 RDKit 才能生成 3D 构象。安装："
                 "python3 -m pip install --target .pylibs rdkit numpy "
                 "（或设置 CHEMFIG_PYLIBS 指向已装好的目录）")

#: 默认随机种子 —— 固定种子让同一分子每次都得到**同一构象**（可复现、可测试）
DEFAULT_SEED = 0xF00D

#: 构象去重的 RMS 阈值（Å）：比这更接近的构象会被剪掉
DEFAULT_PRUNE_RMS = 0.5

#: 最多生成的构象数（取 MMFF 能量最低的那个）
DEFAULT_CONFORMERS = 8


@dataclass
class Conformer3D:
    """一次 3D 构象生成的结果。"""

    smiles: str
    molblock: str
    atoms: int                          # 含氢原子总数
    heavy_atoms: int
    formula: str = ""
    mw: float = 0.0
    energy: float = 0.0                 # MMFF 能量（kcal/mol）
    energy_unit: str = "kcal/mol"
    method: str = ""                    # 例如 "ETKDGv3 + MMFF94"
    source: str = "rdkit"
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"smiles": self.smiles, "molblock": self.molblock,
                "format": MOLBLOCK_FORMAT, "atoms": self.atoms,
                "heavy_atoms": self.heavy_atoms, "formula": self.formula,
                "mw": self.mw, "energy": self.energy, "energy_unit": self.energy_unit,
                "method": self.method, "source": self.source, "notes": list(self.notes)}


def rdkit3d_available() -> bool:
    """RDKit 是否可用（用于 3D）。副作用只有 ``sys.path``（幂等）。"""
    ensure_rdkit_path()
    try:
        from rdkit import Chem                     # noqa: F401
        from rdkit.Chem import AllChem             # noqa: F401
        return True
    except Exception:
        return False


def _build_mol(smiles: str):
    """SMILES → 带显式氢的 RDKit 分子（3D 需要氢，否则构象是"骨头"）。"""
    from rdkit import Chem

    text = str(smiles or "").strip()
    if not text:
        raise RenderError("SMILES 为空")
    # RDKit 默认把 "SMILES Parse Error" 直接写到 stderr —— 本项目约定
    # 库代码不许 print/写日志（AGENTS.md §4），错误通过异常上抛。
    with _quiet_rdkit():
        mol = Chem.MolFromSmiles(text)
    if mol is None:
        raise RenderError("RDKit 无法解析 SMILES %r（写法有误？）" % smiles)
    return mol, Chem.AddHs(mol)


def _quiet_rdkit():
    """临时屏蔽 RDKit 的 C++ 日志（rdBase.BlockLogs）。

    RDKit 默认把 "SMILES Parse Error" 之类**直接写到 stderr**，而本项目约定
    库代码不许 print/写日志（AGENTS.md §4）—— 错误只通过返回值与异常表达。
    ``BlockLogs`` 是 RDKit 自带的上下文管理器；缺失时退化为不屏蔽（不影响正确性）。
    """
    try:
        from rdkit import rdBase
        return rdBase.BlockLogs()
    except Exception:                              # pragma: no cover - 老版本 RDKit
        import contextlib
        return contextlib.nullcontext()


def embed3d(smiles: str, *, seed: int = DEFAULT_SEED, prune_rms: float = DEFAULT_PRUNE_RMS,
            conformers: int = DEFAULT_CONFORMERS, optimise: bool = True) -> Conformer3D:
    """SMILES → 3D 构象（molblock）。

    :param seed:       随机种子（默认固定值 → 同一分子每次同一构象）
    :param prune_rms:  构象去重阈值（Å）
    :param conformers: 生成几个候选构象，取 MMFF 能量最低的
    :param optimise:   是否做 MMFF94 几何优化

    :raises ConfigError: 没装 RDKit（附安装命令）
    :raises RenderError: SMILES 不合法，或构象生成失败
    """
    if not rdkit3d_available():
        raise ConfigError(RDKIT_3D_HINT)
    from rdkit import Chem
    from rdkit.Chem import AllChem, Descriptors, rdMolDescriptors

    _mol, mol = _build_mol(smiles)

    # 参数名以实测为准：Python 的 ETKDGv3 参数对象里**没有** eta
    # （eta 只是 RDKit 命令行工具的选项），设了会 AttributeError。
    params = AllChem.ETKDGv3()
    params.randomSeed = int(seed)
    params.pruneRmsThresh = float(prune_rms)
    params.numThreads = 0                          # 0 = 用全部核心

    count = max(1, int(conformers))
    conf_ids = list(AllChem.EmbedMultipleConfs(mol, numConfs=count, params=params))
    notes: List[str] = []
    if not conf_ids:
        # 少数刚性/病态分子多构象会失败，退回单构象（不做 RMS 剪枝）
        single = AllChem.EmbedMolecule(mol, params)
        if single < 0:
            raise RenderError("无法为 %r 生成 3D 构象（RDKit 嵌入失败）；"
                              "试试换个写法或简化结构" % smiles)
        conf_ids = [single]
        notes.append("多构象搜索失败，已退回单构象。")

    method = "ETKDGv3"
    energies: List[tuple] = []
    if optimise and len(conf_ids) > 0:
        try:
            results = AllChem.MMFFOptimizeMoleculeConfs(mol, numThreads=0)
            # results: [(converged, energy), …] 与 conf_ids 同序
            energies = [(conf_ids[i], res[1]) for i, res in enumerate(results)]
            method = "ETKDGv3 + MMFF94"
            if any(res[0] != 0 for res in results):
                notes.append("部分构象的 MMFF 优化未完全收敛，已取当前最优。")
        except Exception:                          # pragma: no cover - 力场缺失等
            notes.append("MMFF 优化不可用，已使用未优化的 ETKDG 构象。")
            energies = []
    if not energies:
        energies = [(cid, 0.0) for cid in conf_ids]

    best_id, best_energy = min(energies, key=lambda pair: pair[1])
    if len(conf_ids) > 1:
        notes.append("从 %d 个构象中取 MMFF 能量最低者。" % len(conf_ids))

    block = Chem.MolToMolBlock(mol, confId=int(best_id), kekulize=True)
    return Conformer3D(
        smiles=str(smiles).strip(),
        molblock=block,
        atoms=mol.GetNumAtoms(),
        heavy_atoms=mol.GetNumHeavyAtoms(),
        formula=rdMolDescriptors.CalcMolFormula(mol),
        mw=round(Descriptors.MolWt(mol), 2),
        energy=round(float(best_energy), 3),
        method=method,
        notes=notes,
    )


def molblock_has_3d(block: str) -> bool:
    """molblock 里是否真有非零 z 坐标（用于测试与自检，避免"假 3D"）。"""
    lines = str(block).splitlines()
    if len(lines) < 5:
        return False
    try:
        count = int(lines[3][0:3])
    except (ValueError, IndexError):
        return False
    for line in lines[4:4 + count]:
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            if abs(float(parts[2])) > 1e-6:        # 第 3 列是 z
                return True
        except ValueError:
            continue
    return False


def molblock_counts(block: str) -> Dict[str, int]:
    """数出 molblock 里的原子/键条数（测试断言用，不依赖 RDKit）。"""
    lines = str(block).splitlines()
    if len(lines) < 4:
        return {"atoms": 0, "bonds": 0}
    try:
        atoms = int(lines[3][0:3])
        bonds = int(lines[3][3:6])
    except (ValueError, IndexError):
        return {"atoms": 0, "bonds": 0}
    return {"atoms": atoms, "bonds": bonds}
