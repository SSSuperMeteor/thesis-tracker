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
  // The price state is a pill at the bottom of the sidebar, on every page.
  const pill = page.locator("#price-status .pill");
  report.check("pill_text", await pill.innerText());
  report.check("pill_collapsed_at_first", (await pill.getAttribute("aria-expanded")) === "false");
  await pill.click();
  report.check("pill_expanded_after_click", (await pill.getAttribute("aria-expanded")) === "true");
  report.check("price_command_visible", (await page.locator("#price-pop").innerText())
    .includes("uv run prices-ingest"));
  await page.getByRole("button", { name: "复制命令" }).click();
  await page.waitForTimeout(300);
  report.check("copy_command_feedback", (await page.locator("[aria-live]").allInnerTexts())
    .join(" ").includes("已复制"));
  report.check("no_banner_in_the_page", await page.locator("#main .banner").count() === 0);
  // the coverage matrix: filing blocks, a sparkline per company with prices
  report.check("matrix_blocks", await page.locator("table.coverage .blk").count());
  report.check("matrix_has_a_10k_block", await page.locator("table.coverage .blk.k").count() > 0);
  report.check("matrix_sparklines", await page.locator("table.coverage .spark").count());
  report.check("matrix_current_column", await page.locator("table.coverage th.current").count() > 0);
  await page.locator("table.coverage td.cell").first().hover();
  report.check("hover_lights_a_column", await page.locator("table.coverage .col-hl").count() > 1);
  report.check("overview_problems_wide", page.problems.length);
  await context.close();
}

// ---- overview, narrow: the table must show filings, not a blank pane --------
{
  const { context, page } = await openPage(browser, { width: 390, height: 844 });
  await go(page, "/overview");
  report.check("overview_overflow_390", await pageOverflow(page));
  report.check("narrow_visible_filing_cells", await page.evaluate(() => {
    const scroller = document.querySelector(".matrix-scroll");
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
  await go(page, "/company/AAPL");
  report.check("company_chart_present", await page.locator(".chart .plot").count());
  report.check("company_chart_ranges", await page.locator(".range-tabs button").count());
  report.check("company_timeline_blocks", await page.locator(".filings-strip .blk").count() > 0);
  report.check("company_metric_blocks", await page.locator(".metric-grid .metric").count());
  report.check("company_card_tiles", await page.locator(".mini-grid .mini-card").count());
  await go(page, "/company/MSFT");
  report.check("noprice_chart_is_a_sentence", await page.locator(".chart .plot").count() === 0);
  report.check("company_problems", page.problems.length);
  await context.close();
}

// ---- chart axis labels never overlap, wide or narrow ---------------------------
for (const [label, width] of [["wide", 1440], ["narrow", 390], ["tiny", 360]]) {
  const { page, context } = await openPage(browser, { width, height: 900 });
  await go(page, "/company/AAPL");
  report.check(`axis_overlaps_${label}`, await page.evaluate(() => {
    const boxes = Array.from(document.querySelectorAll(".x-axis span"))
      .filter((node) => getComputedStyle(node).display !== "none")
      .map((node) => node.getBoundingClientRect());
    let overlaps = 0;
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1) {
        if (boxes[i].left < boxes[j].right && boxes[j].left < boxes[i].right) overlaps += 1;
      }
    }
    return { visible: boxes.length, overlaps };
  }));
  report.check(`pill_text_inside_${label}`, await page.evaluate(() => {
    const pill = document.querySelector("#price-status .pill");
    const text = pill.querySelector(".pill-text").getBoundingClientRect();
    const chev = pill.querySelector(".chev").getBoundingClientRect();
    return text.right <= chev.left + 1 || text.bottom <= chev.top + 1;
  }));
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
  report.check("card_verdict_badges", await page.locator(".verdict .badge.big").count());
  report.check("card_metric_blocks", await page.locator(".verdict .metric").count());
  report.check("card_chart_levels", await page.locator(".plot .level-label").count());
  report.check("card_has_no_second_band", await page.locator(".band-plot, .band-legend").count() === 0);
  // the chart: ranges, crosshair and keyboard
  await page.getByRole("button", { name: "3 个月" }).click();
  report.check("range_switch_pressed", (await page.getByRole("button", { name: "3 个月" })
    .getAttribute("aria-pressed")) === "true");
  const plot = page.locator(".plot").first();
  await plot.focus();
  await page.keyboard.press("ArrowLeft");
  report.check("keyboard_shows_tooltip", await page.locator(".plot .tip:not([hidden])").count());
  report.check("tooltip_text", await page.locator(".plot .tip").first().innerText());
  const box = await plot.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  report.check("hover_shows_crosshair", await page.locator(".plot .cross:not([hidden])").count());
  // the card can be followed up from its own page
  await page.getByRole("button", { name: "追问这张卡" }).click();
  await page.waitForTimeout(1200);
  report.check("follow_up_opens_a_chat", (await page.evaluate(() => location.hash)).startsWith("#/chat/"));
  report.check("follow_up_is_pinned", (await page.locator("#main").innerText()).includes("从这张卡开始"));
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
