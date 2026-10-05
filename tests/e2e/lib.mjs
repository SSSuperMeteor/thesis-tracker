/* Shared helpers for the browser scenarios.
 *
 * A scenario is driven by pytest (tests/e2e_support.py): pytest starts the real
 * server with a scripted stand-in for the model, passes BASE / TOKEN through the
 * environment, runs the scenario, and asserts on the single JSON line it prints.
 * The scenarios never talk to a real model and never install anything: they use
 * the Playwright and Chromium already present on the machine.
 */
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE);

export const BASE = process.env.BASE;
export const TOKEN = process.env.TOKEN;

export async function launch() {
  return chromium.launch({
    executablePath: process.env.CHROMIUM_PATH,
    args: ["--no-sandbox", "--disable-gpu"],
  });
}

/** A page that remembers every console error, page error and failed request. */
export async function openPage(browser, { width = 1440, height = 900, scheme = "light",
  scale = 1 } = {}) {
  const context = await browser.newContext({
    viewport: { width, height }, colorScheme: scheme, deviceScaleFactor: scale,
  });
  const page = await context.newPage();
  page.problems = [];
  page.posts = [];
  page.on("pageerror", (error) => page.problems.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") page.problems.push(`console: ${message.text()}`);
  });
  page.on("requestfailed", (request) => page.problems.push(
    `requestfailed: ${request.url().replace(TOKEN, "***")}`));
  page.on("response", async (response) => {
    const request = response.request();
    if (request.method() === "POST") {
      page.posts.push({ path: new URL(response.url()).pathname, status: response.status() });
    }
    if (response.status() >= 400) {
      page.problems.push(`http ${response.status()}: ${new URL(response.url()).pathname}`);
    }
  });
  return { context, page };
}

export async function go(page, hash, { ready = "#main h1, #main .error" } = {}) {
  await page.goto(`${BASE}/?token=${TOKEN}#${hash}`, { waitUntil: "load" });
  await page.waitForSelector(ready, { timeout: 20000 });
  await page.waitForTimeout(250);
}

export async function api(path, body) {
  const response = await fetch(`${BASE}${path}${path.includes("?") ? "&" : "?"}token=${TOKEN}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", Host: new URL(BASE).host },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { status: response.status, body: await response.json() };
}

export class Report {
  constructor() { this.checks = {}; this.notes = []; }
  check(name, value) { this.checks[name] = value; }
  note(text) { this.notes.push(text); }
  finish(pages = []) {
    const problems = pages.flatMap((page) => page.problems || []);
    console.log("E2E_RESULT " + JSON.stringify({
      checks: this.checks, notes: this.notes, problems }));
  }
}

/** True for a page whose document scrolls sideways (a layout bug). */
export async function pageOverflow(page) {
  return page.evaluate(() => document.documentElement.scrollWidth
    - document.documentElement.clientWidth);
}
