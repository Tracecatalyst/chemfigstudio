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
from .smiles import ensure_rdkit_path, quiet_rdkit

__all__ = ["Structure2D", "RDKIT_2D_HINT", "from_molblock", "from_smiles",
           "looks_like_molblock", "molblock_to_skeleton",
           "to_molblock", "to_smiles", "to_inchi",
           "DEFAULT_BOND_PX"]

#: 导入时一根"标准键"对应的像素长度 —— 与 :data:`skeleton.DEFAULT_BOND_LENGTH` 一致，
#: 这样导入的结构和手画的结构在同一张画布上比例相同（不会一个巨大一个极小）。
DEFAULT_BOND_PX = 92.0

#: 导出 molblock 时的标准键长（Å）。C–C 单键实测 1.54 Å，1.5 是通行近似值。
STANDARD_BOND_ANGSTROM = 1.5

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
                         known_stereo: Optional[Sequence[int]] = None,
                         hydrogens: Optional[Dict[int, int]] = None,
                         allow_unknown_elements: bool = False,
                         bond_stereo: Optional[Dict[int, str]] = None,
                         aromatic_atoms: Optional[Set[int]] = None) -> Structure2D:
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

    # 不认识的元素**不能静默换成碳** —— 那是另一个分子。（决策与代价见 ADR-016）
    # 审计 F2 实测：`C[Si](C)(C)C`（TMS）会导出成新戊烷、`[Na+].[Cl-]` 导出成
    # 甲基正离子 + 氯离子，都返回 ok:true。宁可报错，也不能悄悄改变化学式。
    if skipped and not allow_unknown_elements:
        unique = sorted({symbol for symbol in skipped if symbol})
        raise RenderError(
            "这个结构里有本工具不支持的元素：%s。画出来会变成另一个分子，所以拒绝。\n"
            "可选：装 RDKit（支持的元素多得多），或者用 --allow-unknown-elements "
            "强行按碳处理（那会改变化学式，导出的文件不代表原分子）。"
            % ", ".join(unique))

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
    if aromatic_atoms:
        # 0-based，与 RDKit 的 GetIdx 一致
        for position, atom in enumerate(skeleton.atoms):
            if position in aromatic_atoms:
                atom.aromatic = True

    if bond_stereo:
        # 键序号是 molblock 的 1-based 口径
        for position, bond in enumerate(skeleton.bonds, start=1):
            value = bond_stereo.get(position)
            if value in ("e", "z"):
                bond.stereo = value

    _assign_default_labels(skeleton, hydrogens=hydrogens)

    notes: List[str] = []
    if dropped:
        notes.append("已隐藏 %d 个显式氢（结构式习惯不画氢）。" % len(dropped))
    if skipped:
        unique = sorted(set(skipped))
        notes.append("这些元素本工具不认识，已按碳处理：%s" % ", ".join(unique))
    if not atoms:
        raise RenderError("去掉氢之后没有原子了 —— 这大概是纯氢分子？")

    # 价键检查：导入一个超价分子时**不能静默画出化学错误**。
    # 只报超价（欠价的氢是隐含的，见 Skeleton.valence_errors 的说明）。
    for issue in skeleton.valence_errors():
        notes.append("价键可能有问题：%s" % issue.text())

    return Structure2D(skeleton=skeleton, title=title, source="molblock",
                       skipped_elements=sorted(set(skipped)), notes=notes)


def to_molblock(skeleton: Skeleton, *, title: str = "",
                bond_px: float = DEFAULT_BOND_PX) -> str:
    """把骨架写成 V2000 molblock（**零依赖**，纯文本拼装，见 ADR-015）。

    为什么零依赖很重要：只有 InChI 导出的话，没装 RDKit 的用户就完全交不出文件；
    而 molblock 是事实标准，纯文本就能拼。

    **不写显式氢**：氢在我们的模型里是隐含的（结构式的本意）。读入方按价键补氢，
    合法且体积小 —— 但也意味着导出的不是 3D 结构（氢没有坐标）。

    坐标：内部是"键长 ≈ ``bond_px`` 像素"，molblock 用 Å。这里除以 ``bond_px``，
    读回来时 :func:`_scale_and_flip` 会按实际键长中位数归一化，正好还原。
    """
    from .skeleton import WEDGE_DOWN, WEDGE_UP

    # 坐标归一化：**不能除以固定的 bond_px**。用户画的键长可能是 60 也可能是 300 像素，
    # 固定除法会导出 0.65 Å 或 3.3 Å 这种非物理键长，别的软件读进去分子会挤成一团或散开。
    # 正确做法：按**实际键长中位数**归一化到标准键长 1.5 Å（C–C 单键 1.54 Å）。
    lengths: List[float] = []
    for bond in skeleton.bonds:
        first, second = skeleton.atom(bond.a), skeleton.atom(bond.b)
        if first and second:
            lengths.append(((second.x - first.x) ** 2
                            + (second.y - first.y) ** 2) ** 0.5)
    typical = sorted(lengths)[len(lengths) // 2] if lengths else 0.0
    # 单个原子（没有键）时没有尺度可言 —— 退回 bond_px，至少形状可控
    factor = (STANDARD_BOND_ANGSTROM / typical) if typical > 1e-6 \
        else (1.0 / (float(bond_px) if bond_px > 1e-6 else DEFAULT_BOND_PX))
    lines = [title or skeleton.title or "", "     chemfigstudio", "",
             "%3d%3d  0  0  0  0  0  0  0  0999 V2000"
             % (len(skeleton.atoms), len(skeleton.bonds))]

    for atom in skeleton.atoms:
        lines.append("%10.4f%10.4f%10.4f %-3s 0  0  0  0  0  0  0  0  0  0  0  0"
                     % (atom.x * factor,
                        -atom.y * factor,          # y 轴翻回去（molblock +y 向上）
                        0.0, atom.element))

    index = {atom.id: position + 1 for position, atom in enumerate(skeleton.atoms)}
    for bond in skeleton.bonds:
        # 注意别叫 first/second —— 上面算键长时那两个名字是 Atom，
        # mypy 会顺着推断出类型冲突。
        start, end = index.get(bond.a), index.get(bond.b)
        if start is None or end is None:
            continue
        stereo = 0
        if bond.wedge == WEDGE_UP:
            stereo = 1
        elif bond.wedge == WEDGE_DOWN:
            stereo = 6
        lines.append("%3d%3d%3d%3d  0  0  0" % (start, end, bond.order, stereo))

    # 形式电荷：M  CHG  n  atom chg ...（一行最多 8 对）
    charged = [(index[atom.id], atom.charge) for atom in skeleton.atoms
               if atom.charge and atom.id in index]
    for start in range(0, len(charged), 8):
        chunk = charged[start:start + 8]
        lines.append("M  CHG%3d" % len(chunk)
                     + "".join("%4d%4d" % (atom_index, charge)
                               for atom_index, charge in chunk))

    lines.append("M  END")
    return "\n".join(lines) + "\n"


def _rdkit_mol_from_skeleton(skeleton: Skeleton):
    """骨架 → RDKit Mol。需要 RDKit；sanitize 失败时给出**可读**的原因。"""
    # **先用自己的价键检查**，别指望 RDKit 兜底：实测 RDKit 的 sanitize 对
    # "碳连 5 根键"这种超价相当宽容，会默默产出一个化学上不成立的分子，
    # 然后 InChI 也是错的（错的 InChI 比报错危险得多 —— 它会进数据库）。
    issues = skeleton.valence_errors()
    if issues:
        raise RenderError(
            "这个骨架有价键问题，没法导出结构文件：%s。请先修好价键。"
            % "；".join(issue.text() for issue in issues))

    ensure_rdkit_path()
    try:
        from rdkit import Chem
    except ImportError as exc:                       # pragma: no cover - 环境问题
        raise ConfigError(RDKIT_2D_HINT) from exc

    with quiet_rdkit():
        mol = Chem.MolFromMolBlock(to_molblock(skeleton), sanitize=True)
    if mol is None:
        raise RenderError("RDKit 无法解析这个骨架（可能是芳香性或价键不成立）")
    _apply_bond_stereo(mol, skeleton)
    return mol


def _apply_bond_stereo(mol, skeleton: Skeleton) -> None:
    """按骨架里记录的 E/Z **校正** RDKit 从坐标推出来的结论（审计 F3）。

    V2000 的 bond stereo 列只能表达"这个双键是立体的、请按坐标解读"，
    没有"显式 E/Z"的字段。于是 RDKit 读回 molblock 时会自己从坐标**推断**几何，
    造成两个后果：

    * 输入本来没指定几何的亚胺（``CC=NC``）被导出成确定的 ``C/C=N/C``（断言 (E)）；
    * 立体信息只活在坐标里 —— 编辑器支持拖原子，拖一下就把反式变成顺式，
      导出的就是另一个异构体，而且没有任何提示。

    这里反过来做：**以骨架里的字段为准**。没指定就清掉（导出不带 ``/`` ``\``），
    指定了就显式设进 RDKit，坐标怎么摆都不影响结果。
    """
    from rdkit import Chem

    bonds = list(mol.GetBonds())
    if len(bonds) != len(skeleton.bonds):       # 顺序万一不对就别乱改
        return
    for rd_bond, sk_bond in zip(bonds, skeleton.bonds):
        if sk_bond.order != 2:
            continue
        if sk_bond.stereo == "":
            rd_bond.SetStereo(Chem.BondStereo.STEREONONE)
            continue
        begin, end = rd_bond.GetBeginAtom(), rd_bond.GetEndAtom()
        ref_begin = next((n.GetIdx() for n in begin.GetNeighbors()
                          if n.GetIdx() != end.GetIdx()), None)
        ref_end = next((n.GetIdx() for n in end.GetNeighbors()
                        if n.GetIdx() != begin.GetIdx()), None)
        if ref_begin is None or ref_end is None:   # 末端双键（=CH2）没有 E/Z
            continue
        rd_bond.SetStereoAtoms(ref_begin, ref_end)
        rd_bond.SetStereo(Chem.BondStereo.STEREOE if sk_bond.stereo == "e"
                          else Chem.BondStereo.STEREOZ)
    # **不要**再调 AssignStereochemistry：它会把刚清掉的立体从坐标又推回来
    # （实测：SetStereo(STEREONONE) 之后 MolToSmiles 是 CC=NC，再 Assign 一次
    #  就变回 C/C=N/C —— 正是审计 F3 说的"按坐标反推"）。


def to_smiles(skeleton: Skeleton) -> str:
    """骨架 → **规范** SMILES（需要 RDKit，见 ADR-015）。

    规范化是算法问题，不是画图问题 —— 自己实现几乎一定错，且错得隐蔽。
    """
    ensure_rdkit_path()
    try:
        from rdkit import Chem
    except ImportError as exc:                       # pragma: no cover
        raise ConfigError(RDKIT_2D_HINT) from exc
    return Chem.MolToSmiles(_rdkit_mol_from_skeleton(skeleton))


def to_inchi(skeleton: Skeleton) -> str:
    """骨架 → InChI（需要 RDKit，见 ADR-015）。

    InChI 回答"这是不是同一个分子"，用来查重；注意它带版本号（RDKit 出 1.06）。
    """
    ensure_rdkit_path()
    try:
        from rdkit.Chem import inchi as _inchi
    except ImportError as exc:                       # pragma: no cover
        raise ConfigError(RDKIT_2D_HINT) from exc
    with quiet_rdkit():
        value = _inchi.MolToInchi(_rdkit_mol_from_skeleton(skeleton))
    if not value:
        raise RenderError("RDKit 没能生成 InChI（这个骨架可能不合法）")
    return value


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


#: 常见元素的中性价（用于**按价键环境**推断氢数，而不是按元素查表）
_DEFAULT_VALENCE = {"C": 4, "N": 3, "O": 2, "S": 2, "P": 3, "B": 3}


def _assign_default_labels(skeleton: Skeleton,
                           hydrogens: Optional[Dict[int, int]] = None) -> None:
    """给"需要写出来的"原子填默认标签 —— **按价键环境推**，不是按元素查表。

    为什么要按环境：同一个元素在分子里的氢数不同。咖啡因里有三个氮，两个是
    N-甲基（只连 2 个重原子）、一个是环内 =N-（连 2 个且有双键）；一律写 ``NH_{2}``
    就画错了。专业工具都是这么做的（看连了几个重原子、有几根重键）。

    规则：
    * **碳**：端基写 ``CH_{3}``（连 1 个重原子），骨架碳留空 —— 结构式里碳是线。
    * **其它元素**：氢数 = 价 − Σ(键级)，写成 ``OH`` / ``NH`` / ``NH_{2}`` / 元素符号；
      按重键数正确退化成 ``O``（羰基）、``N``（=N−）等。
    * 带电原子在标签后加上标（``N^{+}`` / ``O^{-}``）。
    """
    from .skeleton import ELEMENT_LABELS

    order_sum: Dict[str, int] = {}
    degree: Dict[str, int] = {}
    for bond in skeleton.bonds:
        for end in (bond.a, bond.b):
            order_sum[end] = order_sum.get(end, 0) + bond.order
            degree[end] = degree.get(end, 0) + 1

    # 原子 id → molblock 里的 1-based 序号（权威氢数是按那个口径给的）
    order_index = {atom.id: position + 1 for position, atom in enumerate(skeleton.atoms)}

    for atom in skeleton.atoms:
        if not atom.label:
            if atom.element == "C":
                atom.label = _carbon_label(atom, order_sum.get(atom.id, 0),
                                           degree.get(atom.id, 0),
                                           hydrogens, order_index.get(atom.id))
            else:
                atom.label = _hetero_label(atom, order_sum.get(atom.id, 0),
                                           degree.get(atom.id, 0))
        if atom.charge:
            sign = "+" if atom.charge > 0 else "-"
            magnitude = "" if abs(atom.charge) == 1 else str(abs(atom.charge))
            atom.label = (atom.label or atom.element) + "^{%s%s}" % (sign, magnitude)


def _carbon_label(atom: Atom, bond_order_sum: int, degree: int,
                  hydrogens: Optional[Dict[int, int]],
                  index: Optional[int]) -> str:
    """碳的标签：写几个氢**必须看键级与电荷**，不能只看连了几根键。

    踩过（审计 F1）：早期只判断 ``degree <= 1`` 就写 ``CH_{3}``，于是
    乙烯画成 ``CH_{3}=CH_{3}``、甲烷画成 ``CH_{3}``、甲醛画成 ``CH_{3}=O`` ——
    图上写着碳有 5 根键，而**没有任何闸门能拦**（价键检查只看键级之和，
    不看"画出来的文字声称了几根键"）。

    氢数来源优先级：
    1. ``hydrogens``（RDKit 的 ``GetTotalNumHs``）—— 权威，SMILES 那条路走这个；
       molblock 没有隐含氢信息，所以那条路用下面的公式兜底。
    2. ``H = 4 - Σ(键级) - |电荷|`` —— 碳正/碳负离子都是"三根键"，
       所以电荷让可用的键数少一根（而不是多一根，那是 N⁺ 的规则）。
    """
    if hydrogens is not None and index is not None and index in hydrogens:
        count = int(hydrogens[index])
    else:
        count = max(0, 4 - int(bond_order_sum) - abs(int(atom.charge)))
    count = max(0, count)

    if degree >= 2:
        # 骨架碳：结构式里碳是线，氢隐含，不写字
        return ""
    if count <= 0:
        return "C"                     # 裸碳（CCl4 的核心、碳正离子无氢等）
    if count == 1:
        return "CH"
    return "CH_{%d}" % count


def _hetero_label(atom: Atom, bond_order_sum: int, degree: int) -> str:
    """非碳原子的标签：按"还差几个氢"写。

    ``O`` 连 1 个单键 → ``OH``；``O`` 有双键 → ``O``；``N`` 连 2 个 → ``NH``；
    ``N`` 连 3 个 → ``N``。价不知道的元素退回元素符号（**不发明化学写法**）。
    """
    from .skeleton import ELEMENT_LABELS

    valence = _DEFAULT_VALENCE.get(atom.element)
    if valence is None:
        return ELEMENT_LABELS.get(atom.element, atom.element)

    hydrogens = valence - bond_order_sum + atom.charge
    if hydrogens <= 0:
        return atom.element
    if hydrogens == 1:
        return "H" if atom.element == "C" else "%sH" % atom.element
    from . import core

    return "%sH_{%d}" % (atom.element, hydrogens)


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

    with quiet_rdkit():
        mol = Chem.MolFromSmiles(text)
    if mol is None:
        raise RenderError("RDKit 无法解析 SMILES %r（写法有误？）" % smiles)

    # 同位素与自由基：我们的模型里**没有这两类信息**（Atom 只有 element/charge/
    # wedge/aromatic），Accept 了就等于静默丢弃。（同 ADR-016）审计 F2 实测：
    #   [13CH4] → C（丢了 ¹³C 标记）、[2H]C → C（丢了氘）、[CH3] → C（自由基变中性）
    # 宁可拒绝，也不能导出一个化学上不同的分子。
    isotopes = sorted({atom.GetIsotope() for atom in mol.GetAtoms()
                       if atom.GetIsotope()})
    radicals = [atom for atom in mol.GetAtoms() if atom.GetNumRadicalElectrons()]
    if isotopes or radicals:
        reasons = []
        if isotopes:
            reasons.append("同位素标记（%s）"
                           % ", ".join(str(mass) for mass in isotopes))
        if radicals:
            reasons.append("自由基（%d 个原子）" % len(radicals))
        raise RenderError(
            "这个结构带有%s，本工具的骨架模型里没有这两类信息 —— "
            "画出来会丢掉它们（等于另一个分子），所以拒绝。\n"
            "可选：先去掉这些标记再导入，或者直接用别的方法处理这个结构。" % "、".join(reasons))

    # 结构式不画显式氢（否则每个 CH 上都挂着白点、楔形会指到氢上）——
    # 立体信息由楔形键本身承载，不依赖氢原子是否显式存在。
    mol = Chem.AddHs(mol) if keep_hydrogens else Chem.RemoveHs(mol)
    AllChem.Compute2DCoords(mol)
    # 立体化学：先判定手性，再让 RDKit 决定哪根键画楔形（别自己猜哪根）
    Chem.AssignStereochemistry(mol, cleanIt=True, force=True)
    Chem.WedgeMolBonds(mol, mol.GetConformer())

    from rdkit.Chem import Descriptors, rdMolDescriptors

    # 问 RDKit 要双键的 E/Z（不要让导出时靠坐标去猜，见审计 F3）
    stereo_by_bond: Dict[int, str] = {}
    for position, bond in enumerate(mol.GetBonds(), start=1):
        if bond.GetBondType() != Chem.BondType.DOUBLE:
            continue
        stereo = bond.GetStereo()
        if stereo == Chem.BondStereo.STEREOE:
            stereo_by_bond[position] = "e"
        elif stereo == Chem.BondStereo.STEREOZ:
            stereo_by_bond[position] = "z"
        # STEREONONE / STEREOANY / STEREOCIS / STEREOTRANS 都不写：
        # 前两个是"没指定"，后两个是"按坐标"——我们只信 RDKit 的 E/Z 结论

    # 问 RDKit 要权威的手性中心（不自己猜哪根键该画楔形）——
    # 注意：这里报的是 **1 起的原子序号**，正好是 molblock 的编号口径
    centres = Chem.FindMolChiralCenters(mol, includeUnassigned=False,
                                        useLegacyImplementation=False)
    stereo = [index + 1 for index, _label in centres]

    block = Chem.MolToMolBlock(mol, kekulize=True)
    # 这里 keep_hydrogens=True 只表示"别再替我删一遍"：删不删由上面的参数决定
    # RDKit 的芳香性判定是权威的（它做真正的 Hückel 计算 + 环系分析），
    # 抄下来，别让导出时靠拓扑规则去猜（Kekulé 结构是任意的，稠环会猜错）。
    aromatic_ids = {atom.GetIdx() for atom in mol.GetAtoms() if atom.GetIsAromatic()}

    result = molblock_to_skeleton(block, bond_px=bond_px, keep_hydrogens=True,
                                  known_stereo=stereo, bond_stereo=stereo_by_bond,
                                  aromatic_atoms=aromatic_ids)
    result.source = "smiles"
    result.smiles = text
    result.formula = rdMolDescriptors.CalcMolFormula(mol)
    result.mw = round(Descriptors.MolWt(mol), 2)
    # **不要把 SMILES 当标题**：它是表达式不是名字。曾经这里填了 SMILES，
    # 于是对照页/预览图顶上印着 "C1CCCCC1" —— 要显示名字请显式传 title。
    return result


def looks_like_molblock(text: str) -> bool:
    """这段文本像不像 molblock/SDF？

    判定顺序很重要：JSON（`{`）要先被排除，SMILES 里不会出现 `M  END`，
    而 molblock 里可能含有 `{` 之类字符。
    """
    probe = str(text or "")
    if probe.lstrip().startswith("{"):
        return False
    return any(marker in probe
               for marker in ("V2000", "V3000", "M  END", "$$$$"))


def from_molblock(text: str, *, bond_px: float = DEFAULT_BOND_PX,
                  keep_hydrogens: bool = False,
                  allow_unknown_elements: bool = False) -> Structure2D:
    """V2000 molblock / SDF 的**第一条记录** → 可编辑骨架。"""
    # SDF 是多条记录用 "$$$$" 分隔的；只取第一条
    first = str(text).split("$$$$")[0]
    return molblock_to_skeleton(first, bond_px=bond_px,
                                keep_hydrogens=keep_hydrogens,
                                allow_unknown_elements=allow_unknown_elements)
