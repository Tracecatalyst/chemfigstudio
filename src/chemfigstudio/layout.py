"""chemfigstudio.layout — 声明式布局（P3 重构：让 figures.py 只声明结构）。

之前每种图的坐标都硬编码在 `figures.py` 里，想微调列位/行距就得改绘图逻辑。
这里把"版式"抽成**不可变数据**：

* :class:`Slot` —— 一行里的一个槽位（列的 x、垂直偏移、对齐、字号、颜色）
* :class:`RowLayout` —— 行式图（如醇系列）：画布宽、行距、各行槽位、标题
* :class:`BannerLayout` —— 一次性图（反应/酯化/焓对比/骨架）：画布尺寸 + 标题位置

`figures.py` 从此只负责"哪个槽位画什么"，坐标由布局给。改版式只改本文件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import core

__all__ = ["Slot", "RowLayout", "BannerLayout", "series_layout", "banner_layout",
           "figure_layout", "BANNERS"]

#: 显式不传颜色时用墨色（None 更安全：避免 import 期耦合 core 常量）
INK = core.INK
GREY = core.GREY
DELTA_RED = core.DELTA_RED


@dataclass(frozen=True)
class Slot:
    """行内的一个槽位（一列）。"""

    x: float
    dy: float = 0.0                  # 相对行中心的垂直偏移（负 = 偏上）
    align: str = "left"              # left | centre
    font_size: int = 25
    bold: bool = False
    sub_size: int = 17
    colour: Optional[Tuple[int, int, int]] = None    # None = 墨色

    def ink(self) -> Tuple[int, int, int]:
        return self.colour or INK


@dataclass(frozen=True)
class RowLayout:
    """行式图布局：每行一组槽位，行中心等距。"""

    width: int
    first_row_y: float
    pitch: float
    bottom_pad: float
    title_size: int = 26
    title_y: float = 24.0
    chain_dy: int = 36                     # 行式图里锯齿链的垂直步长（像素）
    slots: Dict[str, Slot] = field(default_factory=dict)

    # ------------------------------------------------------------------ 几何
    def row_y(self, index: int) -> float:
        """第 ``index`` 行（0 起）的行中心 y。"""
        return self.first_row_y + index * self.pitch

    def centres(self, rows: int) -> List[float]:
        return [self.row_y(i) for i in range(rows)]

    def height(self, rows: int) -> float:
        """画布高度：``first_row_y + pitch * rows + bottom_pad``（与原硬编码一致）。"""
        return self.first_row_y + self.pitch * max(0, rows) + self.bottom_pad

    def slot(self, name: str) -> Slot:
        if name not in self.slots:
            raise KeyError("layout has no slot %r (have: %s)"
                           % (name, ", ".join(sorted(self.slots))))
        return self.slots[name]


@dataclass(frozen=True)
class BannerLayout:
    """一次性图布局：固定画布 + 标题。"""

    width: int
    height: int
    title_y: float = 24.0
    title_size: int = 26
    title_bold: bool = True
    title_sub: int = 0                     # >0 表示标题用富文本（主字号 + 下标字号）
    margin: float = 90.0

    def content_width(self) -> float:
        return self.width - 2 * self.margin


def series_layout() -> RowLayout:
    """醇系列对照图的版式（原先硬编码在 series_figure 里的那套数字）。"""
    return RowLayout(
        width=1800,
        first_row_y=130.0,
        pitch=168.0,
        bottom_pad=90.0,
        chain_dy=36,
        slots={
            "structure": Slot(x=90, font_size=25, sub_size=17),
            "name": Slot(x=760, dy=-34, font_size=25, bold=True, sub_size=17),
            "formula": Slot(x=760, dy=2, font_size=20, sub_size=14, colour=GREY),
            "delta_h": Slot(x=1040, dy=-18, font_size=21, bold=True, sub_size=14,
                            colour=DELTA_RED),
        },
    )


def banner_layout(width: int, height: int, **kwargs) -> BannerLayout:
    return BannerLayout(width=width, height=height, **kwargs)


#: 一次性图的版式（原先硬编码在各 *_figure 里的画布尺寸与标题位置）
BANNERS: Dict[str, BannerLayout] = {
    "alcohol": BannerLayout(width=1500, height=420, title_y=34.0,
                            title_size=31, title_sub=21),
    "reaction": BannerLayout(width=2100, height=560, title_y=26.0,
                             title_size=31, title_sub=21),
    "chcount": BannerLayout(width=1700, height=520, title_y=28.0,
                            title_size=31, title_sub=21),
    "enthalpy": BannerLayout(width=1500, height=660, title_y=60.0, title_size=28),
    "esterification": BannerLayout(width=1500, height=470, title_y=46.0, title_size=26),
}


def figure_layout(kind: str) -> BannerLayout:
    """取某张一次性图的版式；未登记就报错（避免悄悄用默认值画出尺寸不对的图）。"""
    if kind not in BANNERS:
        raise KeyError("no banner layout for %r (have: %s)"
                       % (kind, ", ".join(sorted(BANNERS))))
    return BANNERS[kind]
