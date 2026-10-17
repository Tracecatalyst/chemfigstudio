#!/usr/bin/env python3
"""生成"结构导入"实测画廊：把一组真实分子从 SMILES 画出来，再拼成一个页面。

用法（需要 RDKit）：
    PYTHONPATH=src:<含 rdkit 的目录> python3 scripts/make-showcase.py [输出目录]

默认输出到 ``examples/import/``，生成 ``index.html``（图片内嵌 base64，双击即看）
加每张 ``<name>.png``。**不提交产物**：这份脚本就是"怎么复现"的答案。
"""

from __future__ import annotations

import base64
import json
import os
import string
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "src"))

#: (文件名, SMILES, 一句话说明) —— 挑的都是"最能暴露化学错误"的分子
CASES = [
    ("benzene", "c1ccccc1", "苯环：六个碳都不写字，交替单双键（Kekulé 式）"),
    ("ethanol", "CCO", "乙醇：CH₃ + OH"),
    ("acetic-acid", "CC(=O)O", "乙酸：羧基的双键氧写 O，羟基写 OH"),
    ("phenol", "c1ccccc1O", "苯酚：环 + 酚羟基"),
    ("salicylic", "O=C(O)c1ccccc1O", "水杨酸：羧基与酚羟基各一处"),
    ("alanine", "C[C@H](N)C(=O)O", "L-丙氨酸：虚线楔形，窄端在手性碳"),
    ("pyrrole", "c1cc[nH]c1", "吡咯：环内那个氮带 1 个氢 → NH"),
    ("pyridine", "c1ccncc1", "吡啶：氮没有氢 → N"),
    ("caffeine", "CN1C=NC2=C1C(=O)N(C)C(=O)N2C",
     "咖啡因：四个氮全无氢、两个 CH₃、两个 C=O"),
    ("aspirin", "CC(=O)Oc1ccccc1C(=O)O", "阿司匹林：13 个重原子"),
    ("toluene", "Cc1ccccc1", "甲苯：只有甲基那个碳写字"),
    ("nicotine", "CN1CCC[C@H]1c1cccnc1", "尼古丁：手性中心 + 双环"),
]

TEMPLATE = string.Template("""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>chemfigstudio · 结构导入实测</title>
<style>
  :root { --ink:#111827; --muted:#6b7280; --line:#e5e7eb; --bg:#f6f7f9; --card:#fff; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:15px/1.6 -apple-system, "PingFang SC", "Helvetica Neue", Arial, sans-serif; }
  header { padding:40px 32px 24px; max-width:1280px; margin:0 auto; }
  h1 { margin:0 0 6px; font-size:26px; letter-spacing:-.01em; }
  header p { margin:0; color:var(--muted); font-size:14px; }
  header code { background:#eef0f4; padding:1px 5px; border-radius:4px; font-size:13px; }
  .grid { max-width:1280px; margin:0 auto; padding:0 32px 64px;
          display:grid; gap:20px;
          grid-template-columns:repeat(auto-fill, minmax(300px, 1fr)); }
  .card { margin:0; background:var(--card); border:1px solid var(--line);
          border-radius:12px; overflow:hidden; display:flex; flex-direction:column;
          box-shadow:0 1px 2px rgba(16,24,40,.04); }
  .shot { background:#fff; display:flex; align-items:center; justify-content:center;
          padding:12px; border-bottom:1px solid var(--line); }
  .shot img { display:block; max-width:100%; max-height:260px; width:auto; height:auto; }
  figcaption { padding:12px 14px 14px; }
  figcaption h3 { margin:0 0 4px; font-size:15px; }
  figcaption h3 code { font-weight:400; font-size:12px; color:var(--muted);
                       display:block; margin-top:3px; word-break:break-all; }
  .note { margin:6px 0 10px; color:var(--muted); font-size:13px; }
  .meta { margin:0; display:flex; flex-wrap:wrap; gap:6px; }
  .badge { font-size:11px; padding:2px 7px; border-radius:999px;
           background:#eef2ff; color:#3730a3; }
  .badge.stereo { background:#fef3c7; color:#92400e; }
  .badge.dim { background:#f3f4f6; color:#6b7280; }
</style></head>
<body>
<header>
  <h1>chemfigstudio · 结构导入实测</h1>
  <p>全部由 SMILES 直接导入生成（RDKit 出 2D 坐标与立体化学）：
     <code>api.render("skeleton", params={"skeleton": "c1ccccc1"})</code>。
     没有一张是手画的坐标。</p>
</header>
<main class="grid">$cards
</main>
</body></html>
""")


def main(argv):
    outdir = argv[1] if len(argv) > 1 else os.path.join(HERE, "examples", "import")
    os.makedirs(outdir, exist_ok=True)

    from chemfigstudio import api, structure

    cards = []
    for name, smiles, note in CASES:
        result = structure.from_smiles(smiles)
        path = os.path.join(outdir, name + ".png")
        rendered = api.render("skeleton", out=path,
                              params={"skeleton": result.skeleton.to_json(),
                                      "title": name})
        with open(path, "rb") as handle:
            data = base64.b64encode(handle.read()).decode("ascii")
        wedges = sum(1 for b in result.skeleton.bonds if b.wedge != "none")
        badge = ('<span class="badge stereo">楔形 %d</span>' % wedges) if wedges else ""
        cards.append("""
  <figure class="card">
    <div class="shot"><img alt="%s" src="data:image/png;base64,%s"></div>
    <figcaption>
      <h3>%s<code>%s</code></h3>
      <p class="note">%s</p>
      <p class="meta"><span class="badge">%s</span>
         <span class="badge">%d 重原子</span>%s
         <span class="badge dim">%d×%d</span></p>
    </figcaption>
  </figure>""" % (name, data, name, smiles, note, result.formula,
                  len(result.skeleton.atoms), badge,
                  rendered.size[0], rendered.size[1]))
        print("  %-12s %-10s %2d 重原子 %d 楔形 %s" % (
            name, result.formula, len(result.skeleton.atoms), wedges,
            tuple(rendered.size)))

    target = os.path.join(outdir, "index.html")
    with open(target, "w", encoding="utf8") as handle:
        handle.write(TEMPLATE.safe_substitute(cards="\n".join(cards)))
    print("\n写出 %s（%.0f KB，图片已内嵌）" % (target, os.path.getsize(target) / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
