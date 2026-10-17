// 用 3Dmol 的 canvas 直接导出 3D 图（docs/images/compare.png 的右半部分素材）。
//
// 为什么不用截图：`Page.captureScreenshot` 在这个页面上永远超时（合成器拿不到帧）。
// 但 3Dmol 的 `pngURI()` 就是 `canvas.toDataURL()` —— 在 JS 层直接拿像素，
// 完全绕开截图。
//
// 用法（需要仓库根目录跑着 GUI 服务）：
//   PYTHONPATH=src python3 -m chemfigstudio.gui.server --port 8790 --no-browser --quiet &
//   ~/.local/bin/ego-browser nodejs < scripts/make-3d-images.js
//
// 产物写到 /tmp/3dshot/，再用 PIL 合成（见 docs/SCREENSHOTS.md）。
const fs = await import("node:fs");

const BASE = "http://127.0.0.1:8790";
const OUT = "/tmp/3dshot";
const JOBS = [
  ["butanol", "CCCCO"],
  ["caffeine", "CN1C=NC2=C1C(=O)N(C)C(=O)N2C"],
];

const task = await taskSpace("3d images");
const page = task.page("p1");
await page.cdp("Emulation.setDeviceMetricsOverride",
  { width: 700, height: 560, deviceScaleFactor: 2, mobile: false });
await page.goto(BASE + "/viewer3d");
await page.evaluate("(async()=>{ await new Promise(r=>setTimeout(r,2500)); return 1; })()");

fs.mkdirSync(OUT, { recursive: true });
for (const [name, smiles] of JOBS) {
  const out = await page.evaluate(`(async () => {
    const res = await fetch("/api/render3d", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ smiles: ${JSON.stringify(smiles)} }),
    });
    const data = await res.json();
    if (!data.ok) return { error: data.error };
    const host = document.createElement("div");
    host.style.cssText = "position:fixed;left:0;top:0;width:680px;height:400px;background:white;z-index:99999";
    document.body.appendChild(host);
    const viewer = $3Dmol.createViewer(host, { backgroundColor: "white" });
    viewer.addModel(data.molblock, "sdf");
    viewer.setStyle({}, { stick: { radius: 0.18 }, sphere: { scale: 0.3 } });
    viewer.zoomTo();
    viewer.zoom(1.15);
    viewer.render();
    await new Promise(r => setTimeout(r, 400));
    return { uri: viewer.pngURI(), formula: data.formula };
  })()`);
  if (out && out.uri) {
    fs.writeFileSync(OUT + "/" + name + ".png",
                     Buffer.from(out.uri.split(",")[1], "base64"));
    console.log(name + " ok " + out.formula);
  } else {
    console.log(name + " FAILED " + JSON.stringify(out).slice(0, 150));
  }
}
