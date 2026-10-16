"""chemfigstudio.canvas — 双后端画布：一份绘制指令，同时产出位图与矢量。

为什么这样设计（契约–11）：

* PNG / PDF 用位图（Pillow）；**SVG 必须是真文本**（文字可选中，不是路径）
* 两个后端共用**同一套坐标与字体度量**（度量取自 Pillow 字体），
  所以三种格式的图元位置偏差 < 1%
* 贴边检测、行重叠自检、自动裁边**继续用位图**做 —— 逻辑一个字没改，零回归风险

高分辨率：``Surface(w, h, scale=2)`` 把所有坐标与字号乘 ``scale``，
即"重新绘制成更大的图"，而不是把位图插值放大。
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from . import core

__all__ = ["Surface", "colour_to_hex"]

Point = Tuple[float, float]


def colour_to_hex(colour) -> str:
    """把 Pillow 的颜色元组转成 ``#rrggbb``（SVG 用）。"""
    if isinstance(colour, str):
        return colour
    if isinstance(colour, (tuple, list)) and len(colour) >= 3:
        return "#%02x%02x%02x" % (int(colour[0]), int(colour[1]), int(colour[2]))
    return "#000000"


class Surface:
    """与 :class:`PIL.ImageDraw.ImageDraw` 方法名兼容的画布。

    支持 :meth:`line` / :meth:`rectangle` / :meth:`ellipse` / :meth:`polygon` /
    :meth:`text` / :meth:`textlength`，以及 :attr:`image` / :attr:`size`。
    """

    def __init__(self, width: int, height: int, *, scale: float = 1.0,
                 svg: bool = False, background=core.WHITE) -> None:
        self.scale = float(scale) if scale else 1.0
        self.background = background
        self.width = max(1, int(round(width * self.scale)))
        self.height = max(1, int(round(height * self.scale)))
        self.image = Image.new("RGB", (self.width, self.height),
                               background if not isinstance(background, str) else core.WHITE)
        self._draw = ImageDraw.Draw(self.image)
        self._svg: Optional[List[str]] = [] if svg else None
        self._font_cache: Dict[Tuple[int, bool], ImageFont.FreeTypeFont] = {}

    # ------------------------------------------------------------------ 兼容层
    @classmethod
    def from_image(cls, image: Image.Image) -> "Surface":
        """把已有的 PIL 图像包成 Surface（向后兼容旧的 save_figure 调用）。"""
        obj = cls(image.width, image.height, scale=1.0, svg=False)
        obj.image = image.convert("RGB")
        obj._draw = ImageDraw.Draw(obj.image)
        return obj

    @property
    def size(self) -> Tuple[int, int]:
        return self.image.size

    # ------------------------------------------------------------------ 内部
    def _pt(self, x: float, y: float) -> Point:
        return (x * self.scale, y * self.scale)

    def _pts(self, xy: Iterable[Sequence[float]]) -> List[Point]:
        """接受点列 ``[(x,y), …]`` 或 PIL 的扁平写法 ``[x0,y0,x1,y1]``。"""
        seq: List[Sequence[float]] = list(xy)
        if seq and not hasattr(seq[0], "__len__"):
            flat = [float(v) for v in seq]                 # type: ignore[arg-type]
            seq = [(flat[i], flat[i + 1]) for i in range(0, len(flat) - 1, 2)]
        return [(float(p[0]) * self.scale, float(p[1]) * self.scale) for p in seq]

    def _box(self, xy) -> Tuple[float, float, float, float]:
        """接受 PIL 的两种写法：``[(x0,y0),(x1,y1)]`` 或 ``[x0,y0,x1,y1]``。"""
        if len(xy) == 2 and hasattr(xy[0], "__len__"):
            (x0, y0), (x1, y1) = xy
        else:
            x0, y0, x1, y1 = list(xy)[:4]
        return (x0 * self.scale, y0 * self.scale, x1 * self.scale, y1 * self.scale)

    def _scaled_font(self, font: Optional[ImageFont.FreeTypeFont]):
        """把调用方给的字体换成缩放后的同款字体（字号来自 FontBook 元数据）。"""
        if font is None:
            return None
        size, bold = core.font_meta(font)
        key = (max(1, int(round(size * self.scale))), bold)
        if key not in self._font_cache:
            self._font_cache[key] = core.fonts(key[0], key[1])
        return self._font_cache[key]

    def _add(self, element: str) -> None:
        if self._svg is not None:
            self._svg.append(element)

    # ------------------------------------------------------------------ 图元
    def line(self, xy, fill=None, width: int = 1, **kwargs) -> None:
        points = self._pts(xy)
        px_width = max(1, int(round(width * self.scale)))
        self._draw.line(points, fill=fill, width=px_width)
        if len(points) >= 2:
            pts = " ".join("%.2f,%.2f" % p for p in points)
            self._add('<polyline points="%s" fill="none" stroke="%s" stroke-width="%d" '
                      'stroke-linecap="round" stroke-linejoin="round"/>'
                      % (pts, colour_to_hex(fill), px_width))

    def polygon(self, xy, fill=None, outline=None, **kwargs) -> None:
        points = self._pts(xy)
        self._draw.polygon(points, fill=fill, outline=outline)
        pts = " ".join("%.2f,%.2f" % p for p in points)
        stroke = (' stroke="%s"' % colour_to_hex(outline)) if outline else ""
        self._add('<polygon points="%s" fill="%s"%s/>' % (pts, colour_to_hex(fill), stroke))

    def rectangle(self, xy, fill=None, outline=None, width: int = 1, **kwargs) -> None:
        x0, y0, x1, y1 = self._box(xy)
        self._draw.rectangle([x0, y0, x1, y1], fill=fill, outline=outline, width=width)
        self._add('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" fill="%s"%s/>'
                  % (x0, y0, x1 - x0, y1 - y0, colour_to_hex(fill),
                     (' stroke="%s"' % colour_to_hex(outline)) if outline else ""))

    def ellipse(self, xy, fill=None, outline=None, width: int = 1, **kwargs) -> None:
        x0, y0, x1, y1 = self._box(xy)
        self._draw.ellipse([x0, y0, x1, y1], fill=fill, outline=outline, width=width)
        self._add('<ellipse cx="%.2f" cy="%.2f" rx="%.2f" ry="%.2f" fill="%s"%s/>'
                  % ((x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2,
                     colour_to_hex(fill),
                     (' stroke="%s"' % colour_to_hex(outline)) if outline else ""))

    def text(self, xy, text: str, font=None, fill=None, anchor: Optional[str] = None,
             **kwargs) -> None:
        x, y = self._pt(float(xy[0]), float(xy[1]))
        scaled = self._scaled_font(font)
        self._draw.text((x, y), text, font=scaled, fill=fill)
        if not text:
            return
        size, bold = core.font_meta(font)
        scaled_size = max(1.0, size * self.scale)
        ascent, _descent = (scaled.getmetrics() if scaled is not None else (scaled_size, 0))
        # Pillow 默认 anchor="la"（左上角对齐 ascent 顶），SVG 用 alphabetic 基线 + 加 ascent
        self._add('<text x="%.2f" y="%.2f" font-family="%s" font-size="%.2f"%s '
                  'fill="%s" xml:space="preserve">%s</text>'
                  % (x, y + ascent, core.SVG_FONT_FAMILY, scaled_size,
                     ' font-weight="bold"' if bold else "",
                     colour_to_hex(fill), html.escape(text)))

    def textlength(self, text: str, font=None) -> float:
        """返回**未缩放**坐标下的文本宽度（调用方按原坐标系排版）。"""
        if font is None:
            return 0.0
        return float(self._draw.textlength(text, font=font))

    # ------------------------------------------------------------------ 输出
    def to_svg(self, view_box: Optional[Sequence[float]] = None,
               width: Optional[int] = None, height: Optional[int] = None) -> str:
        """生成 SVG 文本；``view_box`` 用于与 PNG 完全一致的取景。"""
        if view_box:
            vb = "%.2f %.2f %.2f %.2f" % tuple(view_box)
        else:
            vb = "0 0 %d %d" % (self.width, self.height)
        out_w = int(width or self.width)
        out_h = int(height or self.height)
        header = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                  '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
                  'viewBox="%s">\n' % (out_w, out_h, vb))
        body = "\n".join("  " + element for element in (self._svg or []))
        return header + body + "\n</svg>\n"

    def write_svg(self, path, view_box=None, width=None, height=None) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_svg(view_box, width, height), encoding="utf-8")
        return target
