#!/usr/bin/env python3
"""生成"2D 结构式 vs 3D 构象"对照页。

为什么值得做：这个工具的主场景是**课本风格的结构式**（平面、示性式、孤对电子），
而 3D 视图能回答结构式天生回答不了的问题 —— 环己烷是**椅式**不是平面六边形、
手性中心是**四面体**、稠环是**非平面**的。

产物是一个**自包含** HTML（3Dmol.js 内联、图片 base64 内嵌、molblock 内联），
双击就能开，可以拖动旋转；不依赖服务器、不联网。

用法：
    python3 scripts/make-compare.py [输出目录]     # 默认 examples/compare/
    make compare                                   # 同上

需要 RDKit（3D 构象生成）；没装会给出可执行提示。
"""

from __future__ import annotations

import base64
import html
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "src"))

#: (key, 中文名, SMILES, **为什么 2D 看不出**)
MOLECULES = [
    ("cyclohexane", "环己烷", "C1CCCCC1",
     "2D 只是个正六边形。3D 里它是**椅式**：碳原子交替上下，键角约 111°，"
     "这正是有机化学第一课要讲的东西 —— 平面六边形在能量上不存在。"),
    ("benzene", "苯", "c1ccccc1",
     "2D 画成平面六边形是对的，但看不出**为什么**：六个 π 电子完全离域，"
     "整个环是平面的、C–C 键长都相等（1.39 Å，介于单键与双键之间）。"),
    ("alanine", "L-丙氨酸", "C[C@H](N)C(=O)O",
     "2D 只能用一个虚线楔形暗示立体。3D 里能直接看到手性碳是**四面体**，"
     "四个取代基朝向不同 —— 这就是 L/D 构型的来源。"),
    ("aspirin", "阿司匹林", "CC(=O)Oc1ccccc1C(=O)O",
     "2D 上看不出苯环与酯基、羧基之间的**相对朝向**。3D 里能看出苯环是平面、"
     "两个取代基在同一平面内但朝向不同。"),
    ("caffeine", "咖啡因", "CN1C=NC2=C1C(=O)N(C)C(=O)N2C",
     "2D 画的是两个稠合的六元/五元环。3D 里能看出它们是**共平面**的，"
     "而三个甲基朝向不同（不是叠在一起的）。"),
    ("nicotine", "尼古丁", "CN1CCC[C@H]1c1cccnc1",
     "2D 里吡咯烷环和吡啶环看起来是并排的两个圈。3D 里能看出两环的**二面角**、"
     "以及吡咯烷环的**信封构象** —— 这些直接决定它和受体怎么结合。"),
]

TEMPLATE_HEAD = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>chemfigstudio · 2D 结构式 vs 3D 构象</title>
<style>
  :root { --ink:#111827; --muted:#6b7280; --line:#e5e7eb; --bg:#f6f7f9;
          --card:#fff; --accent:#4f6ef7; --warn:#b45309; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:15px/1.65 -apple-system, "PingFang SC", "Helvetica Neue", Arial, sans-serif; }
  header { max-width:1180px; margin:0 auto; padding:44px 28px 20px; }
  h1 { margin:0 0 8px; font-size:27px; letter-spacing:-.015em; }
  header p { margin:0 0 6px; color:var(--muted); font-size:14.5px; }
  header code { background:#eef0f4; padding:1px 5px; border-radius:4px; font-size:13px; }
  .toolbar { display:flex; gap:10px; align-items:center; flex-wrap:wrap;
             margin-top:16px; padding-top:14px; border-top:1px solid var(--line); }
  .toolbar label { font-size:13.5px; color:var(--muted); display:flex;
                   align-items:center; gap:6px; cursor:pointer; }
  .toolbar input { cursor:pointer; }
  main { max-width:1180px; margin:0 auto; padding:8px 28px 72px; }
  .row { background:var(--card); border:1px solid var(--line); border-radius:14px;
         margin:18px 0; overflow:hidden; }
  .row-head { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap;
              padding:14px 18px 0; }
  .row-head h2 { margin:0; font-size:17px; }
  .row-head code { font-size:12.5px; color:var(--muted); word-break:break-all; }
  .row-head .badge { font-size:11px; padding:2px 8px; border-radius:999px;
                     background:#eef2ff; color:#3730a3; white-space:nowrap; }
  .panes { display:grid; grid-template-columns:1fr 1fr; gap:0; }
  @media (max-width:820px) { .panes { grid-template-columns:1fr; } }
  .pane { padding:12px 18px 18px; }
  .pane + .pane { border-left:1px solid var(--line); }
  @media (max-width:820px) { .pane + .pane { border-left:none;
                                             border-top:1px solid var(--line); } }
  .pane-label { font-size:11.5px; letter-spacing:.05em; text-transform:uppercase;
                color:var(--muted); margin:0 0 8px; }
  .shot { display:flex; align-items:center; justify-content:center; height:280px;
          background:#fff; border:1px solid var(--line); border-radius:10px; overflow:hidden; }
  .shot img { max-width:100%; max-height:100%; width:auto; height:auto; display:block; }
  .viewer { height:280px; background:#fff; border:1px solid var(--line);
            border-radius:10px; overflow:hidden; position:relative; }
  .viewer .hint { position:absolute; left:8px; bottom:6px; font-size:11.5px;
                  color:#9ca3af; pointer-events:none; }
  .why { margin:0; padding:0 18px 16px; font-size:13.5px; color:var(--muted); }
  .why strong { color:var(--warn); font-weight:600; }
  .meta { padding:0 18px 16px; font-size:12px; color:#9ca3af; }
</style></head>
<body>
<header>
  <h1>同一个分子，两种看法</h1>
  <p>左：本工具的主场景 —— <strong>课本风格结构式</strong>（平面、示性式、孤对电子）。
     右：同一个分子的<strong>真实 3D 构象</strong>（RDKit ETKDGv3 + MMFF94），
     <strong>可以拖动旋转</strong>。</p>
  <p>3D 视图补的正是结构式天生回答不了的：<code>环己烷是椅式</code>、
     <code>手性碳是四面体</code>、<code>稠环共平面</code>。
     全部由 <code>SMILES</code> 直接生成，没有一张是手画的。</p>
  <div class="toolbar">
    <label><input type="checkbox" id="spin"> 自动旋转（全部）</label>
    <label><input type="checkbox" id="labels"> 显示元素符号</label>
    <span style="font-size:12.5px;color:#9ca3af">拖动旋转 · 滚轮缩放</span>
  </div>
</header>
<main>
"""

TEMPLATE_ROW = """  <section class="row" data-key="$key">
    <div class="row-head">
      <h2>$name</h2>
      <code>$smiles</code>
      <span class="badge">$formula</span>
      <span class="badge">$atoms 原子（含氢）</span>
      <span class="badge">MMFF $energy kcal/mol</span>
    </div>
    <div class="panes">
      <div class="pane">
        <p class="pane-label">2D 结构式</p>
        <div class="shot"><img alt="$name 2D" src="data:image/png;base64,$png"></div>
      </div>
      <div class="pane">
        <p class="pane-label">3D 构象（可拖动）</p>
        <div class="viewer" data-key="$key"><span class="hint">拖动旋转</span></div>
      </div>
    </div>
    <p class="why">$why</p>
    <p class="meta">$method · 原子 $heavy 个重原子 · 脚本：scripts/make-compare.py</p>
  </section>
"""

TEMPLATE_TAIL = """</main>
<script type="application/json" id="molecules">$data</script>
<script>$three_d_mol_js</script>
<script>
(function () {
  "use strict";
  var data = JSON.parse(document.getElementById("molecules").textContent);
  var built = {};

  function build(host) {
    var key = host.getAttribute("data-key");
    var item = data[key];
    if (!item || built[key]) return;
    built[key] = true;
    var viewer = $3Dmol.createViewer(host, { backgroundColor: "white" });
    viewer.addModel(item.molblock, "sdf");
    applyStyle(viewer, false);
    viewer.zoomTo();
    viewer.zoom(1.25);        // 3Dmol 的 zoomTo 留白偏多，稍微推近一点
    viewer.render();
    host.__viewer = viewer;
    host.querySelector(".hint").style.display = "none";
  }

  function applyStyle(viewer, labels) {
    viewer.setStyle({}, { stick: { radius: 0.14 }, sphere: { scale: 0.26 } });
    if (labels) {
      viewer.addPropertyLabels("elem", {}, { fontColor: "black",
        font: "Arial", fontSize: 13, showBackground: false });
    } else {
      viewer.removeAllLabels();
    }
    viewer.render();
  }

  // 惰性初始化：滚到可见才建 WebGL context（十几个 context 会撞浏览器上限）
  var hosts = Array.prototype.slice.call(document.querySelectorAll(".viewer"));
  if (!("IntersectionObserver" in window)) {
    hosts.forEach(build);
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) { build(entry.target); io.unobserve(entry.target); }
      });
    }, { rootMargin: "300px 0px" });
    hosts.forEach(function (h) { io.observe(h); });
  }

  document.getElementById("spin").addEventListener("change", function (event) {
    Object.keys(built).forEach(function (key) {
      var host = document.querySelector('.viewer[data-key="' + key + '"]');
      if (host && host.__viewer) {
        if (event.target.checked) { host.__viewer.spin("y"); }
        else { host.__viewer.spin(false); }
      }
    });
  });

  document.getElementById("labels").addEventListener("change", function (event) {
    Object.keys(built).forEach(function (key) {
      var host = document.querySelector('.viewer[data-key="' + key + '"]');
      if (host && host.__viewer) applyStyle(host.__viewer, event.target.checked);
    });
  });
})();
</script>
</body></html>
"""


def main(argv):
    outdir = argv[1] if len(argv) > 1 else os.path.join(HERE, "examples", "compare")
    os.makedirs(outdir, exist_ok=True)

    from chemfigstudio import api, render3d, structure
    from chemfigstudio.errors import ConfigError
    from chemfigstudio.smiles import rdkit_available, rdkit_source

    # 2D 与 3D 两条路都从 SMILES 起（RDKit 负责 2D 布局与 3D 构象），
    # 所以在这里一次性检查，别等到循环中间才崩。
    if not rdkit_available():
        print("这个对照页需要 RDKit（它同时提供 2D 布局与 3D 构象）。\n"
              "安装：python3 -m pip install --target .pylibs rdkit numpy\n"
              "或设置 CHEMFIG_PYLIBS 指向已装好的目录。", file=sys.stderr)
        return 3
    print("RDKit：%s" % rdkit_source())

    vendor = os.path.join(HERE, "src", "chemfigstudio", "gui", "static",
                          "vendor", "3Dmol-min.js")
    with open(vendor, encoding="utf8") as handle:
        three_d_mol_js = handle.read()

    rows = []
    payload = {}
    for key, name, smiles, why in MOLECULES:
        two_d = structure.from_smiles(smiles)
        png_path = os.path.join(outdir, key + "-2d.png")
        api.render("skeleton", out=png_path,
                   params={"skeleton": two_d.skeleton.to_json(), "title": name})
        with open(png_path, "rb") as handle:
            png = base64.b64encode(handle.read()).decode("ascii")

        conformer = render3d.embed3d(smiles)
        payload[key] = {"molblock": conformer.molblock, "smiles": smiles}

        row = (TEMPLATE_ROW
               .replace("$key", key).replace("$name", name)
               .replace("$smiles", html.escape(smiles))
               .replace("$formula", conformer.formula)
               .replace("$atoms", str(conformer.atoms))
               .replace("$heavy", str(conformer.heavy_atoms))
               .replace("$energy", "%.2f" % conformer.energy)
               .replace("$method", conformer.method)
               .replace("$why", why)
               .replace("$png", png))
        rows.append(row)
        print("  %-12s %-10s 2D ✓  3D %d 原子 %s" % (
            key, conformer.formula, conformer.atoms, conformer.method))

    document = (TEMPLATE_HEAD
                + "".join(rows)
                + TEMPLATE_TAIL
                .replace("$data", json.dumps(payload, ensure_ascii=False))
                .replace("$three_d_mol_js", three_d_mol_js))

    target = os.path.join(outdir, "index.html")
    with open(target, "w", encoding="utf8") as handle:
        handle.write(document)
    print("\n写出 %s（%.1f MB：3Dmol 内联 + 图片 base64 + molblock 内联）"
          % (target, os.path.getsize(target) / 1e6))
    print("双击即可打开；3D 视图可拖动旋转。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
