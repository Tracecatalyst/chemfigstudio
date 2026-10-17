/**
 * compare.js — 2D 结构式 vs 3D 构象 对照页（支持多分子并排）。
 *
 * 关键取舍：
 *
 * 1. **不新增任何 API**。两个半边都复用已有端点：
 *      2D → POST /api/preview   （不落盘，写配置目录的 preview/）
 *      3D → POST /api/render3d  （只回 molblock 几何，见 ADR-012）
 * 2. **2D 必须串行**。`preview()` 写的是**固定文件名** `preview.png`，多分子并发
 *    请求会互相覆盖 —— 所以 2D 走一条串行队列；3D 返回 JSON，可以全并发。
 * 3. **3D 惰性初始化**：滚到可见才建 WebGL context（多分子容易撞浏览器上限）。
 * 4. `skeleton` 参数本身接受 SMILES（见 structure.py），所以 2D 那半边
 *    把用户输入原样交给后端，前端不做任何转换。
 */

"use strict";

const CMP = {
  catalog: {},
  language: "zh-CN",
  labels: false,
  spinning: false,
  rows: [],
  viewers: {},          // key -> 3Dmol viewer
  built: {},            // key -> 是否已建 WebGL context
  pending3D: {},        // key -> /api/render3d 的返回（等滚动到可见再建）
  token: 0,             // 每次重渲染 +1，丢弃过期的异步结果
  observer: null,
};

/** 预设：只有 i18n key + SMILES，标签全走文案表（中英各一套）。 */
const PRESETS = [
  { key: "cyclohexane", smiles: "C1CCCCC1" },
  { key: "benzene", smiles: "c1ccccc1" },
  { key: "alanine", smiles: "C[C@H](N)C(=O)O" },
  { key: "aspirin", smiles: "CC(=O)Oc1ccccc1C(=O)O" },
  { key: "caffeine", smiles: "CN1C=NC2=C1C(=O)N(C)C(=O)N2C" },
  { key: "nicotine", smiles: "CN1CCC[C@H]1c1cccnc1" },
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

function applyStaticText() {
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    const text = t(node.getAttribute("data-i18n"));
    if (node.tagName === "TITLE") document.title = text;
    else node.textContent = text;
  });
  document.documentElement.lang = CMP.language.startsWith("zh") ? "zh-CN" : "en";
}

/* ------------------------------------------------- 2D 串行队列（见文件头） */

let twoDChain = Promise.resolve();
let twoDSeq = 0;

function queue2D(smiles) {
  const run = twoDChain.then(() => api("/api/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: "skeleton", params: { skeleton: smiles } }),
  }));
  // 队列不能因为某一次失败就断掉
  twoDChain = run.catch(() => null);
  return run;
}

/* ----------------------------------------------------------------- 行 */

function rowElement(item) {
  const section = document.createElement("section");
  section.className = "row";
  section.setAttribute("data-key", item.key);

  const name = item.nameKey ? t(item.nameKey) : item.smiles;
  const why = item.whyKey ? t(item.whyKey) : "";

  section.innerHTML = `
    <div class="row-head">
      <h2></h2>
      <code></code>
      <span class="badges"></span>
    </div>
    <div class="panes">
      <div class="pane">
        <p class="pane-label"></p>
        <div class="shot"><span class="empty"></span><img alt="" hidden></div>
      </div>
      <div class="pane">
        <p class="pane-label"></p>
        <div class="viewer"><span class="empty"></span></div>
      </div>
    </div>
    <p class="why"></p>
    <p class="meta"></p>`;

  section.querySelector("h2").textContent = name;
  section.querySelector("code").textContent = item.smiles;
  const labels = section.querySelectorAll(".pane-label");
  labels[0].textContent = t("compare.twoD");
  labels[1].textContent = t("compare.threeD");
  section.querySelectorAll(".empty").forEach((node) => {
    node.textContent = t("compare.loading");
  });
  if (why) section.querySelector(".why").textContent = why;
  section.querySelector(".viewer").setAttribute("data-key", item.key);
  return section;
}

function renderBadges(section, geometry) {
  const box = section.querySelector(".badges");
  if (!box || !geometry) return;
  box.textContent = "";
  [geometry.formula,
   t("compare.atomCount", { atoms: geometry.atoms }),
   "MMFF " + geometry.energy + " kcal/mol"].forEach((text) => {
    if (!text) return;
    const span = document.createElement("span");
    span.className = "badge";
    span.textContent = text;
    box.appendChild(span);
  });
  const meta = section.querySelector(".meta");
  if (meta) meta.textContent = geometry.method || "";
}

/* ------------------------------------------------------------- 单个分子 */

async function loadRow(section, item, token) {
  // ---- 2D：串行（preview.png 固定文件名，并发会互相覆盖）
  queue2D(item.smiles).then((data) => {
    if (token !== CMP.token) return;
    const img = section.querySelector(".shot img");
    const empty = section.querySelector(".shot .empty");
    if (data && data.ok && img) {
      // 串行保证"文件内容 = 这次请求的结果"；加序号让浏览器不吃缓存
      img.src = data.url + "?k=" + (++twoDSeq);
      img.hidden = false;
      if (empty) empty.hidden = true;
    } else if (empty) {
      empty.textContent = (data && data.error) || t("compare.twoDFailed");
    }
  });

  // ---- 3D：并发（返回 JSON，不冲突）
  const geometry = await api("/api/render3d", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ smiles: item.smiles }),
  });
  if (token !== CMP.token) return;

  const host = section.querySelector(".viewer");
  const empty = host ? host.querySelector(".empty") : null;
  if (geometry && geometry.ok) {
    CMP.pending3D[item.key] = geometry;
    renderBadges(section, geometry);
    if (empty) empty.textContent = t("compare.dragHint");
    maybeBuild3D(host, item.key);
  } else if (empty) {
    empty.textContent = (geometry && geometry.error) || t("compare.threeDFailed");
  }
}

/* ------------------------------------------------------- 3D 惰性初始化 */

function maybeBuild3D(host, key) {
  if (!host || CMP.built[key]) return;
  const geometry = CMP.pending3D[key];
  if (!geometry) return;               // 数据还没回来，等 loadRow 再调一次

  CMP.built[key] = true;
  const viewer = $3Dmol.createViewer(host, { backgroundColor: "white" });
  viewer.addModel(geometry.molblock, "sdf");
  applyStyle(viewer);
  viewer.zoomTo();
  viewer.zoom(1.25);                   // 3Dmol 的 zoomTo 留白偏多，稍微推近
  viewer.render();
  CMP.viewers[key] = viewer;

  const empty = host.querySelector(".empty");
  if (empty) empty.style.display = "none";
  if (CMP.spinning) viewer.spin("y");
}

function applyStyle(viewer) {
  viewer.setStyle({}, { stick: { radius: 0.14 }, sphere: { scale: 0.26 } });
  if (CMP.labels) {
    viewer.addPropertyLabels("elem", {}, { fontColor: "black", font: "Arial",
                                           fontSize: 13, showBackground: false });
  } else {
    viewer.removeAllLabels();
  }
  viewer.render();
}

/* ------------------------------------------------------------- 重渲染 */

function render(list) {
  CMP.token += 1;
  const token = CMP.token;
  CMP.rows = list;
  CMP.viewers = {};
  CMP.built = {};
  CMP.pending3D = {};

  const host = $("cmp-rows");
  host.textContent = "";

  list.forEach((item) => {
    const section = rowElement(item);
    host.appendChild(section);
    loadRow(section, item, token);
  });

  installObservers();
}

function installObservers() {
  if (CMP.observer) CMP.observer.disconnect();
  const hosts = Array.prototype.slice.call(document.querySelectorAll(".viewer"));
  if (!("IntersectionObserver" in window)) {
    hosts.forEach((h) => maybeBuild3D(h, h.getAttribute("data-key")));
    return;
  }
  const io = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      const key = entry.target.getAttribute("data-key");
      maybeBuild3D(entry.target, key);
      if (CMP.built[key]) io.unobserve(entry.target);
    });
  }, { rootMargin: "300px 0px" });
  hosts.forEach((h) => io.observe(h));
  CMP.observer = io;
}

/* ---------------------------------------------------------------- 交互 */

function showSingle() {
  const smiles = ($("cmp-smiles").value || "").trim();
  if (!smiles) { $("cmp-smiles").focus(); return; }
  render([{ key: "custom", nameKey: "", smiles: smiles, whyKey: "" }]);
}

function resetAll() {
  $("cmp-smiles").value = "";
  render(PRESETS.map((preset) => ({
    key: preset.key,
    nameKey: "compare.preset." + preset.key,
    smiles: preset.smiles,
    whyKey: "compare.why." + preset.key,
  })));
}

function installControls() {
  $("cmp-single").addEventListener("click", showSingle);
  $("cmp-reset").addEventListener("click", resetAll);
  $("cmp-back").addEventListener("click", () => { window.location.href = "/"; });
  $("cmp-smiles").addEventListener("keydown", (event) => {
    if (event.key === "Enter") showSingle();
  });
  $("cmp-language").addEventListener("change", () => loadLanguage($("cmp-language").value));

  $("cmp-spin").addEventListener("change", (event) => {
    CMP.spinning = event.target.checked;
    Object.values(CMP.viewers).forEach((viewer) => {
      if (CMP.spinning) viewer.spin("y"); else viewer.spin(false);
    });
  });
  $("cmp-labels").addEventListener("change", (event) => {
    CMP.labels = event.target.checked;
    Object.values(CMP.viewers).forEach(applyStyle);
  });
  window.addEventListener("resize", () => {
    Object.values(CMP.viewers).forEach((viewer) => {
      viewer.resize(); viewer.render();
    });
  });
}

/* ---------------------------------------------------------------- 启动 */

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
}

async function boot() {
  installControls();
  if (typeof $3Dmol === "undefined") {
    $("cmp-status").textContent = "3Dmol.js failed to load";
    return;
  }
  await loadLanguage("auto");
  const wanted = new URLSearchParams(window.location.search).get("smiles");
  if (wanted) {
    $("cmp-smiles").value = wanted;
    showSingle();
  } else {
    resetAll();
  }
}

boot();
