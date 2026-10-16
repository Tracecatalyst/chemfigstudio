"""chemfigstudio.smiles — SMILES / 名称 → 碳数。

两条路径，互为备份：

* **纯 Python**（零依赖）：识别直链伯醇 ``C…CO``，即乙醇～己醇这类
  实验里真正会用到的分子。没装 RDKit 也能用。
* **RDKit**（可选）：装了就能处理支链 / 环 / 杂原子等任意 SMILES，
  并且会多返回分子式与相对分子质量。

两条路径都必须通过同一个安全检查：**恰好一个氧**（也就是一个 -OH），
否则报错并说明原因 —— 避免把 ``CCC(=O)O`` 这类羧酸当成醇画错。
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .errors import RenderError

__all__ = ["SmilesInfo", "NAME_TABLE", "rdkit_available", "resolve_name",
           "resolve_smiles"]


@dataclass
class SmilesInfo:
    """SMILES 解析结果。"""

    smiles: str
    n_carbons: int
    name: str = ""
    formula: str = ""
    mw: float = 0.0
    source: str = "builtin"                 # "builtin" | "rdkit"
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {"smiles": self.smiles, "n_carbons": self.n_carbons,
                "name": self.name, "formula": self.formula,
                "mw": self.mw, "source": self.source, "notes": list(self.notes)}


#: 常见伯醇名称 → 碳数（大小写/连字符都做了归一化）。
NAME_TABLE: Dict[str, int] = {
    "methanol": 1,
    "ethanol": 2,
    "propan-1-ol": 3, "propanol": 3, "1-propanol": 3, "n-propanol": 3,
    "butan-1-ol": 4, "butanol": 4, "1-butanol": 4, "n-butanol": 4,
    "pentan-1-ol": 5, "pentanol": 5, "1-pentanol": 5, "n-pentanol": 5,
    "hexan-1-ol": 6, "hexanol": 6, "1-hexanol": 6, "n-hexanol": 6,
    "heptan-1-ol": 7, "heptanol": 7,
    "octan-1-ol": 8, "octanol": 8,
}
#: 碳数 → 标准名（用于自动补标题）
CARBON_TO_NAME: Dict[int, str] = {
    1: "methanol", 2: "ethanol", 3: "propan-1-ol", 4: "butan-1-ol",
    5: "pentan-1-ol", 6: "hexan-1-ol", 7: "heptan-1-ol", 8: "octan-1-ol",
    9: "nonan-1-ol", 10: "decan-1-ol", 11: "undecan-1-ol", 12: "dodecan-1-ol",
}

_LINEAR_ALCOHOL_RE = re.compile(r"^C+O$", re.IGNORECASE)


# --------------------------------------------------------------------- RDKit 探测

def _lib_candidates() -> List[Path]:
    """候选依赖目录（前插 sys.path 用）。

    顺序：``CHEMFIG_PYLIBS`` → 仓库根 ``.pylibs`` → 当前目录 ``.pylibs``
    → 仓库根 ``vendor``（原生 .app 内嵌的 rdkit/numpy 放在这里）。
    """
    candidates: List[Path] = []
    env = os.environ.get("CHEMFIG_PYLIBS", "")
    for chunk in env.split(os.pathsep):
        if chunk.strip():
            candidates.append(Path(chunk).expanduser())
    here = Path(__file__).resolve()
    for parent in list(here.parents)[:4]:                 # 仓库根附近
        candidates.append(parent / ".pylibs")
        candidates.append(parent / "vendor")              # 原生 app 的内嵌依赖
    candidates.append(Path.cwd() / ".pylibs")
    candidates.append(Path.cwd() / "vendor")
    seen, unique = set(), []
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def ensure_rdkit_path() -> List[str]:
    """把含 ``rdkit`` 包的候选目录前插进 ``sys.path``；返回实际加入的目录。

    幂等：已加过的不会再加。副作用只在 ``sys.path``（可安全重复调用）。
    """
    added: List[str] = []
    for directory in _lib_candidates():
        if not directory.is_dir() or not (directory / "rdkit").exists():
            continue
        text = str(directory)
        if text in sys.path:
            continue
        sys.path.insert(0, text)
        added.append(text)
    return added


def rdkit_source() -> Optional[str]:
    """RDKit 从哪来：``.pylibs`` / ``vendor`` 路径、``user-site`` 或 None（不可用）。

    先做一次 :func:`ensure_rdkit_path` —— 否则 RDKit 装在 ``.pylibs``（或原生 app
    内嵌的 ``vendor``）时这里会误报"不可用"（只有装在 user-site 才碰巧能 import）。
    """
    ensure_rdkit_path()
    try:
        import rdkit                                        # noqa: F401
    except ImportError:
        return None
    module = sys.modules.get("rdkit")
    origin = getattr(module, "__file__", "") or ""
    for directory in _lib_candidates():
        if origin.startswith(str(directory)):
            return str(directory)
    return "user-site"


def rdkit_available() -> bool:
    """RDKit 是否可用（不影响其它功能）。同样先确保 ``.pylibs``/``vendor`` 已入 path。"""
    ensure_rdkit_path()
    try:
        import rdkit  # noqa: F401
        return True
    except Exception:
        return False


def rdkit_hint() -> str:
    return ("未安装 RDKit，只能识别直链伯醇（如 CCO / CCCCO）。"
            "需要任意 SMILES 支持时："
            "python3 -m pip install --target .pylibs rdkit，"
            "然后设置 PYTHONPATH=.pylibs")


# ------------------------------------------------------------------------ 名称

def resolve_name(name: str) -> Optional[int]:
    """按名称猜碳数；不认识返回 ``None``。"""
    if not name:
        return None
    key = name.strip().lower().replace("_", "-").replace(" ", "-")
    key = re.sub(r"-{2,}", "-", key)
    if key in NAME_TABLE:
        return NAME_TABLE[key]

    # "1-butanol" -> "butan-1-ol"
    m = re.match(r"^(\d+)-([a-z]+)anol$", key)
    if m:
        position, stem = m.group(1), m.group(2)
        if position == "1":
            candidate = "%san-1-ol" % stem
            if candidate in NAME_TABLE:
                return NAME_TABLE[candidate]

    # 词干兜底："butanol" / "butan-1-ol" 之外的写法
    stem_match = re.match(r"^([a-z]+)an(?:-1)?-?ol$", key)
    if stem_match:
        stem = stem_match.group(1)
        for canonical, carbons in NAME_TABLE.items():
            if canonical.startswith(stem + "an"):
                return carbons
    return None


# ----------------------------------------------------------------------- SMILES

def _raise_not_an_alcohol(smiles: str) -> None:
    """两条路径**共用**的前检：连"这不是醇"都算不上的输入，报同一条可执行提示。

    为什么要有它（踩过）：内置解析器和 RDKit 原本各写各的文案，同一个 ``C``
    在内置路径会提示「甲醇的 SMILES 是 CO」，在 RDKit 路径只报「0 个氧」——
    装了 RDKit 的用户反而看到更差的提示，测试也因此随机器而红。
    """
    text = str(smiles or "").strip()
    if "O" not in text.upper():
        raise RenderError(
            "SMILES %r 里没有 O —— 它不是醇。甲醇的 SMILES 是 CO"
            "（单独一个 C 是甲烷）。" % smiles)


def _resolve_builtin(smiles: str) -> SmilesInfo:
    """零依赖路径：只认直链伯醇 ``C…CO``。"""
    text = smiles.strip().strip('"').strip("'")
    _raise_not_an_alcohol(smiles)
    if not _LINEAR_ALCOHOL_RE.match(text):
        raise RenderError(
            "无法用内置解析器识别 SMILES %r：它只支持直链伯醇（CO、CCO、CCCO、CCCCO…）。"
            "如需支链/环/杂原子，请安装 RDKit：%s" % (smiles, rdkit_hint()))
    n = text.upper().count("C")
    return SmilesInfo(smiles=text, n_carbons=n, name=CARBON_TO_NAME.get(n, ""),
                      source="builtin")


def _resolve_rdkit(smiles: str) -> SmilesInfo:
    from rdkit import Chem
    from rdkit.Chem import Descriptors, rdMolDescriptors

    _raise_not_an_alcohol(smiles)
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise RenderError("SMILES %r 写法有误，RDKit 无法解析（检查括号/环编号/元素符号）"
                          % smiles)

    symbols = [a.GetSymbol() for a in mol.GetAtoms()]
    if symbols.count("O") != 1:
        raise RenderError("SMILES %r 里有 %d 个氧；本工具只支持含一个氧（一个 -OH）的醇"
                          % (smiles, symbols.count("O")))
    extra = sorted(set(symbols) - {"C", "O", "H"})
    if extra:
        raise RenderError("SMILES %r 里还有 %s；本工具只支持 C/H/O 组成的醇"
                          % (smiles, ", ".join(extra)))

    o_atom = [a for a in mol.GetAtoms() if a.GetSymbol() == "O"][0]
    if o_atom.GetTotalNumHs() != 1 or o_atom.GetDegree() != 1:
        raise RenderError("SMILES %r 里的氧不是羟基 -OH，不能当醇画" % smiles)

    notes: List[str] = []
    if mol.GetRingInfo().NumRings():
        notes.append("该分子含环；本工具按直链伯醇的骨架作画，请人工核对。")
    n = symbols.count("C")
    return SmilesInfo(
        smiles=smiles.strip(),
        n_carbons=n,
        name=CARBON_TO_NAME.get(n, "") or "C%d alcohol" % n,
        formula=rdMolDescriptors.CalcMolFormula(mol),
        mw=round(Descriptors.MolWt(mol), 2),
        source="rdkit",
        notes=notes,
    )


def resolve_smiles(smiles: str) -> SmilesInfo:
    """解析 SMILES → :class:`SmilesInfo`。

    优先 RDKit（若可用），失败或未安装时回退到内置直链解析器。
    """
    if not smiles or not smiles.strip():
        raise RenderError("SMILES 为空")
    if rdkit_available():
        return _resolve_rdkit(smiles)
    return _resolve_builtin(smiles)
