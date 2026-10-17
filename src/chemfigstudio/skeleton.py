"""chemfigstudio.skeleton — 可编辑骨架模型（阶段⑤ 的几何/数据层）。

一张"骨架"= 若干**原子**（2D 坐标 + 元素 + 可选显示文本）+ 若干**键**（连接关系 + 键级）。
编辑操作（加/移/删原子、加/删键、命中测试）都在这里，**与界面无关**，
因此可以被 CLI、GUI、测试、插件同等使用；序列化后直接存进文档的 ``molecules`` 字段。

坐标约定：**图坐标**（与 figures.py 同一套像素坐标），原点左上，``y`` 向下。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .errors import RenderError

__all__ = ["Atom", "Bond", "Skeleton", "Scene", "ELEMENTS", "ELEMENT_LABELS",
           "parse_skeleton_input", "VALENCE", "ValenceIssue",
           "DEFAULT_HIT_RADIUS",
           "DEFAULT_BOND_LENGTH", "ANGLE_STEP", "BOND_HIT_TOLERANCE", "MOLECULE_GAP"]

#: 支持的元素及其显示颜色（十六进制；渲染时交给 core 的颜色常量/默认墨色）
#:
#: 颜色大体沿用 CPK 惯例（C 墨色、O 红、N 蓝、卤素绿/棕、S 黄、P 橙）。
ELEMENTS: Dict[str, str] = {
    "C": "#111827", "H": "#6B7280", "O": "#D61F1F", "N": "#1D4ED8",
    "Cl": "#15803D", "Br": "#92400E", "S": "#A16207", "P": "#C2410C",
    "F": "#0F766E", "I": "#7E22CE", "B": "#B45309",
}
#: 元素切换时的默认显示文本（画示性式时最常用到的写法）
#:
#: 注意：这里给的是**基团写法**（``C`` → ``CH_{3}``），因为这是本工具的主场景。
#: 不在这张表里的元素（金属、稀有气体等）退回元素符号本身 —— 见 :meth:`Atom.text`。
ELEMENT_LABELS: Dict[str, str] = {
    "C": "CH_{3}", "H": "H", "O": "OH", "N": "NH_{2}",
    "S": "SH", "Cl": "Cl", "Br": "Br", "P": "PH_{2}",
    "F": "F", "I": "I", "B": "B",
}

#: 价键上限：元素 → 允许的最大键级之和。
#:
#: **目前只是数据，还没有检查逻辑**（见 docs/ROADMAP.md 的金牌项 ③）。
#: 加检查时请遵守"宁可漏报也不误报"：不在表里的元素一律不检查，
#: 因为配位化合物、自由基、非标准价态都可能合法。
#:
#: （2026-10 修正：这里原先指向一个从未实现的方法和一个从未分配的 ADR 编号，
#:   等于文档在描述不存在的东西。现在改成如实说明"只有数据、没有检查"。）
VALENCE: Dict[str, int] = {"H": 1, "B": 3, "C": 4, "N": 3, "O": 2, "F": 1,
                           "P": 5, "S": 6, "Cl": 1, "Br": 1, "I": 1}

DEFAULT_HIT_RADIUS = 26.0
DEFAULT_BOND_LENGTH = 92.0        # 标准键长（与 CHAIN_DX 一致）
ANGLE_STEP = 30.0                 # 键角吸附步长（度）
BOND_HIT_TOLERANCE = 12.0         # 命中一根键的容差（像素）
MOLECULE_GAP = 60.0               # 多分子纵向堆叠时的间距
ATOM_RADIUS = 17.0          # 渲染时原子的圆点半径


def _point_segment_distance(px: float, py: float, ax: float, ay: float,
                            bx: float, by: float) -> float:
    """点到线段的最短距离（命中一根键时要按"线段"算，不能按无限直线）。"""
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-9:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return ((px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2) ** 0.5


@dataclass
class ValenceIssue:
    """一个原子的价键超限。

    **只表示超价**（Σ键级 > 允许值）。欠价**不是**错误：结构式里氢是隐含的，
    只连 1 根键的碳其实是 CH₃。
    """

    atom_id: str
    element: str
    actual: int              # 该原子的键级之和（一根三键算 3）
    allowed: int             # 价键上限（已计入形式电荷）

    def text(self) -> str:
        """人可读的一句话（给 ``warnings`` / ``notes`` 用）。"""
        charge = ""
        return "%s 原子（%s）的键级和是 %d，超过上限 %d" % (
            self.element, self.atom_id, self.actual, self.allowed)


@dataclass
class Atom:
    """一个原子（或一个基团文本）。"""

    id: str
    element: str = "C"
    x: float = 0.0
    y: float = 0.0
    label: str = ""          # 空 = 用 element 作为显示文本（如 "CH_{3}"）
    charge: int = 0          # 形式电荷（导入真实分子时会有，如 N+ / O-）

    def text(self) -> str:
        """显示文本：用户标签优先，**空标签就显示元素符号**。

        注意别在这里回退到 ``ELEMENT_LABELS``：那张表是"切换元素时给个默认基团写法"
        （``C`` → ``CH_{3}``），不是渲染默认值。骨架碳本来就该**没有文字**（结构式里
        碳骨架是线），一旦回退成 ``CH_{3}``，整张图会变成一堆甲基。
        要不要真的画出这个符号，由渲染层决定（碳的孤立符号可以不画）。
        """
        return self.label or self.element

    def colour(self) -> str:
        return ELEMENTS.get(self.element, ELEMENTS["C"])

    def as_dict(self) -> Dict[str, Any]:
        data = {"id": self.id, "element": self.element, "x": round(self.x, 2),
                "y": round(self.y, 2), "label": self.label}
        if self.charge:
            data["charge"] = self.charge
        return data

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Atom":
        return cls(id=str(raw.get("id") or uuid.uuid4().hex[:6]),
                   element=str(raw.get("element") or "C"),
                   x=float(raw.get("x") or 0.0), y=float(raw.get("y") or 0.0),
                   label=str(raw.get("label") or ""),
                   charge=int(raw.get("charge") or 0))


#: 立体键的三种画法（"立体化学"最简单也最通用的一种表示）
WEDGE_NONE = "none"
WEDGE_UP = "up"          # 实心楔形：朝观察者（molblock 里是 1）
WEDGE_DOWN = "down"      # 虚线楔形：朝里（molblock 里是 6）
WEDGES = (WEDGE_NONE, WEDGE_UP, WEDGE_DOWN)


@dataclass
class Bond:
    """两个原子之间的一根键。"""

    a: str
    b: str
    order: int = 1           # 1 = 单键，2 = 双键
    #: 楔形（手性中心用）：none | up | down，从 a 指向 b，与绘制顺序一致。
    wedge: str = WEDGE_NONE
    #: **双键的 E/Z**：``""``（未指定）、``"e"``、``"z"``。
    #:
    #: 为什么要这个字段：V2000 的 bond stereo 列只能表达"这个双键是立体的，
    #: 按**坐标**解读"（0=either, 3=cis/trans）。如果只靠坐标，就会出现两个问题
    #: （审计 F3）：① 输入本来没指定几何的亚胺会被导出成确定的 (E)；
    #: ② 编辑器支持拖原子，拖一下就把反式变成顺式，而没有任何提示。
    #: 存下真实意图之后，导出时可以按字段**校正** RDKit 从坐标推出来的结论。
    stereo: str = ""

    def as_dict(self) -> Dict[str, Any]:
        data = {"a": self.a, "b": self.b, "order": int(self.order)}
        if self.wedge != WEDGE_NONE:
            data["wedge"] = self.wedge
        if self.stereo:
            data["stereo"] = self.stereo
        return data

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Bond":
        wedge = str(raw.get("wedge") or WEDGE_NONE).lower()
        stereo = str(raw.get("stereo") or "").lower()
        return cls(a=str(raw.get("a")), b=str(raw.get("b")),
                   order=max(1, min(3, int(raw.get("order") or 1))),
                   wedge=wedge if wedge in WEDGES else WEDGE_NONE,
                   stereo=stereo if stereo in ("e", "z") else "")


def parse_skeleton_input(text: str) -> "Skeleton":
    """把用户给的一坨文本变成骨架 —— **支持三种输入**，不新增参数。

    这是"导入真实结构"的入口，按内容自动判断：

    1. 以 ``{`` 开头 → 我们的骨架 JSON（老行为，原样解析）
    2. 含 ``V2000``/``M  END`` → molblock（V2000）或 SDF 第一条记录（不需要 RDKit）
    3. 其它 → SMILES（需要 RDKit 做 2D 布局与立体化学）

    顺序很重要：先认 JSON，再认 molblock，最后才当 SMILES —— 因为 molblock 里
    也可能出现 ``{`` 之类字符，而 SMILES 里不会出现 ``M  END``。
    """
    raw = str(text or "")
    if not raw.strip():
        return Skeleton.chain(4)
    stripped = raw.strip()
    if stripped.startswith("{"):
        return Skeleton.from_json(stripped)
    from .structure import from_molblock, from_smiles
    from .structure import looks_like_molblock

    if looks_like_molblock(raw):
        # ⚠️ **不能 strip**：molblock 的第一行是标题，可以为空。
        # strip 会把那行空标题吃掉，于是"计数行在第 4 行"的约定整体错位
        # （踩过：自己导出的空标题 molblock 读不回来，报"计数行读不出来"）。
        result = from_molblock(raw)
    else:
        result = from_smiles(stripped)
    skeleton = result.skeleton
    if not skeleton.title:
        skeleton.title = result.title
    return skeleton


@dataclass
class Skeleton:
    """一张可编辑骨架。"""

    atoms: List[Atom] = field(default_factory=list)
    bonds: List[Bond] = field(default_factory=list)
    title: str = ""

    # ------------------------------------------------------------------ 查询
    def atom(self, atom_id: str) -> Optional[Atom]:
        for item in self.atoms:
            if item.id == atom_id:
                return item
        return None

    def bounds(self) -> Tuple[float, float, float, float]:
        if not self.atoms:
            return (0.0, 0.0, 1.0, 1.0)
        xs = [a.x for a in self.atoms]
        ys = [a.y for a in self.atoms]
        return (min(xs), min(ys), max(xs), max(ys))

    def aromatic_rings(self) -> List[List[str]]:
        """找出应当画成**圆圈**的芳香环（Kekulé 式的环）。

        判定规则（纯拓扑，零依赖）：

        * 环长 5–7；
        * **环内每个原子都至少参与 1 根双键**（环内或环外都算）。

        为什么要求"至少一根"而不是"环内恰好一根"：萘的两个六元环共享两个桥头碳，
        其中只有一个环的双键是完全交替的（3 根），另一个环内只有 2 根双键 ——
        但那个环的每个原子都在**环外**（桥头碳在另一个环里）参与双键。
        按"恰好一根"判，萘只会有一个环画圆圈，视觉上很怪。

        这条规则的效果（都是刻意的）：
        * 苯 / 吡啶 / **萘（两个环都画）** ✅
        * 吡咯 / 呋喃 / 噻吩 ❌ 不画 —— 五元杂环的杂原子不带双键，课本也多用 Kekulé 式；
        * 环戊二烯 / 环己烯 / 环己二烯 ❌ 不画（有 sp3 碳不带双键，不是芳香）；
        * 环辛四烯 ❌ 不画（8 元环超出窗口，而且它本来就是盆式非芳香）。

        ⚠️ **局限**：这是拓扑启发式，不是真正的芳香性计算。理论上存在
        "每个原子都带 1 根双键却不芳香"的环，那种环会被误画圆圈。要精确判定
        得上 RDKit 的 ``GetIsAromatic``，但本工具保持零依赖 —— 宁可接受这个
        极少数误判，也不让"画个圆圈"变成必须装 RDKit 的功能。
        """
        adjacency: Dict[str, List[str]] = {}
        for bond in self.bonds:
            adjacency.setdefault(bond.a, []).append(bond.b)
            adjacency.setdefault(bond.b, []).append(bond.a)

        rings: Dict[frozenset, List[str]] = {}

        def walk(start: str, path: List[str], seen: set) -> None:
            if len(path) > 7:
                return
            for neighbour in adjacency.get(path[-1], ()):
                if neighbour == start and len(path) >= 5:
                    rings.setdefault(frozenset(path), list(path))
                elif neighbour not in seen and len(path) < 7:
                    seen.add(neighbour)
                    path.append(neighbour)
                    walk(start, path, seen)
                    path.pop()
                    seen.discard(neighbour)

        for atom_id in adjacency:
            walk(atom_id, [atom_id], {atom_id})

        # 只保留"每个原子恰好 1 根环内双键"的环
        aromatic: List[List[str]] = []
        for atom_set, path in rings.items():
            inside = [bond for bond in self.bonds
                      if bond.a in atom_set and bond.b in atom_set]
            if not any(bond.order == 2 for bond in inside):
                continue                      # 环上一根双键都没有，肯定不是芳香环
            # 每个环原子都要在环内**或环外**参与至少一根双键
            has_double: set = set()
            for bond in self.bonds:
                if bond.order == 2 and bond.a in atom_set:
                    has_double.add(bond.a)
                if bond.order == 2 and bond.b in atom_set:
                    has_double.add(bond.b)
            if all(atom in has_double for atom in atom_set):
                aromatic.append(path)
        return aromatic

    def stereo_conflicts(self) -> List[str]:
        """双键记录的 E/Z 与**当前坐标**对不上的地方（拖过原子就会对不上）。

        审计 F3 的第二个后果：立体信息如果只活在坐标里，编辑器拖一下原子
        就换了异构体而毫无提示。现在立体记在 :attr:`Bond.stereo` 上，但用户
        仍然可以在画布上拖 —— 那之后"画面看起来的样子"和"导出会写的东西"
        就分家了。这个方法把分家暴露出来，让调用方警告用户。

        只在双键两端各**恰好一个**取代基时判断（课本里的绝大多数情况）；
        多取代基要 CIP 优先级规则，超出本方法范围，一律跳过不报。
        """
        neighbours: Dict[str, List[str]] = {}
        for bond in self.bonds:
            neighbours.setdefault(bond.a, []).append(bond.b)
            neighbours.setdefault(bond.b, []).append(bond.a)
        by_id = {atom.id: atom for atom in self.atoms}

        def side(origin, tip, axis_from, axis_to):
            """tip 在 axis 的哪一侧（叉积符号）。"""
            ox, oy = axis_to.x - axis_from.x, axis_to.y - axis_from.y
            tx, ty = tip.x - origin.x, tip.y - origin.y
            return 1 if (ox * ty - oy * tx) > 0 else -1

        problems: List[str] = []
        for bond in self.bonds:
            if bond.order != 2 or bond.stereo not in ("e", "z"):
                continue
            left = [n for n in neighbours.get(bond.a, []) if n != bond.b]
            right = [n for n in neighbours.get(bond.b, []) if n != bond.a]
            if len(left) != 1 or len(right) != 1:
                continue                     # 末端双键 / 多取代基：跳过
            a, b = by_id.get(bond.a), by_id.get(bond.b)
            c, d = by_id.get(left[0]), by_id.get(right[0])
            if not all((a, b, c, d)):
                continue
            same_side = side(a, c, a, b) == side(b, d, a, b)
            drawn = "z" if same_side else "e"
            if drawn != bond.stereo:
                problems.append(
                    "%s=%s 记录的是 %s，但画出来是 %s（拖过原子？）"
                    % (bond.a, bond.b, bond.stereo.upper(), drawn.upper()))
        return problems

    def valence_errors(self) -> List[ValenceIssue]:
        """找出**超价**的原子：Σ键级 > 价键上限。

        规则（见 docs/ROADMAP.md 金牌项 ③）：

        * **只看键级之和，不看键数** —— 一根三键算 3，所以 ``=C=`` 两个双键是 4（合法），
          再加一根单键就是 5（超价）。
        * **不报欠价**：氢在结构式里是隐含的，只连 1 根键的碳就是 CH₃。
          按"必须正好 4 根键"去查的话，每个骨架碳都会误报。
        * **电荷修正**：正电荷允许多一根键（铵根 N⁺ 有 4 根是合法的），
          负电荷少一根（O⁻ 只有 1 根）。
        * **不在 ``VALENCE`` 表里的元素不检查**：配位化合物、自由基、非标准价态
          都真实存在 —— 宁可漏报，也不误报（误报会让人把这个功能关掉）。
        """
        order_sum: Dict[str, int] = {}
        for bond in self.bonds:
            order_sum[bond.a] = order_sum.get(bond.a, 0) + bond.order
            order_sum[bond.b] = order_sum.get(bond.b, 0) + bond.order

        issues: List[ValenceIssue] = []
        for atom in self.atoms:
            base = VALENCE.get(atom.element)
            if base is None:                       # 不认识的元素：不检查
                continue
            allowed = base + int(atom.charge)
            actual = order_sum.get(atom.id, 0)
            if actual > allowed:
                issues.append(ValenceIssue(atom_id=atom.id, element=atom.element,
                                           actual=actual, allowed=allowed))
        return issues

    def hit_test(self, x: float, y: float, radius: float = DEFAULT_HIT_RADIUS) -> Optional[Atom]:
        """返回离 ``(x, y)`` 最近且在 ``radius`` 内的原子（拖拽/选中用）。"""
        best: Optional[Atom] = None
        best_distance = float(radius)
        for item in self.atoms:
            distance = ((item.x - x) ** 2 + (item.y - y) ** 2) ** 0.5
            if distance <= best_distance:
                best, best_distance = item, distance
        return best

    def hit_test_bond(self, x: float, y: float,
                      tolerance: float = BOND_HIT_TOLERANCE) -> Optional[Bond]:
        """返回离 ``(x, y)`` 最近且在 ``tolerance`` 内的键（点—线段距离）。"""
        best: Optional[Bond] = None
        best_distance = float(tolerance)
        for bond in self.bonds:
            first, second = self.atom(bond.a), self.atom(bond.b)
            if first is None or second is None:
                continue
            distance = _point_segment_distance(x, y, first.x, first.y, second.x, second.y)
            if distance <= best_distance:
                best, best_distance = bond, distance
        return best

    def set_bond_order(self, a: str, b: str, order: int) -> Bond:
        """改已有键的键级（1 单 / 2 双 / 3 三）。"""
        bond = self.find_bond(a, b)
        if bond is None:
            raise RenderError("no bond between %s and %s" % (a, b))
        bond.order = max(1, min(3, int(order)))
        return bond

    def cycle_bond_order(self, a: str, b: str) -> int:
        """键级循环 1 → 2 → 3 → 1（界面上"点一下切键级"用），返回新键级。"""
        bond = self.find_bond(a, b)
        if bond is None:
            raise RenderError("no bond between %s and %s" % (a, b))
        bond.order = 1 if bond.order >= 3 else bond.order + 1
        return bond.order

    def snap_position(self, x: float, y: float, *, reference: Optional[str] = None,
                      length: float = DEFAULT_BOND_LENGTH,
                      angle_step: float = ANGLE_STEP,
                      max_distance: float = 260.0) -> Tuple[float, float]:
        """把 ``(x, y)`` 吸附成"化学上好看"的位置（阶段⑤ 进阶）。

        * 参照点 = ``reference`` 指定的原子，否则取 ``max_distance`` 内最近的原子
        * **键角**吸附到 ``angle_step`` 的整数倍（默认 30°）
        * **键长**吸附到 ``length`` 的整数倍（92 = 一根键，184 = 两根键长）
        * 没有可参照的原子 → 原样返回（不硬吸）
        """
        import math

        ref = self.atom(reference) if reference else None
        if ref is None:
            ref = self.hit_test(x, y, max_distance)
        if ref is None:
            return (float(x), float(y))

        dx, dy = float(x) - ref.x, float(y) - ref.y
        distance = math.hypot(dx, dy)
        if distance < 1e-6:
            return (ref.x + length, ref.y)
        angle = math.degrees(math.atan2(dy, dx))
        snapped_angle = round(angle / angle_step) * angle_step
        multiple = max(1, int(round(distance / length))) if length > 0 else 1
        snap_length = length * multiple
        radians = math.radians(snapped_angle)
        return (ref.x + math.cos(radians) * snap_length,
                ref.y + math.sin(radians) * snap_length)

    # ------------------------------------------------------------------ 编辑
    def add_atom(self, element: str = "C", x: float = 0.0, y: float = 0.0,
                 label: str = "", atom_id: Optional[str] = None) -> Atom:
        if element not in ELEMENTS:
            raise RenderError("unsupported element %r (known: %s)"
                              % (element, ", ".join(sorted(ELEMENTS))))
        atom = Atom(id=atom_id or uuid.uuid4().hex[:6], element=element, x=float(x),
                    y=float(y), label=label)
        self.atoms.append(atom)
        return atom

    def change_element(self, atom_id: str, element: str,
                       label: Optional[str] = None) -> Atom:
        """切换原子元素（阶段⑤ 收尾）。

        ``label`` 省略时：若原标签还是"上一元素的默认标签"（或空），就换成新元素的默认标签；
        用户自定义过的标签**不动**（不覆盖人家写的东西）。
        """
        atom = self.atom(atom_id)
        if atom is None:
            raise RenderError("no such atom: %s" % atom_id)
        if element not in ELEMENTS:
            raise RenderError("unsupported element %r (known: %s)"
                              % (element, ", ".join(sorted(ELEMENTS))))
        default_old = ELEMENT_LABELS.get(atom.element, atom.element)
        if label is not None:
            atom.label = str(label)
        elif not atom.label or atom.label == default_old:
            atom.label = ELEMENT_LABELS.get(element, element)
        atom.element = element
        return atom

    def move_atom(self, atom_id: str, x: float, y: float) -> Atom:
        atom = self.atom(atom_id)
        if atom is None:
            raise RenderError("no such atom: %s" % atom_id)
        atom.x, atom.y = float(x), float(y)
        return atom

    def remove_atom(self, atom_id: str) -> bool:
        """删原子并**连带删掉它的键**（避免留下悬空键）。"""
        before = len(self.atoms)
        self.atoms = [a for a in self.atoms if a.id != atom_id]
        if len(self.atoms) == before:
            return False
        self.bonds = [b for b in self.bonds if atom_id not in (b.a, b.b)]
        return True

    def add_bond(self, a: str, b: str, order: int = 1) -> Bond:
        if a == b:
            raise RenderError("a bond needs two different atoms")
        for atom_id in (a, b):
            if self.atom(atom_id) is None:
                raise RenderError("no such atom: %s" % atom_id)
        if self.find_bond(a, b) is not None:
            raise RenderError("bond already exists: %s-%s" % (a, b))
        bond = Bond(a=a, b=b, order=max(1, min(3, int(order))))
        self.bonds.append(bond)
        return bond

    def find_bond(self, a: str, b: str) -> Optional[Bond]:
        for bond in self.bonds:
            if {bond.a, bond.b} == {a, b}:
                return bond
        return None

    def remove_bond(self, a: str, b: str) -> bool:
        bond = self.find_bond(a, b)
        if bond is None:
            return False
        self.bonds.remove(bond)
        return True

    # ------------------------------------------------------------ 序列化
    def as_dict(self) -> Dict[str, Any]:
        return {"title": self.title,
                "atoms": [a.as_dict() for a in self.atoms],
                "bonds": [b.as_dict() for b in self.bonds]}

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Skeleton":
        if not isinstance(raw, dict):
            raise RenderError("skeleton must be an object")
        skeleton = cls(title=str(raw.get("title") or ""))
        skeleton.atoms = [Atom.from_dict(a) for a in (raw.get("atoms") or [])]
        known = {a.id for a in skeleton.atoms}
        skeleton.bonds = [Bond.from_dict(b) for b in (raw.get("bonds") or [])
                          if b.get("a") in known and b.get("b") in known]
        return skeleton

    @classmethod
    def from_json(cls, text: str) -> "Skeleton":
        try:
            raw = json.loads(text or "{}")
        except json.JSONDecodeError as exc:
            raise RenderError("skeleton JSON is invalid: %s" % exc) from exc
        return cls.from_dict(raw)

    # ------------------------------------------------------------------ 生成
    @classmethod
    def chain(cls, n_carbons: int = 4, *, x0: float = 120.0, ymid: float = 160.0,
              dx: float = 92.0, dy: float = 50.0) -> "Skeleton":
        """从直链烷基 + 末端 OH 生成骨架（坐标与 figures 的锯齿链一致，便于对照）。"""
        count = max(1, int(n_carbons))
        skeleton = cls(title="")
        previous: Optional[Atom] = None
        for index in range(count):
            atom = skeleton.add_atom("C", x0 + index * dx,
                                     ymid + (dy if index % 2 else -dy),
                                     label=("CH_{3}" if index == 0 else "CH_{2}"))
            if previous is not None:
                skeleton.add_bond(previous.id, atom.id)
            previous = atom
        oxygen = skeleton.add_atom("O", x0 + count * dx,
                                   ymid + (dy if count % 2 else -dy), label="OH")
        if previous is not None:
            skeleton.add_bond(previous.id, oxygen.id)
        return skeleton


@dataclass
class Scene:
    """多分子（图层）：一张图里放若干个骨架，纵向堆叠。

    JSON 兼容两种写法：单分子 ``{"atoms": …, "bonds": …}``（旧格式，继续可用）
    与多分子 ``{"molecules": [{name, atoms, bonds}, …]}``。
    """

    molecules: List[Skeleton] = field(default_factory=list)
    names: List[str] = field(default_factory=list)
    active: int = 0

    # ------------------------------------------------------------------ 构造
    @classmethod
    def single(cls, skeleton: Optional[Skeleton] = None, name: str = "") -> "Scene":
        return cls(molecules=[skeleton or Skeleton.chain(4)], names=[name], active=0)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Scene":
        if not isinstance(raw, dict):
            raise RenderError("scene must be an object")
        if raw.get("molecules"):
            scene = cls()
            for item in raw["molecules"]:
                scene.molecules.append(Skeleton.from_dict(item))
                scene.names.append(str(item.get("name") or ""))
            scene.active = max(0, min(int(raw.get("active") or 0), len(scene.molecules) - 1))
            return scene
        return cls.single(Skeleton.from_dict(raw), str(raw.get("name") or ""))

    @classmethod
    def from_json(cls, text: str) -> "Scene":
        try:
            raw = json.loads(text or "{}")
        except json.JSONDecodeError as exc:
            raise RenderError("skeleton JSON is invalid: %s" % exc) from exc
        return cls.from_dict(raw)

    # ------------------------------------------------------------------ 行为
    def add(self, skeleton: Optional[Skeleton] = None, name: str = "") -> Skeleton:
        molecule = skeleton or Skeleton.chain(4)
        self.molecules.append(molecule)
        self.names.append(name)
        self.active = len(self.molecules) - 1
        return molecule

    def remove(self, index: int) -> bool:
        if not (0 <= index < len(self.molecules)) or len(self.molecules) <= 1:
            return False                                   # 至少留一个分子
        self.molecules.pop(index)
        self.names.pop(index)
        self.active = max(0, min(self.active, len(self.molecules) - 1))
        return True

    def select(self, index: int) -> bool:
        if 0 <= index < len(self.molecules):
            self.active = index
            return True
        return False

    def current(self) -> Skeleton:
        return self.molecules[self.active]

    # ------------------------------------------------------------------ 几何
    def offsets(self, gap: float = MOLECULE_GAP) -> List[Tuple[float, float]]:
        """每个分子的绘制偏移：纵向依次堆叠（各自按其包围盒高度让位）。"""
        result: List[Tuple[float, float]] = []
        offset_y = 0.0
        for molecule in self.molecules:
            _x0, y0, _x1, y1 = molecule.bounds()
            result.append((0.0, offset_y - y0))
            offset_y += (y1 - y0) + gap
        return result

    def bounds(self, gap: float = MOLECULE_GAP) -> Tuple[float, float, float, float]:
        boxes = []
        for (dx, dy), molecule in zip(self.offsets(gap), self.molecules):
            x0, y0, x1, y1 = molecule.bounds()
            boxes.append((x0 + dx, y0 + dy, x1 + dx, y1 + dy))
        if not boxes:
            return (0.0, 0.0, 1.0, 1.0)
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    # ------------------------------------------------------------ 序列化
    def as_dict(self) -> Dict[str, Any]:
        return {"active": self.active,
                "molecules": [dict(m.as_dict(), name=self.names[i] if i < len(self.names) else "")
                              for i, m in enumerate(self.molecules)]}

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False)
