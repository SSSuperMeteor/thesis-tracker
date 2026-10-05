/* Thesis Tracker local workbench.
 *
 * Hard rule for this file: never compute, round, rescale, parse or format a
 * financial number or a timestamp.  Every price, metric, percentage, position,
 * date and label arrives ready to render as a string.  The only arithmetic
 * allowed is counting things like how many filings a cell holds.
 */
import { priceChart, sparkline } from "./chart.js";
import { clear, el, icon, svg } from "./dom.js";

const HORIZONS = [
  { key: "short", label: "短期" },
  { key: "mid", label: "中期" },
  { key: "long", label: "长期" },
];

/* ------------------------------------------------------------ components */

function splitUnit(display) {
  // The number and its unit are already decided; this only gives the unit its
  // smaller, quieter style.
  if (typeof display !== "string") return { number: "—", unit: "" };
  const match = display.match(/^([\d.,−+-]+)\s*(.*)$/);
  if (!match) return { number: display, unit: "" };
  return { number: match[1], unit: match[2] };
}

function quantity(display) {
  const parts = splitUnit(display);
  return [parts.number, parts.unit ? el("span", { class: "unit", text: parts.unit }) : null];
}

const STATES = {
  ok: { shape: "shape-solid", label: "正常", cls: "state state-ok" },
  stale: { shape: "shape-clock", label: "已过期", cls: "state state-warn" },
  error: { shape: "shape-cross", label: "出错", cls: "state state-bad" },
  missing: { shape: "shape-open", label: "缺失", cls: "state state-none" },
  run: { shape: "shape-run", label: "运行中", cls: "state state-run" },
};

function showState(kind, text) {
  const spec = STATES[kind];
  return el("span", { class: spec.cls }, [
    el("span", { class: `shape ${spec.shape}`, "aria-hidden": "true" }),
    el("span", { text: text || spec.label }),
  ]);
}

function statusState(status) {
  if (status === "ok") return showState("ok", "可计算");
  if (status === "not_applicable") return showState("missing", "不适用");
  if (status === "error") return showState("error", "出错");
  return showState("missing", "算不出");
}

/* Empty, loading and error are one component with three moods. */
function notice(kind, title, text, action) {
  return el("div", {
    class: `notice ${kind}`, role: kind === "error" ? "alert" : undefined,
  }, [
    kind === "error"
      ? showState("error", title)
      : el("p", { class: "notice-title", text: title }),
    text ? el("p", { text }) : null,
    action ? el("div", { class: "notice-action" }, action) : null,
  ]);
}

function errorBox(message) {
  return notice("error", "没能打开这一页", message,
    el("a", { href: "#/overview", text: "回到总览" }));
}

function emptyBox(title, hint, action) {
  return notice("empty", title, hint, action);
}

let toastTimer = null;

/* A light confirmation that disappears by itself after two seconds.  It lives in
   one aria-live region, so a screen reader hears it and nothing else moves. */
function toast(text) {
  const region = document.getElementById("toast");
  if (!region) return;
  region.textContent = text;
  region.classList.add("visible");
  if (toastTimer !== null) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    region.textContent = "";
    region.classList.remove("visible");
    toastTimer = null;
  }, 2000);
}

async function copyText(text, done) {
  try {
    await navigator.clipboard.writeText(text);
    toast(done);
  } catch {
    toast(`无法自动复制，请手动输入：${text}`);
  }
}

let panelCount = 0;

/* A panel: a titled white surface, with an optional count, note and action. */
function panel(title, body, options = {}) {
  panelCount += 1;
  const id = `panel-${panelCount}`;
  const { count, note, action } = options;
  return el("section", { class: "panel", "aria-labelledby": id }, [
    el("div", { class: "panel-head" }, [
      el("h2", { id, text: title }),
      count === undefined ? null : el("span", { class: "count-tag", text: String(count) }),
      el("span", { class: "spacer" }),
      action || null,
    ]),
    note ? el("p", { class: "panel-note", text: note }) : null,
    ...[].concat(body),
  ]);
}

function pageHead(title, meta, actions, options = {}) {
  return el("header", { class: "page-head" }, [
    el("div", { class: "titles" }, [
      el("h1", { text: title, translate: options.translate }),
      meta ? el("p", { class: "meta" }, [].concat(meta)) : null,
    ]),
    actions && actions.length ? el("div", { class: "actions" }, actions) : null,
  ]);
}

function mount(host, ...nodes) {
  clear(host);
  host.append(el("div", { class: "page" }, nodes.filter(Boolean)));
}

function table(headers, rows, className = "list") {
  const head = el("tr", {}, headers.map((header) => el("th", {
    scope: "col", class: [header.numeric ? "num" : "", header.end ? "end" : ""].join(" ").trim() || undefined,
    text: header.label,
  })));
  return el("div", { class: "table-scroll" }, [
    el("table", { class: className }, [
      el("thead", {}, head),
      el("tbody", {}, rows),
    ]),
  ]);
}

function arrowLink(href, label) {
  return el("a", { class: "icon-link", href, "aria-label": label, title: label }, icon("arrow"));
}

const BIAS_SHAPES = {
  看多: { shape: "up", cls: "" },
  中性: { shape: "flat", cls: "flat" },
  看空: { shape: "down", cls: "bear" },
};

/* A badge is a shape and a word, so it reads in greyscale. */
function biasBadge(bias, big) {
  const spec = BIAS_SHAPES[bias] || { shape: "flat", cls: "flat" };
  return el("span", { class: `badge bias ${spec.cls} ${big ? "big" : ""}`.trim() }, [
    icon(spec.shape), el("span", { text: bias }),
  ]);
}

function actionBadge(action, big) {
  return el("span", { class: `badge action ${big ? "big" : ""}`.trim() }, [
    icon("dot"), el("span", { text: action }),
  ]);
}

function metricBlock(label, value, options = {}) {
  return el("div", { class: `metric ${options.empty ? "empty" : ""}`.trim() }, [
    el("span", { class: "m-label", text: label }),
    el("span", { class: "m-value" }, value),
    options.note ? el("span", { class: "m-note", text: options.note }) : null,
  ]);
}

function factMarker(segment, onPick) {
  const button = el("button", {
    type: "button", class: "fact-ref", translate: false,
    title: `来源 ${segment.fact_id}`,
    text: segment.display === null || segment.display === undefined ? segment.fact_id : segment.display,
  });
  button.addEventListener("click", () => onPick(segment.fact_id));
  return button;
}

function segments(nodes, onPick) {
  return nodes.map((segment) => (
    segment.type === "text"
      ? document.createTextNode(segment.value)
      : factMarker(segment, onPick)
  ));
}

/* ------------------------------------------------------------------ api */

async function api(path, options = {}) {
  const init = { credentials: "same-origin", headers: { Accept: "application/json" } };
  if (options.body !== undefined) {
    // api() serialises the body itself; a caller that already did would send a
    // JSON string, which the server rightly refuses as "not an object".
    if (typeof options.body === "string") throw new TypeError("api(): pass an object body");
    init.method = "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, init);
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`响应不是有效的 JSON（HTTP ${response.status}）。`);
  }
  if (!response.ok) throw new Error(payload.error || `请求失败（HTTP ${response.status}）。`);
  return payload;
}

/* Route generations and polling.
 *
 * Every navigation bumps ``generation``.  A view reads it once when it starts and
 * fetches through ``load(path, mine)``: if the reader has moved on by the time the
 * response arrives, the view stops instead of painting an old page over the new
 * one.  Polling is one timer for the whole app, replaced (never added to) each
 * time a view asks for another look, and cancelled by the next navigation, so
 * leaving and re-entering a page can never stack up several refresh chains. */
let generation = 0;
let pollTimer = null;
const STALE = Symbol("stale-route");

async function load(path, mine) {
  const payload = await api(path);
  if (mine !== generation) throw STALE;
  return payload;
}

function stopPolling() {
  if (pollTimer !== null) clearTimeout(pollTimer);
  pollTimer = null;
}

function pollAgain(milliseconds, mine) {
  stopPolling();
  pollTimer = setTimeout(() => {
    pollTimer = null;
    if (mine === generation) route({ poll: true });
  }, milliseconds);
}

/* ------------------------------------------------------------ the shell */

const NAV = [
  { hash: "#/overview", label: "总览", icon: "overview" },
  { hash: "#/cards", label: "建议卡", icon: "cards" },
  { hash: "#/jobs", label: "任务", icon: "jobs", badge: true },
];
let runningJobs = 0;

function renderNav(active) {
  const nav = document.getElementById("nav");
  clear(nav);
  for (const item of NAV) {
    const current = active ? item.hash.startsWith(active) : location.hash.startsWith(item.hash);
    nav.append(el("a", {
      href: item.hash,
      "aria-current": current ? "page" : null,
    }, [
      icon(item.icon),
      el("span", { text: item.label }),
      item.badge && runningJobs > 0
        ? el("span", { class: "count", text: `运行中 ${String(runningJobs)}` })
        : null,
    ]));
  }
}

/* The price state, always visible at the bottom of the sidebar.  Clicking it
   opens the command to refresh prices; the page never runs anything itself. */
let priceExpanded = false;

function renderPriceStatus(status, failure) {
  const host = document.getElementById("price-status");
  clear(host);
  if (failure) {
    host.append(el("div", { class: "pill stale", role: "status" }, [
      icon("cross"), el("span", { class: "pill-text" }, [
        el("span", { text: "价格状态读取失败" }), el("small", { text: failure })]),
    ]));
    return;
  }
  const popId = "price-pop";
  const pill = el("button", {
    type: "button", class: `pill ${status.stale ? "stale" : "ok"}`,
    "aria-expanded": priceExpanded ? "true" : "false", "aria-controls": popId,
  }, [
    icon(status.stale ? "clock" : "dot"),
    el("span", { class: "pill-text" }, [
      el("span", { text: status.headline }),
      el("small", { text: status.sub_text }),
    ]),
    icon("chevron", "chev"),
  ]);
  const copy = el("button", { type: "button", class: "small" }, [icon("copy", "small"), "复制命令"]);
  copy.addEventListener("click", () => copyText(status.price_command, "命令已复制"));
  const pop = el("div", { class: "pop", id: popId, hidden: !priceExpanded }, [
    el("p", { text: status.detail }),
    el("code", { text: status.price_command, translate: false }),
    el("p", { text: "在终端运行这条命令，需要运行两次。界面不会替你执行。" }),
    copy,
  ]);
  pill.addEventListener("click", () => {
    priceExpanded = !priceExpanded;
    pill.setAttribute("aria-expanded", priceExpanded ? "true" : "false");
    pop.hidden = !priceExpanded;
  });
  host.append(pop, pill);
}

async function refreshPriceStatus() {
  try {
    renderPriceStatus(await api("/api/price-status"));
  } catch (error) {
    renderPriceStatus(null, error.message);
  }
}

/* ------------------------------------------------------------- overview */

function holeGlyph() {
  // The square is sized by the stylesheet, so this file carries no geometry.
  return svg("svg", { class: "hole", "aria-hidden": "true" }, svg("rect", {}));
}

async function viewOverview(host) {
  const mine = generation;
  const overview = await load("/api/overview", mine);
  const companies = Object.values(overview.companies);
  const summary = overview.price_summary;
  const priceText = summary.latest_price_date
    ? `价格最新至 ${summary.latest_price_date}（落后 ${String(summary.lag_days)} 天）`
    : "还没有价格数据";

  const quarters = overview.quarters;
  const years = [];
  for (const key of quarters) {
    const year = key.slice(0, 4);
    if (!years.length || years[years.length - 1].year !== year) years.push({ year, quarters: [] });
    years[years.length - 1].quarters.push(key);
  }
  const current = overview.current_quarter;

  // Two header rows: the year is written once per year, the quarter under it.
  const yearCells = [el("th", { scope: "col", class: "stick", rowspan: "2", text: "公司" })];
  const quarterCells = [];
  for (const group of years) {
    group.quarters.forEach((key, index) => {
      const start = index === 0 ? "year-start" : "";
      if (index === 0) {
        yearCells.push(el("th", {
          scope: "colgroup", colspan: String(group.quarters.length),
          class: `qhead ${start} ${group.quarters.includes(current) ? "current" : ""}`.trim(),
          title: `${group.year} 年（按财期截止日的日历季度）`,
        }, el("span", { class: "year", text: group.year })));
      }
      quarterCells.push(el("th", {
        scope: "col", class: `quarter-row ${start} ${key === current ? "current" : ""}`.trim(),
        title: key === current
          ? `${group.year} 年 ${key.slice(5)} 季度：当前季度，财报还没有披露`
          : `${group.year} 年 ${key.slice(5)} 季度（按财期截止日的日历季度）`,
      }, el("span", { class: "quarter", text: key.slice(5) })));
    });
  }
  const tails = [
    ["t-price", "最新价", true], ["t-spark", "近半年走势", false],
    ["t-range", "价格数据范围", false], ["t-date", "最新价格日", false],
    ["t-cards", "建议卡", true],
  ];
  for (const [cls, label, numeric] of tails) {
    yearCells.push(el("th", {
      scope: "col", class: `tail ${cls} ${numeric ? "num" : ""}`.trim(), rowspan: "2", text: label,
    }));
  }

  const body = [];
  for (const [ticker, company] of Object.entries(overview.companies)) {
    const cells = [el("th", { scope: "row", class: "stick" }, [
      el("a", { href: `#/company/${ticker}`, class: "ticker", translate: false, text: ticker }),
    ])];
    for (const group of years) {
      group.quarters.forEach((key, index) => {
        const classes = ["cell"];
        if (index === 0) classes.push("year-start");
        if (key === current) classes.push("current");
        cells.push(renderCell(company.periods[key], classes.join(" "), ticker, key,
          company.dashed_quarters.includes(key), overview.empty_gap_days));
      });
    }
    const price = company.price;
    const range = price.start_date ? `${price.start_date} 至 ${price.end_date}` : "没有价格数据";
    cells.push(el("td", { class: "tail t-price num" }, price.latest_close ? quantity(price.latest_close)[0] : "—"));
    cells.push(el("td", { class: "tail t-spark" }, sparkline(company.spark)));
    cells.push(el("td", { class: "tail t-range", text: range }));
    cells.push(el("td", { class: "tail t-date", text: price.end_date || "—" }));
    cells.push(el("td", { class: "tail t-cards num", text: String(company.card_count) }));
    body.push(el("tr", {}, cells));
  }

  const grid = el("table", { class: "coverage" }, [
    el("thead", {}, [el("tr", {}, yearCells), el("tr", {}, quarterCells)]),
    el("tbody", {}, body),
  ]);
  // Hovering a cell lights its whole column; the row lights up by itself.
  let lit = null;
  const light = (key) => {
    if (lit === key) return;
    for (const node of grid.querySelectorAll(".col-hl")) node.classList.remove("col-hl");
    lit = key;
    if (key) for (const node of grid.querySelectorAll(`[data-q="${key}"]`)) node.classList.add("col-hl");
  };
  grid.addEventListener("mouseover", (event) => {
    const cell = event.target.closest("[data-q]");
    light(cell ? cell.dataset.q : null);
  });
  grid.addEventListener("mouseleave", () => light(null));
  const scroller = el("div", { class: "matrix-scroll" }, grid);

  mount(host,
    pageHead("总览", `${String(companies.length)} 家公司｜${priceText}`
      + (summary.missing ? `｜${String(summary.missing)} 家没有价格数据` : "")),
    panel("财报覆盖", [coverageLegend(overview), scroller], {
      count: companies.length, note: "按财期截止日所在的日历季度分列。",
    }));
  const hint = host.querySelector(".page-head .meta");
  if (hint) hint.title = `价格超过 ${String(summary.stale_after_days)} 个日历日未更新即视为过期。`;
  // The newest quarters matter most, so the table opens at that end instead of
  // at 2014.  The company column is pinned, so nothing is hidden.
  revealNewestQuarter(scroller);
  document.title = "总览｜Thesis Tracker";
}

/* Scroll so the newest quarter's column ends at the right edge of what is
   visible.  Scrolling all the way right would be wrong whenever the summary
   columns are not pinned: it would show them and hide the quarters. */
function revealNewestQuarter(scroller) {
  const cells = scroller.querySelectorAll("tbody tr:first-child td.cell");
  const last = cells[cells.length - 1];
  if (!last) return;
  let pinned = 0;
  for (const tail of scroller.querySelectorAll("tbody tr:first-child td.tail")) {
    if (getComputedStyle(tail).position === "sticky") pinned += tail.offsetWidth;
  }
  scroller.scrollLeft += last.getBoundingClientRect().right + pinned
    - scroller.getBoundingClientRect().right;
}

function coverageLegend(overview) {
  const item = (glyph, text) => el("span", {}, [glyph, el("span", { text })]);
  return el("div", { class: "legend" }, [
    item(el("span", { class: "blk k", text: "K" }), "年报 10-K"),
    item(el("span", { class: "blk q", text: "Q" }), "季报 10-Q"),
    item(el("span", { class: "blk q amended", text: "Q" }), "修订申报"),
    // The hatched box means one thing only: a real hole in this company's filing
    // history.  A quarter it simply has not reported is left blank.
    item(holeGlyph(), overview.empty_gap_label),
    item(el("span", { class: "swatch", "aria-hidden": "true" }), "当前季度"),
  ]);
}

function renderCell(period, className, ticker, key, dashed, gapDays) {
  if (!period) {
    if (!dashed) return el("td", { class: `${className} is-blank`, "data-q": key });
    return el("td", {
      class: `${className} is-missing`, "data-q": key,
      title: `这一季度没有已存档的财报：${ticker} 相邻两次财报相隔超过 ${String(gapDays)} 天`,
    }, holeGlyph());
  }
  const entries = period.entries || [];
  const items = entries.map((entry) => {
    const letter = entry.key_form === "10-K" ? "K" : entry.key_form === "10-Q" ? "Q" : entry.key_form;
    const kind = entry.key_form === "10-K" ? "k" : "q";
    const detail = [
      entry.form,
      `财期截止 ${entry.period_end}`,
      `披露日 ${entry.filed_at}`,
      `accession ${entry.accession}`,
      entry.is_amendment ? "修订申报" : null,
    ].filter(Boolean).join("\n");
    return el("li", {}, el("span", {
      class: `blk ${kind} ${entry.is_amendment ? "amended" : ""}`.trim(), title: detail, text: letter,
    }));
  });
  const summary = entries.map((entry) => entry.form).join(" + ");
  return el("td", {
    class: `${className} has-filing`, "data-q": key,
    title: `${ticker}\n${summary}\n财期截止 ${period.period_end}\n披露日 ${period.filed_at}\naccession ${period.accession}`,
  }, el("ul", {}, items));
}

/* -------------------------------------------------------------- company */

function conversationSection(page, conversations, loadError) {
  const newButton = el("button", { type: "button" }, [icon("plus", "small"), "新建对话"]);
  const problems = el("div", { "aria-live": "polite" },
    loadError ? errorBox(`对话列表读取失败：${loadError}`) : null);
  newButton.addEventListener("click", async () => {
    newButton.disabled = true;
    try {
      const created = await api("/api/companies/conversations", { body: { ticker: page.ticker } });
      location.hash = `#/chat/${created.conversation.conversation_id}`;
    } catch (error) {
      newButton.disabled = false;
      problems.replaceChildren(errorBox(error.message));
    }
  });
  const rows = conversations.map((item) => el("tr", {}, [
    el("td", {}, el("a", { href: `#/chat/${item.conversation_id}`, text: item.title })),
    el("td", { class: "num", text: String(item.messages_count) }),
    el("td", { class: "num", text: `${String(item.usage.levels.conversation.input_tokens)} / ${String(item.usage.levels.conversation.output_tokens)}` }),
    el("td", { text: item.usage.levels.conversation.cost_text || "—" }),
    el("td", { text: item.last_activity_at }),
    el("td", {}, item.pending_proposal
      ? el("span", { class: "tag", text: `待确认：${item.pending_proposal.horizon_label}` })
      : el("span", { class: "section-note", text: "—" })),
    el("td", { class: "end" }, arrowLink(`#/chat/${item.conversation_id}`, `打开对话：${item.title}`)),
  ]));
  return panel("对话", [
    conversations.length
      ? table([{ label: "标题" }, { label: "条数", numeric: true },
        { label: "输入 / 输出 token", numeric: true }, { label: "费用" },
        { label: "最近活动" }, { label: "待确认" }, { label: "", end: true }], rows)
      : emptyBox("还没有对话。", "新建一个对话，只讨论这家公司。"),
    problems,
  ], { count: conversations.length, action: newButton });
}

function filingDetail(filing) {
  return [filing.form, `财期截止 ${filing.period_end}`, `披露日 ${filing.filed_at}`,
    `accession ${filing.accession}`, filing.is_amendment ? "修订申报" : null].filter(Boolean).join("\n");
}

function filingsTimeline(filings) {
  // Newest first from the backend; a timeline reads oldest to newest.
  const byYear = new Map();
  for (const filing of [...filings].reverse()) {
    const year = filing.period_end.slice(0, 4);
    if (!byYear.has(year)) byYear.set(year, []);
    byYear.get(year).push(filing);
  }
  return el("div", {
    class: "filings-strip", tabindex: "0", role: "group", "aria-label": "按年份排列的财报，可横向滚动",
  }, [...byYear.entries()].map(([year, items]) => el("div", { class: "year-group" }, [
    el("span", { class: "year-label", text: year }),
    el("ul", {}, items.map((filing) => {
      const kind = filing.key_form === "10-K" ? "k" : "q";
      const letter = filing.key_form === "10-K" ? "K" : filing.key_form === "10-Q" ? "Q" : filing.key_form;
      return el("li", {}, [
        el("span", {
          class: `blk ${kind} ${filing.is_amendment ? "amended" : ""}`.trim(),
          title: filingDetail(filing), text: letter,
        }),
        el("small", { text: filing.period_end.slice(5) }),
      ]);
    })),
  ])));
}

function filingsTable(filings) {
  return table(
    [{ label: "表单" }, { label: "财期截止" }, { label: "披露日" }, { label: "accession" },
      { label: "财年", numeric: true }, { label: "SEC 财期" }],
    filings.map((filing) => el("tr", {}, [
      el("td", { text: filing.is_amendment ? `${filing.form}（修订）` : filing.form }),
      el("td", { text: filing.period_end }),
      el("td", { text: filing.filed_at }),
      el("td", { class: "mono", translate: false, text: filing.accession }),
      el("td", { class: "num", text: String(filing.fiscal_year) }),
      el("td", { text: filing.fiscal_period }),
    ])));
}

function metricCard(metric) {
  const computed = metric.status === "ok" && metric.display;
  return el("div", {
    class: `metric mcard ${computed ? "" : "empty"}`.trim(),
    title: metric.fact_id ? `来源 ${metric.fact_id}` : undefined,
  }, [
    el("span", { class: "m-name", text: metric.label }),
    el("span", { class: "m-value" }, computed ? quantity(metric.display) : "—"),
    el("span", { class: "m-note", text: metric.period_end ? `财期截止 ${metric.period_end}` : "没有财期" }),
    computed ? null : el("span", { class: "m-note", text: metric.reason ? metric.reason.message : "算不出。" }),
    el("span", { class: "m-note" }, statusState(metric.status)),
  ]);
}

function miniCard(card) {
  return el("a", {
    class: "mini-card", href: `#/cards/${card.card_id}`,
    "aria-label": `${card.horizon}${card.action}建议卡，创建于 ${card.created_at}`,
  }, [
    el("span", { class: "mini-badges" }, [biasBadge(card.bias), actionBadge(card.action)]),
    el("span", { class: "mini-meta" }, [
      el("span", { text: `周期 ${card.horizon}` }),
      el("span", { text: card.created_at }),
      card.rules_label ? el("span", { text: card.rules_label }) : null,
    ]),
    icon("arrow"),
  ]);
}

async function viewCompany(host, ticker) {
  const mine = generation;
  const page = await load(`/api/companies/${encodeURIComponent(ticker)}`, mine);
  // Fetched before anything is drawn, so a slow second request can never leave a
  // half-built page behind.  A failure is reported on the page, not swallowed.
  let conversations = [];
  let conversationError = null;
  try {
    conversations = (await load(
      `/api/companies/conversations?ticker=${encodeURIComponent(page.ticker)}`, mine)).conversations;
  } catch (error) {
    if (error === STALE) throw error;
    conversationError = error.message;
  }
  const price = page.price;
  const priceState = price.available
    ? (price.stale
      ? showState("stale", `价格已过期（最新 ${price.end_date}，落后 ${String(price.age_days)} 天）`)
      : showState("ok", "价格有效"))
    : showState("missing", price.reason === "mixed_batches" ? "价格批次不一致" : "没有价格数据");
  const meta = [priceState, el("span", {
    text: price.available
      ? `　最新价 ${price.latest_close}（${price.latest_date}）｜数据范围 ${price.start_date} 至 ${price.end_date}｜CIK ${page.cik}`
      : `　CIK ${page.cik}`,
  })];

  const metrics = page.fundamentals;
  const cards = page.cards;
  const strip = filingsTimeline(page.filings);
  mount(host,
    pageHead(page.ticker, meta, [analysisButton(page.ticker, host)], { translate: false }),
    panel("价格走势", priceChart(page.chart, page.ticker)),
    panel("财报时间线", [strip,
      el("details", { class: "details-link" }, [
        el("summary", { text: "展开完整财报列表" }), filingsTable(page.filings)]),
    ], {
      count: page.filings.length,
      note: "K 是年报 10-K，Q 是季报 10-Q，角上的圆点是修订申报；下方是财期截止的月日。",
    }),
    panel("可算的财务指标", [
      el("div", { class: "metric-grid" }, metrics.metrics.map(metricCard)),
    ], { count: metrics.metrics.length, note: `最新财期披露日 ${metrics.data_end_date || "—"}。` }),
    panel("建议卡", cards.length
      ? el("div", { class: "mini-grid" }, cards.map(miniCard))
      : emptyBox("还没有这家公司的建议卡。", "选择周期后点击生成建议卡。"), { count: cards.length }),
    conversationSection(page, conversations, conversationError));
  strip.scrollLeft = strip.scrollWidth;
  document.title = `${page.ticker}｜Thesis Tracker`;
}

/* 一个短编号加一个复制按钮：完整编号只在按钮里出现，行里不重复。 */
function copyRow(label, short, full) {
  const button = el("button", { type: "button", class: "small" }, [icon("copy", "small"), "复制完整编号"]);
  button.addEventListener("click", () => copyText(full, "编号已复制"));
  return el("div", { class: "row" }, [
    el("span", { class: "label", text: label }),
    el("span", { class: "value" }, [
      el("span", { class: "mono", translate: false, text: short }),
      button,
    ]),
  ]);
}

function row(label, value, numeric) {
  const parts = numeric ? quantity(value) : [value];
  return el("div", { class: "row" }, [
    el("span", { class: "label", text: label }),
    el("span", { class: "value" }, parts),
  ]);
}

function analysisButton(ticker, host) {
  const button = el("button", { type: "button", class: "primary", text: "生成建议卡" });
  button.addEventListener("click", () => openAnalysisDialog(ticker, host));
  return button;
}

function openAnalysisDialog(ticker, host) {
  const dialog = el("dialog", { "aria-labelledby": "analyze-title" });
  const horizonSelect = el("select", { id: "analyze-horizon" },
    HORIZONS.map((item) => el("option", { value: item.key, text: item.label })));
  horizonSelect.value = "mid";
  const usageLine = el("p", { class: "usage", text: "正在读取最近几次分析的用量。" });
  const body = el("div", { class: "dialog-body" }, [
    el("h2", { id: "analyze-title", text: `生成 ${ticker} 的建议卡` }),
    el("p", { text: "这一步会调用 DeepSeek 并按量计费，需要你确认后才会开始。" }),
    el("div", {}, [el("label", { for: "analyze-horizon", text: "周期　" }), horizonSelect]),
    usageLine,
  ]);
  const cancel = el("button", { type: "button", text: "取消" });
  const confirm = el("button", { type: "button", class: "primary", text: "确认并排队" });
  const message = el("p", { class: "reason", text: "" });
  const actions = el("div", { class: "dialog-actions" }, [message, el("span", { class: "spacer" }), cancel, confirm]);
  dialog.append(body, actions);
  host.append(dialog);

  cancel.addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => dialog.remove());
  confirm.addEventListener("click", async () => {
    confirm.disabled = true;
    try {
      const result = await api("/api/analyze", {
        body: { ticker, horizon: horizonSelect.value, confirm: true },
      });
      dialog.close();
      location.hash = `#/jobs/${result.job.job_id}`;
    } catch (error) {
      message.textContent = error.message;
      confirm.disabled = false;
    }
  });

  api("/api/usage").then((usage) => {
    usageLine.textContent = usage.analyses === 0
      ? "最近还没有分析记录，因此没有历史平均用量。"
      : `最近 ${String(usage.analyses)} 次分析平均：输入 ${String(usage.input_tokens)} token，`
        + `输出 ${String(usage.output_tokens)} token（不含任何金额）。`;
  }).catch(() => {
    usageLine.textContent = "读不到历史用量。";
  });
  dialog.showModal();
}

/* ------------------------------------------------------------ card list */

async function viewCards(host, query) {
  const mine = generation;
  const params = new URLSearchParams(query || "");
  const ticker = params.get("ticker") || "";
  const horizon = params.get("horizon") || "";
  const cards = (await load(`/api/cards${query ? `?${query}` : ""}`, mine)).cards;

  const tickerInput = el("input", { type: "text", id: "card-ticker",
    value: ticker, placeholder: "公司代码", size: "8", translate: false });
  const horizonSelect = el("select", { id: "card-horizon" }, [
    el("option", { value: "", text: "全部周期" }),
    ...HORIZONS.map((item) => el("option", { value: item.key, text: item.label })),
  ]);
  horizonSelect.value = horizon;
  const apply = el("button", { type: "button", class: "small", text: "筛选" });
  apply.addEventListener("click", () => {
    const next = new URLSearchParams();
    if (tickerInput.value.trim()) next.set("ticker", tickerInput.value.trim().toUpperCase());
    if (horizonSelect.value) next.set("horizon", horizonSelect.value);
    location.hash = `#/cards${next.toString() ? `?${next.toString()}` : ""}`;
  });
  const filters = el("div", { class: "toolbar" }, [
    el("label", { for: "card-ticker", text: "公司" }), tickerInput,
    el("label", { for: "card-horizon", text: "周期" }), horizonSelect, apply,
  ]);

  const list = cards.length
    ? table([{ label: "公司" }, { label: "周期" }, { label: "倾向" }, { label: "动作" },
      { label: "创建时价格", numeric: true }, { label: "创建时间" },
      { label: "规则版本" }, { label: "", end: true }],
    cards.map((card) => el("tr", {}, [
      el("td", {}, el("a", { href: `#/company/${card.ticker}`, translate: false, text: card.ticker })),
      el("td", { text: card.horizon }),
      el("td", {}, biasBadge(card.bias)),
      el("td", {}, actionBadge(card.action)),
      el("td", { class: "num" }, quantity(card.creation_price)),
      el("td", { text: card.created_at }),
      el("td", {}, [
        el("span", { text: card.version_mark }),
        card.rules_label
          ? el("span", {
            class: "tag",
            title: "这张卡由更早的校验器或提示词生成，详情页底部有完整版本。",
          }, [
            el("span", { class: "shape shape-open", "aria-hidden": "true" }),
            el("span", { text: card.rules_label }),
          ])
          : null,
      ]),
      el("td", { class: "end" }, arrowLink(`#/cards/${card.card_id}`,
        `查看 ${card.ticker} ${card.horizon}${card.action}建议卡`)),
    ])))
    : emptyBox("还没有符合条件的建议卡。", "在公司页选择周期后点击生成建议卡。");
  mount(host,
    pageHead("建议卡", `共 ${String(cards.length)} 张`),
    panel("全部建议卡", [filters, list], { count: cards.length }));
  document.title = "建议卡｜Thesis Tracker";
}

/* ---------------------------------------------------------- card detail */

/* One evidence panel for the card page and the chat page: a fact reads the same
   wherever it appears.  A derived fact says so, with its formula on hover. */
function evidenceRows(groups, rowsMap, options = {}) {
  return groups.map((group) => el("section", { class: "fact-group" }, [
    el("h3", { text: options.counts ? `${group.label}（${String(group.count)}）` : group.label }),
    ...group.facts.map((fact) => {
      const computed = fact.category === "derived";
      const rowNode = el("div", {
        class: `fact-row${fact.category === "card" ? " from-card" : ""}${computed ? " computed" : ""}`,
        tabindex: "-1",
        "data-fact-id": fact.fact_id,
        title: computed ? `计算结果｜${fact.formula_text || ""}` : `来源 ${fact.fact_id}`,
      }, [
        el("div", { class: "fact-head" }, [
          el("span", { class: "fact-name", text: fact.label }),
          el("span", { class: "fact-value", text: fact.display }),
        ]),
        el("div", { class: "fact-meta" }, [
          el("span", { text: fact.source_label }),
          el("span", { class: "fact-date", text: fact.date_or_period || "—" }),
        ]),
      ]);
      rowsMap.set(fact.fact_id, rowNode);
      return rowNode;
    }),
  ]));
}

function highlighter(rowsMap) {
  return (key) => {
    const rowNode = rowsMap.get(key);
    if (!rowNode) return;
    rowNode.scrollIntoView({ block: "center", behavior: "smooth" });
    // Focus as well, so a keyboard user lands on the same row a click targets.
    rowNode.focus({ preventScroll: true });
    rowNode.classList.remove("flash");
    void rowNode.offsetWidth;
    rowNode.classList.add("flash");
  };
}

async function viewCard(host, cardId) {
  const mine = generation;
  const card = await load(`/api/cards/${encodeURIComponent(cardId)}`, mine);
  const rowsMap = new Map();
  const pick = highlighter(rowsMap);

  const ratio = card.auto_computed.find((item) => item.label === "盈亏比");
  const ratioText = ratio ? ratio.text : "无";
  const entry = card.entry_range
    ? [splitUnit(card.entry_range[0]).number, " – ", ...quantity(card.entry_range[1])]
    : "无";
  const level = (display) => (display ? quantity(display) : "无");
  const verdict = panel("结论", el("div", { class: "verdict" }, [
    el("div", { class: "verdict-line" }, [
      biasBadge(card.bias, true), actionBadge(card.action, true),
      el("p", { class: "verdict-meta" }, [
        el("span", {}, ["周期 ", el("strong", { text: card.horizon })]),
        el("span", {}, ["置信度 ", el("strong", { text: card.confidence }), `（${card.confidence_calibration}）`]),
        card.price_data_end ? el("span", { text: `价格数据截至 ${card.price_data_end}` }) : null,
      ]),
    ]),
    el("div", { class: "metrics" }, [
      metricBlock("买点区间", entry, { empty: !card.entry_range }),
      metricBlock("止损", level(card.stop_loss), { empty: !card.stop_loss }),
      metricBlock("目标", level(card.target_price), { empty: !card.target_price }),
      metricBlock("盈亏比", ratioText, { empty: !ratio || ratioText.startsWith("无法") }),
    ]),
    card.hints.length ? el("div", { class: "hints" }, card.hints.map((hint) => el("p", { text: hint }))) : null,
  ]));

  const rationale = [];
  if (card.stop_rationale.length) {
    rationale.push(el("p", { class: "entry" }, [el("span", { class: "line-label", text: "止损依据" }), ...segments(card.stop_rationale, pick)]));
  }
  if (card.target_rationale.length) {
    rationale.push(el("p", { class: "entry" }, [el("span", { class: "line-label", text: "目标依据" }), ...segments(card.target_rationale, pick)]));
  }

  const reading = el("div", { class: "reading" }, [
    verdict,
    panel("价格与价位", priceChart(card.chart, card.ticker)),
    panel("理由", card.reasons.map((list) => el("p", { class: "entry" }, segments(list, pick)))),
    rationale.length ? panel("止损与目标依据", rationale) : null,
    panel("失效条件", card.invalidations.map((item) => el("div", { class: "entry" }, [
      el("p", { class: "machine" }, [
        el("span", { class: "line-label", text: item.machine_label }),
        ...segments(item.machine_check, pick),
      ]),
      el("p", { class: "explain" }, [
        el("span", { class: "line-label", text: item.explanation_label }),
        ...segments(item.explanation, pick),
      ]),
    ]))),
    panel(card.auto_computed_heading, [
      // The value column is right-aligned, so its header is too: a header that
      // sits on the other side of the column reads as a different column.
      table([{ label: "项目" }, { label: "结果", numeric: true }],
        card.auto_computed.map((item) => el("tr", {}, [
          el("th", { scope: "row", text: item.label }),
          el("td", { class: "num", text: item.text }),
        ]))),
    ], { note: autoComputedNote(card) }),
    panel("数据缺口", card.gaps.length
      ? table([{ label: "项目" }, { label: "状态" }, { label: "原因" }],
        card.gaps.map((gap) => el("tr", {}, [
          el("th", { scope: "row", text: gap.label }),
          el("td", {}, statusState(gap.status)),
          el("td", { class: "reason", text: gap.reason ? gap.reason.message : "" }),
        ])))
      : el("p", { class: "section-note", text: "没有数据缺口。" }), { count: card.gaps.length }),
    panel("尝试记录", card.attempts.map((attempt) => el("div", { class: "entry" }, [
      el("p", { text: `第 ${String(attempt.attempt_no)} 稿｜${attempt.requested_horizon || "—"}｜${attempt.created_at}` }),
      el("p", { class: "explain", text: attempt.headline }),
      attempt.violations.length
        ? el("ul", { class: "violations" }, attempt.violations.map((violation) => el("li", {}, [
          el("span", { class: "rule", text: `${violation.rule} ${violation.location}` }),
          el("span", { text: `：${violation.message}` }),
        ])))
        : null,
    ])), { count: card.attempts.length }),
    el("div", {}, [
      el("p", { class: "disclaimer", text: card.disclaimer }),
      el("p", { class: "versions", text:
        `提示词版本 ${card.versions.prompt_version || "—"}｜校验器版本 ${card.versions.validator_version}`
        + `｜请求模型 ${card.versions.requested_model || "—"}｜返回模型 ${card.versions.returned_model || "—"}` }),
    ]),
  ]);

  const evidenceBody = el("div", { class: "evidence-scroll" },
    evidenceRows(card.fact_groups, rowsMap));

  const ask = el("button", { type: "button" }, [icon("chat", "small"), "追问这张卡"]);
  const askNotice = el("div", { "aria-live": "polite" });
  ask.addEventListener("click", async () => {
    ask.disabled = true;
    try {
      const created = await api("/api/companies/conversations", {
        body: { ticker: card.ticker, card_id: card.card_id },
      });
      location.hash = `#/chat/${created.conversation.conversation_id}`;
    } catch (error) {
      ask.disabled = false;
      askNotice.replaceChildren(errorBox(error.message));
    }
  });

  mount(host,
    pageHead(`${card.ticker} 建议卡`, `分析截至 ${card.as_of}｜创建于 ${card.created_at}`, [ask], { translate: false }),
    askNotice,
    el("div", { class: "card-layout" }, [
      reading,
      el("aside", { class: "evidence", "aria-label": "事实证据" }, [
        el("h2", { text: `事实（${String(card.facts.length)} 条）` }),
        evidenceBody,
      ]),
    ]));
  document.title = `${card.ticker} ${card.as_of}｜建议卡`;
}

function autoComputedNote(card) {
  const parts = ["这几项由卡里的价位算出，不参与校验。"];
  if (card.hints.length) parts.push("提示按经验阈值给出，还没有用到期结果检验过。");
  return parts.join("");
}

/* ----------------------------------------------------------------- jobs */

function jobState(job) {
  if (job.status === "succeeded") return showState("ok", job.status_label);
  if (job.status === "failed") return showState("error", job.status_label);
  if (job.status === "interrupted") return showState("stale", job.status_label);
  if (job.status === "running") return showState("run", "运行中");
  return showState("missing", job.status_label);
}

async function viewJobs(host) {
  const mine = generation;
  const jobs = (await load("/api/jobs", mine)).jobs;
  runningJobs = jobs.filter((job) => job.status === "running").length;
  renderNav();
  const content = jobs.length
    ? table(
      [{ label: "类型" }, { label: "状态" }, { label: "参数" }, { label: "创建时间" },
        { label: "开始" }, { label: "结束" }, { label: "错误" }, { label: "", end: true }],
      jobs.map((job) => el("tr", {}, [
        el("td", { text: job.kind_label }),
        el("td", {}, jobState(job)),
        el("td", { text: job.parameter_summary }),
        el("td", { text: job.created_at }),
        el("td", { text: job.started_at || "—" }),
        el("td", { text: job.finished_at || "—" }),
        el("td", { class: "reason", text: job.error || "" }),
        el("td", { class: "end" }, arrowLink(`#/jobs/${job.job_id}`, `查看任务：${job.kind_label} ${job.parameter_summary}`)),
      ])))
    : emptyBox("还没有任务。", "在公司页点击生成建议卡，确认后会在这里排队。");
  mount(host,
    pageHead("任务", `共 ${String(jobs.length)} 个任务。同一时间只运行一个分析。`),
    panel("全部任务", content, { count: jobs.length }));
  document.title = "任务｜Thesis Tracker";
}

const TOOL_STATUS = { ok: "成功", error: "失败" };

function stepLine(event, kind) {
  const when = event.at_display ? event.at_display : "";
  if (event.event === "prefetch") {
    return ["准备", `读取本地数据 ${String(event.tool_calls)} 次`, when];
  }
  // Jobs recorded before the usage-carrying event existed still render as text,
  // never as a raw event name.
  if (event.event === "round_start") {
    return ["模型", `第 ${String(event.round)} 轮（旧记录，没有用量）`, when];
  }
  if (event.event === "round") {
    // The usage on this event is what the round actually spent, plus the
    // running total, so the list never shows a placeholder zero.
    return ["模型", `第 ${String(event.round)} 轮`
      + `｜本轮输入 ${String(event.round_input_tokens)}｜输出 ${String(event.round_output_tokens)}`
      + `｜累计输入 ${String(event.input_tokens)}｜输出 ${String(event.output_tokens)}`, when];
  }
  if (event.event === "tool_call") {
    const args = event.args ? Object.entries(event.args)
      .map(([key, value]) => `${key} ${String(value)}`).join("，") : "";
    return ["工具", `${event.tool} ${args}｜${String(event.bytes)} 字节｜${TOOL_STATUS[event.status] || event.status}`, when];
  }
  if (event.event === "draft_rejected") {
    return ["校验", `第 ${String(event.attempt)} 稿被拒：${(event.rules || []).join("、")}`, when];
  }
  if (event.event === "passed") {
    // A chat turn publishes an answer; only an analysis archives a card.
    if (kind === "chat_turn") return ["完成", "回答已发布", when];
    return ["完成", `建议卡已生成｜存档编号 ${event.card_id}`, when];
  }
  if (event.event === "rejected") {
    return ["拒绝", `原因：${event.reason_label}${(event.rules || []).length ? `｜规则 ${(event.rules || []).join("、")}` : ""}`, when];
  }
  return ["其他", "这一步没有可显示的说明", when];
}

function stepItem(event, kind) {
  const [label, detail, when] = stepLine(event, kind);
  const outcome = event.event === "passed" ? "done" : event.event === "rejected" ? "bad" : "";
  return el("li", { class: outcome || undefined }, [
    el("span", { class: "kind", text: label }),
    el("span", { class: "detail", text: detail }),
    el("span", { class: "at", text: when }),
  ]);
}

function jobTail(job) {
  const parts = [];
  if (job.error) {
    parts.push(panel("错误", notice("error", "任务失败", job.error)));
  }
  if (job.result && job.result.card_id) {
    parts.push(panel("结果", el("div", { class: "rows" }, [
      copyRow("存档编号", job.result.card_id_short, job.result.card_id),
      row("公司", job.result.ticker),
      row("分析截至日", job.result.as_of),
      row("周期", job.result.horizon),
      job.result.stats
        ? row("模型用量", `输入 ${String(job.result.stats.input_tokens)}｜输出 ${String(job.result.stats.output_tokens)} token`)
        : null,
      el("div", { class: "row" }, [
        el("span", { class: "label", text: "建议卡" }),
        el("span", { class: "value" }, el("a", {
          href: `#/cards/${job.result.card_id}`, text: "打开建议卡" })),
      ]),
    ].filter(Boolean))));
  }
  return parts;
}

async function viewJob(host, jobId, options = {}) {
  const mine = generation;
  const job = await load(`/api/jobs/${encodeURIComponent(jobId)}`, mine);
  const live = job.status === "running" || job.status === "queued";
  const steps = options.poll && host.dataset.job === jobId ? host.querySelector("#job-steps") : null;
  if (steps) {
    // A refresh of the page already on screen adds only the new steps and swaps
    // the status, so the live list is never rebuilt (and re-announced) wholesale.
    for (const event of job.progress.slice(steps.children.length)) steps.append(stepItem(event, job.kind));
    const empty = host.querySelector("#job-empty");
    if (empty && job.progress.length) empty.remove();
    steps.closest(".panel").querySelector(".count-tag").textContent = String(job.progress.length);
    host.querySelector("#job-state").replaceChildren(jobState(job));
    host.querySelector("#job-tail").replaceChildren(...jobTail(job));
    const note = host.querySelector("#job-running");
    if (note && !live) note.remove();
  } else {
    host.dataset.job = jobId;
    const list = el("ol", { class: "steps", id: "job-steps", "aria-live": "polite" },
      job.progress.map((event) => stepItem(event, job.kind)));
    mount(host,
      pageHead(job.kind_label, [el("span", { id: "job-state" }, jobState(job)),
        el("span", { text: `　${job.parameter_summary}` })]),
      panel("概览", el("div", { class: "rows" }, [
        copyRow("任务编号", job.job_id_short, job.job_id),
        row("创建时间", job.created_at),
        row("开始", job.started_at || "—"),
        row("结束", job.finished_at || "—"),
      ])),
      panel("进度", [
        job.progress.length ? null : el("p", { class: "reason", id: "job-empty", text: "还没有进度事件。" }),
        list,
        live ? el("p", { class: "running-note", id: "job-running" },
          showState("run", "正在运行，步骤会自动出现在这里")) : null,
      ], { count: job.progress.length }),
      el("div", { id: "job-tail", class: "stack" }, jobTail(job)));
  }
  if (live) pollAgain(1500, mine);
  document.title = "任务｜Thesis Tracker";
}

/* ---------------------------------------------------------------- chat */

/* A conversation is a reading column beside the same evidence panel the card
   page uses, so a reader learns one layout.  Nothing here formats a number:
   每一段可见文字都来自服务端。 */

function chatSegments(nodes, pick, facts) {
  return (nodes || []).map((segment) => {
    if (segment.type === "text") return document.createTextNode(segment.visible);
    if (segment.type === "card_link") {
      return el("a", { class: "card-link", href: segment.href, text: segment.visible });
    }
    const info = facts.get(segment.fact_id);
    const computed = Boolean(info) && info.category === "derived";
    const button = el("button", {
      type: "button", class: `fact-ref ${computed ? "computed" : ""}`.trim(), translate: false,
      title: computed
        ? `计算结果｜${info.formula_text || ""}`
        : `来源 ${segment.fact_id || segment.card_id || ""}`,
      text: segment.visible,
    });
    button.addEventListener("click", () => pick(segment));
    return button;
  });
}

function toolPanel(view) {
  if (!view.tool_summary) return null;
  return el("details", { class: "tools" }, [
    el("summary", { text: `${view.tool_summary.label}｜共 ${view.tool_summary.bytes} 字节` }),
    el("ul", { class: "tool-list" }, view.tool_calls.map((call) => el("li", {}, [
      el("span", { class: "mono", translate: false, text: call.tool }),
      el("span", { text: `（${call.args_text}）` }),
      el("span", { class: "tool-status", text: `${call.status_label}｜${call.bytes_text}` }),
      call.reason ? el("span", { class: "explain", text: call.reason }) : null,
    ]))),
  ]);
}

function refusalBlock(rejected) {
  return el("div", { class: "refusal", role: "status" }, [
    el("p", { class: "refusal-head", text: rejected.headline }),
    el("p", { class: "section-note", text:
      `模型连续 ${String(rejected.attempt_count)} 稿都没有通过校验；`
      + "草稿没有显示，下面是最后一稿的违规项。" }),
    el("ul", { class: "violations" }, rejected.violations.map((violation) => el("li", {}, [
      el("span", { class: "rule", text: `${violation.rule} ${violation.location}` }),
      el("span", { text: `：${violation.message}` }),
    ]))),
  ]);
}

function messageBlock(view, pick, facts) {
  const body = [];
  if (view.rejected) {
    body.push(refusalBlock(view.rejected));
  } else if (view.segments) {
    body.push(el("p", { class: "message-text" }, chatSegments(view.segments, pick, facts)));
  } else {
    body.push(el("p", { class: "message-text", text: view.text || "" }));
  }
  if (view.tool_summary) body.push(toolPanel(view));
  if (view.usage_line) body.push(el("p", { class: "usage-line", text: view.usage_line }));
  return el("article", { class: `message ${view.role}`, "data-message-id": view.message_id }, [
    el("header", { class: "message-head" }, [
      el("span", { class: "who", text: view.role_label }),
      el("span", { class: "when", text: view.created_at }),
    ]),
    ...body,
  ]);
}

/* A proposal that has been decided stays on the page, quietly: the reader should
   be able to see what they chose and, for a confirmed one, where the analysis is. */
function decidedProposal(proposal) {
  const where = proposal.job_id
    ? el("a", { href: `#/jobs/${proposal.job_id}`,
      text: `任务${proposal.job_status_label ? `：${proposal.job_status_label}` : ""}` })
    : null;
  return el("section", { class: "proposal decided", "aria-label": "生成建议卡的提议" }, [
    el("p", { class: "rows-line" }, [
      el("span", { class: "who", text: `已处理：${proposal.status_label}` }),
      el("span", { text: `｜周期 ${proposal.horizon_label}｜${proposal.reason}` }),
    ]),
    where ? el("p", { class: "section-note" }, where) : null,
  ]);
}

function proposalPanel(proposal) {
  if (!proposal) return null;
  if (!proposal.confirmable) return decidedProposal(proposal);
  const confirm = el("button", { type: "button", class: "primary", text: "确认生成" });
  const dismiss = el("button", { type: "button", text: "忽略" });
  const box = el("section", { class: "proposal", "aria-label": "生成建议卡的提议" }, [
    el("h2", { text: "AI 提议生成新的建议卡" }),
    el("p", { class: "rows-line", text: `周期 ${proposal.horizon_label}｜${proposal.reason}` }),
    el("p", { class: "section-note", text: proposal.estimate }),
    el("div", { class: "proposal-actions" }, [confirm, dismiss]),
  ]);
  const decide = (path) => async () => {
    confirm.disabled = true;
    dismiss.disabled = true;
    try {
      await api(path, { body: { proposal_id: proposal.proposal_id } });
      await route({ poll: true });
    } catch (error) {
      confirm.disabled = false;
      dismiss.disabled = false;
      box.append(errorBox(error.message));
    }
  };
  confirm.addEventListener("click", decide("/api/proposals/confirm"));
  dismiss.addEventListener("click", decide("/api/proposals/dismiss"));
  return box;
}

function usageTable(usage) {
  const order = ["message", "conversation", "company", "today"];
  return el("div", { class: "table-scroll" }, el("table", { class: "list usage-table" }, [
    el("thead", {}, el("tr", {}, [
      el("th", { scope: "col", text: "范围" }),
      el("th", { scope: "col", class: "num", text: "输入" }),
      el("th", { scope: "col", class: "num", text: "输出" }),
      el("th", { scope: "col", class: "num", text: "缓存命中" }),
      el("th", { scope: "col", text: "费用" }),
    ])),
    el("tbody", {}, order.filter((key) => usage.levels[key]).map((key) => {
      const level = usage.levels[key];
      const same = key === "message" && usage.levels.message.input_tokens === 0
        && usage.levels.message.output_tokens === 0;
      return same ? null : el("tr", {}, [
        el("th", { scope: "row", text: level.scope }),
        el("td", { class: "num", text: String(level.input_tokens) }),
        el("td", { class: "num", text: String(level.output_tokens) }),
        el("td", { class: "num", text: String(level.cache_hit_tokens) }),
        el("td", { class: "cost", text: level.cost_text || "—" }),
      ]);
    })),
  ]));
}

function composer(startup, conversationId, turn) {
  const input = el("textarea", {
    class: "chat-input", rows: "2",
    placeholder: "问这家公司的问题，例如：现在距离止损还有多远？",
    "aria-label": "向这家公司提问",
  });
  const submit = el("button", { type: "button", class: "primary", text: "发送" });
  // While a turn is in flight the server refuses a second question, so the form
  // says why it is waiting instead of looking broken.
  const waiting = turn.in_flight;
  if (waiting) {
    input.disabled = true;
    submit.disabled = true;
  }
  const idleNote = `${startup.billing_note}｜每条最多 ${String(startup.max_message_chars)} 字｜Enter 发送，Shift+Enter 换行`;
  const status = el("p", { class: "composer-note", role: "status",
    text: waiting ? `${turn.label}…` : idleNote });
  const form = el("div", { class: "composer" }, [
    input,
    el("div", { class: "composer-row" }, [status, el("span", { class: "spacer" }), submit]),
  ]);
  const send = async () => {
    const text = input.value.trim();
    if (!text || submit.disabled) return;
    submit.disabled = true;
    input.disabled = true;
    status.textContent = "已发送，正在回答…";
    try {
      await api(`/api/conversations/${encodeURIComponent(conversationId)}/messages`, {
        body: { text },
      });
      input.value = "";
      await route({ poll: true });
    } catch (error) {
      submit.disabled = false;
      input.disabled = false;
      status.textContent = idleNote;
      form.append(errorBox(error.message));
    }
  };
  submit.addEventListener("click", send);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  });
  return form;
}

async function viewChat(host, conversationId) {
  const mine = generation;
  const page = await load(`/api/conversations/${encodeURIComponent(conversationId)}`, mine);
  const rowsMap = new Map();
  const highlight = highlighter(rowsMap);
  const pick = (segment) => {
    // A card field's evidence row is keyed card|<card id>|<field>, the same id the
    // backend gave it; looking it up by card alone found nothing.
    highlight(segment.fact_id || `card|${segment.card_id}|${segment.field}`);
  };
  const facts = new Map();
  for (const group of page.evidence.groups) for (const fact of group.facts) facts.set(fact.fact_id, fact);

  const startup = page.startup;
  const thread = el("div", { class: "thread", role: "log", "aria-label": "对话内容" }, [
    el("p", { class: "section-note", text: startup.data_note }),
    ...(page.messages.length
      ? page.messages.map((message) => messageBlock(message, pick, facts))
      : [emptyBox("还没有对话内容。", "下面是几个可以直接点的问题。")]),
    page.messages.length
      ? null
      : el("div", { class: "chips" }, startup.hint_chips.map((chip) => {
        const button = el("button", { type: "button", class: "chip", text: chip });
        button.addEventListener("click", () => {
          const input = host.querySelector(".chat-input");
          if (!input) return;
          input.value = chip;
          input.focus();
        });
        return button;
      })),
    ...page.proposals.map((proposal) => proposalPanel(proposal)),
    page.turn.error ? notice("error", "没有得到回答", page.turn.error) : null,
    page.messages.length < startup.history_window
      ? null
      : el("p", { class: "section-note", text: page.history.note }),
  ]);
  const dock = el("div", { class: "composer-dock" }, [
    composer(startup, conversationId, page.turn),
    el("div", { class: "usage-bar" }, [
      el("span", { text: page.usage.summary_line }),
      el("details", {}, [
        el("summary", { text: "明细" }),
        usageTable(page.usage),
        el("p", { class: "section-note", text: page.usage.price_note }),
      ]),
    ]),
  ]);

  const evidenceBody = el("div", { class: "evidence-scroll" },
    page.evidence.groups.length
      ? evidenceRows(page.evidence.groups, rowsMap, { counts: true })
      : [el("p", { class: "section-note", text: "这个对话还没有读过任何数据。" })]);

  const pinned = page.pinned_card_id
    ? el("span", {}, ["　从这张卡开始：", el("a", { href: `#/cards/${page.pinned_card_id}`, text: page.pinned_card_id_short })])
    : null;
  mount(host,
    pageHead(`${page.ticker} 对话`, [
      el("a", { class: "back", href: `#/company/${page.ticker}`, text: `返回 ${page.ticker}` }),
      el("span", { text: `　创建于 ${page.created_at}｜最近活动 ${page.last_activity_at}` }),
      pinned,
    ], null, { translate: false }),
    el("div", { class: "card-layout chat-layout" }, [
      el("div", { class: "reading" }, [thread, dock]),
      el("aside", { class: "evidence", "aria-label": "本对话的证据" }, [
        el("h2", { text: `证据（${String(page.evidence.count)} 条）` }),
        evidenceBody,
      ]),
    ]));
  document.title = `${page.ticker} 对话｜Thesis Tracker`;
  // A confirmed proposal's analysis finishes on its own schedule; keep looking
  // until it has been announced in the conversation.
  const analysing = page.proposals.some((proposal) => proposal.status === "confirmed"
    && !proposal.card_id && ["queued", "running"].includes(proposal.job_status));
  if (page.turn.in_flight || analysing) pollAgain(1500, mine);
}

/* -------------------------------------------------------------- routing */

function showError(host) {
  return (error) => mount(host, errorBox(error.message));
}

async function route(options = {}) {
  generation += 1;
  stopPolling();
  const host = document.getElementById("main");
  const raw = location.hash.replace(/^#/, "") || "/overview";
  const [path, query] = raw.split("?");
  const [, head, tail] = path.split("/");
  if (path === "/jobs" || head === "jobs") renderNav("#/jobs");
  else if (head === "cards") renderNav("#/cards");
  else renderNav("#/overview");
  if (!options.poll) refreshPriceStatus();
  const known = (head === undefined || head === "" || head === "overview"
    || ((head === "company" || head === "chat") && tail)
    || head === "cards" || head === "jobs");
  try {
    if (!known) throw new Error("没有这个页面。地址可能输错了，可以回到总览。");
    if (head === "company" && tail) await viewCompany(host, decodeURIComponent(tail));
    else if (head === "cards" && tail) await viewCard(host, decodeURIComponent(tail));
    else if (head === "cards") await viewCards(host, query);
    else if (head === "chat" && tail) await viewChat(host, decodeURIComponent(tail));
    else if (head === "jobs" && tail) await viewJob(host, decodeURIComponent(tail), options);
    else if (head === "jobs") await viewJobs(host);
    else await viewOverview(host);
  } catch (error) {
    // A navigation that overtook this one is not an error, just a view the reader
    // no longer wants.
    if (error !== STALE) showError(host)(error);
  }
  if (options.navigated) {
    // A new page starts at its top, and a screen reader lands on its heading.
    window.scrollTo(0, 0);
    const heading = host.querySelector("h1");
    if (heading) {
      heading.setAttribute("tabindex", "-1");
      heading.focus({ preventScroll: true });
    }
  }
}

window.addEventListener("hashchange", () => route({ navigated: true }));
if (document.readyState === "loading") {
  window.addEventListener("DOMContentLoaded", () => { renderNav(); route(); });
} else {
  renderNav();
  route();
}
