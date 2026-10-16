#!/usr/bin/env python3
"""生成 macOS 应用图标（.icns）。

直接用项目自己的绘图代码画一张"课本风格"的分子图当图标，
保证图标风格与输出一致，也不引入任何图标依赖。

用法：scripts/make-icon.py <输出.icns>     # 需要 macOS 的 iconutil
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from PIL import Image, ImageDraw                     # noqa: E402
from chemfigstudio import core                        # noqa: E402

SIZE = 1024


def draw_icon() -> Image.Image:
    """透明底 + 白色圆角卡片 + 一条示性式链（与工具输出同款风格）。"""
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pad = int(SIZE * 0.045)
    d.rounded_rectangle([pad, pad, SIZE - pad, SIZE - pad],
                        radius=int(SIZE * 0.22), fill=(255, 255, 255, 255),
                        outline=(229, 231, 235, 255), width=max(2, SIZE // 256))

    # 用工具自己的 draw_chain：放大到图标里好看的比例
    style = core.ChainStyle(n_carbons=3, label_size=170, sub_size=120,
                            dx=300, dy=120, bond_width=30, label_style="h2")
    core.draw_chain(d, 170, 560, style)
    return img


def build_icns(out_path: pathlib.Path) -> None:
    icon = draw_icon()
    with tempfile.TemporaryDirectory() as tmp:
        iconset = pathlib.Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for base in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = base * scale
                if px > SIZE:
                    continue
                name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
                icon.resize((px, px), Image.LANCZOS).save(iconset / name)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out_path)],
                       check=True)


def main(argv: list) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    out = pathlib.Path(argv[1])
    build_icns(out)
    print("已生成图标：%s (%d 字节)" % (out, out.stat().st_size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
