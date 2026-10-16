/**
 * viewer3d.js — 3D 分子查看器前端。
 *
 * 分工（见 docs/DECISIONS.md ADR-012）：
 *   服务端 POST /api/render3d  →  只回几何数据（molblock）
 *   这里用 3Dmol.js 渲染        →  我们不自己写 3D 数学、不自己写渲染
 *
 * 所以本文件只有三件事：取数据、套样式、把画布导出成 PNG。
 */

"use strict";

const VD = {
  viewer: null,
  model: null,
  catalog: {},
  language: "auto",
  languages: [],
  current: null,        // 最近一次 /api/render3d 的返回
  spinning: false,
  timer: null,
};

const $ = (id) => document.getElementById(id);

function t(key, vars) {
  let text = VD.catalog[key] || key;
  if (vars) {
    for (const [k, v] of Object.entries(vars)) text = text.split("{" + k + "}").join(v);
  }
  return text;
}

async function api(path, options) {
  const res = await fetch(path, options);
  let data = null;
  try { data = await res.json(); } catch (e) { data = { ok: false, error: "bad JSON" }; }
  if (!res.ok && data && !data.error) data.error = "HTTP " + res.status;
  return data;
}

function setStatus(text) { $("vd-status").textContent = text || ""; }

function showError(message) {
  const box = $("vd-error");
  if (!message) { box.hidden = true; box.textContent = ""; return; }
  box.hidden = false;
  box.textContent = message;
}

function applyStaticText() {
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    const text = t(node.getAttribute("data-i18n"));
    if (node.tagName === "TITLE") document.title = text;
    else node.textContent = text;
  });
  document.documentElement.lang = VD.language.startsWith("zh") ? "zh-CN" : "en";
}

/* --------------------------------------------------------------- 3D 样式 */

/** 3Dmol 的样式表；空间填充用 vdW 半径，球棍用小半径 + 棍。 */
function styleFor(name, labels) {
  let style;
  switch (name) {
    case "spacefill":
      style = { sphere: { scale: 1.0 } };
      break;
    case "stick":
      style = { stick: { radius: 0.18 } };
      break;
    case "line":
      style = { line: { linewidth: 2.0 } };
      break;
    case "ballstick":
    default:
      style = { stick: { radius: 0.15 }, sphere: { scale: 0.28 } };
      break;
  }
  if (labels) style = Object.assign({}, style, { stick: (style.stick || { radius: 0.15 }) });
  return { style: style, labels: !!labels };
}

function applyStyle() {
  if (!VD.viewer || !VD.model) return;
  const wanted = $("vd-style").value;
  const labels = $("vd-labels").checked;
  const spec = styleFor(wanted, labels);
  VD.viewer.setStyle({}, spec.style);
  if (labels) {
    // 元素符号：位置取原子坐标，颜色跟随元素
    VD.viewer.addPropertyLabels("elem", {}, {
      fontColor: "black", font: "Arial", fontSize: 14, showBackground: false,
    });
  } else {
    VD.viewer.removeAllLabels();
  }
  VD.viewer.render();
}

/* ------------------------------------------------------------- 取几何数据 */

async function build() {
  const smiles = ($("vd-smiles").value || "").trim();
  if (!smiles) { showError(t("viewer3d.needSmiles")); return; }
  showError("");
  setStatus(t("viewer3d.building"));
  const data = await api("/api/render3d", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ smiles: smiles }),
  });
  if (!data || !data.ok) {
    setStatus("");
    // 503 = 环境缺 RDKit（不是用户写错），文案里已经带安装指引
    showError((data && data.error) || t("viewer3d.failed"));
    $("vd-empty").hidden = false;
    return;
  }
  VD.current = data;
  loadModel(data.molblock);
  renderMeta(data);
  setStatus(t("viewer3d.ready", { formula: data.formula || smiles, atoms: data.atoms }));
}

function loadModel(molblock) {
  const host = $("vd-host");
  if (!VD.viewer) VD.viewer = $3Dmol.createViewer(host, { backgroundColor: "white" });
  VD.viewer.removeAllModels();
  VD.viewer.removeAllLabels();
  VD.model = VD.viewer.addModel(molblock, "sdf");
  $("vd-empty").hidden = true;
  applyStyle();
  VD.viewer.zoomTo();
  VD.viewer.render();
}

function renderMeta(data) {
  const rows = [
    [t("viewer3d.meta.formula"), data.formula || "-"],
    [t("viewer3d.meta.atoms"), String(data.atoms)],
    [t("viewer3d.meta.energy"), data.energy + " " + (data.energy_unit || "")],
    [t("viewer3d.meta.method"), data.method || "-"],
  ];
  const list = $("vd-meta");
  list.textContent = "";
  rows.forEach(([label, value]) => {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.textContent = label;
    const code = document.createElement("code");
    code.textContent = value;
    li.appendChild(span);
    li.appendChild(code);
    list.appendChild(li);
  });
  if (data.notes && data.notes.length) {
    data.notes.forEach((note) => {
      const li = document.createElement("li");
      li.className = "v3d-note";
      li.textContent = note;
      list.appendChild(li);
    });
  }
}

/* ------------------------------------------------------------- 静帧导出 */

/**
 * 在**独立的大画布**上重绘一次再导出 —— 不放大屏幕上的位图。
 * 这与 core.save_figure 的 "重新绘制成更大的图" 是同一个原则（契约 §6.4）。
 */
function exportViewer(width, transparent) {
  const aspect = 3 / 4;                                  // 与 stage 比例一致
  const height = Math.max(1, Math.round(width * aspect));
  const host = document.createElement("div");
  host.style.position = "fixed";
  host.style.left = "-10000px";
  host.style.top = "0";
  host.style.width = width + "px";
  host.style.height = height + "px";
  document.body.appendChild(host);
  let viewer = null;
  try {
    viewer = $3Dmol.createViewer(host, { backgroundColor: "white" });
    viewer.addModel(VD.current.molblock, "sdf");
    const spec = styleFor($("vd-style").value, $("vd-labels").checked);
    viewer.setStyle({}, spec.style);
    if (spec.labels) {
      viewer.addPropertyLabels("elem", {}, { fontColor: "black", font: "Arial", fontSize: 14 });
    }
    // 与屏幕上看到的是同一个朝向：沿用当前视角矩阵
    try {
      viewer.setView(VD.viewer.getView());
    } catch (e) { /* 拿不到视角就退化成默认取景，不算失败 */ }
    if (transparent) viewer.setBackgroundColor("rgba(0,0,0,0)", 0);
    viewer.zoomTo();
    viewer.render();
    return { uri: viewer.pngURI(), width: viewer.getCanvas().width,
             height: viewer.getCanvas().height };
  } finally {
    if (viewer) { try { viewer.clear(); } catch (e) { /* 忽略 */ } }
    host.remove();
  }
}

function download(uri, filename) {
  const link = document.createElement("a");
  link.href = uri;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function exportPng() {
  if (!VD.current) { showError(t("viewer3d.needBuild")); return; }
  const width = Math.max(320, Math.min(4096, parseInt($("vd-width").value, 10) || 3000));
  setStatus(t("viewer3d.exporting", { width: width }));
  // 让状态文字先画出来，再做重活（大画布渲染会占住主线程几百毫秒）
  setTimeout(() => {
    try {
      const shot = exportViewer(width, $("vd-transparent").checked);
      const name = (VD.current.formula || "molecule") + "_3d.png";
      download(shot.uri, name);
      setStatus(t("viewer3d.exported", { w: shot.width, h: shot.height }));
    } catch (e) {
      setStatus("");
      showError(t("viewer3d.exportFailed", { message: String(e && e.message || e) }));
    }
  }, 30);
}

/* ------------------------------------------------------------------ 交互 */

function toggleSpin() {
  if (!VD.viewer) return;
  VD.spinning = !VD.spinning;
  $("vd-spin").classList.toggle("active", VD.spinning);
  if (VD.spinning) {
    VD.viewer.spin("y");
  } else {
    VD.viewer.spin(false);
  }
}

function installControls() {
  $("vd-build").addEventListener("click", build);
  $("vd-fit").addEventListener("click", () => {
    if (VD.viewer) { VD.viewer.zoomTo(); VD.viewer.render(); }
  });
  $("vd-reset").addEventListener("click", () => {
    if (VD.viewer) { VD.viewer.setView([0, 0, 0, 0, 0, 0, 0, 0, 1]); VD.viewer.zoomTo(); VD.viewer.render(); }
  });
  $("vd-spin").addEventListener("click", toggleSpin);
  $("vd-style").addEventListener("change", applyStyle);
  $("vd-labels").addEventListener("change", applyStyle);
  $("vd-export").addEventListener("click", exportPng);
  $("vd-back").addEventListener("click", () => { window.location.href = "/"; });
  $("vd-smiles").addEventListener("keydown", (event) => {
    if (event.key === "Enter") build();
  });
  $("vd-language").addEventListener("change", () => loadLanguage($("vd-language").value));
  // 窗口变化时让 3Dmol 重新适配（否则画布与容器不同步）
  window.addEventListener("resize", () => {
    if (VD.viewer) { VD.viewer.resize(); VD.viewer.render(); }
  });
}

/* ------------------------------------------------------------------ 启动 */

async function loadLanguage(code) {
  const data = await api("/api/i18n/" + encodeURIComponent(code || "auto"));
  if (!data || !data.ok) return;
  VD.catalog = data.catalog || {};
  VD.language = data.language || "zh-CN";
  VD.languages = data.languages || [];
  const select = $("vd-language");
  select.textContent = "";
  VD.languages.forEach((code2) => {
    const option = document.createElement("option");
    option.value = code2;
    option.textContent = code2;
    if (code2 === VD.language) option.selected = true;
    select.appendChild(option);
  });
  applyStaticText();
}

async function boot() {
  installControls();
  if (typeof $3Dmol === "undefined") {
    showError(t("viewer3d.noLib"));
    return;
  }
  await loadLanguage("auto");
  const wanted = new URLSearchParams(window.location.search).get("smiles");
  if (wanted) $("vd-smiles").value = wanted;
  if ($("vd-smiles").value.trim()) build();
}

boot();
