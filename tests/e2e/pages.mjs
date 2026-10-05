/* Overview, company, card detail, jobs and edge routes, in a real browser.
 *
 * SCENARIO = {"card_id": "...", "conversation_id": "...", "job_id": "..."}
 */
import { Report, api, go, launch, openPage, pageOverflow } from "./lib.mjs";

const scenario = JSON.parse(process.env.SCENARIO || "{}");
const report = new Report();
const browser = await launch();

// Text that must never reach a reader: unrendered values, raw timestamps,
// internal identifiers and English codes.
const FORBIDDEN = [/undefined/, /\bNaN\b/, /\[object/, /\bnull\b/,
  /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/, /\+00:00/, /conversation_id/, /message_id/,
  /correction_limit/, /round_limit/, /sec_metr\b/, /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}/];

async function leaks(page, scope = "#main") {
  const text = await page.locator(scope).innerText();
  return FORBIDDEN.filter((pattern) => pattern.test(text)).map(String);
}

// ---- overview, wide ---------------------------------------------------------
{
  const { context, page } = await openPage(browser, { width: 1440, height: 900 });
  await context.grantPermissions(["clipboard-read", "clipboard-write"],
    { origin: process.env.BASE });
  await go(page, "/overview");
  report.check("overview_title", await page.locator("#main h1").innerText());
  report.check("overview_rows", await page.locator("table.coverage tbody tr").count());
  report.check("overview_leaks", await leaks(page));
  report.check("overview_overflow_1440", await pageOverflow(page));
  report.check("price_command_visible", (await page.locator("#main").innerText())
    .includes("uv run prices-ingest"));
  const copy = page.getByRole("button", { name: "复制命令" }).first();
  if (await copy.count()) {
    await copy.click();
    await page.waitForTimeout(300);
    report.check("copy_command_feedback", (await page.locator("[aria-live]").allInnerTexts())
      .join(" ").includes("已复制"));
  }
  report.check("overview_problems_wide", page.problems.length);
  await context.close();
}

// ---- overview, narrow: the table must show filings, not a blank pane --------
{
  const { context, page } = await openPage(browser, { width: 390, height: 844 });
  await go(page, "/overview");
  report.check("overview_overflow_390", await pageOverflow(page));
  report.check("narrow_visible_filing_cells", await page.evaluate(() => {
    const scroller = document.querySelector(".table-scroll");
    const box = scroller.getBoundingClientRect();
    let visible = 0;
    for (const cell of document.querySelectorAll("td.has-filing")) {
      const rect = cell.getBoundingClientRect();
      const x = rect.left + rect.width / 2;
      const y = rect.top + rect.height / 2;
      if (x < box.left || x > box.right || y < box.top || y > box.bottom) continue;
      const top = document.elementFromPoint(x, y);
      if (top && cell.contains(top)) visible += 1;
    }
    return visible;
  }));
  await context.close();
}

// ---- company pages: fresh, stale and no-price ---------------------------------
{
  const { page, context } = await openPage(browser);
  for (const [ticker, tag] of [["AAPL", "fresh"], ["NVDA", "stale"], ["MSFT", "noprice"]]) {
    await go(page, `/company/${ticker}`);
    report.check(`company_${tag}_title`, await page.locator("#main h1").innerText());
    report.check(`company_${tag}_leaks`, await leaks(page));
    report.check(`company_${tag}_text`, (await page.locator("#main").innerText()).slice(0, 4000));
  }
  report.check("company_problems", page.problems.length);
  await context.close();
}

// ---- card detail: a fact marker leads to its evidence row ---------------------
{
  const { page, context } = await openPage(browser);
  await go(page, `/cards/${scenario.card_id}`);
  report.check("card_leaks", await leaks(page));
  report.check("card_marker_count", await page.locator(".reading .fact-ref").count());
  report.check("card_evidence_rows", await page.locator(".evidence .fact-row").count());
  await page.locator(".reading .fact-ref").first().click();
  await page.waitForTimeout(400);
  report.check("card_marker_highlights_row", await page.evaluate(() => Boolean(
    document.querySelector(".fact-row.flash")
    || document.activeElement.classList.contains("fact-row"))));
  report.check("card_has_disclaimer", await page.locator(".disclaimer").count() > 0);
  report.check("card_problems", page.problems.length);
  await context.close();
}

// ---- jobs: readable parameters, and one refresh chain per page ---------------
{
  const { page, context } = await openPage(browser);
  await go(page, "/jobs");
  report.check("jobs_leaks", await leaks(page));
  report.check("jobs_text", await page.locator("#main").innerText());
  page.fetches = 0;
  page.on("request", (request) => {
    if (request.url().includes(`/api/jobs/${scenario.job_id}`)) page.fetches += 1;
  });
  await go(page, `/jobs/${scenario.job_id}`);
  for (let visit = 0; visit < 3; visit += 1) {
    await go(page, "/overview");
    await go(page, `/jobs/${scenario.job_id}`);
  }
  page.fetches = 0;
  await page.waitForTimeout(6200);
  // One chain at 1.5s is four fetches in six seconds; stacked chains would be far more.
  report.check("job_fetches_in_6s_after_revisits", page.fetches);
  report.check("job_leaks", await leaks(page));
  await context.close();
}

// ---- the analysis dialog: keyboard, focus and a double click ------------------
{
  const { page, context } = await openPage(browser);
  await go(page, "/company/AAPL");
  const trigger = page.getByRole("button", { name: "生成建议卡" });
  await trigger.focus();
  await page.keyboard.press("Enter");
  await page.waitForSelector("dialog[open]");
  report.check("dialog_focus_inside", await page.evaluate(() =>
    Boolean(document.activeElement.closest("dialog"))));
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  report.check("dialog_closed_by_escape", await page.locator("dialog").count() === 0);
  report.check("dialog_focus_returns", await page.evaluate(() =>
    document.activeElement.textContent.includes("生成建议卡")));
  await trigger.click();
  await page.waitForSelector("dialog[open]");
  report.check("dialog_warns_about_billing", (await page.locator("dialog").innerText())
    .includes("按量计费"));
  const before = (await api("/api/jobs")).body.jobs.length;
  await page.getByRole("button", { name: "确认并排队" }).dblclick();
  await page.waitForTimeout(1200);
  const after = (await api("/api/jobs")).body.jobs.length;
  report.check("double_click_creates_one_job", after - before);
  await context.close();
}

// ---- routing: unknown pages, back/forward, overtaking navigations -----------
{
  const { page, context } = await openPage(browser);
  await go(page, "/nonsense");
  report.check("unknown_route_says_so", (await page.locator("#main").innerText())
    .includes("没有这个页面"));
  await go(page, "/company/");
  report.check("empty_company_route_says_so", (await page.locator("#main").innerText())
    .includes("没有这个页面"));
  await go(page, "/overview");
  await page.locator("a.ticker").first().click();
  await page.waitForSelector("#main h1:has-text('AAPL')");
  await page.goBack();
  await page.waitForSelector("#main h1:has-text('总览')");
  report.check("back_returns_to_overview", true);
  await page.goForward();
  await page.waitForSelector("#main h1:has-text('AAPL')");
  report.check("forward_returns_to_company", true);
  // Two navigations in a row: the page must end on the second one.
  await page.evaluate(() => {
    location.hash = "#/company/NVDA";
    location.hash = "#/jobs";
  });
  await page.waitForTimeout(1500);
  report.check("last_navigation_wins", await page.locator("#main h1").innerText());
  await page.reload();
  await page.waitForSelector("#main h1");
  report.check("reload_keeps_the_route", await page.locator("#main h1").innerText());
  await context.close();
}

// ---- zoom 125% and 150%: no sideways page scroll --------------------------------
for (const [scale, width] of [[1.25, 1152], [1.5, 960]]) {
  const { page, context } = await openPage(browser, { width, height: 720, scale });
  for (const hash of ["/overview", "/company/AAPL", `/cards/${scenario.card_id}`, "/jobs"]) {
    await go(page, hash);
    report.check(`overflow_zoom_${scale}_${hash.split("/")[1]}`, await pageOverflow(page));
  }
  await context.close();
}
report.finish([]);
await browser.close();
