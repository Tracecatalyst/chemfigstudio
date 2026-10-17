#!/usr/bin/env python3
"""生成 README 顶部那张 GIF（docs/images/demo.gif）。

为什么是 GIF 而不是一张静态图：这个工具真正的卖点是**改一个参数，图立刻跟着变**
（静态 PNG 只能展示"画出来长什么样"）。所以第一幕是骨架碳数 2→12 连续增长。

三幕：

1. 骨架碳数递增 —— "参数一变，图立刻变"；
2. 苯的 ``rings`` 关/开 —— Kekulé 式 vs 圆圈；
3. 真实分子 —— 乙醇 / 阿司匹林 / 咖啡因 / 丙氨酸（带楔形键），全部从 SMILES 来。

用法::

    PYTHONPATH=src python3 scripts/make-demo-gif.py [-o docs/images/demo.gif]

需要 RDKit（第 3 幕要解析 SMILES）。没有它时第三幕会失败 —— 这个脚本不做退避，
直接报错，因为半张 GIF 比报错更糟。
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CANVAS = (900, 460)
BACKGROUND = (255, 255, 255)
CAPTION_COLOUR = (88, 88, 88)
#: 小分子放大上限 —— 再大就糊了；乙烷这种只需要 2 倍左右
MAX_UPSCALE = 2.5


def compose(source: pathlib.Path, caption: str):
    """把"内容自适应尺寸"的图居中贴到统一画布上，底部加一行说明。

    必须统一画布：GIF 的每一帧尺寸不同是合法的，但播放时会跳 —— 看起来像抖动。
    """
    from PIL import Image, ImageDraw

    from chemfigstudio import core

    image = Image.open(source).convert("RGB")
    canvas = Image.new("RGB", CANVAS, BACKGROUND)
    fit = min((CANVAS[0] - 70) / image.width, (CANVAS[1] - 120) / image.height)
    scale = min(fit, MAX_UPSCALE)
    if abs(scale - 1.0) > 0.01:
        image = image.resize(
            (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
            Image.LANCZOS)
    canvas.paste(image, ((CANVAS[0] - image.width) // 2,
                         (CANVAS[1] - 60 - image.height) // 2 + 8))
    if caption:
        draw = ImageDraw.Draw(canvas)
        font = core.font_for_text(core.fonts(22), caption)
        width = core.text_width(draw, caption, font, font)
        draw.text(((CANVAS[0] - width) / 2, CANVAS[1] - 52), caption,
                  font=font, fill=CAPTION_COLOUR)
    return canvas


def build(output: pathlib.Path) -> None:
    from PIL import Image

    from chemfigstudio import figures

    frames, durations = [], []
    with tempfile.TemporaryDirectory() as tmp:
        scratch = pathlib.Path(tmp)

        # 第一幕：改参数，图立刻变
        for count in range(2, 13):
            source = scratch / ("chain%d.png" % count)
            figures.skeleton_figure(source, "C" * count)
            frames.append(compose(source, "skeleton  —  %d carbons" % count))
            durations.append(300)

        # 第二幕：芳香环的两种画法
        for rings in (False, True):
            source = scratch / ("benzene%d.png" % rings)
            figures.skeleton_figure(source, "c1ccccc1", rings=rings)
            frames.append(compose(source, "rings %s" % ("on" if rings else "off")))
            durations.append(1100)

        # 第三幕：真实分子（要多花一点时间看）
        for label, smiles in (("ethanol", "CCO"),
                              ("aspirin", "CC(=O)Oc1ccccc1C(=O)O"),
                              ("caffeine", "CN1C=NC2=C1C(=O)N(C)C(=O)N2C"),
                              ("alanine — wedge bond", "C[C@H](N)C(=O)O")):
            source = scratch / ("molecule%d.png" % abs(hash(smiles)))
            figures.skeleton_figure(source, smiles)
            frames.append(compose(source, label))
            durations.append(1000)

    output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(output, save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, optimize=True, disposal=2)
    print("已生成 %s（%d 帧，%.1f s，%.0f KB）"
          % (output, len(frames), sum(durations) / 1000, output.stat().st_size / 1024))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成 README 顶部的演示 GIF")
    parser.add_argument("-o", "--out", default=str(ROOT / "docs/images/demo.gif"))
    args = parser.parse_args(argv)
    build(pathlib.Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
