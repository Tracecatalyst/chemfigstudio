"""chemfigstudio.figures — 四种图的构造函数。

每种图都是纯函数风格：``xxx_figure(out, **params) -> FigureResult``，
不打印、不 exit，出错靠异常 / ``warnings`` 表达（这样才能被 GUI 与测试复用）。

单位写法面向 10 年级：``kJ/g``、``kJ/mol``（**不用负指数**）。
键名一律用连字符：``C-H``、``C-C``、``O-H``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import Image

from . import core
from .layout import RowLayout, figure_layout, series_layout
from .core import (
    DELTA_RED, GREY, INK, OXYGEN_RED, WHITE,
    ChainStyle, SaveReport,
)

__all__ = [
    "FigureResult", "ALCOHOL_SERIES", "COMBUSTION_ENTHALPY_KJ_PER_MOL",
    "alcohol_figure", "series_figure", "reaction_figure", "chcount_figure",
    "chain_style", "draw_lone_pairs",
]


# ------------------------------------------------------- 示性式的"化学词汇表"
#
# 这些字样**只能出现在本层**（ARCHITECTURE §6.5）：core.py 只负责把给定的文字摆到
# 几何位置上，不认识"醇""羟基"。所以示性式的所有标签在这里组装。
_ALCOHOL_VOCAB = {
    "head": "CH_{3}",                    # 链首：甲基
    "vertex_h2": "H_{2}",                # 转角写 H2（课本骨架式）
    "vertex_ch2": "CH_{2}",              # 转角写 CH2（示性式写法）
    "end": "OH",                         # 羟基
    "note_vertex": "2 C-H",              # 转角碳上的 C-H 计数
    "note_head": "3 C-H",                # 甲基碳上的 C-H 计数
}


def chain_style(n_carbons: int, *, label_style: str = "h2", note: bool = False,
                note_size: int = 13, **geometry) -> ChainStyle:
    """组装一条**醇**示性式锯齿链的样式（化学词汇都在这里，core 不认识它们）。

    ``**geometry`` 透传给 :class:`core.ChainStyle`（``dx`` / ``dy`` / ``label_size`` …）。
    """
    vertex = _ALCOHOL_VOCAB["vertex_ch2"] if label_style == "ch2" \
        else _ALCOHOL_VOCAB["vertex_h2"]
    return ChainStyle(n_carbons=n_carbons, label_style=label_style, note=note,
                      note_size=note_size,
                      head_label=_ALCOHOL_VOCAB["head"],
                      vertex_label=vertex,
                      end_label=_ALCOHOL_VOCAB["end"],
                      end_colour=OXYGEN_RED,          # 氧画红（颜色常量仍在 core）
                      end_lone_pairs=True,
                      note_label=_ALCOHOL_VOCAB["note_vertex"] if note else "",
                      note_head_label=_ALCOHOL_VOCAB["note_head"] if note else "",
                      **geometry)


def draw_lone_pairs(draw, ox: float, oy: float, font_size: float,
                    colour=OXYGEN_RED) -> None:
    """在原子标签旁画两对点，并把它解释成**孤对电子**（化学含义在本层）。

    几何由 :func:`core.draw_dot_pairs` 提供 —— 那也是 core 里不带化学名字的图元。
    """
    core.draw_dot_pairs(draw, ox, oy, font_size, colour)


@dataclass
class FigureResult:
    """一次成图的结果（GUI 直接把它转成 JSON 返回给前端）。"""

    kind: str
    report: SaveReport
    smiles_info: Optional[object] = None      # 由 api.render() 在用了 SMILES 时填入

    @property
    def path(self) -> Path:
        return self.report.path

    @property
    def size(self) -> Tuple[int, int]:
        return self.report.size

    @property
    def warnings(self) -> List[str]:
        return self.report.warnings

    @property
    def outputs(self) -> Dict[str, str]:
        """各格式的实际文件路径（{"png": …, "svg": …, "pdf": …}）。"""
        return self.report.outputs

    def as_dict(self) -> Dict[str, object]:
        data = self.report.as_dict()
        data["kind"] = self.kind
        if self.smiles_info is not None and hasattr(self.smiles_info, "as_dict"):
            data["smiles"] = self.smiles_info.as_dict()
        return data


#: 五种醇的默认系列数据（名称, 化学式标记, 碳数, 文献 ΔHc / kJ g^-1）。
#: 数值来源：NIST Chemistry WebBook（老师发的数据表同源）。
ALCOHOL_SERIES: Tuple[Tuple[str, str, int, str], ...] = (
    ("ethanol",     "C_{2}H_{5}OH",  2, "−29.7"),
    ("propan-1-ol", "C_{3}H_{7}OH",  3, "−33.6"),
    ("butan-1-ol",  "C_{4}H_{9}OH",  4, "−36.1"),
    ("pentan-1-ol", "C_{5}H_{11}OH", 5, "−37.8"),
    ("hexan-1-ol",  "C_{6}H_{13}OH", 6, "−39.0"),
)

#: 文献燃烧焓（kJ/mol），用于燃烧反应图的 ΔH 标注；可按需覆盖。
COMBUSTION_ENTHALPY_KJ_PER_MOL: Dict[int, int] = {
    2: -1367, 3: -2021, 4: -2676, 5: -3331, 6: -3980,
}


def _combustion_coefficients(n_carbons: int) -> Tuple[int, int, int, int, Optional[int]]:
    """返回 (醇系数, O2, CO2, H2O, 放大倍数)。

    ``C_nH_(2n+1)OH + 1.5n O2 -> n CO2 + (n+1) H2O``；
    n 为奇数时 1.5n 不是整数，于是整条方程式乘 2。
    """
    n = int(n_carbons)
    if (3 * n) % 2 == 0:
        return 1, (3 * n) // 2, n, n + 1, None
    return 2, 3 * n, 2 * n, 2 * n + 2, 2


# --------------------------------------------------------------------- 单分子图

def alcohol_figure(out, n_carbons: int = 4, name: Optional[str] = None,
                   delta_h: Optional[str] = None, label_style: str = "h2",
                   title: Optional[str] = None,
                   *, formats: Sequence[str] = ("png",),
                   dpi: Optional[int] = None,
                   transparent: bool = False,
                   scale_override: Optional[float] = None) -> FigureResult:
    """单张醇的示性式图（默认丁醇，可带 ΔHc 标注）。"""
    n = max(1, int(n_carbons))
    name = name or "C%d alcohol" % n
    title = title if title is not None else name

    banner = figure_layout("alcohol")
    width, height = banner.width, banner.height
    scale = scale_override if scale_override is not None else core.dpi_scale(width, height, dpi)

    surface = core.new_surface(width, height, scale=scale, svg="svg" in formats)

    image, draw = surface.image, surface
    if title:
        core.draw_rich_centred(draw, banner.title_y, title,
                               core.fonts(banner.title_size, banner.title_bold),
                               core.fonts(banner.title_sub), cx=560)
    core.draw_chain(draw, 130, 200, chain_style(n, label_style=label_style))
    if delta_h:
        core.draw_rich_centred(draw, 330, "ΔH_{c} = %s kJ/g" % delta_h,
                               core.fonts(27, True), core.fonts(19), cx=560, fill=DELTA_RED)
    report = core.save_figure(surface, out, formats=formats, dpi=dpi,
                                     transparent=transparent)
    return FigureResult("alcohol", report)


# ------------------------------------------------------------------------ 系列图

METHANOL_ROW: Tuple[str, str, int, str] = ("methanol", "CH_{3}OH", 1, "−22.7")


def series_figure(out, entries: Optional[Sequence[Sequence[object]]] = None,
                  include_methanol: bool = False,
                  title: str = "The alcohol series, from the shortest chain to the longest",
                  show_delta_h: bool = True,
                   *, layout: "Optional[RowLayout]" = None,
                   formats: Sequence[str] = ("png",),
                   dpi: Optional[int] = None,
                   transparent: bool = False,
                   scale_override: Optional[float] = None) -> FigureResult:
    """五种醇的系列对照图（每行：结构 + 名称 + 化学式 + 文献 ΔHc）。

    **版式来自** :func:`chemfigstudio.layout.series_layout`（列位/行距/字号），
    本函数只声明"每行哪个槽位画什么" —— P3 布局层重构。传 ``layout`` 可整套换版式。
    """
    data = list(entries) if entries else list(ALCOHOL_SERIES)
    if include_methanol:
        data = [METHANOL_ROW] + [row for row in data if row[0] != "methanol"]
    grid = layout or series_layout()
    width = grid.width
    pitch = grid.pitch
    height = int(round(grid.height(len(data))))
    chain_dy = grid.chain_dy
    structure, name_slot = grid.slot("structure"), grid.slot("name")
    formula_slot, delta_slot = grid.slot("formula"), grid.slot("delta_h")

    scale = scale_override if scale_override is not None else core.dpi_scale(width, height, dpi)


    surface = core.new_surface(width, height, scale=scale, svg="svg" in formats)


    image, draw = surface.image, surface
    core.draw_rich(draw, 40, 30, title, core.fonts(31, True), core.fonts(21))
    if show_delta_h:
        draw.text((1000, 64), "ΔHc (literature)", font=core.fonts(19), fill=GREY)
        core.draw_rich(draw, 1000, 88, "kJ/g", core.fonts(19), core.fonts(13), fill=GREY)

    centres: List[int] = []
    for i, entry in enumerate(data):
        nm, formula, n, dh = entry[0], entry[1], int(float(str(entry[2]))), entry[3]
        ym = int(grid.row_y(i))
        centres.append(ym)
        core.draw_chain(draw, structure.x, ym,
                        chain_style(n, label_size=structure.font_size,
                                    sub_size=structure.sub_size, dy=chain_dy))
        core.draw_rich(draw, name_slot.x, ym + name_slot.dy, str(nm),
                       core.fonts(name_slot.font_size, name_slot.bold),
                       core.fonts(name_slot.sub_size))
        core.draw_rich(draw, formula_slot.x, ym + formula_slot.dy, str(formula),
                       core.fonts(formula_slot.font_size),
                       core.fonts(formula_slot.sub_size), fill=formula_slot.ink())
        if show_delta_h and dh:
            core.draw_rich(draw, delta_slot.x, ym + delta_slot.dy, "%s kJ/g" % dh,
                           core.fonts(delta_slot.font_size, delta_slot.bold),
                           core.fonts(delta_slot.sub_size), fill=delta_slot.ink())

    report = core.save_figure(surface, out, formats=formats, dpi=dpi,
                                     transparent=transparent)

    # 行重叠自检（精确版，与缩放无关）：见 _series_overlap_warnings 的说明
    report.warnings.extend(_series_overlap_warnings(image, centres, pitch, scale))
    return FigureResult("series", report)


# ---------------------------------------------------------------------- 反应图

def reaction_figure(out, n_carbons: int = 4,
                    delta_h_kj_per_mol: Optional[int] = None,
                    note: str = ("Condensed structures (Lewis-style): oxygen atoms are red "
                                 "and their lone pairs are shown as dots."),
                   width: Optional[int] = None,
                   *, formats: Sequence[str] = ("png",),
                   dpi: Optional[int] = None,
                   transparent: bool = False,
                   scale_override: Optional[float] = None) -> FigureResult:
    """完全燃烧反应图：结构 + 系数 + 箭头 + 总方程式 + ΔH。"""
    n = max(1, int(n_carbons))
    a_coef, o2, co2, h2o, _scale = _combustion_coefficients(n)
    if delta_h_kj_per_mol is None:
        delta_h_kj_per_mol = COMBUSTION_ENTHALPY_KJ_PER_MOL.get(n)

    banner = figure_layout("reaction")
    base_width = banner.width
    width = int(width) if width else base_width    # 大碳数时会被自动加宽（见函数末尾）
    height = banner.height
    scale = scale_override if scale_override is not None else core.dpi_scale(base_width, height, dpi)
    equation_cx = 700 if width == base_width else width / 2.0

    surface = core.new_surface(width, height, scale=scale, svg="svg" in formats)

    image, draw = surface.image, surface
    core.draw_rich(draw, 40, banner.title_y, "Complete combustion of %s in excess oxygen"
                   % ("butan-1-ol" if n == 4 else "C%dH%dOH" % (n, 2 * n + 1)),
                   core.fonts(banner.title_size, banner.title_bold),
                   core.fonts(banner.title_sub))

    ym = 210
    x_after_oh = core.draw_chain(draw, 90, ym, chain_style(n))
    draw.text((x_after_oh + 44, ym - 58), "+", font=core.fonts(34, True), fill=INK)

    x = x_after_oh + 104
    if a_coef > 1:      # 奇数碳时方程式整体乘 2，系数标在分子左边
        core.draw_rich(draw, 40, ym - 58, "%d" % a_coef, core.fonts(30), core.fonts(21))
    core.draw_formula(draw, x, ym - 58, "%d O=O" % o2, core.fonts(30), core.fonts(21))

    arrow_x0, arrow_y = x + 230, ym
    draw.line([arrow_x0, arrow_y, arrow_x0 + 100, arrow_y], fill=INK, width=5)
    draw.polygon([(arrow_x0 + 112, arrow_y), (arrow_x0 + 88, arrow_y - 11),
                  (arrow_x0 + 88, arrow_y + 11)], fill=INK)
    draw.text((arrow_x0 + 10, ym - 58), "ignite", font=core.fonts(19), fill=GREY)

    xc = x + 380
    core.draw_formula(draw, xc, ym - 58, "%d O=C=O" % co2, core.fonts(30), core.fonts(21))
    draw.text((xc + 250, ym - 58), "+", font=core.fonts(34, True), fill=INK)
    core.draw_formula(draw, xc + 300, ym - 58, "%d H-O-H" % h2o, core.fonts(30), core.fonts(21))

    # 总方程式（含系数）
    lh = ("C_{%d}H_{%d}OH" % (n, 2 * n + 1)) if a_coef == 1 else ("%d C_{%d}H_{%d}OH" % (a_coef, n, 2 * n + 1))
    equation = "%s  +  %d O_{2}   →   %d CO_{2}  +  %d H_{2}O" % (lh, o2, co2, h2o)
    core.draw_rich_centred(draw, ym + 120, equation, core.fonts(42, True), core.fonts(27), cx=equation_cx)
    if delta_h_kj_per_mol:
        core.draw_rich_centred(draw, ym + 186,
                               "ΔH = %d kJ/mol" % delta_h_kj_per_mol,
                               core.fonts(27, True), core.fonts(19), cx=equation_cx, fill=DELTA_RED)
    if note:
        core.draw_rich(draw, 40, height - 70, note, core.fonts(19), core.fonts(13), fill=GREY)

    # 自检：内容被画布右边缘切掉 → 换更宽的画布重画一次（大碳数/大系数时会发生）
    # 注意：墨迹框是**设备像素**，必须与 surface.width 比（比逻辑宽度是单位错误，会误判）
    box = core.ink_box(image, margin=0)
    if box and box[2] >= surface.width - 1 and width == base_width:
        return reaction_figure(out, n_carbons=n_carbons,
                               delta_h_kj_per_mol=delta_h_kj_per_mol, note=note,
                               width=int(base_width * 1.7), formats=formats, dpi=dpi,
                               transparent=transparent, scale_override=scale)

    return FigureResult("reaction", core.save_figure(surface, out, formats=formats, dpi=dpi,
                                     transparent=transparent))


# ------------------------------------------------------------------- C-H 计数图

def chcount_figure(out, n_carbons: int = 4,
                   summary: Optional[str] = None,
                   note: str = ("Every carbon forms four bonds; the bonds that do not join "
                                "another carbon join hydrogen, and those are the C-H bonds."),
                   *, formats: Sequence[str] = ("png",),
                   dpi: Optional[int] = None,
                   transparent: bool = False,
                   scale_override: Optional[float] = None) -> FigureResult:
    """标出每个 CH2 / CH3 上的 C-H 键数目（服务 Criterion B 的假设论证）。"""
    n = max(1, int(n_carbons))
    if summary is None:
        summary = "%d C-H bonds   ·   %d C-C bonds   ·   1 O-H bond" % (2 * n + 1, n - 1)

    banner = figure_layout("chcount")
    width, height = banner.width, banner.height
    scale = scale_override if scale_override is not None else core.dpi_scale(width, height, dpi)

    surface = core.new_surface(width, height, scale=scale, svg="svg" in formats)

    image, draw = surface.image, surface
    core.draw_rich(draw, 40, banner.title_y, "Where the C-H bonds are in %s"
                   % ("butan-1-ol" if n == 4 else "C%dH%dOH" % (n, 2 * n + 1)),
                   core.fonts(banner.title_size, banner.title_bold),
                   core.fonts(banner.title_sub))
    core.draw_chain(draw, 120, 250, chain_style(n, note=True, note_size=19))
    core.draw_rich(draw, 120, 430, summary, core.fonts(26, True), core.fonts(18))
    if note:
        core.draw_rich(draw, 120, 478, note, core.fonts(19), core.fonts(13), fill=GREY)
    return FigureResult("chcount", core.save_figure(surface, out, formats=formats, dpi=dpi,
                                     transparent=transparent))


def _series_overlap_warnings(image, centres: Sequence[float], pitch: float,
                             scale: float) -> List[str]:
    """精确的行重叠检测：算每行在**自己的行带**里的墨迹外框。

    只有在相邻两行的墨迹**都越过行间距中线**时才告警 —— 与 DPI/缩放无关。

    旧版做法是"在设备像素图里找一条全白行"，但它用未缩放坐标去索引缩放后的
    图像（``scale > 1`` 时查错了行），所以自适应重绘后会误报。
    """
    from PIL import ImageChops

    warnings: List[str] = []
    half = pitch / 2.0
    boxes: List[Optional[Tuple[int, int]]] = []
    for centre in centres:
        top = max(0, int(round((centre - half) * scale)))
        bottom = min(image.height, int(round((centre + half) * scale)))
        if bottom - top < 2:
            boxes.append(None)
            continue
        strip = image.crop((0, top, image.width, bottom)).convert("RGB")
        bbox = ImageChops.difference(strip, Image.new("RGB", strip.size, WHITE)).getbbox()
        boxes.append((top + bbox[1], top + bbox[3]) if bbox else None)

    for index in range(len(centres) - 1):
        first, second = boxes[index], boxes[index + 1]
        if first is None or second is None:
            continue
        boundary = (centres[index] + half) * scale          # 行间距中线（设备像素）
        if first[1] >= boundary - 1 and second[0] <= boundary + 1:
            warnings.append(
                "第 %d 行与 %d 行可能重叠（两行墨迹都越过行间距中线）：请调大 PITCH"
                % (index + 1, index + 2))
    return warnings

#: 班级数据 vs 文献（kJ/g）—— 来自本次实验报告，供 enthalpy 对比图使用
ENTHALPY_SERIES: Tuple[Tuple[str, float, float], ...] = (
    ("ethanol", 8.93, 29.7),
    ("propan-1-ol", 6.24, 33.6),
    ("butan-1-ol", 10.21, 36.1),
    ("pentan-1-ol", 14.35, 37.8),
    ("hexan-1-ol", 16.69, 39.0),
)

_ACID_NAMES = {1: "methanoic acid", 2: "ethanoic acid", 3: "propanoic acid",
               4: "butanoic acid", 5: "pentanoic acid", 6: "hexanoic acid"}
_ALKYL_NAMES = {1: "methyl", 2: "ethyl", 3: "propyl", 4: "butyl", 5: "pentyl", 6: "hexyl"}
_ALKANOL_NAMES = {1: "methanol", 2: "ethanol", 3: "propan-1-ol", 4: "butan-1-ol",
                  5: "pentan-1-ol", 6: "hexan-1-ol"}
_ALKYL_FORMULA = {1: "CH_{3}", 2: "C_{2}H_{5}", 3: "C_{3}H_{7}", 4: "C_{4}H_{9}",
                  5: "C_{5}H_{11}", 6: "C_{6}H_{13}"}


def _centre_text(draw, y: float, text: str, font, fill=INK,
                 width: Optional[float] = None) -> None:
    """水平居中画一行富文本（用 text_width 量宽，不依赖画布内部字段）。"""
    canvas_width = width if width is not None else getattr(draw, "width", 1500)
    measured = core.text_width(draw, text, font, font)
    core.draw_rich(draw, (canvas_width - measured) / 2.0, y, text, font, font, fill=fill)


def enthalpy_figure(out, entries: Optional[Sequence[Sequence[object]]] = None,
                    title: Optional[str] = None, unit: str = "kJ/g",
                    show_percent: bool = True,
                    *, formats: Sequence[str] = ("png",), dpi: Optional[int] = None,
                    transparent: bool = False,
                    scale_override: Optional[float] = None) -> FigureResult:
    """燃烧焓对比条形图：每组两个柱（班级数据 vs 文献），可选显示百分比。"""
    # 兼容两种写法：(name, measured, literature) 或 (slug, name, measured, literature)
    data = []
    for entry in (entries or ENTHALPY_SERIES):
        row = list(entry)
        if len(row) >= 4:
            row = row[1:4]
        data.append((str(row[0]), float(str(row[1])), float(str(row[2]))))
    banner = figure_layout("enthalpy")
    width, height = banner.width, banner.height
    scale = scale_override if scale_override is not None else core.dpi_scale(width, height, dpi)
    surface = core.new_surface(width, height, scale=scale, svg="svg" in formats)
    draw = surface

    top, bottom = 170.0, 540.0
    left, right = 120.0, 1440.0
    highest = max(max(measured, literature) for _n, measured, literature in data) * 1.18

    _centre_text(draw, banner.title_y, title or ("Combustion enthalpy: class data vs literature (%s)" % unit),
                 core.fonts(banner.title_size, banner.title_bold), fill=INK, width=width)

    draw.line([left, bottom, right, bottom], fill=INK, width=3)
    draw.line([left, top, left, bottom], fill=INK, width=3)

    group = (right - left) / float(len(data))
    bar_w = group * 0.26
    for index, (name, measured, literature) in enumerate(data):
        centre = left + group * (index + 0.5)
        for x0, value, colour in ((centre - bar_w * 1.1, measured, OXYGEN_RED),
                                  (centre + bar_w * 0.1, literature, INK)):
            bar_top = bottom - (value / highest) * (bottom - top)
            draw.rectangle([x0, bar_top, x0 + bar_w, bottom], fill=colour)
            draw.text((x0 + bar_w / 2 - 16, bar_top - 30), "%.2f" % value,
                      font=core.fonts(17), fill=colour)
        name_font = core.fonts(18, True)
        draw.text((centre - core.text_width(draw, name, name_font, name_font) / 2, bottom + 18),
                  name, font=name_font, fill=INK)
        if show_percent and literature:
            percent = "%.0f%%" % (measured / literature * 100.0)
            font = core.fonts(16)
            draw.text((centre - core.text_width(draw, percent, font, font) / 2, bottom + 46),
                      percent, font=font, fill=GREY)

    legend_y = bottom + 96
    draw.rectangle([left, legend_y, left + 26, legend_y + 18], fill=OXYGEN_RED)
    draw.text((left + 36, legend_y - 2), "class data (%s)" % unit, font=core.fonts(18), fill=INK)
    draw.rectangle([left + 300, legend_y, left + 326, legend_y + 18], fill=INK)
    draw.text((left + 336, legend_y - 2), "literature (%s)" % unit, font=core.fonts(18), fill=INK)

    report = core.save_figure(surface, out, formats=formats, dpi=dpi, transparent=transparent)
    return FigureResult("enthalpy", report)


def esterification_figure(out, acid_carbons: int = 2, alcohol_carbons: int = 1,
                          title: Optional[str] = None,
                          *, formats: Sequence[str] = ("png",), dpi: Optional[int] = None,
                          transparent: bool = False,
                          scale_override: Optional[float] = None) -> FigureResult:
    """酯化反应：羧酸 + 醇 --浓硫酸/加热--> 酯 + 水（醇用锯齿链，氧红色带孤对电子）。"""
    acid = max(1, min(6, int(acid_carbons)))
    alcohol = max(1, min(6, int(alcohol_carbons)))
    banner = figure_layout("esterification")
    width, height = banner.width, banner.height
    scale = scale_override if scale_override is not None else core.dpi_scale(width, height, dpi)
    surface = core.new_surface(width, height, scale=scale, svg="svg" in formats)
    draw = surface

    _centre_text(draw, banner.title_y, title or "Esterification: carboxylic acid + alcohol -> ester + water",
                 core.fonts(banner.title_size, banner.title_bold), fill=INK, width=width)
    ym = 210.0

    # 羧酸（富文本：下标用 _{ } 标记，不塞 Unicode）
    core.draw_formula(draw, 90, ym, _ALKYL_FORMULA[acid] + "COOH",
                      core.fonts(30), core.fonts(21))
    draw.text((90, ym + 84), _ACID_NAMES[acid], font=core.fonts(19), fill=GREY)

    draw.text((360, ym - 6), "+", font=core.fonts(34, True), fill=INK)

    # 醇：用与其它图同一套锯齿链（含红色氧与孤对电子）
    core.draw_chain(draw, 430, ym + 20, chain_style(alcohol, label_style="h2"))
    draw.text((430, ym + 120), _ALKANOL_NAMES[alcohol], font=core.fonts(19), fill=GREY)

    # 箭头 + 条件
    arrow_x0, arrow_y = 880.0, ym
    draw.line([arrow_x0, arrow_y, arrow_x0 + 120, arrow_y], fill=INK, width=5)
    draw.polygon([(arrow_x0 + 134, arrow_y), (arrow_x0 + 108, arrow_y - 11),
                  (arrow_x0 + 108, arrow_y + 11)], fill=INK)
    draw.text((arrow_x0 + 6, arrow_y - 62), "conc. H_{2}SO_{4}", font=core.fonts(18), fill=GREY)
    draw.text((arrow_x0 + 34, arrow_y + 22), "heat", font=core.fonts(18), fill=GREY)

    # 酯 + 水
    ester_formula = _ALKYL_FORMULA[acid] + "COO" + _ALKYL_FORMULA[alcohol]
    core.draw_formula(draw, 1060, ym, ester_formula, core.fonts(30), core.fonts(21))
    ester_name = "%s %s" % (_ALKYL_NAMES[alcohol],
                            _ACID_NAMES[acid].replace("oic acid", "oate"))
    draw.text((1060, ym + 84), ester_name, font=core.fonts(19), fill=GREY)

    draw.text((1380, ym - 6), "+", font=core.fonts(34, True), fill=INK)
    core.draw_formula(draw, 1430, ym, "H_{2}O", core.fonts(30), core.fonts(21))

    report = core.save_figure(surface, out, formats=formats, dpi=dpi, transparent=transparent)
    return FigureResult("esterification", report)


def skeleton_figure(out, skeleton: str = "", title: Optional[str] = None,
                    *, formats: Sequence[str] = ("png",), dpi: Optional[int] = None,
                    transparent: bool = False,
                    scale_override: Optional[float] = None) -> FigureResult:
    """画一张可编辑骨架（阶段⑤）：原子用圆点 + 富文本标签，键用线段（双键画两条）。

    ``skeleton`` 是 :class:`chemfigstudio.skeleton.Skeleton` 的 JSON；留空则用默认直链。
    """
    from .skeleton import ATOM_RADIUS, Scene

    scene = Scene.from_json(skeleton) if skeleton else Scene.single()
    offsets = scene.offsets()
    labels = scene.names
    x0, y0, x1, y1 = scene.bounds()
    padding = 90.0
    width = int(max(360.0, (x1 - x0) + padding * 2))
    height = int(max(200.0, (y1 - y0) + padding * 2))
    offset_x, offset_y = padding - x0, padding - y0 + 30.0
    height += 70                                    # 给标题留位置

    scale = scale_override if scale_override is not None else core.dpi_scale(width, height, dpi)
    surface = core.new_surface(width, height, scale=scale, svg="svg" in formats)
    draw = surface

    def place(atom, dx: float = 0.0, dy: float = 0.0) -> Tuple[float, float]:
        return (atom.x + dx + offset_x, atom.y + dy + offset_y)

    # 逐分子绘制（多分子时按 Scene.offsets() 纵向堆叠）
    for index, data in enumerate(scene.molecules):
        dx, dy = offsets[index]
        for bond in data.bonds:
            first, second = data.atom(bond.a), data.atom(bond.b)
            if first is None or second is None:
                continue
            _draw_bond(draw, place(first, dx, dy), place(second, dx, dy), bond)
        for atom in data.atoms:
            _draw_atom(draw, place(atom, dx, dy), atom)
        if index < len(labels) and labels[index]:
            label_font = core.fonts(18)
            draw.text((place(data.atoms[0], dx, dy)[0] if data.atoms else offset_x,
                       place(data.atoms[0], dx, dy)[1] - 46 if data.atoms else offset_y),
                      labels[index], font=label_font, fill=GREY)

    if title or scene.molecules[0].title:
        text = title or scene.molecules[0].title
        font = core.fonts(24, True)
        draw.text(((width - core.text_width(draw, text, font, font)) / 2, 22), text,
                  font=font, fill=INK)

    report = core.save_figure(surface, out, formats=formats, dpi=dpi, transparent=transparent)
    return FigureResult("skeleton", report)


def _draw_bond(draw, first: Tuple[float, float], second: Tuple[float, float], bond) -> None:
    """画一根键：单键一条线，双键/三键若干条平行线。"""
    ax, ay = first
    bx, by = second
    if bond.order == 1:
        draw.line([ax, ay, bx, by], fill=INK, width=4)
        return
    dx, dy = bx - ax, by - ay
    length = max(1.0, (dx * dx + dy * dy) ** 0.5)
    nx, ny = -dy / length * 6.0, dx / length * 6.0
    offsets = (-1.6, 0.0, 1.6) if bond.order >= 3 else (-1.0, 1.0)
    width = 3 if bond.order == 2 else 2
    for sign in offsets:
        draw.line([ax + nx * sign, ay + ny * sign, bx + nx * sign, by + ny * sign],
                  fill=INK, width=width)


def _draw_atom(draw, centre: Tuple[float, float], atom) -> None:
    """画一个原子：白底圆点 + 元素配色 + 富文本标签。"""
    from .skeleton import ATOM_RADIUS

    ax, ay = centre
    draw.ellipse([ax - ATOM_RADIUS, ay - ATOM_RADIUS, ax + ATOM_RADIUS, ay + ATOM_RADIUS],
                 fill=WHITE, outline=_hex_to_rgb(atom.colour()), width=3)
    label = atom.text()
    font = core.fonts(19, True)
    draw.text((ax - core.text_width(draw, label, font, core.fonts(13)) / 2, ay - 12),
              label, font=font, fill=_hex_to_rgb(atom.colour()))


def _hex_to_rgb(value: str) -> Tuple[int, int, int]:
    """``#rrggbb`` → Pillow 颜色元组（骨架里颜色用十六进制存，便于前端直接吃）。"""
    text = str(value or "").lstrip("#")
    if len(text) != 6:
        return INK
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return INK
