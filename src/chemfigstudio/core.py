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
    "draw_formula", "draw_chain",
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
DPI_TARGET_LONGEST: int = 3000   # 300 dpi 时输出最长边约 3000 px（契约验收 8）

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
#: 中文（CJK）字体候选。**只在文本里真的含 CJK 字符时才用** —— 英文仍走 Arial，
#: 这样既有输出（6 张示例图）逐字节不变。
#:
#: 为什么需要它：Arial 没有中文字形，中文标题会渲染成一个 ``□``（空心方块）。
#: 那不是"字体难看"，是**内容丢失** —— 用户看不到自己写的标题。
CJK_FONT_CANDIDATES: Tuple[Path, ...] = (
    Path("/System/Library/Fonts/STHeiti Medium.ttc"),
    Path("/System/Library/Fonts/STHeiti Light.ttc"),
    Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
    Path("/System/Library/Fonts/Supplemental/Songti.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    Path("C:/Windows/Fonts/msyh.ttc"),
)
CJK_BOLD_FONT_CANDIDATES: Tuple[Path, ...] = (
    Path("/System/Library/Fonts/STHeiti Medium.ttc"),
    Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    Path("C:/Windows/Fonts/msyhbd.ttc"),
)
ENV_FONT_CJK = "CHEMFIG_FONT_CJK"

#: 真正需要中文字体的码位区间。**故意不含箭头（→）、希腊字母（Δ）、
#: 数学符号（×、−、₂）** —— 那些 Arial 有，切字体反而会改变既有输出的观感。
CJK_RANGES: Tuple[Tuple[int, int], ...] = (
    (0x2E80, 0x2EFF),      # CJK 部首补充
    (0x3040, 0x30FF),      # 日文平假名/片假名
    (0x3400, 0x4DBF),      # 统一表意文字扩展 A
    (0x4E00, 0x9FFF),      # 统一表意文字
    (0xF900, 0xFAFF),      # 兼容表意文字
    (0xFF00, 0xFFEF),      # 全角字符（中文标点 ，：；）
    (0x3000, 0x303F),      # CJK 标点（。、「」）
)


def needs_cjk(text: str) -> bool:
    """这段文本里有没有必须用中文字体才能显示的字符？"""
    for character in str(text or ""):
        code = ord(character)
        for low, high in CJK_RANGES:
            if low <= code <= high:
                return True
    return False


#: 允许用环境变量强制指定字体（移植到别的机器/CI 时很有用）。
ENV_FONT = "CHEMFIG_FONT"
ENV_FONT_BOLD = "CHEMFIG_FONT_BOLD"


# --------------------------------------------------------------------------- 字体

class FontBook:
    """按 ``(size, bold)`` 缓存 TrueType 字体对象。

    Pillow 每次 ``truetype()`` 都要读盘，画一张系列图要几十次，缓存后快一个量级。
    """

    def __init__(self) -> None:
        self._cache: Dict[Tuple[int, bool, bool], ImageFont.FreeTypeFont] = {}
        #: id(font) -> (size, bold, cjk)。**cjk 一定要记**：否则画布缩放时
        #: 按 (size, bold) 重建字体，会把中文字体悄悄换回 Arial（踩过）。
        self._meta: Dict[int, Tuple[int, bool, bool]] = {}

    def get(self, size: int, bold: bool = False,
            cjk: bool = False) -> ImageFont.FreeTypeFont:
        key = (int(size), bool(bold), bool(cjk))
        if key not in self._cache:
            font = resolve_font(int(size), bool(bold), cjk=bool(cjk))
            self._cache[key] = font
            self._meta[id(font)] = (int(size), bool(bold), bool(cjk))
        return self._cache[key]

    def clear(self) -> None:
        self._cache.clear()
        self._meta.clear()

    def meta(self, font: ImageFont.FreeTypeFont) -> Tuple[int, bool]:
        """返回字体的 (size, bold)；查不到就用 font.size 兜底。"""
        found = self._meta.get(id(font))
        if found:
            return (found[0], found[1])
        return (int(getattr(font, "size", 24)), False)

    def is_cjk(self, font: ImageFont.FreeTypeFont) -> bool:
        """这个字体是不是中文字体？（画布缩放时要靠它别把字体换回去）"""
        found = self._meta.get(id(font))
        return bool(found[2]) if found else False


def _first_existing(paths: Iterable[Path]) -> Optional[Path]:
    for p in paths:
        try:
            if p.exists():
                return p
        except OSError:      # 权限/超长路径等，直接跳过
            continue
    return None


def resolve_font(size: int, bold: bool = False,
                 cjk: bool = False) -> ImageFont.FreeTypeFont:
    """定位并加载字体；找不到时抛 RuntimeError（附排查提示）。

    ``cjk=True`` 走中文字体（只有文本真的含中文时才会用到）。
    """
    if cjk:
        env_cjk = os.environ.get(ENV_FONT_CJK)
        cjk_candidates: Tuple[Path, ...] = (
            ((Path(env_cjk),) if env_cjk else ())
            + (CJK_BOLD_FONT_CANDIDATES if bold else CJK_FONT_CANDIDATES))
        path = _first_existing(cjk_candidates)
        if path is not None:
            return ImageFont.truetype(str(path), size)
        # 没有中文字体就退回 Arial：中文仍会变方块，但英文/数字至少正常，
        # **不能因为缺中文字体就让整张图渲染失败**。
        return resolve_font(size, bold, cjk=False)

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


def fonts(size: int, bold: bool = False,
          cjk: bool = False) -> ImageFont.FreeTypeFont:
    """模块级字体缓存的简写：``fonts(27, True)``。"""
    return _FONTS.get(size, bold, cjk)


def font_for_text(font: ImageFont.FreeTypeFont,
                  text: str) -> ImageFont.FreeTypeFont:
    """文本含中文时换成**同字号**的中文字体，否则原样返回。

    这是"零调用点改动"的关键：所有标题都经过 :func:`draw_rich` /
    :func:`draw_rich_centred` / :func:`text_width`，在这三处统一换字体即可，
    不必改几十个 ``core.fonts(...)`` 调用。
    """
    if not needs_cjk(text):
        return font
    size, bold = _FONTS.meta(font)
    return _FONTS.get(size, bold, True)


def font_meta(font: Optional[ImageFont.FreeTypeFont]) -> Tuple[int, bool]:
    """模块级字体元数据访问（canvas.Surface 缩放字号时要用）。"""
    if font is None:
        return (0, False)
    return _FONTS.meta(font)


def font_is_cjk(font: Optional[ImageFont.FreeTypeFont]) -> bool:
    """这个字体是不是中文字体（缩放时要原样带过去）。"""
    if font is None:
        return False
    return _FONTS.is_cjk(font)


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
    """带上下标文本的渲染宽度。

    注意：宽度必须用**即将真正绘制用的那个字体**来算。中文走中文字体时字形宽度
    与 Arial 不同（实测 "Benzene" 是 107 vs 113），用错字体会让居中偏掉。
    """
    total = 0.0
    for kind, chunk in parse_markup(text):
        piece = font if kind == "n" else sub_font
        total += draw.textlength(chunk, font=font_for_text(piece, chunk))
    return total


def draw_rich(draw: "ImageDraw.ImageDraw", x: float, y: float, text: str,
              font: ImageFont.FreeTypeFont, sub_font: ImageFont.FreeTypeFont,
              fill: Tuple[int, int, int] = INK) -> float:
    """绘制带 ``_{}`` / ``^{}`` 的文本，返回结束处的 x。

    每个 chunk 单独决定字体（见 :func:`font_for_text`）：同一个标题里
    "苯 Benzene" 这种混排，中文那截走中文字体、英文那截仍走 Arial。
    """
    for kind, chunk in parse_markup(text):
        if kind == "_":
            piece = font_for_text(sub_font, chunk)
            draw.text((x, y + font.size * _SUB_DOWN), chunk, font=piece, fill=fill)
            x += draw.textlength(chunk, font=piece)
        elif kind == "^":
            piece = font_for_text(sub_font, chunk)
            draw.text((x, y - font.size * _SUB_UP), chunk, font=piece, fill=fill)
            x += draw.textlength(chunk, font=piece)
        else:
            piece = font_for_text(font, chunk)
            draw.text((x, y), chunk, font=piece, fill=fill)
            x += draw.textlength(chunk, font=piece)
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


def draw_dot_pairs(draw: "ImageDraw.ImageDraw", ox: float, oy: float,
                   font_size: float, colour: Tuple[int, int, int] = OXYGEN_RED) -> None:
    """在一个标签旁上下各画两个小圆点（共 4 点）。

    这是**纯几何图元**：它不知道这四点代表什么。化学解释在
    :func:`figures.draw_lone_pairs`（孤对电子）。
    """
    radius = 3
    cx = ox + 9
    for y in (oy - 19, oy - 11, oy + font_size + 5, oy + font_size + 13):
        draw.ellipse([cx - radius, y - radius, cx + radius, y + radius], fill=colour)


#: 旧名（向后兼容）：语义是化学的，所以对外只承诺 :func:`draw_dot_pairs`
draw_lone_pairs = draw_dot_pairs


# --------------------------------------------------------------------- 分子骨架

@dataclass
class ChainStyle:
    """一条锯齿链的绘制参数。

    **本层不认识化学**（ARCHITECTURE §6.5）：所有要写出来的文字都由调用方给 ——
    `figures.py` 通过 :func:`figures.chain_style` 填入示性式的标签（``CH_{3}`` /
    ``H_{2}`` / ``OH`` …）。这里的默认值是中性占位，直接构造 ``ChainStyle()``
    得到的是"没有文字的锯齿链"，而不是某个分子。
    """

    n_carbons: int = 4
    label_size: int = 27
    sub_size: int = 19
    dx: int = CHAIN_DX
    dy: int = CHAIN_DY
    bond_width: int = BOND_WIDTH
    bond_colour: Tuple[int, int, int] = INK
    note: bool = False               # True 时在转角与链首各标一条 note_label
    note_size: int = 13
    note_colour: Tuple[int, int, int] = GREY
    # ---- 由调用方提供的文字（core 不解释它们的含义）----
    head_label: str = ""             # 链首左上方
    vertex_label: str = ""           # 每个转角
    end_label: str = ""              # 链尾
    end_colour: Optional[Tuple[int, int, int]] = None   # None = 与键同色
    end_lone_pairs: bool = False     # 在 end_label 旁画点对（几何图元）
    note_label: str = ""             # 转角旁的注记文字
    note_head_label: str = ""        # 链首旁的注记文字
    #: 兼容旧参数：``"ch2"`` 历史上代表"转角写 CH2"，现在只被 figures 解释
    label_style: str = "h2"


def draw_chain(draw: "ImageDraw.ImageDraw", x0: float, ymid: float,
               style: Optional[ChainStyle] = None) -> float:
    """画一条 ``n`` 个顶点的锯齿链，并把调用方给的文字摆上去。

    返回链尾标签之后的 x（没有尾标签时返回链尾 x）。画什么字由
    :class:`ChainStyle` 的 ``head_label`` / ``vertex_label`` / ``end_label`` 决定 ——
    本函数不认识"醇""羟基"这类概念（ARCHITECTURE §6.5）。
    """
    st = style or ChainStyle()
    f_lab = fonts(st.label_size, True)
    sf_lab = fonts(st.sub_size)
    f_note = fonts(st.note_size)
    n = max(1, int(st.n_carbons))

    vertices = [(x0 + i * st.dx, ymid + (-st.dy if i % 2 else st.dy))
                for i in range(n)]
    for a, b in zip(vertices, vertices[1:]):
        draw.line([a, b], fill=st.bond_colour, width=st.bond_width)

    # 链首标签（文字由调用方给）
    w_head = 0.0
    if st.head_label:
        w_head = text_width(draw, st.head_label, f_lab, sf_lab)
        draw_rich(draw, vertices[0][0] - w_head - 16, vertices[0][1] - f_lab.size * 0.55,
                  st.head_label, f_lab, sf_lab, fill=st.bond_colour)

    # 转角标签（每个中间顶点一个）
    for i in range(1, n):
        x, y = vertices[i]
        above = y < ymid
        ty = y - f_lab.size - 14 if above else y + 16
        if st.vertex_label:
            draw_rich_centred(draw, ty, st.vertex_label, f_lab, sf_lab,
                              fill=st.bond_colour, cx=x)
        if st.note and st.note_label:
            note_y = (ty - f_note.size - 4) if above else (ty + sf_lab.size + 8)
            draw_rich_centred(draw, note_y, st.note_label, f_note, fonts(st.note_size - 4),
                              fill=st.note_colour, cx=x)

    # 链尾：先把键连到标签位置，再写文字（颜色可由调用方指定）
    tail = (x0 + n * st.dx, ymid + (-st.dy if n % 2 else st.dy))
    ox, oy = tail[0] + 16, tail[1] - f_lab.size * 0.55
    draw.line([vertices[-1], (ox - 5, oy + f_lab.size * 0.36)],
              fill=st.bond_colour, width=st.bond_width)
    if st.end_label:
        draw_rich(draw, ox, oy, st.end_label, f_lab, sf_lab,
                  fill=st.end_colour or st.bond_colour)
        if st.end_lone_pairs:
            draw_lone_pairs(draw, ox, oy, f_lab.size)

    if st.note and st.note_label:
        draw_rich_centred(draw, vertices[0][1] + f_lab.size + 8, st.note_head_label,
                          f_note, fonts(st.note_size - 4), fill=st.note_colour,
                          cx=vertices[0][0] - w_head / 2 - 16)

    return ox + (draw.textlength(st.end_label, font=f_lab) if st.end_label else 0.0)


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

    这样"300 dpi"意味着**输出最长边约 3000 px**（契约验收 8），
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
    * ``crop=True``：按墨迹包围盒裁边，PNG 与 SVG 使用**同一取景**（验收 11）
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
