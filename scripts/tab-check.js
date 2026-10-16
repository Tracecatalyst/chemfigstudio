/* 标签（tab）功能专用检查 —— 独立于主闸门，便于单独迭代。
 *
 * 用法：make tab-check   （或手动起服务后 ego-browser nodejs < scripts/tab-check.js）
 *
 * 按 ego-browser Skill 的指引：**等待用 waitForFunction / waitForSelector**，
 * 不要在一个 page.evaluate 里堆多处 setTimeout —— evaluate 没有 timeout 选项，
 * 长任务会撞上运行时安全上限（15s），整轮失败。这里每段都短、等待交给 waitForFunction。
 */
"use strict";
const URL = "http://127.0.0.1:8765/";
const checks = [];
const record = (name, ok, detail) => checks.push({ name, ok: !!ok, detail: String(detail || "") });

const task = await taskSpace("chemfigstudio tab check");
const page = task.page("p1");
await page.cdp("Emulation.setDeviceMetricsOverride",
               { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false }).catch(() => {});
await page.goto(URL, { timeout: 45000, waitUntil: "domcontentloaded" });
await page.waitForSelector("#tabs .tab", { timeout: 20000 });

const tabCount = () => page.evaluate("document.querySelectorAll('#tabs .tab').length");
const activeDomId = () => page.evaluate(
  "(() => { const el = document.querySelector('#tabs .tab.active'); return el ? el.dataset.tabId : null; })()");
const sessionActive = async () => (await page.evaluate(
  "fetch('/api/session').then(r => r.json()).then(d => d.session ? d.session.activeId : null)"));

// 1) 新建标签：数量 +1，名字互不相同
const before = await tabCount();
await page.click("#tab-new");
await page.waitForFunction((n) => document.querySelectorAll("#tabs .tab").length === n + 1,
                           before, { timeout: 6000 });
const labels = await page.evaluate(
  "[...document.querySelectorAll('#tabs .tab')].map(t => t.textContent.replace(/[•✎\\s]+$/,'').trim())");
record("新建标签且名字可区分",
       labels.length === before + 1 && new Set(labels).size === labels.length,
       JSON.stringify({ before, labels }));

// 2) 点一个"非当前"标签 → DOM 高亮移动，且会话同步（等待交给 waitForFunction）
const targetId = await page.evaluate(`(() => {
    const el = [...document.querySelectorAll("#tabs .tab")].find(t => !t.classList.contains("active"));
    if (!el) return null;
    el.click();
    return el.dataset.tabId;
})()`);
if (targetId) {
  await page.waitForFunction((id) => {
    const el = document.querySelector("#tabs .tab.active");
    return !!el && el.dataset.tabId === id;
  }, targetId, { timeout: 6000 }).catch(() => {});
}
const domId = await activeDomId();
const sId = await sessionActive();
record("点非当前标签能切换（DOM 高亮 + 会话同步）",
       !!targetId && domId === targetId && sId === targetId,
       JSON.stringify({ targetId, domId, sessionId: sId }));

// 3) 切换后**表单内容**跟着变（用户真正感知的"切换成功"）
await page.selectOption("#kind", { value: "series" });
await page.click("#render");
await page.evaluate("new Promise(r => setTimeout(r, 500))");
const kindA = await page.evaluate("document.querySelector('#kind').value");
await page.click("#tab-new");
await page.waitForFunction(() => document.querySelector("#kind").value === "alcohol",
                           undefined, { timeout: 6000 }).catch(() => {});
const kindB = await page.evaluate("document.querySelector('#kind').value");
await page.evaluate("[...document.querySelectorAll('#tabs .tab')][0].click()");
await page.waitForFunction(() => document.querySelector("#kind").value === "series",
                           undefined, { timeout: 6000 }).catch(() => {});
const kindBack = await page.evaluate("document.querySelector('#kind').value");
record("切换标签后表单内容跟着变（不是假死）",
       kindA === "series" && kindB === "alcohol" && kindBack === "series",
       JSON.stringify({ kindA, kindB, kindBack }));

// 4) 跨标签不污染：改 B 不能动 A
await page.evaluate("[...document.querySelectorAll('#tabs .tab')][1].click()");
await page.waitForFunction(() => document.querySelector("#kind").value === "alcohol",
                           undefined, { timeout: 6000 }).catch(() => {});
await page.selectOption("#kind", { value: "chcount" });
await page.click("#render");
await page.evaluate("new Promise(r => setTimeout(r, 500))");
const kindB2 = await page.evaluate(
  "fetch('/api/session').then(r => r.json()).then(d => d.document ? d.document.figure.kind : null)");
await page.evaluate("[...document.querySelectorAll('#tabs .tab')][0].click()");
await page.waitForFunction(() => document.querySelector("#kind").value === "series",
                           undefined, { timeout: 6000 }).catch(() => {});
const kindA2 = await page.evaluate(
  "fetch('/api/session').then(r => r.json()).then(d => d.document ? d.document.figure.kind : null)");
record("跨标签不污染（改 B 不动 A）",
       kindB2 === "chcount" && kindA2 === "series",
       JSON.stringify({ kindB2, kindA2 }));

console.log(JSON.stringify({ ok: checks.every((c) => c.ok), checks }, null, 1));
await task.finish({ keep: [] });
