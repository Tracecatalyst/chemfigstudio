/* 多宽度截图 + 布局断言（三栏骨架的验收工具）。
 * 用法：make ui-shots（会自己起服务、截图、跑断言）
 */
"use strict";
const URL = "http://127.0.0.1:8765/";
const WIDTHS = [1440, 1280, 1024, 768, 380];

const task = await taskSpace("chemfigstudio ui shots");
const page = task.page("p1");
const results = [];

async function shot(width, dark) {
  await page.cdp("Emulation.setDeviceMetricsOverride",
                 { width, height: 900, deviceScaleFactor: 1, mobile: false });
  await page.cdp("Emulation.setEmulatedMedia", { features: [
    { name: "prefers-color-scheme", value: dark ? "dark" : "light" }] }).catch(() => {});
  await page.goto(URL, { timeout: 45000, waitUntil: "domcontentloaded" });
  await page.waitForSelector("#fields .row", { timeout: 20000 });
  // 只留一个标签：截图要反映"干净启动"，而不是测试留下的标签堆
  await page.evaluate(`(async () => {
      for (let guard = 0; guard < 12; guard++) {
        const tabs = document.querySelectorAll("#tabs .tab");
        if (tabs.length <= 1) break;
        const close = document.querySelector("#tab-close");
        if (!close) break;
        close.click();
        await new Promise((r) => setTimeout(r, 250));
      }
  })()`);
  await page.waitForTimeout(600);
  const info = await page.evaluate(`(() => {
      const box = (sel) => { const el = document.querySelector(sel);
        return el ? el.getBoundingClientRect() : null; };
      const rect = (sel) => { const r = box(sel);
        return r ? { w: Math.round(r.width), h: Math.round(r.height) } : null; };
      return {
        scrollW: document.documentElement.scrollWidth,
        innerW: innerWidth,
        mainCols: getComputedStyle(document.querySelector("main")).gridTemplateColumns,
        generate: rect("#render"),
        finder: rect("#reveal"),
        preview: rect("#preview-wrap"),
        sheetDisplay: getComputedStyle(document.querySelector("#export-panel")).display,
      };
  })()`);
  // 截图：0.6.0.12 起可用。路径**一定**回传（失败时文件不存在，由外部脚本跳过）。
  const name = "/tmp/chemfig-ui-" + width + (dark ? "-dark" : "") + ".png";
  let shotError = null;
  try { await page.screenshot({ path: name }); } catch (err) { shotError = String(err).slice(0, 80); }
  results.push(Object.assign({ width, dark: !!dark, path: name, shotError }, info));
}

for (const width of WIDTHS) { await shot(width, false); }
await shot(1440, true);                       // 深色模式样张
console.log(JSON.stringify({ results }, null, 1));
await task.finish({ keep: [] });
