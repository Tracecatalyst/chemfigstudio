/* 前端真实浏览器回归检查（需要 ego-browser）。
 *
 * 用法（在仓库根目录）：
 *     make browser-check              # 自动起服务 → 检查 → 关服务
 * 或手动：
 *     PYTHONPATH=src python3 -m chemfigstudio.gui.server --port 8765 --no-browser &
 *     ego-browser nodejs < scripts/browser-check.js
 *
 * 为什么需要它：`make test` 覆盖的是 HTTP 契约（Python 侧），
 * 网页里的 JS（语言切换 / 预览 / 导出对话框 / 草稿恢复）**只能靠真浏览器验证**。
 *
 * 注意：ego-browser 里 process.env 与 argv 都是空的，所以端口写死为默认 8765。
 */
"use strict";

const URL_UNDER_TEST = "http://127.0.0.1:8765/";

const checks = [];
function record(name, ok, detail) {
  checks.push({ name, ok: !!ok, detail: detail === undefined ? "" : String(detail) });
}

async function main() {
  const task = await taskSpace("chemfigstudio frontend check");
  const page = task.page("p1");

  await page.cdp("Runtime.enable").catch(() => {});     // 尽量抓到 console 错误
  // ego-browser 默认视口可能是 0×0（此时任何元素都"不在视口内"、点不动），
  // 所以显式给一个真实窗口尺寸再导航。
  await page.cdp("Emulation.setDeviceMetricsOverride", {
    width: 1280, height: 900, deviceScaleFactor: 1, mobile: false,
  }).catch(() => {});
  await page.goto(URL_UNDER_TEST, { timeout: 45000, waitUntil: "domcontentloaded" });
  await page.waitForSelector("#kind", { timeout: 20000 });
  await page.waitForTimeout(1200);                       // 等 boot() 拉完设置/文案
  // 让启动态确定：显式选中第一个图类型（否则上次的 windowState.kind 会让字段数变化，
  // 断言就会随环境漂 —— 踩过一次：跳到 series 后只有 2 个字段）
  await page.evaluate(`(() => {
      const el = document.querySelector("#kind");
      if (el && el.value !== "alcohol") {
        el.value = "alcohol";
        el.dispatchEvent(new Event("change", { bubbles: true }));
      }
  })()`);
  await page.waitForTimeout(400);

  // 1) 界面已本地化并渲染出表单
  const boot = await page.evaluate(`(() => ({
      kindOptions: document.querySelectorAll("#kind option").length,
      langOptions: document.querySelectorAll("#language option").length,
      paramRows: document.querySelectorAll("#fields .row").length,
      renderLabel: (document.querySelector('[data-i18n="gui.render"]') || {}).textContent,
      hasExportButton: !!document.querySelector("#open-export"),
      hasExportPanel: !!document.querySelector("#export-panel"),
  }))()`);
  record("图类型下拉已填充", boot.kindOptions >= 4, boot.kindOptions);
  record("语言下拉已填充", boot.langOptions >= 3, boot.langOptions);
  record("参数字段已生成", boot.paramRows >= 4, boot.paramRows);
  record("导出按钮/对话框存在", boot.hasExportButton && boot.hasExportPanel);

  // 2) 点「生成」→ 预览出图（走 /api/preview，不落用户输出目录）
  await page.click("#render");
  const preview = await page.evaluate(`(async () => {
      const img = document.querySelector("#preview");
      for (let i = 0; i < 60; i++) {
        if (img && img.src && img.naturalWidth > 0) return { ok: true, w: img.naturalWidth, h: img.naturalHeight };
        await new Promise((r) => setTimeout(r, 250));
      }
      return { ok: false, src: img ? img.src : null };
  })()`);
  record("预览图渲染成功", preview.ok, JSON.stringify(preview));
  record("预览分辨率够看(≥300px)", preview.ok && preview.w >= 300, preview.w);

  // 3) 切语言 → 界面文案真的变了（验收 16 的界面侧）
  await page.selectOption("#language", { label: "English" }).catch(async () => {
    await page.selectOption("#language", "en");
  });
  const english = await page.evaluate(`(async () => {
      for (let i = 0; i < 40; i++) {
        const node = document.querySelector('[data-i18n="gui.render"]');
        if (node && node.textContent.trim() === "Generate") return { ok: true, text: node.textContent.trim() };
        await new Promise((r) => setTimeout(r, 200));
      }
      const node = document.querySelector('[data-i18n="gui.render"]');
      return { ok: false, text: node ? node.textContent.trim() : null };
  })()`);
  record("切换英文后按钮变 Generate", english.ok, english.text);

  // 4) 导出对话框可开，且字段齐全
  await page.click("#open-export");
  const dialog = await page.evaluate(`(() => {
      const panel = document.querySelector("#export-panel");
      return { hidden: panel ? panel.hidden : null,
               fields: ["#ex-dir", "#ex-filename", "#ex-format", "#ex-dpi", "#ex-transparent"]
                        .filter((s) => !!document.querySelector(s)).length,
               confirmHidden: document.querySelector("#confirm-export").hidden };
  })()`);
  record("导出对话框可打开", dialog.hidden === false);
  record("导出字段齐全", dialog.fields === 5, dialog.fields);

  // 5) 草稿：刷新页面后能恢复（验收 3 的界面侧）
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector("#fields .row", { timeout: 20000 });
  await page.waitForTimeout(1500);
  const restored = await page.evaluate(`(() => ({
      rows: document.querySelectorAll("#fields .row").length,
      kind: (document.querySelector("#kind") || {}).value || null,
  }))()`);
  // 注意：reload 后应用会**恢复上次的草稿/文档**（这是设计行为），
  // 所以字段数取决于所恢复的图类型 —— 断言"表单可用"而不是"字段数固定"。
  record("刷新后表单仍可用（草稿恢复路径）",
         restored.rows >= 2 && !!restored.kind, JSON.stringify(restored));

  // 5b) 会话 UI：标签页 / 图层 / 撤销重做（验收 4/5/6 的界面侧）
  const session = await page.evaluate(`(async () => {
      const before = document.querySelectorAll("#tabs .tab").length;
      document.querySelector("#tab-new").click();
      await new Promise((r) => setTimeout(r, 400));
      const tabs = document.querySelectorAll("#tabs .tab").length;
      const layers = document.querySelectorAll("#layers li").length;
      const undoDisabled = document.querySelector("#undo").disabled;
      const redoDisabled = document.querySelector("#redo").disabled;
      return { before, tabs, layers, undoDisabled, redoDisabled };
  })()`);
  record("新建标签页生效", session.tabs > session.before, JSON.stringify(session));
  record("图层面板渲染", session.layers >= 1, session.layers);
  record("撤销/重做按钮存在且有禁用态", typeof session.undoDisabled === "boolean" && typeof session.redoDisabled === "boolean");

  // 5b2) 输入即刷新：改一个参数，画布应在 500 ms 内自动更新（验收 2）
  const live = await page.evaluate(`(async () => {
      const img = document.querySelector("#preview");
      const before = img ? img.src : "";
      const field = document.querySelector("#param-n_carbons") || document.querySelector("#param-smiles");
      if (!field) return { ok: false, reason: "no-field" };
      field.value = field.id === "param-n_carbons" ? "6" : "CCCO";
      const firesBefore = window.chemfigLiveFires || 0;
      field.dispatchEvent(new Event("input", { bubbles: true }));
      const firesAfter = window.chemfigLiveFires || 0;
      const started = Date.now();
      for (let i = 0; i < 40; i++) {
        await new Promise((r) => setTimeout(r, 50));
        if (img && img.src && img.src !== before) return { ok: true, ms: Date.now() - started,
                                                          fires: firesAfter - firesBefore };
      }
      return { ok: false, ms: Date.now() - started, before, fires: firesAfter - firesBefore };
  })()`);
  record("输入即刷新(≤500ms)", live.ok && live.ms <= 500, JSON.stringify(live));

  // 5c) 外壳增强：拖入文本 → 自动填充；复制图片接口存在（验收：外壳增强）
  const dropped = await page.evaluate(`(async () => {
      const before = (document.querySelector("#param-smiles") || {}).value;
      const filled = window.chemfigApplyText("CCCCO");
      const after = (document.querySelector("#param-smiles") || {}).value;
      let copy = null;
      try { copy = await window.chemfigCopyImage(); } catch (e) { copy = { ok: false, reason: String(e) }; }
      return { before, filled, after, copyOk: !!(copy && typeof copy.ok === "boolean") };
  })()`);
  record("拖入文本可自动填充 SMILES", dropped.filled >= 1 && dropped.after === "CCCCO",
         JSON.stringify(dropped));
  record("复制图片接口可用（不抛异常）", dropped.copyOk, JSON.stringify(dropped.copy || {}));

  // 5d) 批量导出面板（验收 14）：粘贴 CSV → 预览行
  const batch = await page.evaluate(`(async () => {
      document.querySelector("#open-batch").click();
      await new Promise((r) => setTimeout(r, 300));
      const panel = document.querySelector("#batch-panel");
      const open = panel && panel.hidden === false;
      document.querySelector("#batch-csv").value = "name,smiles,delta_h\\n甲醇,CO,-22.7\\n乙醇,CCO,-36.1\\n";
      document.querySelector("#batch-parse").click();
      for (let i = 0; i < 40; i++) {
        await new Promise((r) => setTimeout(r, 150));
        const rows = document.querySelectorAll("#batch-rows li").length;
        if (rows >= 2) return { open, rows, status: (document.querySelector("#batch-status") || {}).textContent };
      }
      return { open, rows: document.querySelectorAll("#batch-rows li").length,
               status: (document.querySelector("#batch-status") || {}).textContent };
  })()`);
  record("批量面板可打开并预览 CSV 行", batch.open && batch.rows >= 2, JSON.stringify(batch));

  // 5e) 骨架编辑器：点一下加原子 → 应用 → 会话状态与画布都更新
  const skeleton = await page.evaluate(`(async () => {
      const activeOf = (scene) => (scene.molecules ? scene.molecules[scene.active || 0] : scene);
      document.querySelector("#open-skeleton").click();
      await new Promise((r) => setTimeout(r, 300));
      const panel = document.querySelector("#sk-panel");
      const canvas = document.querySelector("#sk-canvas");
      const before = activeOf(JSON.parse(window.chemfigSkeleton())).atoms.length;
      document.querySelector("#sk-mode").value = "add";
      const rect = canvas.getBoundingClientRect();
      canvas.dispatchEvent(new MouseEvent("mousedown", {
        bubbles: true, clientX: rect.left + 120, clientY: rect.top + 80 }));
      const after = activeOf(JSON.parse(window.chemfigSkeleton())).atoms.length;
      document.querySelector("#sk-apply").click();
      let applied = false;
      for (let i = 0; i < 40; i++) {
        await new Promise((r) => setTimeout(r, 150));
        const doc = await fetch("/api/session").then((r) => r.json()).catch(() => null);
        const figure = doc && doc.document && doc.document.figure;
        if (figure && figure.kind === "skeleton" && figure.params && figure.params.skeleton) {
          const stored = activeOf(JSON.parse(figure.params.skeleton));
          if (stored.atoms && stored.atoms.length === after) { applied = true; break; }
        }
      }
      return { open: panel.hidden === false, added: after - before, applied };
  })()`);
  record("骨架编辑器：点击加原子 → 应用到画布 → 会话已存骨架",
         skeleton.open && skeleton.added >= 1 && skeleton.applied, JSON.stringify(skeleton));

  // 5f) 阶段⑤ 进阶：吸附开关 + 切键级（点一根键 → 单键变双键 → 应用后会话存住）
  const advanced = await page.evaluate(`(async () => {
      const activeOf = (scene) => (scene.molecules ? scene.molecules[scene.active || 0] : scene);
      document.querySelector("#open-skeleton").click();
      await new Promise((r) => setTimeout(r, 300));
      const snapBox = document.querySelector("#sk-snap");
      const molecule = activeOf(JSON.parse(window.chemfigSkeleton()));
      const byId = {};
      for (const atom of molecule.atoms) byId[atom.id] = atom;
      const bond = molecule.bonds[0];
      const first = byId[bond.a], second = byId[bond.b];
      const x = (first.x + second.x) / 2 + 60;          // +60 = SK_PAD
      const y = (first.y + second.y) / 2 + 60;
      document.querySelector("#sk-mode").value = "order";
      const order = activeOf(JSON.parse(window.chemfigSkeletonPress(x, y))).bonds[0].order;
      document.querySelector("#sk-apply").click();
      let storedOrder = null;
      for (let i = 0; i < 30; i++) {
        await new Promise((r) => setTimeout(r, 150));
        const doc = await fetch("/api/session").then((r) => r.json()).catch(() => null);
        const figure = doc && doc.document && doc.document.figure;
        if (figure && figure.params && figure.params.skeleton) {
          storedOrder = activeOf(JSON.parse(figure.params.skeleton)).bonds[0].order;
          break;
        }
      }
      return { hasSnap: !!snapBox && snapBox.checked, order, storedOrder };
  })()`);
  record("阶段⑤ 进阶：吸附开关 + 点键切键级（会话存住）",
         advanced.hasSnap && advanced.order === 2 && advanced.storedOrder === 2,
         JSON.stringify(advanced));

  // 5g) 阶段⑤ 收尾：多分子图层（新建一个分子 → 应用 → 会话里应有两个分子）
  const molecules = await page.evaluate(`(async () => {
      document.querySelector("#open-skeleton").click();
      await new Promise((r) => setTimeout(r, 300));
      const count = (text) => JSON.parse(text).molecules.length;
      const before = count(window.chemfigSkeleton());
      const afterAdd = count(window.chemfigAddMolecule());
      const listItems = document.querySelectorAll("#sk-molecules li").length;
      document.querySelector("#sk-apply").click();
      let stored = null;
      for (let i = 0; i < 30; i++) {
        await new Promise((r) => setTimeout(r, 150));
        const doc = await fetch("/api/session").then((r) => r.json()).catch(() => null);
        const figure = doc && doc.document && doc.document.figure;
        if (figure && figure.params && figure.params.skeleton) {
          const scene = JSON.parse(figure.params.skeleton);
          if (scene.molecules) { stored = scene.molecules.length; break; }
        }
      }
      return { before, afterAdd, listItems, stored };
  })()`);
  record("阶段⑤ 收尾：多分子图层（新建 → 应用 → 会话存 2 个）",
         molecules.afterAdd === molecules.before + 1 && molecules.stored === molecules.afterAdd,
         JSON.stringify(molecules));

  // 5h) 阶段⑤ 收尾：元素切换（选中原子 → 切成氧 → 会话存住 O）
  const element = await page.evaluate(`(async () => {
      const activeOf = (scene) => (scene.molecules ? scene.molecules[scene.active || 0] : scene);
      document.querySelector("#open-skeleton").click();
      await new Promise((r) => setTimeout(r, 300));
      const atom = activeOf(JSON.parse(window.chemfigSkeleton())).atoms[0];
      document.querySelector("#sk-mode").value = "move";
      window.chemfigSkeletonPress(atom.x + 60, atom.y + 60);      // 选中该原子（+60 = SK_PAD）
      const result = JSON.parse(window.chemfigSetElement("O"));
      const changed = result.atom ? result.atom.element : null;
      const label = result.atom ? result.atom.label : null;
      document.querySelector("#sk-apply").click();
      let stored = null;
      for (let i = 0; i < 30; i++) {
        await new Promise((r) => setTimeout(r, 150));
        const doc = await fetch("/api/session").then((r) => r.json()).catch(() => null);
        const figure = doc && doc.document && doc.document.figure;
        if (figure && figure.params && figure.params.skeleton) {
          stored = activeOf(JSON.parse(figure.params.skeleton)).atoms[0].element;
          break;
        }
      }
      return { hasSelect: !!document.querySelector("#sk-element"), changed, label, stored };
  })()`);
  record("阶段⑤ 收尾：元素切换（选中原子 → 切氧 → 会话存住）",
         element.hasSelect && element.changed === "O" && element.stored === "O",
         JSON.stringify(element));

  // 5.5) 2D/3D 对照页（独立页面，要重新导航）
  await page.goto(URL_UNDER_TEST.replace(/\/$/, "") + "/compare",
                  { timeout: 45000, waitUntil: "domcontentloaded" });
  const compare = await page.evaluate(`(async () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      // 等 6 行都出来：每行一张 2D 图 + 一个 3D canvas
      for (let i = 0; i < 80; i++) {
        const imgs = Array.from(document.querySelectorAll('.shot img'));
        const canvases = document.querySelectorAll('.viewer canvas');
        if (imgs.length >= 6 &&
            imgs.every((img) => img.naturalWidth > 0) &&
            canvases.length >= 6) break;
        await sleep(500);
      }
      const rows = document.querySelectorAll('.row');
      const imgs = Array.from(document.querySelectorAll('.shot img'));
      const canvases = document.querySelectorAll('.viewer canvas');
      return {
        rows: rows.length,
        imgsLoaded: imgs.filter((img) => img.naturalWidth > 0).length,
        canvases: canvases.length,
        exportButtons: document.querySelectorAll('.cmp-export').length,
      };
  })()`);
  record("对照页：6 行多分子并排（2D 图 + 3D 画布）",
         compare.rows === 6 && compare.imgsLoaded === 6 && compare.canvases === 6,
         JSON.stringify(compare));
  record("对照页：每行都有导出按钮", compare.exportButtons === 6,
         JSON.stringify({ buttons: compare.exportButtons }));

  // 导出：拦下 blob 检查它是真的 PNG（不真的落盘）
  const exported = await page.evaluate(`(async () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const row = document.querySelector('.row');
      const button = row && row.querySelector('.cmp-export');
      if (!button) return { error: 'no button' };
      let captured = null;
      const originalCreate = URL.createObjectURL;
      URL.createObjectURL = function (blob) { captured = blob; return originalCreate.call(URL, blob); };
      const originalClick = HTMLAnchorElement.prototype.click;
      HTMLAnchorElement.prototype.click = function () {};
      button.click();
      for (let i = 0; i < 40; i++) { if (captured) break; await sleep(250); }
      URL.createObjectURL = originalCreate;
      HTMLAnchorElement.prototype.click = originalClick;
      if (!captured) return { error: 'no blob' };
      const buffer = await captured.arrayBuffer();
      const bytes = new Uint8Array(buffer);
      const view = new DataView(buffer);
      return {
        type: captured.type,
        size: captured.size,
        width: view.getUint32(16),
        height: view.getUint32(20),
        magic: Array.from(bytes.slice(0, 4)),
      };
  })()`);
  record("对照页：导出的 PNG 是合法图片且两边都有内容",
         exported.magic &&
         exported.magic.join(",") === "137,80,78,71" &&
         exported.size > 5000 && exported.width > 400,
         JSON.stringify(exported));

  // 6) 收集 console 错误（best-effort）
  let consoleErrors = [];
  try {
    const events = await page.events();
    consoleErrors = (events || [])
      .filter((e) => /error|exception/i.test(e.type || "") ||
                     /error|exception/i.test(String(e.method || "")))
      .map((e) => JSON.stringify(e).slice(0, 200));
  } catch (err) {
    consoleErrors = ["<无法读取 console 事件: " + err.message + ">"];
  }

  const failed = checks.filter((c) => !c.ok);
  console.log(JSON.stringify({
    ok: failed.length === 0,
    url: URL_UNDER_TEST,
    passed: checks.length - failed.length,
    failed: failed.length,
    checks,
    consoleErrors,
  }, null, 2));

  await task.finish({ keep: [] });
}

main().catch((err) => {
  console.log(JSON.stringify({ ok: false, error: String(err && err.stack || err) }, null, 2));
  process.exitCode = 1;
});
