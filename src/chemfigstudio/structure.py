"""chemfigstudio.structure — 把**真实分子**导进来，变成可编辑的骨架。

为什么需要它（这是本工具与"科研工具"之间最大的一道墙）：

以前只能"从参数画图"——`skeleton` 的坐标全靠手画，于是画得出六价碳、画不出苯环，
也**读不了别人给的结构文件**。现在补上入口：

* ``from_molblock(text)`` —— 纯文本解析 V2000 molblock，**不需要任何依赖**
  （molblock 是公开的行业标准格式，RDKit / ChemDraw / PubChem 都出这个）。
* ``from_smiles(smiles)`` —— 任意 SMILES，走 RDKit（可选依赖）：RDKit 负责
  **2D 坐标生成**与**立体化学感知**，本模块只做"翻译"，绝不自己写布局算法。

两条路都产出同一个 :class:`chemfigstudio.skeleton.Skeleton`，因此导入后能直接
在画布上拖、连键、切键级，或导出成图。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .errors import ConfigError, RenderError
from .skeleton import (
    ELEMENTS,
    WEDGE_DOWN,
    WEDGE_NONE,
    WEDGE_UP,
    Atom,
    Bond,
    Skeleton,
)
from .smiles import ensure_rdkit_path

__all__ = ["Structure2D", "RDKIT_2D_HINT", "from_molblock", "from_smiles",
           "molblock_to_skeleton", "DEFAULT_BOND_PX"]

#: 导入时一根"标准键"对应的像素长度 —— 与 :data:`skeleton.DEFAULT_BOND_LENGTH` 一致，
#: 这样导入的结构和手画的结构在同一张画布上比例相同（不会一个巨大一个极小）。
DEFAULT_BOND_PX = 92.0

RDKIT_2D_HINT = ("从 SMILES 导入需要 RDKit（它负责 2D 坐标生成与立体化学感知）。"
                 "安装：python3 -m pip install --target .pylibs rdkit numpy；"
                 "或者改用 molblock/SDF 文件导入（那条路不需要 RDKit）")

#: molblock 里键的立体标记 → 我们的 wedge 取值
_WEDGE_BY_MOLBLOCK = {"1": WEDGE_UP, "6": WEDGE_DOWN}


@dataclass
class Structure2D:
    """一次结构导入的结果。"""

    skeleton: Skeleton
    title: str = ""
    smiles: str = ""
    formula: str = ""
    mw: float = 0.0
    source: str = "molblock"                 # "molblock" | "smiles"
    skipped_elements: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"skeleton": self.skeleton.as_dict(), "title": self.title,
                "smiles": self.smiles, "formula": self.formula, "mw": self.mw,
                "source": self.source,
                "skipped_elements": list(self.skipped_elements),
                "notes": list(self.notes)}


# ------------------------------------------------------------------ molblock

def _parse_charges(lines: List[str], counts_index: int) -> Dict[int, int]:
    """读 ``M  CHG`` 行 → ``{原子序号(1 起): 电荷}``。"""
    charges: Dict[int, int] = {}
    for line in lines[counts_index:]:
        if line.startswith("M  END"):
            break
        if not line.startswith("M  CHG"):
            continue
        parts = line.split()
        # 形如：M  CHG  n  idx1 chg1 idx2 chg2 …
        try:
            pairs = int(parts[2])
        except (IndexError, ValueError):
            continue
        for i in range(pairs):
            try:
                index = int(parts[3 + i * 2])
                value = int(parts[4 + i * 2])
            except (IndexError, ValueError):
                continue
            charges[index] = value
    return charges


def molblock_to_skeleton(block: str, *, bond_px: float = DEFAULT_BOND_PX,
                         keep_hydrogens: bool = False,
                         known_stereo: Optional[Sequence[int]] = None) -> Structure2D:
    """V2000 molblock → 可编辑骨架（纯文本解析，零依赖）。

    :param bond_px:         一根标准键的像素长度（坐标会整体缩放到这个尺度）
    :param keep_hydrogens:  是否保留显式氢（结构式通常不画氢；默认丢掉）
    :param known_stereo:    已知的立体中心（**molblock 原子序号，1 起**）。
                            由调用方给（SMILES 那条路问 RDKit 要）；不给就在
                            能确定时用度数兜底 —— 见 :func:`_normalise_wedges`。
    """
    if not str(block or "").strip():
        raise RenderError("molblock 是空的")
    lines = str(block).splitlines()
    if len(lines) < 4:
        raise RenderError("这不像 molblock：至少要有 4 行头信息")

    # V3000 是完全不同的语法，老实说不支持比解析出来一半强
    if "V3000" in lines[3]:
        raise RenderError("暂不支持 V3000 molblock（本工具按 V2000 解析）。"
                          "在导出方把格式切成 V2000，或先转成 SDF/MOL 的 V2000。")

    try:
        atom_count = int(lines[3][0:3])
        bond_count = int(lines[3][3:6])
    except (ValueError, IndexError) as exc:
        raise RenderError("molblock 的计数行读不出来（第 4 行）：%s" % exc) from exc

    if atom_count <= 0:
        raise RenderError("molblock 里一个原子都没有")
    if len(lines) < 4 + atom_count:
        raise RenderError("molblock 声称有 %d 个原子，但内容不够" % atom_count)

    title = lines[0].strip()
    charges = _parse_charges(lines, 4 + atom_count)

    raw: List[Tuple[float, float, str, int]] = []          # (x, y, 元素, 电荷)
    skipped: List[str] = []
    for index in range(atom_count):
        line = lines[4 + index]
        try:
            x = float(line[0:10])
            y = float(line[10:20])
        except ValueError as exc:
            raise RenderError("第 %d 个原子的坐标读不出来：%r" % (index + 1, line)) from exc
        symbol = line[31:34].strip() if len(line) > 31 else ""
        if not symbol:
            parts = line.split()
            symbol = parts[3] if len(parts) > 3 else "C"
        # 归一化首字母大写（molblock 里偶有 "CL" 这种写法）
        symbol = symbol[:1].upper() + symbol[1:].lower() if len(symbol) > 1 else symbol.upper()
        if symbol not in ELEMENTS:
            skipped.append(symbol or "?")
        raw.append((x, y, symbol if symbol in ELEMENTS else "C",
                    charges.get(index + 1, 0)))

    bonds: List[Bond] = []
    for index in range(bond_count):
        line = lines[4 + atom_count + index]
        try:
            first = int(line[0:3])
            second = int(line[3:6])
            order = int(line[6:9] or 1)
        except ValueError as exc:
            raise RenderError("第 %d 根键读不出来：%r" % (index + 1, line)) from exc
        if not (1 <= first <= atom_count and 1 <= second <= atom_count):
            raise RenderError("第 %d 根键指向了不存在的原子（%d-%d）"
                              % (index + 1, first, second))
        wedge = _WEDGE_BY_MOLBLOCK.get(line[9:12].strip(), WEDGE_NONE)
        bonds.append(Bond(a="m%d" % first, b="m%d" % second,
                          order=max(1, min(3, order)), wedge=wedge))

    # 氢：结构式一般不画，默认丢掉（连带删键）
    dropped: set = set()
    if not keep_hydrogens:
        dropped = {"m%d" % (i + 1) for i, item in enumerate(raw) if item[2] == "H"}

    atoms = [Atom(id="m%d" % (i + 1), element=item[2], x=item[0], y=item[1],
                  charge=item[3])
             for i, item in enumerate(raw) if ("m%d" % (i + 1)) not in dropped]
    bonds = [b for b in bonds if b.a not in dropped and b.b not in dropped]

    skeleton = Skeleton(atoms=atoms, bonds=bonds, title=title)
    _normalise_wedges(skeleton, stereo={"m%d" % i for i in (known_stereo or [])})
    _scale_and_flip(skeleton, bond_px)
    _assign_default_labels(skeleton)

    notes: List[str] = []
    if dropped:
        notes.append("已隐藏 %d 个显式氢（结构式习惯不画氢）。" % len(dropped))
    if skipped:
        unique = sorted(set(skipped))
        notes.append("这些元素本工具不认识，已按碳处理：%s" % ", ".join(unique))
    if not atoms:
        raise RenderError("去掉氢之后没有原子了 —— 这大概是纯氢分子？")

    return Structure2D(skeleton=skeleton, title=title, source="molblock",
                       skipped_elements=sorted(set(skipped)), notes=notes)


def _normalise_wedges(skeleton: Skeleton, stereo: Optional[set] = None) -> None:
    """把楔形键统一成"**窄端 = 立体中心**"的画法。

    为什么必须统一：楔形的化学含义全靠**哪一端是尖的** —— 尖的那端才是决定构型的
    原子。molblock 只规定 "1 = 楔形 / 6 = 虚线"，**没说哪端是尖的**；而渲染层只能从
    ``Bond.a`` 画到 ``Bond.b``。同一份数据按相反方向画出来是**两种对映体**
    —— 这是"看上去没问题、化学是错的"里最危险的一类。

    判定顺序：
    1. ``stereo``（已知手性中心，**来自 RDKit**）：楔形必须从它出发。有权威答案时
       不猜。
    2. 退化情况（纯 molblock，不知道手性）：用度数启发式 —— 楔形两端里取代基多的
       那个更可能是立体中心（它连 3~4 个，另一端通常只连 1 个）。**只在两端度数
       不同时才动**，度数相同时保持原样（不猜）。
    """
    if not skeleton.bonds:
        return
    degree: Dict[str, int] = {}
    for bond in skeleton.bonds:
        degree[bond.a] = degree.get(bond.a, 0) + 1
        degree[bond.b] = degree.get(bond.b, 0) + 1

    for bond in skeleton.bonds:
        if bond.wedge == WEDGE_NONE:
            continue
        must_flip = False
        if stereo:
            if bond.b in stereo and bond.a not in stereo:
                must_flip = True
        elif degree.get(bond.a, 0) < degree.get(bond.b, 0):
            must_flip = True
        if must_flip:
            bond.a, bond.b = bond.b, bond.a
            bond.wedge = WEDGE_DOWN if bond.wedge == WEDGE_UP else WEDGE_UP


def _scale_and_flip(skeleton: Skeleton, bond_px: float) -> None:
    """把 molblock 的 Å 坐标缩放成像素，并把 y 轴翻过来。

    molblock 的 y 向上为正，画布的 y 向下为正 —— 不翻的话分子会上下镜像
    （手性中心的楔形就指反了，这是"看起来对但化学错"的典型）。
    """
    if not skeleton.atoms:
        return
    lengths: List[float] = []
    for bond in skeleton.bonds:
        first, second = skeleton.atom(bond.a), skeleton.atom(bond.b)
        if first and second:
            lengths.append(((second.x - first.x) ** 2 + (second.y - first.y) ** 2) ** 0.5)
    typical = sorted(lengths)[len(lengths) // 2] if lengths else 0.0
    factor = (bond_px / typical) if typical > 1e-6 else 1.0

    for atom in skeleton.atoms:
        atom.x = round(atom.x * factor, 2)
        atom.y = round(-atom.y * factor, 2)      # y 翻转


def _assign_default_labels(skeleton: Skeleton) -> None:
    """给"需要写出来的"原子填默认标签，让导入结果看起来像结构式。

    规则：
    * 骨架碳（只连碳）不写字 —— 结构式里碳骨架是线，写字会满屏都是 ``CH_{3}``。
    * 非碳原子写默认基团/元素符号（``OH`` / ``NH_{2}`` / ``Cl`` …）。
    * **重键上的原子退回元素符号**：羧基里的双键氧要写 ``O`` 而不是 ``OH``，
      羰基同理。这个坑很隐蔽 —— 全都写 ``OH`` 的话图看起来没问题，化学是错的。
    """
    from .skeleton import ELEMENT_LABELS

    multiple = set()                             # 落在重键上的原子
    for bond in skeleton.bonds:
        if bond.order > 1:
            multiple.add(bond.a)
            multiple.add(bond.b)

    for atom in skeleton.atoms:
        if atom.label:
            continue
        if atom.element == "C":
            # 碳一律交给"是不是端基"决定：端基写 CH_{3}，骨架碳留空让线自己说话。
            # （曾经这里写成 if 重键 / elif 重键 / else 默认标签，于是**每个单键碳**
            #   都落到 else 被标成 CH_{3} —— 阿司匹林画成了一堆甲基。）
            atom.label = "CH_{3}" if _is_terminal_carbon(skeleton, atom) else ""
        elif atom.id in multiple:
            atom.label = atom.element                # 双键氧 → "O"，不写 OH
        else:
            atom.label = ELEMENT_LABELS.get(atom.element, atom.element)
        if atom.charge:
            sign = "+" if atom.charge > 0 else "-"
            magnitude = "" if abs(atom.charge) == 1 else str(abs(atom.charge))
            atom.label = (atom.label or atom.element) + "^{%s%s}" % (sign, magnitude)


def _is_terminal_carbon(skeleton: Skeleton, atom: Atom) -> bool:
    """这个碳是不是链端（只连 1 个重原子）—— 端基要写出 ``CH_{3}``。"""
    neighbours = 0
    for bond in skeleton.bonds:
        if atom.id in (bond.a, bond.b):
            neighbours += 1
    return neighbours <= 1


# -------------------------------------------------------------------- SMILES

def from_smiles(smiles: str, *, bond_px: float = DEFAULT_BOND_PX,
                keep_hydrogens: bool = False) -> Structure2D:
    """任意 SMILES → 可编辑骨架（需要 RDKit）。

    分工：**RDKit 负责 2D 布局与立体化学**（"不造轮子"），本函数只负责把它翻译成
    我们的骨架。没有 RDKit 时抛 :class:`ConfigError` 并给出两条可行替代。
    """
    text = str(smiles or "").strip()
    if not text:
        raise RenderError("SMILES 为空")
    ensure_rdkit_path()
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
    except ImportError as exc:
        raise ConfigError(RDKIT_2D_HINT) from exc

    from .render3d import _quiet_rdkit

    with _quiet_rdkit():
        mol = Chem.MolFromSmiles(text)
    if mol is None:
        raise RenderError("RDKit 无法解析 SMILES %r（写法有误？）" % smiles)

    # 结构式不画显式氢（否则每个 CH 上都挂着白点、楔形会指到氢上）——
    # 立体信息由楔形键本身承载，不依赖氢原子是否显式存在。
    mol = Chem.AddHs(mol) if keep_hydrogens else Chem.RemoveHs(mol)
    AllChem.Compute2DCoords(mol)
    # 立体化学：先判定手性，再让 RDKit 决定哪根键画楔形（别自己猜哪根）
    Chem.AssignStereochemistry(mol, cleanIt=True, force=True)
    Chem.WedgeMolBonds(mol, mol.GetConformer())

    from rdkit.Chem import Descriptors, rdMolDescriptors

    # 问 RDKit 要权威的手性中心（不自己猜哪根键该画楔形）——
    # 注意：这里报的是 **1 起的原子序号**，正好是 molblock 的编号口径
    centres = Chem.FindMolChiralCenters(mol, includeUnassigned=False,
                                        useLegacyImplementation=False)
    stereo = [index + 1 for index, _label in centres]

    block = Chem.MolToMolBlock(mol, kekulize=True)
    # 这里 keep_hydrogens=True 只表示"别再替我删一遍"：删不删由上面的参数决定
    result = molblock_to_skeleton(block, bond_px=bond_px, keep_hydrogens=True,
                                  known_stereo=stereo)
    result.source = "smiles"
    result.smiles = text
    result.formula = rdMolDescriptors.CalcMolFormula(mol)
    result.mw = round(Descriptors.MolWt(mol), 2)
    if not result.title:
        result.title = text
    return result


def from_molblock(text: str, *, bond_px: float = DEFAULT_BOND_PX,
                  keep_hydrogens: bool = False) -> Structure2D:
    """V2000 molblock / SDF 的**第一条记录** → 可编辑骨架。"""
    # SDF 是多条记录用 "$$$$" 分隔的；只取第一条
    first = str(text).split("$$$$")[0]
    return molblock_to_skeleton(first, bond_px=bond_px,
                                keep_hydrogens=keep_hydrogens)
