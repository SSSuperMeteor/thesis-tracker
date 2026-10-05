/* Layout assertions driven by the Playwright already present in this
 * environment (the system Chromium is used; nothing is installed).
 *
 *   node scripts/webapp_layout_check.mjs --port 8765 --token <token> --out <dir>
 *
 * Checks, on the pages that have them:
 *   - the card detail's two columns are at most 64px apart at 1440px;
 *   - no page scrolls horizontally at any supported width;
 *   - nothing in the evidence panel wraps a date onto two lines;
 *   - wide tables scroll in their own container instead of the page.
 * It also writes screenshots for the visual pass.  Exit code is non-zero when
 * an assertion fails.
 */
import { chromium } from "/home/meteor/.hermes/node/lib/node_modules/playwright/index.mjs";
import { mkdir, readFile } from "node:fs/promises";

const args = Object.fromEntries(
  process.argv.slice(2).reduce((pairs, item, index, all) => {
    if (item.startsWith("--")) pairs.push([item.slice(2), all[index + 1]]);
    return pairs;
  }, []),
);
const PORT = args.port || "8765";
const TOKEN = args.token || "";
const OUT = args.out || "/home/meteor/webapp-shots";
const CHROME = args.chrome || "/snap/bin/chromium";
const BASE = `http://127.0.0.1:${PORT}`;
const WIDTHS = [360, 390, 768, 900, 1024, 1099, 1280, 1440, 1920];
const WIDES_FOR_BAND = [360, 390, 500, 640, 768, 900, 1024, 1099, 1280, 1440, 1920];
const MAX_GAP_PX = 64;

await mkdir(OUT, { recursive: true });
let cardId = args.card || null;
if (!cardId) {
  const response = await fetch(`${BASE}/api/cards?token=${TOKEN}`, {
    headers: { Host: `127.0.0.1:${PORT}` },
  });
  const payload = await response.json();
  cardId = payload.cards[0].card_id;
}
const pages = [
  { name: "overview", hash: "/overview" },
  { name: "cards", hash: "/cards" },
  { name: "card", hash: `/cards/${cardId}` },
  { name: "jobs", hash: "/jobs" },
];
console.log(`port=${PORT} card=${cardId}`);

const browser = await chromium.launch({ executablePath: CHROME, args: ["--no-sandbox", "--disable-gpu"] });
const failures = [];
const problems = [];

async function open(scheme, viewport) {
  const context = await browser.newContext({ viewport, colorScheme: scheme, deviceScaleFactor: 1 });
  const page = await context.newPage();
  const console_errors = [];
  page.on("pageerror", (error) => console_errors.push(`pageerror ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") console_errors.push(`console ${message.text()}`);
  });
  return { context, page, console_errors };
}

async function goto(page, hash) {
  await page.goto(`${BASE}/?token=${TOKEN}#${hash}`, { waitUntil: "load" });
  await page.waitForSelector("#main h1, #main .error", { timeout: 20000 });
  await page.waitForTimeout(350);
}

// ---- 1. the two columns are one group, tightly spaced ----------------------
for (const scheme of ["light", "dark"]) {
  const { context, page, console_errors } = await open(scheme, { width: 1440, height: 900 });
  await goto(page, `/cards/${cardId}`);
  const gap = await page.evaluate(() => {
    const reading = document.querySelector(".reading");
    const evidence = document.querySelector(".evidence");
    if (!reading || !evidence) return null;
    const a = reading.getBoundingClientRect();
    const b = evidence.getBoundingClientRect();
    const group = document.querySelector(".card-layout").getBoundingClientRect();
    return { gap: Math.round((b.left - a.right) * 100) / 100,
      left: Math.round(a.left), right: Math.round(b.right),
      width: Math.round(group.width) };
  });
  if (gap === null) failures.push({ check: "two-column gap", scheme, reason: "no columns" });
  else {
    if (gap.gap > MAX_GAP_PX) failures.push({ check: "two-column gap", scheme, gap });
    console.log(`${gap.gap <= MAX_GAP_PX ? "OK  " : "FAIL"} ${scheme} 两栏空隙 ${gap.gap}px`
      + `（左 ${gap.left}，右 ${gap.right}，整组 ${gap.width}px）`);
  }
  // no date may wrap inside the panel
  const wrapped = await page.evaluate(() => Array.from(document.querySelectorAll(".fact-date"))
    .filter((node) => node.getClientRects().length > 1 ||
      node.scrollWidth > node.clientWidth + 1)
    .map((node) => node.textContent));
  if (wrapped.length) failures.push({ check: "date wrap", scheme, wrapped });
  console.log(`${wrapped.length ? "FAIL" : "OK  "} ${scheme} 证据面板日期换行数 ${wrapped.length}`);
  const hintCount = await page.locator(".hints p").count();
  const groups = await page.locator(".fact-group").count();
  const rows = await page.locator(".fact-row").count();
  console.log(`     提示 ${hintCount} 条｜来源分组 ${groups} 组｜事实行 ${rows} 行`);
  if (console_errors.length) problems.push({ scheme, console_errors });
  await context.close();
}

// ---- 1b. band labels never overlap at any width ----------------------------
for (const width of WIDES_FOR_BAND) {
  const { context, page } = await open("light", { width, height: 900 });
  await goto(page, `/cards/${cardId}`);
  const result = await page.evaluate(() => {
    const boxes = Array.from(document.querySelectorAll(".band-mark .stack")).map((node) => {
      const box = node.getBoundingClientRect();
      return { text: node.textContent.slice(0, 8), left: box.left, right: box.right,
        top: box.top, bottom: box.bottom };
    });
    const overlaps = [];
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1) {
        const a = boxes[i];
        const b = boxes[j];
        if (a.left < b.right - 1 && b.left < a.right - 1 &&
            a.top < b.bottom - 1 && b.top < a.bottom - 1) {
          overlaps.push([a.text, b.text]);
        }
      }
    }
    const scroller = document.querySelector(".band-scroll");
    return { count: boxes.length, overlaps,
      scrolls: scroller ? scroller.scrollWidth > scroller.clientWidth : false };
  });
  if (result.overlaps.length) {
    failures.push({ check: "band label overlap", width, overlaps: result.overlaps });
  }
  console.log(`${result.overlaps.length ? "FAIL" : "OK  "} ${String(width).padStart(4)}px `
    + `价位带标签 ${result.count} 个，重叠 ${result.overlaps.length}`
    + `${result.scrolls ? "｜轴容器内滚动" : ""}`);
  await context.close();
}

// ---- 2. no page scrolls sideways; wide tables scroll themselves ------------
for (const width of WIDTHS) {
  const { context, page } = await open("light", { width, height: 900 });
  for (const target of pages) {
    await goto(page, target.hash);
    const result = await page.evaluate(() => {
      const overflow = document.documentElement.scrollWidth
        - document.documentElement.clientWidth;
      const scrollers = Array.from(document.querySelectorAll(".table-scroll"))
        .filter((node) => node.scrollWidth > node.clientWidth).length;
      const columns = document.querySelector(".card-layout");
      const stacked = columns ? getComputedStyle(columns).gridTemplateColumns.split(" ").length : 0;
      return { overflow, scrollers, stacked };
    });
    if (result.overflow > 0) {
      failures.push({ check: "horizontal overflow", width, page: target.name, ...result });
    }
    console.log(`${result.overflow > 0 ? "FAIL" : "OK  "} ${String(width).padStart(4)}px `
      + `${target.name.padEnd(9)} 溢出 ${result.overflow}px`
      + `${result.scrollers ? `｜容器内滚动表 ${result.scrollers}` : ""}`
      + `${target.name === "card" ? `｜栏数 ${result.stacked}` : ""}`);
  }
  await context.close();
}

// ---- 3. screenshots for the visual pass -----------------------------------
for (const scheme of ["light", "dark"]) {
  for (const target of pages) {
    for (const [label, viewport] of [["1440", { width: 1440, height: 900 }],
      ["390", { width: 390, height: 844 }]]) {
      const { context, page } = await open(scheme, viewport);
      await goto(page, target.hash);
      await page.screenshot({ path: `${OUT}/${target.name}-${label}-${scheme}.png`, fullPage: true });
      await context.close();
    }
  }
}

await browser.close();
if (problems.length) console.log("\n控制台问题：", JSON.stringify(problems, null, 1));
if (failures.length) {
  console.log("\n失败项：");
  console.log(JSON.stringify(failures, null, 1));
  process.exit(1);
}
console.log(`\n通过：两栏空隙 ≤ ${MAX_GAP_PX}px，日期无换行，360-1920px 无整页横向溢出。`);
console.log(`截图写入 ${OUT}`);
