/* Visual self-check driven by the Playwright already present in this
 * environment (system Chromium is used; nothing is installed).
 *
 *   node scripts/webapp_screenshots.mjs --port 8765 --token <token> --out <dir>
 *
 * It writes screenshots for the three main pages at 1440x900 and 390x844 in
 * both colour schemes, and reports the horizontal-overflow probe for every
 * supported width from 360px to 1920px.  Exit code is non-zero on overflow.
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
const TOKEN = args.token || "verify-token-2f9a";
const OUT = args.out || "/home/meteor/webapp-shots";
const CHROME = args.chrome || "/snap/bin/chromium";
const BASE = `http://127.0.0.1:${PORT}`;
const WIDTHS = [360, 390, 768, 900, 1024, 1099, 1280, 1440, 1920];

const cards = JSON.parse(await readFile(new URL(`file://${OUT}/cards.json`), "utf8").catch(() => "null"))
  || null;

await mkdir(OUT, { recursive: true });

const browser = await chromium.launch({ executablePath: CHROME, args: ["--no-sandbox", "--disable-gpu"] });
const failures = [];

async function openPage(scheme, viewport) {
  const context = await browser.newContext({
    viewport,
    colorScheme: scheme,
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(`pageerror ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") problems.push(`console ${message.text()}`);
  });
  return { context, page, problems };
}

async function goto(page, hash) {
  await page.goto(`${BASE}/?token=${TOKEN}#${hash}`, { waitUntil: "load" });
  await page.waitForSelector("#main h1, #main .error", { timeout: 20000 });
  await page.waitForTimeout(400);
}

const cardId = cards && cards.card_id;
const pages = [
  { name: "overview", hash: "/overview" },
  { name: "company", hash: "/company/AAPL" },
  { name: "card", hash: `/cards/${cardId}` },
  { name: "cards", hash: "/cards" },
  { name: "jobs", hash: "/jobs" },
];

for (const scheme of ["light", "dark"]) {
  for (const target of pages) {
    if (target.name === "card" && !cardId) continue;
    for (const [label, viewport] of [["1440", { width: 1440, height: 900 }],
      ["390", { width: 390, height: 844 }]]) {
      const { context, page, problems } = await openPage(scheme, viewport);
      await goto(page, target.hash);
      const overflow = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
        level: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      }));
      const error = await page.locator("#main .error").count();
      const name = `${target.name}-${label}-${scheme}`;
      await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
      const bad = overflow.level > 0 || error > 0 || problems.length > 0;
      if (bad) failures.push({ name, overflow, error, problems });
      console.log(`${bad ? "FAIL" : "OK  "} ${name}.png  页面横向溢出 ${overflow.level}px`
        + `  错误框 ${error}  控制台问题 ${problems.length}`);
      await context.close();
    }
  }
}

for (const width of WIDTHS) {
  const { context, page, problems } = await openPage("light", { width, height: 900 });
  for (const target of pages) {
    if (target.name === "card" && !cardId) continue;
    await goto(page, target.hash);
    const overflow = await page.evaluate(() =>
      document.documentElement.scrollWidth - document.documentElement.clientWidth);
    // The wide coverage table must scroll inside its own container instead.
    const inner = await page.evaluate(() => {
      const box = document.querySelector(".table-scroll");
      return box ? { scrollable: box.scrollWidth > box.clientWidth, clips: box.clientWidth } : null;
    });
    if (overflow > 0) failures.push({ width, page: target.name, overflow, problems });
    console.log(`${overflow > 0 ? "FAIL" : "OK  "} ${String(width).padStart(4)}px `
      + `${target.name.padEnd(9)} 整页横向溢出 ${overflow}px  宽表容器自滚 ${inner ? inner.scrollable : "无"}`);
  }
  await context.close();
}

await browser.close();
if (failures.length) {
  console.log("\n失败项：");
  console.log(JSON.stringify(failures, null, 2));
  process.exit(1);
}
console.log(`\n全部截图写入 ${OUT}；360-1920px 无整页横向溢出。`);
