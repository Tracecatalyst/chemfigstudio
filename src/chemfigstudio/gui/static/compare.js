/**
 * compare.js — 2D 结构式 vs 3D 构象 对照页。
 *
 * 关键取舍：**不新增任何 API**。两个半边都复用已有端点：
 *   2D → POST /api/preview   （不落盘、写配置目录的 preview/，见 ADR/验收 1/2）
 *   3D → POST /api/render3d  （只回 molblock 几何数据，见 ADR-012）
 * 这样对照页只是"把已有的两块并排放"，服务端一行渲染逻辑都没重复。
 *
 * `skeleton` 参数本身接受 SMILES / molblock / JSON（见 structure.py），
 * 所以 2D 那半边直接把用户输入的 SMILES 交给它，不需要前端做任何转换。
 */

"use strict";

const CMP = {
  viewer: null,
  seq: 0,              // 请求序号：用户狂点"生成"时只认最后一次的结果
  catalog: {},
  language: "zh-CN",
  spinning: false,
  current: null,
};

/** 预设分子：只放 name 的 i18n key + SMILES + 一句"2D 看不出什么"的 key。 */
const PRESETS = [
  { id: "cyclohexane", smiles: "C1CCCCC1" },
  { id: "benzene", smiles: "c1ccccc1" },
  { id: "alanine", smiles: "C[C@H](N)C(=O)O" },
  { id: "aspirin", smiles: "CC(=O)Oc1ccccc1C(=O)O" },
  { id: "caffeine", smiles: "CN1C=NC2=C1C(=O)N(C)C(=O)N2C" },
  { id: "nicotine", smiles: "CN1CCC[C@H]1c1cccnc1" },
];

const $ = (id) => document.getElementById(id);

function t(key, vars) {
  let text = CMP.catalog[key] || key;
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

function setStatus(text) { $("cmp-status").textContent = text || ""; }

function showError(message) {
  const box = $("cmp-error");
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
  document.documentElement.lang = CMP.language.startsWith("zh") ? "zh-CN" : "en";
}

/* ------------------------------------------------------------- 预设下拉 */

function buildPresets() {
  const select = $("cmp-preset");
  select.textContent = "";
  PRESETS.forEach((preset) => {
    const option = document.createElement("option");
    option.value = preset.id;
    option.textContent = t("compare.preset." + preset.id);
    select.appendChild(option);
  });
  select.addEventListener("change", () => {
    const preset = PRESETS.find((p) => p.id === select.value);
    if (preset) {
      $("cmp-smiles").value = preset.smiles;
      $("cmp-why").textContent = t("compare.why." + preset.id);
      build();
    }
  });
}

/* --------------------------------------------------------------- 生成对照 */

async function build() {
  const smiles = ($("cmp-smiles").value || "").trim();
  if (!smiles) { showError(t("compare.needSmiles")); return; }
  showError("");
  setStatus(t("compare.building"));

  // 防竞态：两个请求都返回后要确认自己还是"最新那次"。
  // 2D 那半边走 /api/preview，写的是**固定文件名** preview.png；
  // 用户连点两次时，先发的请求可能后返回，不过滤就会把旧分子画上去。
  const seq = ++CMP.seq;

  // 两个半边并行取数据：拼错/缺 RDKit 时各自的错误都能独立报出来
  const [image, geometry] = await Promise.all([
    api("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "skeleton", params: { skeleton: smiles } }),
    }),
    api("/api/render3d", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ smiles: smiles }),
    }),
  ]);

  if (seq !== CMP.seq) return;         // 已经有更新的一次请求，丢弃这次结果

  // ---- 2D 半边
  if (image && image.ok) {
    $("cmp-2d").src = image.url + "?t=" + Date.now();
    $("cmp-2d").hidden = false;
    $("cmp-2d-empty").hidden = true;
  } else {
    $("cmp-2d").hidden = true;
    $("cmp-2d-empty").hidden = false;
    showError((image && image.error) || t("compare.twoDFailed"));
  }

  // ---- 3D 半边
  if (geometry && geometry.ok) {
    CMP.current = geometry;
    loadModel(geometry.molblock);
    renderMeta(geometry);
    $("cmp-3d-empty").hidden = true;
    setStatus(t("compare.ready", { formula: geometry.formula || smiles,
                                   atoms: geometry.atoms }));
  } else {
    $("cmp-3d-empty").hidden = false;
    $("cmp-meta").textContent = "";
    // 缺 RDKit 时后端给的是可执行提示（503），原样显示
    showError((geometry && geometry.error) || t("compare.threeDFailed"));
    setStatus("");
  }
}

function loadModel(molblock) {
  if (!CMP.viewer) CMP.viewer = $3Dmol.createViewer($("cmp-host"), { backgroundColor: "white" });
  CMP.viewer.removeAllModels();
  CMP.viewer.removeAllLabels();
  CMP.viewer.addModel(molblock, "sdf");
  CMP.viewer.setStyle({}, { stick: { radius: 0.14 }, sphere: { scale: 0.26 } });
  CMP.viewer.zoomTo();
  CMP.viewer.zoom(1.25);        // 3Dmol 的 zoomTo 留白偏多，稍微推近
  CMP.viewer.render();
}

function renderMeta(data) {
  const rows = [
    [t("compare.meta.formula"), data.formula || "-"],
    [t("compare.meta.atoms"), String(data.atoms)],
    [t("compare.meta.energy"), data.energy + " " + (data.energy_unit || "")],
    [t("compare.meta.method"), data.method || "-"],
  ];
  const list = $("cmp-meta");
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
}

/* ------------------------------------------------------------------ 交互 */

function toggleSpin() {
  if (!CMP.viewer) return;
  CMP.spinning = !CMP.spinning;
  $("cmp-spin").classList.toggle("active", CMP.spinning);
  if (CMP.spinning) CMP.viewer.spin("y");
  else CMP.viewer.spin(false);
}

function installControls() {
  $("cmp-build").addEventListener("click", build);
  $("cmp-spin").addEventListener("click", toggleSpin);
  $("cmp-back").addEventListener("click", () => { window.location.href = "/"; });
  $("cmp-smiles").addEventListener("keydown", (event) => {
    if (event.key === "Enter") build();
  });
  $("cmp-language").addEventListener("change", () => loadLanguage($("cmp-language").value));
  window.addEventListener("resize", () => {
    if (CMP.viewer) { CMP.viewer.resize(); CMP.viewer.render(); }
  });
}

/* ------------------------------------------------------------------ 启动 */

async function loadLanguage(code) {
  const data = await api("/api/i18n/" + encodeURIComponent(code || "auto"));
  if (!data || !data.ok) return;
  CMP.catalog = data.catalog || {};
  CMP.language = data.language || "zh-CN";
  const select = $("cmp-language");
  select.textContent = "";
  (data.languages || []).forEach((code2) => {
    const option = document.createElement("option");
    option.value = code2;
    option.textContent = code2;
    if (code2 === CMP.language) option.selected = true;
    select.appendChild(option);
  });
  applyStaticText();
  buildPresets();                       // 预设名称要跟着语言走
  const current = PRESETS.find((p) => p.smiles === $("cmp-smiles").value.trim());
  if (current) $("cmp-why").textContent = t("compare.why." + current.id);
}

async function boot() {
  installControls();
  if (typeof $3Dmol === "undefined") {
    showError(t("compare.noLib"));
    return;
  }
  await loadLanguage("auto");
  const wanted = new URLSearchParams(window.location.search).get("smiles");
  if (wanted) $("cmp-smiles").value = wanted;
  build();
}

boot();
