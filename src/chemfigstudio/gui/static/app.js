/* chemfigstudio GUI —— 无框架、无 CDN，离线可用。
 *
 * 契约（ARCHITECTURE.md §8）：
 *   GET  /api/figures?lang=xx   → 表单参数表（本地化，含 choice_labels）
 *   GET  /api/settings          → 设置 + 可选语言
 *   PUT  /api/settings          → {"changes":{...}} 更新设置
 *   GET  /api/i18n/<lang>       → 文案目录（catalog）
 *   GET  /api/health            → 版本 / 语言
 *
 * 所有界面文案都来自 catalog：HTML 用 data-i18n 标注，JS 用 t()。
 */
"use strict";

const state = {
  catalog: {}, languages: [], language: "auto",
  settings: null, figures: [], current: null, lastFile: null, version: "",
};

const $ = (id) => document.getElementById(id);

/** 取文案；缺失时回退到 key 本身，永不显示空白。 */
function t(key, vars) {
  let text = state.catalog[key] || key;
  if (vars) {
    for (const [k, v] of Object.entries(vars)) {
      text = text.split("{" + k + "}").join(v);
    }
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

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) node.appendChild(c);
  return node;
}

/** 把 HTML 里带 data-i18n 的元素刷成当前语言。 */
function applyStaticText() {
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    const key = node.getAttribute("data-i18n");
    const text = t(key);
    if (node.tagName === "TITLE") document.title = text;
    else node.textContent = text;
  });
  document.documentElement.lang = state.language.startsWith("zh") ? "zh-CN" : "en";
}

/* ------------------------------------------------------------------ 表单 */

function buildField(spec) {
  const id = "param-" + spec.name;
  let control;

  if (spec.type === "choice") {
    control = el("select", { id });
    (spec.choices || []).forEach((choice, index) => {
      const label = (spec.choice_labels && spec.choice_labels[index]) || choice;
      const opt = el("option", { value: choice, text: label });
      if (choice === spec.default) opt.selected = true;
      control.appendChild(opt);
    });
  } else if (spec.type === "bool") {
    control = el("input", { id, type: "checkbox" });
    control.checked = !!spec.default;
  } else if (spec.type === "int") {
    control = el("input", { id, type: "number", value: spec.default ?? "" });
    if (spec.min !== null && spec.min !== undefined) control.min = spec.min;
    if (spec.max !== null && spec.max !== undefined) control.max = spec.max;
  } else if (spec.name === "title" || spec.name === "summary") {
    // 标题/说明可能很长（默认标题就有 40+ 字），用多行框，避免被单行 input 截断
    control = el("textarea", { id, rows: "2", spellcheck: "false" });
    control.value = spec.default ?? "";
  } else {
    control = el("input", { id, type: "text", value: spec.default ?? "", spellcheck: "false" });
  }

  const frag = document.createDocumentFragment();
  frag.appendChild(el("label", { class: "row" },
    [el("span", { class: "lbl", text: spec.label }), control]));
  if (spec.help) frag.appendChild(el("p", { class: "help", text: spec.help }));
  return frag;
}

function renderFields() {
  const box = $("fields");
  box.textContent = "";
  if (!state.current) return;
  $("kind-desc").textContent = state.current.description || "";
  $("filename").value = state.current.default_filename || "";
  for (const spec of state.current.params) {
    const frag = buildField(spec);
    frag.querySelectorAll("input, select").forEach((node) => {
      node.addEventListener("input", scheduleLivePreview);
      node.addEventListener("change", scheduleLivePreview);
    });
    box.appendChild(frag);
  }
}

function collectParams() {
  const params = {};
  for (const spec of state.current.params) {
    const node = $("param-" + spec.name);
    if (!node) continue;
    params[spec.name] = spec.type === "bool" ? node.checked : node.value;
  }
  return params;
}

/* --------------------------------------------------------------- 交互动作 */

function showError(message) {
  const box = $("error");
  if (!message) { box.hidden = true; box.textContent = ""; return; }
  box.hidden = false;
  box.textContent = message;
}

function showWarnings(list) {
  const box = $("warnings");
  box.textContent = "";
  for (const w of list || []) box.appendChild(el("div", { class: "warn", text: "⚠ " + w }));
}

async function doRender() {
  showError("");
  const active = (state.session && state.session.activeId) || null;
  await sessionAction({ action: "apply", tabId: active,
                        kind: state.current.kind, params: collectParams() });
  $("render").disabled = true;
  try {
    const data = await api("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind: state.current.kind,
        params: collectParams(),
        filename: $("filename").value.trim() || null,
      }),
    });
    if (!data.ok) { showError(data.error || t("error.render_failed", { message: "" })); return; }
    state.lastFile = data.file;
    $("empty").hidden = true;
    const img = $("preview");
    img.hidden = false;
    img.src = data.url + "?t=" + Date.now();
    $("path").textContent = data.path;
    $("size").textContent = (data.size || []).join(" × ") + " px";
    showWarnings(data.warnings);
    $("reveal").disabled = true;                 // 预览不是文件，不能"在 Finder 中显示"
    scheduleDraft();
  } finally {
    $("render").disabled = false;
  }
}

/* ---- 草稿：自动保存（不写用户输出目录，验收 3） ---- */
let draftTimer = null;

function scheduleDraft() {
  if (draftTimer) clearTimeout(draftTimer);
  draftTimer = setTimeout(saveDraft, 1200);
}

async function saveDraft() {
  if (!state.current) return;
  const document = {
    mode: "figure",
    figure: { kind: state.current.kind, params: collectParams() },
    export: {
      format: $("ex-format") ? $("ex-format").value : (state.settings.defaultFormat || "png"),
      dpi: state.settings.defaultDpi || 300,
      transparent: !!state.settings.transparent,
      filename: $("filename").value || "figure.png",
    },
  };
  const data = await api("/api/documents", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ document }),
  });
  if (data.ok) state.draftId = data.id;
}

/** 启动时恢复最近一次草稿（验收 3：关掉再打开，未导出的工作还在）。 */
async function restoreLatestDraft() {
  const list = await api("/api/documents");
  if (!list.ok || !list.drafts || !list.drafts.length) return false;
  const latest = list.drafts[0];
  const data = await api("/api/documents/" + encodeURIComponent(latest.id));
  if (!data.ok) return false;
  const figure = (data.document && data.document.figure) || {};
  const kind = figure.kind || "alcohol";
  const restored = state.figures.find((f) => f.kind === kind);
  if (!restored) return false;
  state.current = restored;
  $("kind").value = kind;
  renderFields();
  for (const [name, value] of Object.entries(figure.params || {})) {
    const node = $("param-" + name);
    if (!node) continue;
    if (node.type === "checkbox") node.checked = !!value; else node.value = value;
  }
  return true;
}

async function doReveal() {
  if (!state.lastFile) return;
  await api("/api/reveal", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file: state.lastFile }),
  });
}

/* ------------------------------------------------------------------ 设置 */

function fillLanguageSelects() {
  for (const id of ["language", "set-language"]) {
    const box = $(id);
    if (!box) continue;
    box.textContent = "";
    for (const item of state.languages) {
      const opt = el("option", { value: item.code, text: item.label });
      if (item.code === state.language) opt.selected = true;
      box.appendChild(opt);
    }
  }
}

async function openSettings() {
  $("settings-panel").hidden = false;
  $("set-output-dir").value = state.settings.outputDir || "";
  $("set-format").value = state.settings.defaultFormat || "png";
  $("set-dpi").value = state.settings.defaultDpi || 300;
  $("set-transparent").checked = !!state.settings.transparent;
  $("set-allowed-dirs").value = (state.settings.allowedWriteDirs || []).join("\n");
  $("settings-path").textContent = state.settings._path || "";
  $("settings-status").textContent = "";
}

async function saveSettings() {
  const changes = {
    outputDir: $("set-output-dir").value.trim(),
    language: $("set-language").value,
    defaultFormat: $("set-format").value,
    defaultDpi: Number($("set-dpi").value) || 300,
    transparent: $("set-transparent").checked,
    // 多行文本框 → 绝对路径列表（服务端会再过一遍：只收绝对路径）
    allowedWriteDirs: $("set-allowed-dirs").value
      .split("\n").map((line) => line.trim()).filter(Boolean),
  };
  const data = await api("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ changes }),
  });
  if (!data.ok) { $("settings-status").textContent = data.error || "error"; return; }
  state.settings = data.settings;
  $("settings-status").textContent = t("gui.settings.saved");
  if (data.language && data.language !== state.language) {
    await switchLanguage(data.language, true);
  } else {
    $("outdir-hint").textContent = t("gui.outdir", { path: state.settings._resolved.outputDir });
  }
}

/** 切语言：拉新 catalog + 本地化参数表，然后刷新界面。 */
async function switchLanguage(code, persist) {
  const info = await api("/api/i18n/" + encodeURIComponent(code || "auto"));
  if (!info.ok) return;
  state.language = info.language;
  state.catalog = info.catalog || {};
  state.languages = info.languages || state.languages;

  const figs = await api("/api/figures?lang=" + encodeURIComponent(code || "auto"));
  if (figs.ok) {
    state.figures = figs.figures;
    state.current = state.figures.find((f) => f.kind === state.current?.kind) || state.figures[0];
  }
  if (persist) {
    await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ changes: { language: code || "auto" } }),
    });
  }
  applyStaticText();
  fillLanguageSelects();
  renderFields();
}

/* ------------------------------------- 外壳增强：拖放 / 剪贴板 / 记住布局 */

/** 把拖进来的文本解析成参数（CSV 第一行数据 或 纯 SMILES），返回填了几个字段。 */
function chemfigApplyText(text) {
  if (!state.current || !text) return 0;
  const lines = String(text).split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  if (!lines.length) return 0;
  const withCsv = lines.find((line) => line.includes(","));
  const row = withCsv ? withCsv.split(",").map((s) => s.trim()) : null;

  let smiles = "";
  let deltaH = "";
  let name = "";
  if (row) {
    // 有表头就跳过表头；否则按 name,smiles,delta_h 猜
    const looksHeader = /smiles/i.test(row.join(" "));
    const dataRow = looksHeader
      ? lines.slice(lines.indexOf(withCsv) + 1).find((l) => l.includes(","))
      : withCsv;
    if (dataRow) {
      const cells = dataRow.split(",").map((s) => s.trim());
      if (!looksHeader && cells.length >= 3) { name = cells[0]; smiles = cells[1]; deltaH = cells[2]; }
      else { name = cells[0] || ""; smiles = cells[1] || ""; deltaH = cells[2] || ""; }
    }
  } else {
    smiles = lines[0];
  }
  if (!smiles) return 0;

  let filled = 0;
  for (const [field, value] of [["smiles", smiles], ["delta_h", deltaH], ["name", name]]) {
    const node = $("param-" + field);
    if (node && value) { node.value = value; filled += 1; }
  }
  if (filled) { showError(""); showWarnings([t("gui.drop.loaded", { value: smiles })]); }
  return filled;
}

/** 复制当前预览图到剪贴板（失败返回原因，不抛异常）。 */
async function chemfigCopyImage() {
  try {
    const img = $("preview");
    if (!img || !img.src || img.hidden) return { ok: false, reason: "no-preview" };
    if (!navigator.clipboard || !window.ClipboardItem) return { ok: false, reason: "unsupported" };
    const blob = await (await fetch(img.src)).blob();
    await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
    return { ok: true };
  } catch (err) {
    return { ok: false, reason: String(err && err.message || err) };
  }
}

function installDragAndDrop() {
  const stop = (event) => { event.preventDefault(); event.stopPropagation(); };
  ["dragenter", "dragover", "dragleave", "drop"].forEach((name) =>
    document.addEventListener(name, stop, false));
  document.addEventListener("drop", async (event) => {
    const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    const text = file ? await file.text() : (event.dataTransfer || {}).getData
      ? event.dataTransfer.getData("text") : "";
    const filled = chemfigApplyText(text);
    if (filled) await doRender();
  });
}

/** 记住布局：窗口尺寸 + 侧栏折叠状态（防抖写进设置）。 */
let layoutTimer = null;
function installLayoutMemory() {
  const save = () => {
    if (layoutTimer) clearTimeout(layoutTimer);
    layoutTimer = setTimeout(() => {
      api("/api/settings", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ changes: { windowState: {
          width: window.innerWidth, height: window.innerHeight,
          kind: state.current ? state.current.kind : null,
        } } }),
      });
    }, 800);
  };
  window.addEventListener("resize", save);
  return save;
}

function restoreLayout() {
  const layout = (state.settings && state.settings.windowState) || {};
  if (layout.kind) {
    const found = state.figures.find((f) => f.kind === layout.kind);
    if (found) { state.current = found; $("kind").value = layout.kind; renderFields(); }
  }
}

/* ------------------------------------------- 会话：标签页 / 图层 / 撤销重做 */

/** 把某个文档套用到表单。
 *  切标签/撤销/重做**必须**调用它，否则服务端换了文档、界面却不动 →
 *  用户看到的就是"点了标签没反应"（踩过：两个标签内容不同也看不出来）。 */
function applyDocument(document, tabId) {
  if (!document) return false;
  state.document = document;
  state.formTabId = tabId || state.formTabId;      // 表单从此代表这个标签
  const figure = document.figure || {};
  const kind = figure.kind || "alcohol";
  const found = state.figures.find((f) => f.kind === kind);
  if (found) {
    state.current = found;
    $("kind").value = kind;
    renderFields();
  }
  for (const [name, value] of Object.entries(figure.params || {})) {
    const node = $("param-" + name);
    if (!node) continue;
    if (node.type === "checkbox") node.checked = !!value;
    else node.value = value;
  }
  return true;
}

async function sessionAction(payload, thenRefresh) {
  // 写入类动作（apply）必须打到"表单代表的标签"，否则会把 A 的参数写进 B（跨标签污染）
  if (payload && payload.action === "apply" && !payload.tabId) {
    payload = Object.assign({}, payload,
                            { tabId: state.formTabId || (state.session || {}).activeId || null });
  }
  const data = await api("/api/session", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (data.ok) {
    state.session = data.session;
    // 会改变"当前文档"的动作 → 必须把文档套回表单（否则界面不动 = 假死）
    const switching = ["select", "newTab", "closeTab", "undo", "redo"].indexOf(payload.action) >= 0;
    if (switching && data.document) applyDocument(data.document, data.activeId);
    renderSession();
    if (thenRefresh) await refreshPreview();
  }
  return data;
}

function renderSession() {
  const session = state.session || { tabs: [] };
  const strip = $("tabs");
  strip.textContent = "";
  for (const tab of session.tabs) {
    const button = el("button", {
      class: "tab" + (tab.id === session.activeId ? " active" : ""),
      text: tab.label + (tab.dirty ? " •" : ""),
      title: t("gui.tab.rename_hint"),
      // 点击一律切换（改名只走双击 / ✎ 按钮）——
      // 之前"点当前标签=改名"依赖 state.session.activeId，一旦与界面失步，
      // 用户点的标签会被当成当前标签去改名，表现就是"点了没反应"。
      onclick: () => sessionAction({ action: "select", tabId: tab.id }, true),
      ondblclick: (event) => startRenameTab(event.target, tab),
      // 双击就地改名（WKWebView 不支持 window.prompt，所以用行内 input）
      ondblclick: (event) => startRenameTab(event.target, tab),
    });
    strip.appendChild(button);
  }
  const active = (session.tabs || []).find((t) => t.id === session.activeId);
  $("undo").disabled = !(active && active.canUndo);
  $("redo").disabled = !(active && active.canRedo);
  $("tab-close").disabled = !session.tabs.length;

  const layers = $("layers");
  layers.textContent = "";
  const current = state.document;
  for (const layer of (current && current.layers) || []) {
    const box = el("input", { type: "checkbox" });
    box.checked = !!layer.visible;
    box.addEventListener("change", () => sessionAction(
      { action: "toggleLayer", layerId: layer.id, visible: box.checked }, true));
    layers.appendChild(el("li", {}, [box, el("span", { text: layer.name })]));
  }
  const add = el("button", { class: "ghost", text: t("gui.layer.add"),
                             onclick: () => sessionAction({ action: "addLayer", name: "layer" }, true) });
  layers.appendChild(el("li", {}, [add]));
}

/** 输入即刷新：改参数后 250 ms 自动重画（验收 2 的 ≤500 ms 要求）。
 *  只重画预览，**不导出、不落用户输出目录**；导出仍要点「导出图片…」。 */
let liveTimer = null;

window.chemfigLiveFires = 0;      // 调试/测试用：live 处理器被触发次数

function scheduleLivePreview() {
  window.chemfigLiveFires += 1;
  if (liveTimer) clearTimeout(liveTimer);
  liveTimer = setTimeout(() => {
    refreshPreview().catch((err) => showError(String(err && err.message || err)));
  }, 250);
}

function installLivePreview() {
  const handler = (event) => {
    const target = event.target || {};
    if (!target.id || target.id.indexOf("param-") !== 0) return;   // 只管参数控件
    if (liveTimer) clearTimeout(liveTimer);
    scheduleLivePreview();
  };
  document.addEventListener("input", handler, true);      // 捕获阶段，重建表单也不丢
  document.addEventListener("change", handler, true);
}

/** 只刷新画布：不落用户输出目录（验收 1/2）。 */
async function refreshPreview() {
  if (!state.current) return;
  const data = await api("/api/preview", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: state.current.kind, params: collectParams() }),
  });
  if (!data.ok) return;
  const img = $("preview");
  $("empty").hidden = true;
  img.hidden = false;
  img.src = data.url + "?t=" + Date.now();
  $("path").textContent = data.path;
  $("size").textContent = (data.size || []).join(" × ") + " px";
}

/* ------------------------------------------------------------- 导出（验收 7/12） */

function openExport() {
  $("export-panel").hidden = false;
  const settings = state.settings || {};
  $("ex-dir").value = (settings._resolved && settings._resolved.outputDir) || "";
  $("ex-filename").value = ($("filename").value || "figure").replace(/\.[a-z]+$/i, "");
  $("ex-format").value = settings.defaultFormat || "png";
  $("ex-dpi").value = settings.defaultDpi || 300;
  $("ex-transparent").checked = !!settings.transparent;
  $("export-status").textContent = "";
  $("confirm-export").hidden = true;
}

function exportPayload(overwrite) {
  const fmt = $("ex-format").value;
  return {
    kind: state.current.kind,
    params: collectParams(),
    dir: $("ex-dir").value.trim() || null,
    // 去掉用户可能自己写的扩展名，再按所选格式补一个 —— 否则会出现 x.png.png
    filename: ($("ex-filename").value.trim() || "figure").replace(/\.[a-z0-9]+$/i, "") + "." + fmt,
    format: fmt,
    dpi: Number($("ex-dpi").value) || 300,
    transparent: $("ex-transparent").checked,
    overwrite: !!overwrite,
  };
}

async function doExport(overwrite) {
  $("export-status").textContent = "…";
  const data = await api("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(exportPayload(overwrite)),
  });
  if (data.needs_confirmation) {
    // 验收 12：先问，用户取消则一个字节都不写
    $("confirm-export").hidden = false;
    $("export-status").textContent = (data.message || "") + "\n" + (data.existing || []).join("\n");
    return;
  }
  if (!data.ok) { $("export-status").textContent = data.error || "error"; return; }
  $("confirm-export").hidden = true;
  $("export-status").textContent = t("export.exported_to", { path: data.path });
  state.lastFile = data.file;
  showWarnings(data.warnings);
}

/* ------------------------------------------------- 批量导出（验收 14） */

function openBatch() {
  $("batch-panel").hidden = false;
  $("batch-dir").value = (state.settings._resolved && state.settings._resolved.outputDir) || "";
  $("batch-format").value = state.settings.defaultFormat || "png";
  $("batch-dpi").value = state.settings.defaultDpi || 300;
  $("batch-status").textContent = "";
  $("batch-rows").textContent = "";
}

function batchPayload(extra) {
  const payload = {
    csv: $("batch-csv").value,
    dir: $("batch-dir").value.trim() || null,
    format: $("batch-format").value,
    dpi: Number($("batch-dpi").value) || 300,
  };
  return Object.assign(payload, extra || {});
}

function renderBatchRows(data) {
  const list = $("batch-rows");
  list.textContent = "";
  for (const item of data.items || data.rows || []) {
    const label = (item.name || item.smiles) + (item.error ? "  ✗" : "  ✓");
    list.appendChild(el("li", {}, [el("span", { text: label })]));
  }
}

async function batchRequest(extra) {
  $("batch-status").textContent = "…";
  const data = await api("/api/batch", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(batchPayload(extra)),
  });
  if (data.needs_confirmation) {
    $("batch-status").textContent = (data.message || "") + "\n" + (data.existing || []).join("\n");
    return data;
  }
  if (!data.ok) { $("batch-status").textContent = data.error || "error"; return data; }
  if (data.dryRun) {
    $("batch-status").textContent = t("batch.parsed", { count: data.count, dir: data.dir });
    renderBatchRows(data);
  } else {
    $("batch-status").textContent = t("batch.done", { succeeded: data.succeeded, failed: data.failed });
    renderBatchRows(data);
  }
  return data;
}

/* ------------------------------------- 骨架编辑器（阶段⑤，复用会话/预览/撤销） */

const SK_PAD = 60, SK_HIT = 26;
state.skeleton = null;
state.scene = { molecules: [], names: [], active: 0 };
state.formTabId = null;      // 表单当前代表哪个标签的文档（写入必须打到它，不能打到"活动标签"）
state.skSelected = null;

/** 与后端 skeleton.Skeleton.chain 相同的默认几何（保证"重置为直链"一致）。 */
function skDefaultChain(n) {
  const atoms = [], bonds = [];
  for (let i = 0; i < n; i++) {
    atoms.push({ id: "c" + i, element: "C", x: 120 + i * 92,
                 y: 160 + (i % 2 ? 50 : -50), label: i === 0 ? "CH_{3}" : "CH_{2}" });
    if (i > 0) bonds.push({ a: "c" + (i - 1), b: "c" + i, order: 1 });
  }
  atoms.push({ id: "o0", element: "O", x: 120 + n * 92, y: 160 + (n % 2 ? 50 : -50),
               label: "OH" });
  bonds.push({ a: "c" + (n - 1), b: "o0", order: 1 });
  return { title: "", atoms, bonds };
}

function skContext() {
  const canvas = $("sk-canvas");
  return canvas.getContext("2d");
}

function skToCanvas(atom) { return [atom.x + SK_PAD, atom.y + SK_PAD]; }
function skFromCanvas(x, y) { return [x - SK_PAD, y - SK_PAD]; }

function skHit(x, y) {
  let best = null, bestDistance = SK_HIT;
  for (const atom of state.skeleton.atoms) {
    const [cx, cy] = skToCanvas(atom);
    const distance = Math.hypot(cx - x, cy - y);
    if (distance <= bestDistance) { best = atom; bestDistance = distance; }
  }
  return best;
}

/** 点—线段距离：命中一根键要按线段算（与后端 skeleton._point_segment_distance 一致）。 */
function skPointSegment(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const lengthSq = dx * dx + dy * dy;
  if (lengthSq <= 1e-9) return Math.hypot(px - ax, py - ay);
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lengthSq));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

function skHitBond(x, y) {
  const skeleton = state.skeleton || { atoms: [], bonds: [] };
  const byId = {};
  for (const atom of skeleton.atoms) byId[atom.id] = atom;
  let best = null, bestDistance = 14;
  for (const bond of skeleton.bonds) {
    const first = byId[bond.a], second = byId[bond.b];
    if (!first || !second) continue;
    const [ax, ay] = skToCanvas(first), [bx, by] = skToCanvas(second);
    const distance = skPointSegment(x, y, ax, ay, bx, by);
    if (distance <= bestDistance) { best = bond; bestDistance = distance; }
  }
  return best;
}

/** 吸附：键角吸到 30° 的倍数、键长吸到 92 的倍数（与后端 snap_position 同一规则）。 */
function skSnap(atom) {
  const skeleton = state.skeleton || { atoms: [] };
  let reference = null, shortest = 260;
  for (const other of skeleton.atoms) {
    if (other === atom) continue;
    const distance = Math.hypot(other.x - atom.x, other.y - atom.y);
    if (distance <= shortest) { reference = other; shortest = distance; }
  }
  if (!reference) return;
  const dx = atom.x - reference.x, dy = atom.y - reference.y;
  const length = Math.hypot(dx, dy);
  const step = 30 * Math.PI / 180;
  const angle = length < 1e-6 ? 0 : Math.round(Math.atan2(dy, dx) / step) * step;
  const multiple = Math.max(1, Math.round(length / 92));
  atom.x = Math.round(reference.x + Math.cos(angle) * 92 * multiple);
  atom.y = Math.round(reference.y + Math.sin(angle) * 92 * multiple);
}

/** 元素 → 默认显示文本（与后端 skeleton.ELEMENT_LABELS 保持一致）。 */
const SK_LABELS = { C: "CH_{3}", H: "H", O: "OH", N: "NH_{2}",
                    S: "SH", Cl: "Cl", Br: "Br", P: "PH_{2}" };

function skCurrentElement() {
  const box = $("sk-element");
  return box ? box.value : "C";
}

/** 切换元素：默认标签跟着换；用户自定义过的标签不动（与后端 change_element 同规则）。 */
function skSetElement(element, atom) {
  const target = atom || state.skSelected;
  if (!target) return null;
  const defaultOld = SK_LABELS[target.element] || target.element;
  if (!target.label || target.label === defaultOld) target.label = SK_LABELS[element] || element;
  target.element = element;
  return target;
}

function skColour(element) {
  return { C: "#111827", H: "#6B7280", O: "#D61F1F", N: "#1D4ED8",
           Cl: "#15803D", Br: "#92400E", S: "#A16207", P: "#C2410C" }[element] || "#111827";
}

function skDraw() {
  const ctx = skContext();
  const canvas = $("sk-canvas");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const skeleton = state.skeleton || { atoms: [], bonds: [] };
  const byId = {};
  for (const atom of skeleton.atoms) byId[atom.id] = atom;

  ctx.strokeStyle = "#111827";
  for (const bond of skeleton.bonds) {
    const first = byId[bond.a], second = byId[bond.b];
    if (!first || !second) continue;
    const [ax, ay] = skToCanvas(first), [bx, by] = skToCanvas(second);
    ctx.lineWidth = bond.order > 1 ? 2 : 3;
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.lineTo(bx, by);
    ctx.stroke();
  }
  for (const atom of skeleton.atoms) {
    const [cx, cy] = skToCanvas(atom);
    const colour = skColour(atom.element);
    ctx.beginPath();
    ctx.arc(cx, cy, 15, 0, Math.PI * 2);
    ctx.fillStyle = "#fff";
    ctx.fill();
    ctx.lineWidth = atom === state.skSelected ? 5 : 3;
    ctx.strokeStyle = atom === state.skSelected ? "#4f6ef7" : colour;
    ctx.stroke();
    ctx.fillStyle = colour;
    ctx.font = "bold 15px Arial";
    const text = atom.label || atom.element;
    ctx.fillText(text.replace(/[_^{}]/g, ""), cx - ctx.measureText(text).width / 3, cy + 5);
  }
}

/** 场景：多个分子（图层）纵向堆叠；state.skeleton 始终指向当前分子。 */
function skWrapScene(parsed) {
  if (parsed && parsed.molecules && parsed.molecules.length) {
    return { molecules: parsed.molecules, names: parsed.molecules.map((m) => m.name || ""),
             active: Math.max(0, Math.min(parsed.active || 0, parsed.molecules.length - 1)) };
  }
  if (parsed && parsed.atoms && parsed.atoms.length) {
    return { molecules: [parsed], names: [""], active: 0 };
  }
  return { molecules: [skDefaultChain(4)], names: [""], active: 0 };
}

function skSyncSkeleton() {
  state.skeleton = state.scene.molecules[state.scene.active];
}

/** 双击标签 → 就地变成输入框；回车/失焦提交，Esc 取消。 */
function startRenameTab(button, tab) {
  const input = el("input", { class: "tab-input", type: "text", value: tab.label });
  button.replaceWith(input);
  input.focus();
  input.select();
  let done = false;
  const finish = async (commit) => {
    if (done) return;
    done = true;
    const title = input.value.trim();
    if (commit && title && title !== tab.label) {
      await sessionAction({ action: "renameTab", tabId: tab.id, title: title });
    }
    renderSession();
  };
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") finish(true);
    if (event.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
}

function renderMolecules() {
  const list = $("sk-molecules");
  if (!list) return;
  list.textContent = "";
  state.scene.molecules.forEach((molecule, index) => {
    const active = index === state.scene.active;
    const label = (state.scene.names[index] || t("sk.molecule") + " " + (index + 1))
      + " · " + molecule.atoms.length + t("sk.atoms");
    list.appendChild(el("li", {}, [
      el("button", { class: active ? "tab active" : "tab", text: label,
                     onclick: () => { state.scene.active = index; skSyncSkeleton();
                                      state.skSelected = null; skDraw(); renderMolecules(); } }),
    ]));
  });
  list.appendChild(el("li", {}, [
    el("button", { class: "ghost", text: t("sk.add_molecule"),
                   onclick: () => skAddMolecule() }),
    el("button", { class: "ghost", text: t("sk.remove_molecule"),
                   onclick: () => skRemoveMolecule() }),
  ]));
}

function skAddMolecule() {
  state.scene.molecules.push(skDefaultChain(4));
  state.scene.names.push("");
  state.scene.active = state.scene.molecules.length - 1;
  skSyncSkeleton();
  state.skSelected = null;
  skDraw();
  renderMolecules();
}

function skRemoveMolecule() {
  if (state.scene.molecules.length <= 1) return;          // 至少留一个
  state.scene.molecules.splice(state.scene.active, 1);
  state.scene.names.splice(state.scene.active, 1);
  state.scene.active = Math.max(0, state.scene.active - 1);
  skSyncSkeleton();
  skDraw();
  renderMolecules();
}

function skSceneJson() {
  return JSON.stringify({
    active: state.scene.active,
    molecules: state.scene.molecules.map((molecule, index) => Object.assign(
      { name: state.scene.names[index] || "" }, molecule)),
  });
}

/** 打开 3D 查看器：把当前表单里的 SMILES 带过去，省得用户再敲一遍。 */
function openViewer3d() {
  const field = $("param-smiles");
  const smiles = field && field.value ? field.value.trim() : "";
  const url = smiles ? "/viewer3d?smiles=" + encodeURIComponent(smiles) : "/viewer3d";
  window.location.href = url;
}

/** 打开"2D 结构式 vs 3D 构象"对照页，把当前表单里的 SMILES 带过去。 */
function openCompare() {
  const field = $("param-smiles");
  const smiles = field && field.value ? field.value.trim() : "";
  const url = smiles ? "/compare?smiles=" + encodeURIComponent(smiles) : "/compare";
  window.location.href = url;
}

function openSkeleton() {
  $("sk-panel").hidden = false;
  let parsed = null;
  try {
    const raw = ($("param-skeleton") || {}).value || "";
    if (raw) parsed = JSON.parse(raw);
  } catch (err) { parsed = null; }
  state.scene = skWrapScene(parsed);
  skSyncSkeleton();
  state.skSelected = null;
  $("sk-status").textContent = t("sk.hint");
  renderMolecules();
  skDraw();
}

/** 提交到会话：进历史（因此撤销/重做对骨架编辑同样有效）+ 刷新画布。 */
async function skCommit() {
  const skeleton = skSceneJson();
  if ($("param-skeleton")) $("param-skeleton").value = skeleton;
  await sessionAction({ action: "apply", kind: "skeleton",
                        params: { skeleton: skeleton } });
  await refreshPreview();
  $("sk-status").textContent = t("sk.applied", { atoms: state.skeleton.atoms.length,
                                                 bonds: state.skeleton.bonds.length });
}

function installSkeletonEditor() {
  const canvas = $("sk-canvas");
  let dragging = null;

  /** 在逻辑画布坐标 (x, y) 上"按下"：鼠标事件与测试接缝共用这一条路径。 */
  function skPress(x, y) {
    const hit = skHit(x, y);
    const mode = $("sk-mode").value;
    if (mode === "order") {
      const bond = skHitBond(x, y);
      if (bond) { bond.order = bond.order >= 3 ? 1 : bond.order + 1; skDraw(); }
      return;
    }
    if (mode === "add" || (!hit && mode === "move")) {
      const [ax, ay] = skFromCanvas(x, y);
      const element = skCurrentElement();
      const atom = { id: "a" + Math.random().toString(16).slice(2, 8), element: element,
                     x: Math.round(ax), y: Math.round(ay),
                     label: SK_LABELS[element] || element };
      state.skeleton.atoms.push(atom);
      state.skSelected = atom;
      skDraw();
      return;
    }
    if (!hit) return;
    if (mode === "delete") {
      state.skeleton.atoms = state.skeleton.atoms.filter((a) => a !== hit);
      state.skeleton.bonds = state.skeleton.bonds.filter((b) => b.a !== hit.id && b.b !== hit.id);
      if (state.skSelected === hit) state.skSelected = null;
      skDraw();
      return;
    }
    if (mode === "bond") {
      if (state.skSelected && state.skSelected !== hit) {
        const exists = state.skeleton.bonds.some((b) =>
          (b.a === state.skSelected.id && b.b === hit.id) ||
          (b.a === hit.id && b.b === state.skSelected.id));
        if (!exists) state.skeleton.bonds.push({ a: state.skSelected.id, b: hit.id, order: 1 });
      }
      state.skSelected = hit;
      skDraw();
      return;
    }
    state.skSelected = hit;
    dragging = hit;
    skDraw();
  }

  canvas.addEventListener("mousedown", (event) => {
    const rect = canvas.getBoundingClientRect();
    // CSS 宽度可能与内部像素尺寸不同 → 按比例换算回逻辑坐标
    const scaleX = canvas.width / rect.width, scaleY = canvas.height / rect.height;
    skPress((event.clientX - rect.left) * scaleX, (event.clientY - rect.top) * scaleY);
  });

  canvas.addEventListener("mousemove", (event) => {
    if (!dragging) return;
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width, scaleY = canvas.height / rect.height;
    const [ax, ay] = skFromCanvas((event.clientX - rect.left) * scaleX,
                                  (event.clientY - rect.top) * scaleY);
    dragging.x = Math.round(ax);
    dragging.y = Math.round(ay);
    if ($("sk-snap") && $("sk-snap").checked) skSnap(dragging);   // 实时吸附预览
    skDraw();
  });

  canvas.addEventListener("mouseup", () => {
    if (!dragging) return;
    if ($("sk-snap") && $("sk-snap").checked) skSnap(dragging);   // 松手时吸附
    dragging = null;
    skDraw();
    skCommit();
  });

  $("sk-reset").addEventListener("click", () => {
    state.scene.molecules[state.scene.active] = skDefaultChain(4);
    skSyncSkeleton();
    state.skSelected = null;
    skDraw();
    renderMolecules();
  });
  $("sk-apply").addEventListener("click", skCommit);
  if ($("sk-element")) {
    $("sk-element").addEventListener("change", (event) => {
      if (skSetElement(event.target.value)) { skDraw(); }
    });
  }
  window.chemfigSkeleton = () => skSceneJson();          // 返回场景（含 molecules）
  window.chemfigAddMolecule = () => { skAddMolecule(); return skSceneJson(); };
  window.chemfigSetElement = (element) => {          // 测试接缝：切当前选中原子的元素
    if ($("sk-element")) $("sk-element").value = element;
    const target = skSetElement(element);
    skDraw();
    return JSON.stringify({ scene: JSON.parse(skSceneJson()), atom: target });
  };
  window.chemfigRemoveMolecule = () => { skRemoveMolecule(); return skSceneJson(); };
  // 测试接缝：按逻辑画布坐标执行一次"按下"（阶段⑤ 的浏览器断言用它，避开 CSS 缩放）
  window.chemfigSkeletonPress = (x, y) => { skPress(x, y); return window.chemfigSkeleton(); };
}

/* ------------------------------------------------------------------ 启动 */

async function boot() {
  const health = await api("/api/health");
  if (health.ok) state.version = health.version;
  $("version").textContent = "chemfigstudio " + (state.version || "");

  const settings = await api("/api/settings");
  if (!settings.ok) { showError("cannot read /api/settings"); return; }
  state.settings = settings.settings;
  state.languages = settings.languages || [];
  state.language = settings.settings.language || "auto";

  await switchLanguage(state.language, false);

  const select = $("kind");
  for (const fig of state.figures) {
    select.appendChild(el("option", { value: fig.kind, text: fig.title + "（" + fig.kind + "）" }));
  }
  state.current = state.figures[0];
  select.addEventListener("change", () => {
    state.current = state.figures.find((f) => f.kind === select.value) || state.figures[0];
    renderFields();
  });
  $("render").addEventListener("click", doRender);
  $("reveal").addEventListener("click", doReveal);
  $("open-settings").addEventListener("click", openSettings);
  $("close-settings").addEventListener("click", () => { $("settings-panel").hidden = true; });
  $("save-settings").addEventListener("click", saveSettings);
  $("language").addEventListener("change", (e) => switchLanguage(e.target.value, true));
  $("open-export").addEventListener("click", openExport);
  $("open-batch").addEventListener("click", openBatch);
  $("open-skeleton").addEventListener("click", openSkeleton);
  $("open-viewer3d").addEventListener("click", openViewer3d);
  $("open-compare").addEventListener("click", openCompare);
  $("sk-close").addEventListener("click", () => { $("sk-panel").hidden = true; });
  installSkeletonEditor();
  $("batch-close").addEventListener("click", () => { $("batch-panel").hidden = true; });
  $("batch-parse").addEventListener("click", () => batchRequest({ dryRun: true }));
  $("batch-run").addEventListener("click", async () => {
    const first = await batchRequest({});
    if (first && first.needs_confirmation) await batchRequest({ overwrite: true });
  });
  $("batch-file").addEventListener("change", async (event) => {
    const file = event.target.files && event.target.files[0];
    if (file) { $("batch-csv").value = await file.text(); await batchRequest({ dryRun: true }); }
  });
  $("copy-image").addEventListener("click", async () => {
    const result = await chemfigCopyImage();
    showWarnings([result.ok ? t("gui.copy") : String(result.reason)]);
  });
  $("tab-new").addEventListener("click", () => sessionAction({ action: "newTab" }, true));
  $("tab-close").addEventListener("click", () => {
    const active = (state.session || {}).activeId;
    if (active) sessionAction({ action: "closeTab", tabId: active }, true);
  });
  $("undo").addEventListener("click", () => sessionAction({ action: "undo" }, true));
  $("redo").addEventListener("click", () => sessionAction({ action: "redo" }, true));
  $("close-export").addEventListener("click", () => { $("export-panel").hidden = true; });
  $("do-export").addEventListener("click", () => doExport(false));
  $("confirm-export").addEventListener("click", () => doExport(true));

  $("outdir-hint").textContent = t("gui.outdir", { path: state.settings._resolved.outputDir });
  renderFields();
  const session = await api("/api/session");
  if (session.ok) {
    state.session = session.session;
    state.document = session.document;
    state.formTabId = session.activeId || (session.session || {}).activeId || null;
  }
  renderSession();
  await restoreLatestDraft();                    // 验收 3
}

boot();
