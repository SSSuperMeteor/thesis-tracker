/* The chat page, driven through the real UI against a scripted stand-in model.
 *
 * SCENARIO = {"flow": "answer" | "card" | "refused" | "dismiss" | "confirm",
 *             "ticker": "AAPL", "question": "...", "card_id": "..."}
 */
import { Report, api, go, launch, openPage, pageOverflow } from "./lib.mjs";

const scenario = JSON.parse(process.env.SCENARIO || "{}");
const ticker = scenario.ticker || "AAPL";
const report = new Report();
const browser = await launch();
const { page } = await openPage(browser);

// 1. the only way into a conversation from the UI: the company page's button
await go(page, `/company/${ticker}`);
await page.getByRole("button", { name: "新建对话" }).click();
await page.waitForTimeout(1500);
const hash = await page.evaluate(() => location.hash);
report.check("new_conversation_opens_chat", hash.startsWith("#/chat/"));
report.check("create_post_status", (page.posts.find(
  (post) => post.path === "/api/companies/conversations") || {}).status);
if (!hash.startsWith("#/chat/")) {
  report.finish([page]);
  await browser.close();
  process.exit(0);
}
const conversationId = hash.slice("#/chat/".length);

// 2. ask a question from the composer
const input = page.getByLabel("向这家公司提问");
await input.fill(scenario.question || "现在最新收盘价是多少？");
await input.press("Enter");
report.check("send_post_status", await (async () => {
  await page.waitForTimeout(800);
  return (page.posts.find((post) => post.path.endsWith("/messages")) || {}).status;
})());

// 3. the answer must arrive without the reader reloading the page
const resultSelector = scenario.flow === "refused" ? ".refusal" : ".message.assistant";
let arrived = true;
try {
  await page.waitForSelector(resultSelector, { timeout: 12000 });
} catch {
  arrived = false;
}
report.check("answer_arrives_without_reload", arrived);
if (!arrived) {
  await page.reload();
  await page.waitForSelector("#main h1");
  await page.waitForTimeout(500);
  report.check("answer_present_after_reload", await page.locator(resultSelector).count() > 0);
}
report.check("composer_enabled_after_answer",
  await input.isEnabled().catch(() => false));

const flow = scenario.flow || "answer";
if (flow === "answer" || flow === "card") {
  const markers = page.locator(".message.assistant .fact-ref");
  report.check("marker_count", await markers.count());
  if (await markers.count()) {
    await markers.first().click();
    await page.waitForTimeout(500);
    report.check("marker_highlights_evidence_row", await page.evaluate(() => {
      const row = document.querySelector(".fact-row.flash");
      const focused = document.activeElement && document.activeElement.classList.contains("fact-row");
      return Boolean(row) || Boolean(focused);
    }));
  }
  report.check("answer_text", (await page.locator(".message.assistant .message-text").first()
    .innerText().catch(() => "")));
  // a second conversation must start empty: nothing leaks across conversations
  const other = await api("/api/companies/conversations", { ticker });
  await go(page, `/chat/${other.body.conversation.conversation_id}`);
  report.check("other_conversation_has_no_messages", await page.locator(".message").count() === 0);
  report.check("other_conversation_has_no_evidence",
    await page.locator(".evidence .fact-row").count() === 0);
  await go(page, `/chat/${conversationId}`);
  report.check("first_conversation_keeps_its_evidence",
    await page.locator(".evidence .fact-row").count() > 0);
}
if (flow === "answer") {
  // An unbroken 400-character token must wrap inside its block, not push the
  // page or the reading column wider.
  const long = "x".repeat(400);
  await page.getByLabel("向这家公司提问").fill(long);
  await page.getByLabel("向这家公司提问").press("Enter");
  await page.waitForFunction(() => document.querySelectorAll(".message.user").length >= 2,
    null, { timeout: 12000 }).catch(() => {});
  await page.waitForTimeout(600);
  report.check("long_message_page_overflow", await pageOverflow(page));
  report.check("long_message_block_overflow", await page.evaluate(() => {
    const blocks = Array.from(document.querySelectorAll(".message.user"));
    return Math.max(0, ...blocks.map((node) => node.scrollWidth - node.clientWidth));
  }));
}
if (flow === "derived") {
  // A computed result is labelled as one, with its formula and sources on hover.
  const marker = page.locator(".message.assistant .fact-ref.computed").first();
  report.check("computed_marker_present", await marker.count() > 0);
  report.check("computed_marker_title", await marker.getAttribute("title"));
  const row = page.locator(".evidence .fact-row.computed").first();
  report.check("computed_row_present", await row.count() > 0);
  report.check("computed_row_title", await row.getAttribute("title"));
  report.check("computed_row_tag", await row.locator(".fact-name").evaluate(
    (node) => getComputedStyle(node, "::after").content));
  report.check("plain_rows_have_no_tag", await page.locator(
    ".evidence .fact-row:not(.computed)").evaluateAll((nodes) => nodes.every(
    (node) => getComputedStyle(node.querySelector(".fact-name"), "::after").content === "none"
      || getComputedStyle(node.querySelector(".fact-name"), "::after").content === "normal")));
}
if (flow === "answer") {
  // On a phone, with the tool list opened (its argument text holds long ids), the
  // page must not scroll sideways.
  for (const width of [360, 390]) {
    await page.setViewportSize({ width, height: 800 });
    await page.evaluate(() => document.querySelectorAll("details").forEach((d) => { d.open = true; }));
    await page.waitForTimeout(300);
    report.check(`chat_overflow_${width}`, await pageOverflow(page));
    report.note(`overflowing at ${width}: ` + await page.evaluate(() => {
      const wide = document.documentElement.clientWidth;
      return Array.from(document.querySelectorAll("body *"))
        .filter((node) => node.getBoundingClientRect().right > wide + 1)
        .slice(0, 6).map((node) => `${node.tagName}.${node.className}`).join(" | ");
    }));
  }
  await page.setViewportSize({ width: 1440, height: 900 });
}
if (flow === "refused") {
  const text = await page.locator(".refusal").innerText();
  report.check("refusal_text", text);
  report.check("no_assistant_answer_shown", await page.locator(".message.assistant .message-text").count() === 0
    || !(await page.locator(".message.assistant").innerText()).includes("建议买入"));
}
if (flow === "dismiss" || flow === "confirm") {
  report.check("proposal_visible", await page.locator(".proposal").count() > 0);
  const buttonName = flow === "dismiss" ? "忽略" : "确认生成";
  await page.locator(".proposal").getByRole("button", { name: buttonName }).click();
  await page.waitForTimeout(1200);
  const postPath = flow === "dismiss" ? "/api/proposals/dismiss" : "/api/proposals/confirm";
  report.check("decision_post_status", (page.posts.find((post) => post.path === postPath) || {}).status);
  report.check("proposal_text_after", await page.locator(".proposal").first().innerText());
  await page.reload();
  await page.waitForSelector("#main h1");
  await page.waitForTimeout(500);
  const afterReload = await page.locator(".proposal").count()
    ? await page.locator(".proposal").first().innerText() : "";
  report.check("proposal_state_survives_reload", afterReload);
  report.check("proposal_buttons_after_reload",
    await page.locator(".proposal").getByRole("button").count());
}
report.finish([page]);
await browser.close();
