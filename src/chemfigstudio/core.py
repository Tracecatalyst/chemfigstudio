"""chemfigstudio.core — 绘图内核：字体、文本排版、图元、画布与裁边。

风格铁律（完整版见 docs/STYLE_SPEC.md，改图前必读）：

1. **只用 Arial**。上下标一律手绘（把字符画小并偏移基线），
   **绝不使用 Unicode 的 ``₂`` / ``⁻¹``** —— Arial 缺少这些码位，
   在 Word 里会变成空心方块 ``□``。
2. 颜色固定：氧原子 ``#D61F1F``，键与正文 ``#111827``，
   说明文字 ``#788291``，ΔH 数值 ``#BE123C``，背景纯白。
3. **先在超大画布上绘制，再按墨迹包围盒自动裁边**，四周保留 26 px 白边。
   画布给小了内容会被切掉，``save_figure()`` 会把这种情况作为
   ``warnings`` 返回，调用方（CLI/GUI）必须展示出来。
4. 单位写法面向 10 年级：``kJ/g``、``kJ/mol``，不用负指数。

本模块只依赖 Pillow，无网络、无化学引擎、无第三方渲染服务。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFont

__all__ = [
    "WHITE", "INK", "GREY", "OXYGEN_RED", "DELTA_RED",
    "CHAIN_DX", "CHAIN_DY", "CROP_MARGIN",
    "fonts", "parse_markup", "text_width", "draw_rich", "draw_rich_centred",
    "draw_formula", "draw_lone_pairs", "draw_chain",
    "new_canvas", "save_figure", "SaveReport", "font_report", "resolve_font",
]

# --------------------------------------------------------------------------- 常量

WHITE: Tuple[int, int, int] = (255, 255, 255)
INK: Tuple[int, int, int] = (17, 24, 39)          # 键、正文
GREY: Tuple[int, int, int] = (120, 130, 145)      # 说明文字
OXYGEN_RED: Tuple[int, int, int] = (214, 31, 31)  # 氧原子
DELTA_RED: Tuple[int, int, int] = (190, 18, 60)   # ΔH 数值

CHAIN_DX: int = 92   # 锯齿链水平步长（px）
CHAIN_DY: int = 50   # 锯齿链垂直步长（px）
BOND_WIDTH: int = 5
CROP_MARGIN: int = 26
SVG_FONT_FAMILY: str = "Arial, Helvetica, sans-serif"
DPI_TARGET_LONGEST: int = 3000   # 300 dpi 时输出最长边约 3000 px（契约）

_SUB_DOWN = 0.26     # 下标基线偏移量（× 主字号）
_SUB_UP = 0.34       # 上标基线偏移量（× 主字号）

#: macOS 上 Arial 的位置；其它平台按顺序回退。
FONT_CANDIDATES: Tuple[Path, ...] = (
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    Path("/Library/Fonts/Arial.ttf"),
    Path("/System/Library/Fonts/Supplemental/Helvetica.ttf"),
    Path("/usr/share/fonts/truetype/msttcorefonts/Arial.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
)
BOLD_FONT_CANDIDATES: Tuple[Path, ...] = (
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/Library/Fonts/Arial Bold.ttf"),
    Path("/System/Library/Fonts/Supplemental/Helvetica Bold.ttf"),
    Path("/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
)
#: 允许用环境变量强制指定字体（移植到别的机器/CI 时很有用）。
ENV_FONT = "CHEMFIG_FONT"
ENV_FONT_BOLD = "CHEMFIG_FONT_BOLD"


# --------------------------------------------------------------------------- 字体

class FontBook:
    """按 ``(size, bold)`` 缓存 TrueType 字体对象。

    Pillow 每次 ``truetype()`` 都要读盘，画一张系列图要几十次，缓存后快一个量级。
    """

    def __init__(self) -> None:
        self._cache: Dict[Tuple[int, bool], ImageFont.FreeTypeFont] = {}
        self._meta: Dict[int, Tuple[int, bool]] = {}     # id(font) -> (size, bold)

    def get(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        key = (int(size), bool(bold))
        if key not in self._cache:
            font = resolve_font(*key)
            self._cache[key] = font
            self._meta[id(font)] = key
        return self._cache[key]

    def clear(self) -> None:
        self._cache.clear()
        self._meta.clear()

    def meta(self, font: ImageFont.FreeTypeFont) -> Tuple[int, bool]:
        """返回字体的 (size, bold)；查不到就用 font.size 兜底。"""
        found = self._meta.get(id(font))
        if found:
            return found
        return (int(getattr(font, "size", 24)), False)


def _first_existing(paths: Iterable[Path]) -> Optional[Path]:
    for p in paths:
        try:
            if p.exists():
                return p
        except OSError:      # 权限/超长路径等，直接跳过
            continue
    return None


def resolve_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """定位并加载字体；找不到时抛 RuntimeError（附排查提示）。"""
    env = os.environ.get(ENV_FONT_BOLD if bold else ENV_FONT)
    candidates: Tuple[Path, ...] = (
        (Path(env),) if env else ()
    ) + (BOLD_FONT_CANDIDATES if bold else FONT_CANDIDATES)

    path = _first_existing(candidates)
    if path is not None:
        return ImageFont.truetype(str(path), size)

    # 最后一搏：交给 Pillow 按字体名解析（Linux fontconfig 常能命中）
    for name in (("Arial Bold", "Arial") if bold else ("Arial",)):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    raise RuntimeError(
        "找不到 Arial 字体。请设置环境变量 %s（粗体用 %s）指向一个 .ttf 文件。"
        % (ENV_FONT, ENV_FONT_BOLD)
    )


_FONTS = FontBook()


def fonts(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """模块级字体缓存的简写：``fonts(27, True)``。"""
    return _FONTS.get(size, bold)


def font_meta(font: Optional[ImageFont.FreeTypeFont]) -> Tuple[int, bool]:
    """模块级字体元数据访问（canvas.Surface 缩放字号时要用）。"""
    if font is None:
        return (0, False)
    return _FONTS.meta(font)


def dpi_scale(width: int, height: int, dpi: Optional[int],
              target_longest: int = DPI_TARGET_LONGEST) -> float:
    """由 DPI 推出绘制缩放系数：300 dpi 时最长边约 ``target_longest``，其它成比例。

    这样"提高 DPI"= 重新绘制成更大的图（真放大），而不是插值拉大位图。
    """
    if not dpi:
        return 1.0
    longest = max(1, int(max(width, height)))
    return max(0.2, min(8.0, (float(dpi) / 300.0) * (float(target_longest) / longest)))


def new_surface(width: int, height: int, *, scale: float = 1.0, svg: bool = False,
                background=WHITE):
    """新建画布（双后端）。延迟导入 canvas 以免与 canvas→core 形成循环。"""
    from .canvas import Surface
    return Surface(width, height, scale=scale, svg=svg, background=background)


def font_report() -> List[Dict[str, object]]:
    """自检用：返回字体解析结果（CLI ``doctor`` 子命令会打印）。"""
    out: List[Dict[str, object]] = []
    for bold in (False, True):
        try:
            f = resolve_font(24, bold)
            out.append({"bold": bold, "ok": True, "path": getattr(f, "path", "?")})
        except RuntimeError as exc:
            out.append({"bold": bold, "ok": False, "error": str(exc)})
    return out


# ----------------------------------------------------------------- 文本（含上下标）

def parse_markup(text: str) -> List[Tuple[str, str]]:
    """把 ``CH_{3}`` 之类的标记切成 ``[(kind, chunk), ...]``。

    kind: ``"n"`` 正文、``"_"`` 下标、``"^"`` 上标。
    """
    out: List[Tuple[str, str]] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "_^" and i + 1 < len(text) and text[i + 1] == "{":
            end = text.index("}", i)
            out.append((ch, text[i + 2:end]))
            i = end + 1
        else:
            nxt = len(text)
            for marker in ("_{", "^{"):
                pos = text.find(marker, i)
                if pos != -1:
                    nxt = min(nxt, pos)
            out.append(("n", text[i:nxt]))
            i = nxt
    return out


def text_width(draw: "ImageDraw.ImageDraw", text: str,
               font: ImageFont.FreeTypeFont, sub_font: ImageFont.FreeTypeFont) -> float:
    """带上下标文本的渲染宽度。"""
    total = 0.0
    for kind, chunk in parse_markup(text):
        total += draw.textlength(chunk, font=(font if kind == "n" else sub_font))
    return total


def draw_rich(draw: "ImageDraw.ImageDraw", x: float, y: float, text: str,
              font: ImageFont.FreeTypeFont, sub_font: ImageFont.FreeTypeFont,
              fill: Tuple[int, int, int] = INK) -> float:
    """绘制带 ``_{}`` / ``^{}`` 的文本，返回结束处的 x。"""
    for kind, chunk in parse_markup(text):
        if kind == "_":
            draw.text((x, y + font.size * _SUB_DOWN), chunk, font=sub_font, fill=fill)
            x += draw.textlength(chunk, font=sub_font)
        elif kind == "^":
            draw.text((x, y - font.size * _SUB_UP), chunk, font=sub_font, fill=fill)
            x += draw.textlength(chunk, font=sub_font)
        else:
            draw.text((x, y), chunk, font=font, fill=fill)
            x += draw.textlength(chunk, font=font)
    return x


def draw_rich_centred(draw: "ImageDraw.ImageDraw", y: float, text: str,
                      font: ImageFont.FreeTypeFont, sub_font: ImageFont.FreeTypeFont,
                      fill: Tuple[int, int, int] = INK, cx: float = 0) -> float:
    """在 ``cx`` 处居中绘制带上下标的文本，返回结束处的 x。"""
    return draw_rich(draw, cx - text_width(draw, text, font, sub_font) / 2,
                     y, text, font, sub_font, fill)


def draw_formula(draw: "ImageDraw.ImageDraw", x: float, y: float, text: str,
                 font: ImageFont.FreeTypeFont, sub_font: ImageFont.FreeTypeFont,
                 oxygen: Tuple[int, int, int] = OXYGEN_RED,
                 carbon: Tuple[int, int, int] = INK) -> float:
    """化学式的逐字符绘制：O 用红色，其余用深色。

    上下标继承前一个正常字符的颜色（``H_{2}O`` 里 ``2`` 跟随 H 的颜色，
    ``O`` 之后的下标跟随红色），与课本插图一致。
    """
    last = carbon
    for kind, chunk in parse_markup(text):
        if kind in "_^":
            dy = font.size * (_SUB_DOWN if kind == "_" else -_SUB_UP)
            draw.text((x, y + dy), chunk, font=sub_font, fill=last)
            x += draw.textlength(chunk, font=sub_font)
        else:
            for ch in chunk:
                colour = oxygen if ch == "O" else carbon
                last = colour
                draw.text((x, y), ch, font=font, fill=colour)
                x += draw.textlength(ch, font=font)
    return x


def draw_lone_pairs(draw: "ImageDraw.ImageDraw", ox: float, oy: float,
                    font_size: float, colour: Tuple[int, int, int] = OXYGEN_RED) -> None:
    """在氧原子上画两对孤对电子（每对上下叠两个点，共 4 点）。"""
    radius = 3
    cx = ox + 9
    for y in (oy - 19, oy - 11, oy + font_size + 5, oy + font_size + 13):
        draw.ellipse([cx - radius, y - radius, cx + radius, y + radius], fill=colour)


# --------------------------------------------------------------------- 分子骨架

@dataclass
class ChainStyle:
    """一条示性式碳链的绘制参数。"""

    n_carbons: int = 4
    label_style: str = "h2"          # "h2" = 转角写 H2（课本骨架式）；"ch2" = 写 CH2
    label_size: int = 27
    sub_size: int = 19
    dx: int = CHAIN_DX
    dy: int = CHAIN_DY
    bond_width: int = BOND_WIDTH
    bond_colour: Tuple[int, int, int] = INK
    note: bool = False               # True 时在每个转角标出 "2 C-H"，在 CH3 标 "3 C-H"
    note_size: int = 13
    note_colour: Tuple[int, int, int] = GREY


def draw_chain(draw: "ImageDraw.ImageDraw", x0: float, ymid: float,
               style: Optional[ChainStyle] = None) -> float:
    """在 ``(x0, ymid)`` 起画 ``CH3-(CH2)n-2-OH``，返回 ``OH`` 之后的 x。"""
    st = style or ChainStyle()
    f_lab = fonts(st.label_size, True)
    sf_lab = fonts(st.sub_size)
    f_note = fonts(st.note_size)
    n = max(1, int(st.n_carbons))

    vertices = [(x0 + i * st.dx, ymid + (-st.dy if i % 2 else st.dy))
                for i in range(n)]
    for a, b in zip(vertices, vertices[1:]):
        draw.line([a, b], fill=st.bond_colour, width=st.bond_width)

    # CH3 标在链首左上方
    w_ch3 = (draw.textlength("CH", font=f_lab) + draw.textlength("3", font=sf_lab))
    draw_rich(draw, vertices[0][0] - w_ch3 - 16, vertices[0][1] - f_lab.size * 0.55,
              "CH_{3}", f_lab, sf_lab, fill=st.bond_colour)

    # 中间碳：转角标注
    for i in range(1, n):
        x, y = vertices[i]
        above = y < ymid
        ty = y - f_lab.size - 14 if above else y + 16
        label = "CH_{2}" if st.label_style == "ch2" else "H_{2}"
        draw_rich_centred(draw, ty, label, f_lab, sf_lab, fill=st.bond_colour, cx=x)
        if st.note:
            note_y = (ty - f_note.size - 4) if above else (ty + sf_lab.size + 8)
            draw_rich_centred(draw, note_y, "2 C-H", f_note, fonts(st.note_size - 4),
                              fill=st.note_colour, cx=x)

    # OH：键直接连到 O 上，O 为红色，带孤对电子
    tail = (x0 + n * st.dx, ymid + (-st.dy if n % 2 else st.dy))
    ox, oy = tail[0] + 16, tail[1] - f_lab.size * 0.55
    draw.line([vertices[-1], (ox - 5, oy + f_lab.size * 0.36)],
              fill=st.bond_colour, width=st.bond_width)
    draw_rich(draw, ox, oy, "OH", f_lab, sf_lab, fill=OXYGEN_RED)
    draw_lone_pairs(draw, ox, oy, f_lab.size)

    if st.note:
        draw_rich_centred(draw, vertices[0][1] + f_lab.size + 8, "3 C-H",
                          f_note, fonts(st.note_size - 4), fill=st.note_colour,
                          cx=vertices[0][0] - w_ch3 / 2 - 16)

    return ox + draw.textlength("OH", font=f_lab)


# ------------------------------------------------------------------ 画布与输出

def new_canvas(width: int, height: int) -> Tuple[Image.Image, "ImageDraw.ImageDraw"]:
    """新建纯白 RGB 画布，返回 ``(image, draw)``。"""
    image = Image.new("RGB", (int(width), int(height)), WHITE)
    return image, ImageDraw.Draw(image)


@dataclass
class SaveReport:
    """一次出图的结果：路径、最终尺寸、警告列表。"""

    path: Path
    size: Tuple[int, int] = (0, 0)
    warnings: List[str] = field(default_factory=list)
    cropped: bool = False
    outputs: Dict[str, str] = field(default_factory=dict)   # {"png":…, "svg":…, "pdf":…}
    scale: float = 1.0                                      # 实际绘制缩放（供按目标像素重绘）

    def as_dict(self) -> Dict[str, object]:
        return {"path": str(self.path), "size": list(self.size),
                "warnings": list(self.warnings), "cropped": self.cropped,
                "outputs": dict(self.outputs)}


def _edge_hits(image: Image.Image) -> List[str]:
    """检测内容是否贴到画布边缘（贴边 = 已经被切掉了）。"""
    bbox = ImageChops.difference(image.convert("RGB"),
                                 Image.new("RGB", image.size, WHITE)).getbbox()
    if not bbox:
        return []
    x0, y0, x1, y1 = bbox
    hits: List[str] = []
    if x0 <= 1:
        hits.append("左")
    if y0 <= 1:
        hits.append("上")
    if x1 >= image.width - 1:
        hits.append("右")
    if y1 >= image.height - 1:
        hits.append("下")
    return hits


def ink_box(image: Image.Image, margin: int = CROP_MARGIN):
    """返回裁切后的墨迹外框 ``(x0, y0, x1, y1)``；空图返回 None。"""
    bbox = ImageChops.difference(image.convert("RGB"),
                                 Image.new("RGB", image.size, WHITE)).getbbox()
    if not bbox:
        return None
    x0, y0, x1, y1 = bbox
    return (max(0, x0 - margin), max(0, y0 - margin),
            min(image.width, x1 + margin), min(image.height, y1 + margin))


def render_figure(kind: str, out, size: Tuple[int, int], paint, *,
                  formats: Sequence[str] = ("png",), dpi: Optional[int] = None,
                  transparent: bool = False, margin: int = CROP_MARGIN):
    """两遍渲染：先按 1:1 量出裁切后的内容尺寸，再按 DPI 目标像素重绘。

    这样"300 dpi"意味着**输出最长边约 3000 px**（契约），
    而且是重新绘制（字体、线宽都跟着放大），不是把位图插值拉大。
    """
    wanted = tuple(str(f).lower().lstrip(".") for f in formats) or ("png",)
    scale = 1.0
    if dpi:
        probe = new_surface(size[0], size[1], svg=False)
        paint(probe, probe.image, probe)
        box = ink_box(probe.image, margin)
        content_w = (box[2] - box[0]) if box else size[0]
        content_h = (box[3] - box[1]) if box else size[1]
        scale = dpi_scale(content_w, content_h, dpi)

    surface = new_surface(size[0], size[1], scale=scale, svg="svg" in wanted)
    paint(surface, surface.image, surface)
    report = save_figure(surface, out, formats=wanted, dpi=dpi, margin=margin,
                         transparent=transparent)
    return report


def new_surface_from_image(image: Image.Image):
    """把 PIL 图像包成 Surface（向后兼容旧的 ``save_figure(image, ...)`` 调用）。"""
    from .canvas import Surface
    return Surface.from_image(image)


def save_figure(target, path: os.PathLike, *, crop: bool = True,
                margin: int = CROP_MARGIN, verbose: bool = False,
                formats: Sequence[str] = ("png",), dpi: Optional[int] = None,
                transparent: bool = False) -> SaveReport:
    """保存画布：主路径 + ``formats`` 里要求的其它格式。

    * ``target`` 可以是 :class:`chemfigstudio.canvas.Surface`，也可以是 PIL 图像（向后兼容）
    * ``crop=True``：按墨迹包围盒裁边，PNG 与 SVG 使用**同一取景**
    * ``transparent=True``：PNG 把纯白像素设为透明；SVG 本来无背景；PDF 保持白底
    """
    surface = target if hasattr(target, "image") else new_surface_from_image(target)
    image = surface.image
    stem = Path(path).with_suffix("")
    wanted = [str(fmt).lower().lstrip(".") for fmt in formats] or ["png"]
    warnings: List[str] = []

    hits = _edge_hits(image)
    if hits:
        warnings.append(
            "内容碰到画布边缘（%s），可能被裁切：请把画布尺寸调大" % "、".join(hits))

    box = None
    if crop:
        bbox = ImageChops.difference(image.convert("RGB"),
                                     Image.new("RGB", image.size, WHITE)).getbbox()
        if bbox:
            x0, y0, x1, y1 = bbox
            box = (max(0, x0 - margin), max(0, y0 - margin),
                   min(image.width, x1 + margin), min(image.height, y1 + margin))

    rendered = image.crop(box) if box else image.convert("RGB")
    outputs: Dict[str, str] = {}
    main_path = Path(path)

    if "png" in wanted:
        target_png = main_path if main_path.suffix.lower() == ".png" else Path(str(stem) + ".png")
        target_png.parent.mkdir(parents=True, exist_ok=True)
        out_image = rendered
        if transparent:
            out_image = rendered.convert("RGBA")
            out_image.putdata([(0, 0, 0, 0) if p[:3] == WHITE else p
                               for p in out_image.getdata()])
        save_kwargs: Dict[str, object] = {}
        if dpi:
            save_kwargs["dpi"] = (int(dpi), int(dpi))
        out_image.save(target_png, **save_kwargs)
        outputs["png"] = str(target_png)
        main_path = target_png

    if "svg" in wanted and getattr(surface, "_svg", None) is not None:
        target_svg = (main_path if main_path.suffix.lower() == ".svg"
                      else Path(str(stem) + ".svg"))
        # viewBox = (min-x, min-y, width, height)；box 是 (x0,y0,x1,y1)，必须换算
        view = ((box[0], box[1], box[2] - box[0], box[3] - box[1]) if box else None)
        surface.write_svg(target_svg, view_box=view,
                          width=rendered.width, height=rendered.height)
        outputs["svg"] = str(target_svg)
        if main_path.suffix.lower() == ".svg":
            main_path = target_svg

    if "svg" in wanted and getattr(surface, "_svg", None) is None:
        warnings.append("请求了 SVG 但画布未启用矢量后端（svg=True），已跳过 SVG 输出")

    if "pdf" in wanted:
        target_pdf = (main_path if main_path.suffix.lower() == ".pdf"
                      else Path(str(stem) + ".pdf"))
        target_pdf.parent.mkdir(parents=True, exist_ok=True)
        rendered.convert("RGB").save(target_pdf, "PDF", resolution=float(dpi or 300))
        outputs["pdf"] = str(target_pdf)
        if main_path.suffix.lower() == ".pdf":
            main_path = target_pdf

    # 主路径必须是**真的写出来了**的那个文件（例如只要求 svg 时不能用 .png 当主路径）
    produced = [Path(outputs[fmt]) for fmt in wanted if fmt in outputs]
    if produced:
        main_path = produced[0]
    report = SaveReport(path=main_path, size=rendered.size, warnings=warnings,
                        cropped=crop, outputs=outputs,
                        scale=float(getattr(surface, "scale", 1.0) or 1.0))
    if verbose:
        print("已生成", main_path, rendered.size)
        for fmt, out_path in outputs.items():
            print("   %-3s %s" % (fmt, out_path))
        for w in warnings:
            print("  ⚠ " + w)
    return report
