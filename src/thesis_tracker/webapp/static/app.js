/* Thesis Tracker local workbench.
 *
 * Hard rule for this file: the frontend never computes, rounds, rescales or
 * unit-formats a financial number.  Every price, metric, percentage, position
 * and label arrives from the backend as a string and is rendered as text.
 * Allowed number work is limited to non-financial presentation facts such as
 * how many filings a cell holds.
 */

const HORIZONS = [
  { key: "short", label: "短期" },
  { key: "mid", label: "中期" },
  { key: "long", label: "长期" },
];

const HORIZON_LABEL = Object.fromEntries(HORIZONS.map((item) => [item.key, item.label]));

/* ------------------------------------------------------------------ dom */

function el(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  const { text, class: className, translate, ...rest } = options;
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  if (translate === false) node.setAttribute("translate", "no");
  for (const [key, value] of Object.entries(rest)) {
    if (value === undefined || value === null || value === false) continue;
    node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of [].concat(children)) {
    if (child !== null && child !== undefined) node.append(child);
  }
  return node;
}

function clear(node) {
  node.replaceChildren();
  return node;
}

function splitUnit(display) {
  // The backend already decided the number and the unit text; the page only
  // gives the unit its smaller, quieter style.
  if (typeof display !== "string") return { number: "—", unit: "" };
  const match = display.match(/^([\d.,\u2212+-]+)\s*(.*)$/);
  if (!match) return { number: display, unit: "" };
  return { number: match[1], unit: match[2] };
}

function quantity(display) {
  const parts = splitUnit(display);
  return [parts.number, parts.unit ? el("span", { class: "unit", text: parts.unit }) : null];
}

const STATES = {
  ok: { shape: "shape-solid", label: "正常" },
  stale: { shape: "shape-clock", label: "已过期" },
  error: { shape: "shape-cross", label: "出错" },
  missing: { shape: "shape-open", label: "缺失" },
};

function showState(kind, text) {
  const spec = STATES[kind];
  const classes = { ok: "state state-ok", stale: "state state-warn",
    error: "state state-bad", missing: "state state-none" };
  return el("span", { class: classes[kind] }, [
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

function errorBox(message) {
  return el("div", { class: "error", role: "alert" }, [
    showState("error", "请求失败"),
    el("p", { text: message }),
  ]);
}

function loadingBox() {
  return el("div", { class: "loading" }, [
    el("div", { class: "skeleton w60" }),
    el("div", { class: "skeleton w80" }),
    el("div", { class: "skeleton w60" }),
    el("p", { text: "正在读取本地数据库。" }),
  ]);
}

/* ------------------------------------------------------------------ api */

async function api(path, options = {}) {
  const init = { credentials: "same-origin", headers: { Accept: "application/json" } };
  if (options.body !== undefined) {
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

/* ------------------------------------------------------------- fragments */

function pageHead(title, meta) {
  return el("div", { class: "page-head" }, [
    el("h1", { text: title }),
    meta ? el("p", { class: "meta", text: meta }) : null,
  ]);
}

function panelSection(title, children, note) {
  return el("section", { class: "section" }, [
    el("h2", { text: title }),
    note ? el("p", { class: "section-note", text: note }) : null,
    ...[].concat(children),
  ]);
}

function table(headers, rows, className = "list") {
  const head = el("tr", {}, headers.map((header) => el("th", {
    scope: "col", class: header.numeric ? "num" : undefined, text: header.label,
  })));
  return el("div", { class: "table-scroll" }, [
    el("table", { class: className }, [
      el("thead", {}, head),
      el("tbody", {}, rows),
    ]),
  ]);
}

function emptyBox(title, hint) {
  return el("div", { class: "empty" }, [
    el("p", { text: title }),
    hint ? el("p", { text: hint }) : null,
  ]);
}

function factMarker(segment, onPick) {
  const button = el("button", {
    type: "button", class: "fact-ref", translate: false,
    title: `事实 ${segment.fact_id}`,
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

/* ------------------------------------------------------- price banner */

function priceBanner(overview) {
  const prices = Object.values(overview.companies).map((company) => company.price);
  const stale = prices.filter((price) => price.stale);
  if (stale.length === 0) return null;
  const dated = stale.filter((price) => price.end_date).map((price) => price.end_date);
  const newest = dated.length ? dated.reduce((a, b) => (a > b ? a : b)) : null;
  const missing = stale.length - dated.length;

  const detail = newest ? `价格数据已过期（最新 ${newest}）` : "价格数据缺失";
  const hint = newest
    ? `在终端运行 ${overview.price_command}，需要运行两次。`
    : `${String(missing)} 家公司没有价格数据；在终端运行 ${overview.price_command}，需要运行两次。`;
  const extra = newest && missing ? `另有 ${String(missing)} 家公司没有价格数据。` : "";

  const status = el("span", { class: "meta", text: "" });
  const button = el("button", { type: "button", class: "small", text: "复制命令" });
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(overview.price_command);
      status.textContent = "命令已复制。";
    } catch {
      status.textContent = `无法自动复制，请手动输入：${overview.price_command}`;
    }
  });

  return el("div", { class: "banner", role: "status" }, [
    showState("stale", detail),
    el("code", { class: "command", text: overview.price_command }),
    button,
    el("span", { class: "section-note", text: hint }),
    extra ? el("span", { class: "section-note", text: extra }) : null,
    status,
  ]);
}

/* ------------------------------------------------------------- overview */

async function viewOverview(host) {
  const overview = await api("/api/overview");
  clear(host);
  const banner = priceBanner(overview);
  if (banner) host.append(banner);

  const staleCount = Object.values(overview.companies)
    .filter((company) => company.price.stale).length;
  host.append(pageHead(
    "总览",
    `数据库里 ${String(Object.keys(overview.companies).length)} 家公司｜`
    + `数据截至 ${overview.reference_date}｜价格过期阈值 ${String(overview.stale_after_days)} 天`
    + (staleCount ? `｜${String(staleCount)} 家价格已过期` : ""),
  ));

  const quarters = overview.quarters;
  const years = [];
  for (const key of quarters) {
    const year = key.slice(0, 4);
    if (!years.length || years[years.length - 1].year !== year) years.push({ year, quarters: [] });
    years[years.length - 1].quarters.push(key);
  }

  // Two header rows: the year is written once per year, the quarter under it.
  const yearCells = [el("th", { scope: "col", class: "stick", rowspan: "2", text: "公司" })];
  const quarterCells = [];
  for (const group of years) {
    group.quarters.forEach((key, index) => {
      const classes = index === 0 ? "year-start" : "";
      if (index === 0) {
        yearCells.push(el("th", {
          scope: "colgroup", colspan: String(group.quarters.length), class: `qhead ${classes}`,
          title: `${group.year} 年（按财期截止日的日历季度）`,
        }, el("span", { class: "year", text: group.year })));
      }
      quarterCells.push(el("th", {
        scope: "col", class: `quarter-row ${classes}`,
        title: `${group.year} 年 ${key.slice(5)} 季度（按财期截止日的日历季度）`,
      }, el("span", { class: "quarter", text: key.slice(5) })));
    });
  }
  yearCells.push(el("th", { scope: "col", class: "tail", rowspan: "2", text: "价格数据范围" }));
  yearCells.push(el("th", { scope: "col", class: "tail", rowspan: "2", text: "最新价格日" }));
  yearCells.push(el("th", { scope: "col", class: "tail num", rowspan: "2", text: "建议卡" }));
  // The summary columns have no calendar-quarter cell, so the quarter row ends
  // with one empty cell per pinned column, which keeps the columns aligned.
  const trailing = ["", "", ""].map(() => el("th", { scope: "col", class: "tail", "aria-hidden": "true" }));

  const body = [];
  for (const [ticker, company] of Object.entries(overview.companies)) {
    const cells = [el("th", { scope: "row", class: "stick" }, [
      el("a", { href: `#/company/${ticker}`, class: "ticker", translate: false, text: ticker }),
    ])];
    for (const group of years) {
      group.quarters.forEach((key, index) => {
        const classes = ["cell"];
        if (index === 0) classes.push("year-start");
        cells.push(renderCell(company.periods[key], classes.join(" "), ticker));
      });
    }
    const price = company.price;
    const range = price.start_date ? `${price.start_date} 至 ${price.end_date}` : "没有价格数据";
    cells.push(el("td", { class: "tail", text: range }));
    cells.push(el("td", { class: "tail", text: price.end_date || "—" }));
    cells.push(el("td", { class: "tail num", text: String(company.card_count) }));
    body.push(el("tr", {}, cells));
  }

  host.append(el("section", { class: "section" }, [
    el("div", { class: "toolbar" }, [
      el("h2", { text: `财报覆盖（${Object.keys(overview.companies).length} 家公司）` }),
      el("span", { class: "spacer" }),
      el("span", {
        class: "section-note",
        text: `按财期截止日所在的日历季度分列；数据截至 ${overview.reference_date}`,
      }),
    ]),
    el("div", { class: "table-scroll" }, [
      el("table", { class: "coverage" }, [
        el("thead", {}, [el("tr", {}, yearCells),
                         el("tr", {}, [...quarterCells, ...trailing])]),
        el("tbody", {}, body),
      ]),
    ]),
  ]));
  document.title = "总览｜Thesis Tracker";
}

function renderCell(period, className, ticker) {
  if (!period) {
    return el("td", { class: className, title: "这一季度没有已存档的财报" }, [
      el("span", { class: "is-missing" }, el("span", { "aria-hidden": "true" })),
    ]);
  }
  const entries = period.entries || [];
  const items = entries.map((entry) => {
    const letter = entry.key_form === "10-K" ? "K" : entry.key_form === "10-Q" ? "Q" : entry.key_form;
    const detail = [
      entry.form,
      `财期截止 ${entry.period_end}`,
      `披露日 ${entry.filed_at}`,
      `accession ${entry.accession}`,
      entry.is_amendment ? "修订申报" : null,
    ].filter(Boolean).join("\n");
    return el("li", { class: entry.is_amendment ? "amended" : undefined, title: detail, text: letter });
  });
  const summary = entries.map((entry) => entry.form).join(" + ");
  return el("td", {
    class: className,
    title: `${ticker}\n${summary}\n财期截止 ${period.period_end}\n披露日 ${period.filed_at}\naccession ${period.accession}`,
  }, el("ul", {}, items));
}

/* -------------------------------------------------------------- company */

async function viewCompany(host, ticker) {
  const page = await api(`/api/companies/${encodeURIComponent(ticker)}`);
  clear(host);
  const price = page.price;
  host.append(pageHead(
    page.ticker,
    `CIK ${page.cik}｜分析截至日 ${page.as_of}`,
  ));
  const priceState = price.available
    ? (price.stale
      ? showState("stale", `价格已过期（最新 ${price.end_date}，落后 ${String(price.age_days)} 天）`)
      : showState("ok", `价格有效（最新 ${price.end_date}）`))
    : showState("missing", price.reason === "mixed_batches" ? "价格批次不一致" : "没有价格数据");
  host.append(el("div", { class: "toolbar" }, [
    priceState,
    el("span", { class: "spacer" }),
    analysisButton(page.ticker, host),
  ]));
  if (price.stale || !price.available) {
    host.append(el("div", { class: "banner", role: "status" }, [
      el("span", { class: "section-note", text: `在终端运行 ${page.price_command}，需要运行两次。` }),
    ]));
  }

  host.append(panelSection("财报", table(
    [{ label: "表单" }, { label: "财期截止" }, { label: "披露日" }, { label: "accession" },
      { label: "财年" }, { label: "SEC 财期" }],
    page.filings.map((filing) => el("tr", {}, [
      el("td", { text: filing.is_amendment ? `${filing.form}（修订）` : filing.form }),
      el("td", { text: filing.period_end }),
      el("td", { text: filing.filed_at }),
      el("td", { class: "mono", translate: false, text: filing.accession }),
      el("td", { class: "num", text: String(filing.fiscal_year) }),
      el("td", { text: filing.fiscal_period }),
    ])),
  ), "SEC 财期是申报原始字段，仅作对照；页面与接口不按它分列。"));

  const window = price.windows && price.windows.length ? price.windows[0] : null;
  host.append(panelSection("价格", el("div", { class: "rows" }, [
    row("数据范围", price.start_date ? `${price.start_date} 至 ${price.end_date}` : "没有价格数据"),
    row("行数", price.available ? String(price.rows) : "—"),
    row("最新收盘价", price.latest_close, true),
    row("抓取时间", window ? `${window.retrieved_at}（${window.provider}）` : "—"),
    row("过期阈值", `${String(page.stale_after_days)} 个日历日（与校验器 D03 同一常量）`),
  ])));

  const metrics = page.fundamentals;
  const metricRows = metrics.metrics.map((metric) => el("tr", {}, [
    el("th", { scope: "row", text: metric.label }),
    el("td", { class: "num" }, quantity(metric.display)),
    el("td", { class: "num", text: metric.period_end || "—" }),
    el("td", { text: metric.fact_id ? metric.fact_id.slice(0, 8) : "—", class: "mono", translate: false }),
    el("td", {}, statusState(metric.status)),
    el("td", { class: "reason", text: metric.reason ? metric.reason.message : "" }),
  ]));
  host.append(panelSection("可算的财务指标", table(
    [{ label: "指标" }, { label: "最新值", numeric: true }, { label: "财期截止", numeric: true },
      { label: "事实编号" }, { label: "状态" }, { label: "算不出的原因" }],
    metricRows,
  ), `走现有 get_fundamental_metrics 只读路径；数据截至 ${metrics.data_end_date || "—"}。`));

  const cards = page.cards;
  host.append(panelSection("建议卡", cards.length
    ? table([{ label: "创建时间" }, { label: "周期" }, { label: "动作" }, { label: "倾向" },
      { label: "校验器" }, { label: "提示词版本" }, { label: "" }],
    cards.map((card) => el("tr", {}, [
      el("td", { text: card.created_at }),
      el("td", { text: card.horizon }),
      el("td", { text: card.action }),
      el("td", { text: card.bias }),
      el("td", { class: "mono", text: card.validator_version }),
      el("td", { class: "mono", text: card.prompt_version }),
      el("td", {}, el("a", { href: `#/cards/${card.card_id}`, text: "查看" })),
    ])))
    : emptyBox("还没有这家公司的建议卡。", "选择周期后点击生成建议卡。")));
  document.title = `${page.ticker}｜Thesis Tracker`;
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
    el("div", {}, [el("label", { for: "analyze-horizon", text: "周期" }), horizonSelect]),
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
  const params = new URLSearchParams(query || "");
  const ticker = params.get("ticker") || "";
  const horizon = params.get("horizon") || "";
  const cards = (await api(`/api/cards${query ? `?${query}` : ""}`)).cards;
  clear(host);
  host.append(pageHead("建议卡", `共 ${String(cards.length)} 张`));

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
  host.append(el("div", { class: "toolbar" }, [
    el("label", { for: "card-ticker", text: "公司" }), tickerInput,
    el("label", { for: "card-horizon", text: "周期" }), horizonSelect, apply,
  ]));

  host.append(cards.length
    ? table([{ label: "公司" }, { label: "周期" }, { label: "动作" }, { label: "倾向" },
      { label: "创建时价格", numeric: true }, { label: "创建时间" },
      { label: "校验器" }, { label: "提示词版本" }, { label: "" }],
    cards.map((card) => el("tr", {}, [
      el("td", {}, el("a", { href: `#/company/${card.ticker}`, translate: false, text: card.ticker })),
      el("td", { text: card.horizon }),
      el("td", { text: card.action }),
      el("td", { text: card.bias }),
      el("td", { class: "num" }, quantity(card.creation_price)),
      el("td", { text: card.created_at }),
      el("td", { class: "mono", text: card.validator_version }),
      el("td", { class: "mono", text: card.prompt_version }),
      el("td", {}, el("a", { href: `#/cards/${card.card_id}`, text: "查看" })),
    ])))
    : emptyBox("还没有符合条件的建议卡。", "在公司页选择周期后点击生成建议卡。"));
  document.title = "建议卡｜Thesis Tracker";
}

/* ---------------------------------------------------------- card detail */

async function viewCard(host, cardId) {
  const card = await api(`/api/cards/${encodeURIComponent(cardId)}`);
  clear(host);
  const evidenceRows = new Map();
  const pick = (factId) => {
    const row = evidenceRows.get(factId);
    if (!row) return;
    row.scrollIntoView({ block: "center", behavior: "smooth" });
    row.classList.remove("flash");
    void row.offsetWidth;
    row.classList.add("flash");
  };

  const reading = el("div", { class: "reading" }, [
    el("div", { class: "judgment" }, [
      el("p", { class: "bias", text: `${card.bias}｜${card.action}` }),
      el("p", {}, [
        el("span", { class: "chip", text: `置信度 ${card.confidence}（${card.confidence_calibration}）` }),
        el("span", { class: "chip", text: card.horizon }),
      ]),
      card.price_data_end
        ? el("p", { class: "meta", text: `价格数据截至 ${card.price_data_end}` })
        : null,
      priceLine(card),
    ]),
    card.price_band ? priceBand(card.price_band) : el("p", { class: "section-note", text: "此动作没有价位" }),
    block("理由", card.reasons.map((segmentsList) =>
      el("p", { class: "entry" }, segments(segmentsList, pick)))),
    card.stop_rationale.length
      ? block("止损依据", el("p", { class: "entry" }, segments(card.stop_rationale, pick)))
      : null,
    card.target_rationale.length
      ? block("目标依据", el("p", { class: "entry" }, segments(card.target_rationale, pick)))
      : null,
    block("失效条件", card.invalidations.map((item) => el("div", { class: "entry" }, [
      el("p", { class: "machine" }, segments(item.machine_check, pick)),
      el("p", { class: "explain" }, segments(item.explanation, pick)),
    ]))),
    block("自动计算（Python）", table([{ label: "项目" }, { label: "结果" }],
      card.auto_computed.map((item) => el("tr", {}, [
        el("th", { scope: "row", text: item.label }),
        el("td", { class: "num", text: item.text }),
      ]))), "只做显示，不参与校验。"),
    block("数据缺口", card.gaps.length
      ? table([{ label: "项目" }, { label: "状态" }, { label: "原因" }],
        card.gaps.map((gap) => el("tr", {}, [
          el("th", { scope: "row", text: gap.label }),
          el("td", {}, statusState(gap.status)),
          el("td", { class: "reason", text: gap.reason ? gap.reason.message : "" }),
        ])))
      : el("p", { class: "section-note", text: "没有数据缺口。" })),
    block("尝试记录", card.attempts.map((attempt) => el("div", { class: "entry" }, [
      el("p", { text: `第 ${String(attempt.attempt_no)} 稿｜${attempt.requested_horizon || "—"}｜${attempt.created_at}` }),
      el("p", { class: "explain", text: attempt.headline }),
      attempt.violations.length
        ? el("ul", { class: "violations" }, attempt.violations.map((violation) => el("li", {}, [
          el("span", { class: "rule", text: `${violation.rule} ${violation.location}` }),
          el("span", { text: `：${violation.message}` }),
        ])))
        : null,
    ]))),
    el("p", { class: "disclaimer", text: card.disclaimer }),
    el("p", { class: "versions", text:
      `提示词版本 ${card.versions.prompt_version || "—"}｜校验器版本 ${card.versions.validator_version}`
      + `｜请求模型 ${card.versions.requested_model || "—"}｜返回模型 ${card.versions.returned_model || "—"}` }),
  ]);

  const factsTable = el("table", { class: "evidence-table" }, [
    el("colgroup", {}, [
      el("col", { class: "c-fact" }), el("col", { class: "c-value" }),
      el("col", { class: "c-date" }), el("col", { class: "c-src" }),
    ]),
    el("thead", {}, el("tr", {}, [
      el("th", { scope: "col", text: "事实" }),
      el("th", { scope: "col", text: "值" }),
      el("th", { scope: "col", text: "日期或财期" }),
      el("th", { scope: "col", text: "来源" }),
    ])),
    el("tbody", {}, card.facts.map((fact) => {
      const tr = el("tr", { id: `fact-${fact.fact_id}` }, [
        el("th", { scope: "row" }, [
          el("span", { text: fact.label }),
          el("br"),
          // The full identifier is long; the row keeps a short form and the
          // complete id stays available in the title and in the highlight key.
          el("span", { class: "fact-id src", translate: false, title: fact.fact_id,
            text: `${fact.fact_id.slice(0, 12)}…${fact.fact_id.slice(-6)}` }),
        ]),
        el("td", { class: "num", text: fact.display }),
        el("td", { text: fact.date_or_period || "—" }),
        el("td", { class: "src", text: fact.provider || fact.formula || "—" }),
      ]);
      evidenceRows.set(fact.fact_id, tr);
      return tr;
    })),
  ]);

  host.append(
    pageHead(`${card.ticker} 建议卡`, `${card.as_of}｜创建于 ${card.created_at}`),
    el("div", { class: "card-layout" }, [
      reading,
      el("aside", { class: "evidence", "aria-label": "事实证据" }, [
        el("h2", { text: `事实（${String(card.facts.length)} 条）` }),
        el("div", { class: "table-scroll" }, factsTable),
      ]),
    ]),
  );
  document.title = `${card.ticker} ${card.as_of}｜建议卡`;
}

function priceLine(card) {
  const parts = [el("span", { text: "买点 " })];
  if (card.entry_range) {
    parts.push(...quantity(card.entry_range[0]));
    parts.push(el("span", { text: " 至 " }));
    parts.push(...quantity(card.entry_range[1]));
  } else {
    parts.push(el("span", { text: "无" }));
  }
  parts.push(el("span", { text: "｜止损 " }));
  parts.push(...(card.stop_loss ? quantity(card.stop_loss) : [el("span", { text: "无" })]));
  parts.push(el("span", { text: "｜目标 " }));
  parts.push(...(card.target_price ? quantity(card.target_price) : [el("span", { text: "无" })]));
  return el("p", { class: "prices" }, parts);
}

function block(title, children, note) {
  return el("section", { class: "block" }, [
    el("h2", { text: title }),
    note ? el("p", { class: "section-note", text: note }) : null,
    ...[].concat(children),
  ]);
}

/* The axis is inset by 4.5em on each side (see --band-inset), so a marker at
 * 0% or 100% still has room for its label inside the column.  Both the
 * percentage and the 0-1 fraction are computed by the backend; this only turns
 * the fraction into a CSS length. */
function markerOffset(fraction) {
  return `left:calc(4.5em + (100% - 9em) * ${fraction})`;
}

function priceBand(band) {
  const marks = band.markers;
  const entry = marks.filter((mark) => mark.key.startsWith("entry_"));
  // The plot's height and the stacked label rows are the backend's geometry, in
  // pixels; the label line box is fixed in CSS so the two agree exactly.
  const plot = el("div", { class: "band-plot",
    style: `height:${band.plot_height_px}px` }, [
    el("div", { class: "band-axis" }, [
      entry.length === 2
        ? el("span", {
          class: "band-range",
          style: `${markerOffset(entry[0].fraction)};right:calc(4.5em + (100% - 9em) * ${1 - entry[1].fraction})`,
        })
        : null,
    ]),
    ...marks.map((mark) => el("span", {
      class: `band-mark${mark.key.startsWith("entry_") ? " is-entry" : ""}`,
      style: `${markerOffset(mark.fraction)};--label-offset:${mark.label_offset_px || 0}px`,
    }, [
      // Two block lines, no <br>: the stack's height must be exactly the two
      // 16px line boxes the backend reserved for it.
      el("span", { class: "stack" }, [
        el("span", { class: "name", text: mark.label }),
        el("span", { class: "price", text: mark.value }),
      ]),
      el("span", { class: "tick" }),
    ])),
  ]);
  return el("div", { class: "band" }, [
    el("p", { class: "section-note", text: "价位带（位置由后端按价格算出）" }),
    plot,
  ]);
}

/* ----------------------------------------------------------------- jobs */

let runningJobs = 0;

async function viewJobs(host) {
  const jobs = (await api("/api/jobs")).jobs;
  runningJobs = jobs.filter((job) => job.status === "running").length;
  renderNav();
  clear(host);
  host.append(pageHead("任务", `共 ${String(jobs.length)} 个任务；同一时间只运行一个分析。`));
  if (!jobs.length) {
    host.append(emptyBox("还没有任务。", "在公司页点击生成建议卡，确认后会在这里排队。"));
    return;
  }
  host.append(table(
    [{ label: "类型" }, { label: "状态" }, { label: "参数" }, { label: "创建时间" },
      { label: "开始" }, { label: "结束" }, { label: "错误" }, { label: "" }],
    jobs.map((job) => el("tr", {}, [
      el("td", { text: job.kind_label }),
      el("td", {}, jobState(job)),
      el("td", { class: "mono", translate: false, text: parameterText(job.parameters) }),
      el("td", { text: job.created_at }),
      el("td", { text: job.started_at || "—" }),
      el("td", { text: job.finished_at || "—" }),
      el("td", { class: "reason", text: job.error || "" }),
      el("td", {}, el("a", { href: `#/jobs/${job.job_id}`, text: "查看" })),
    ])),
  ));
  document.title = "任务｜Thesis Tracker";
}

function parameterText(parameters) {
  return Object.entries(parameters).map(([key, value]) => `${key}=${String(value)}`).join(" ");
}

function jobState(job) {
  if (job.status === "succeeded") return showState("ok", job.status_label);
  if (job.status === "failed") return showState("error", job.status_label);
  if (job.status === "interrupted") return showState("stale", job.status_label);
  if (job.status === "running") return showState("stale", "运行中");
  return showState("missing", job.status_label);
}

function stepLine(event) {
  if (event.event === "prefetch") {
    return ["准备", `预取 ${String(event.tool_calls)} 次本地工具，基础包 ${String(event.base_pack_bytes)} 字节`];
  }
  if (event.event === "round_start") {
    return ["请求", `第 ${String(event.round)} 轮｜累计输入 ${String(event.input_tokens)}｜输出 ${String(event.output_tokens)}`];
  }
  if (event.event === "tool_call") {
    return ["工具", `${event.tool} ${event.args ? parameterText(event.args) : ""}｜${String(event.bytes)} 字节｜${event.status}`];
  }
  if (event.event === "draft_rejected") {
    return ["校验", `第 ${String(event.attempt)} 稿被拒：${(event.rules || []).join("、")}`];
  }
  if (event.event === "passed") {
    return ["完成", `建议卡已生成｜存档编号 ${event.card_id}`];
  }
  if (event.event === "rejected") {
    return ["拒绝", `原因 ${event.reason}${(event.rules || []).length ? `｜规则 ${(event.rules || []).join("、")}` : ""}`];
  }
  return ["事件", event.event];
}

async function viewJob(host, jobId) {
  const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
  clear(host);
  host.append(pageHead(job.kind_label, `任务 ${job.job_id}`));
  host.append(el("div", { class: "toolbar" }, [
    jobState(job),
    el("span", { class: "mono", translate: false, text: parameterText(job.parameters) }),
  ]));
  host.append(panelSection("逐步进度", el("div", { class: "panel" }, [
    el("ol", { class: "steps", "aria-live": "polite" }, job.progress.map((event) => {
      const [kind, detail] = stepLine(event);
      return el("li", {}, [
        el("span", { class: "kind", text: kind }),
        el("span", { class: "detail", text: detail }),
      ]);
    })),
    job.progress.length ? null : el("p", { class: "reason", text: "还没有进度事件。" }),
  ]), "不做假进度条：不知道总量时只显示已完成的步骤。"));
  if (job.error) {
    host.append(panelSection("错误", el("div", { class: "error" }, [
      showState("error", "任务失败"), el("p", { text: job.error }),
    ])));
  }
  if (job.result && job.result.card_id) {
    host.append(panelSection("结果", el("div", { class: "rows" }, [
      row("存档编号", job.result.card_id),
      row("公司", job.result.ticker),
      row("分析截至日", job.result.as_of),
      row("周期", job.result.horizon),
      job.result.stats
        ? row("模型用量", `输入 ${String(job.result.stats.input_tokens)}｜输出 ${String(job.result.stats.output_tokens)} token`)
        : null,
      el("div", { class: "row" }, [
        el("span", { class: "label", text: "建议卡" }),
        el("span", { class: "value" }, el("a", { href: `#/cards/${job.result.card_id}`, text: "打开" })),
      ]),
    ].filter(Boolean))));
  }
  if (job.status === "running" || job.status === "queued") {
    setTimeout(() => { if (location.hash === `#/jobs/${jobId}`) viewJob(host, jobId).catch(showError(host)); }, 1500);
  }
  document.title = "任务｜Thesis Tracker";
}

/* -------------------------------------------------------------- routing */

const NAV = [
  { hash: "#/overview", label: "总览" },
  { hash: "#/cards", label: "建议卡" },
  { hash: "#/jobs", label: "任务", badge: true },
];

function renderNav(active) {
  const nav = document.getElementById("nav");
  clear(nav);
  for (const item of NAV) {
    const current = active ? item.hash.startsWith(active) : location.hash.startsWith(item.hash);
    nav.append(el("a", {
      href: item.hash,
      "aria-current": current ? "page" : null,
    }, [
      el("span", { text: item.label }),
      item.badge && runningJobs > 0
        ? el("span", { class: "count", text: `运行中 ${String(runningJobs)}` })
        : null,
    ]));
  }
}

function showError(host) {
  return (error) => {
    clear(host);
    host.append(errorBox(error.message));
  };
}

async function route() {
  const host = document.getElementById("main");
  const raw = location.hash.replace(/^#/, "") || "/overview";
  const [path, query] = raw.split("?");
  const [, head, tail] = path.split("/");
  if (path === "/jobs" || head === "jobs") renderNav("#/jobs");
  else if (head === "cards") renderNav("#/cards");
  else renderNav("#/overview");
  try {
    if (head === "company" && tail) await viewCompany(host, decodeURIComponent(tail));
    else if (head === "cards" && tail) await viewCard(host, decodeURIComponent(tail));
    else if (head === "cards") await viewCards(host, query);
    else if (head === "jobs" && tail) await viewJob(host, decodeURIComponent(tail));
    else if (head === "jobs") await viewJobs(host);
    else await viewOverview(host);
  } catch (error) {
    showError(host)(error);
  }
}

window.addEventListener("hashchange", route);
if (document.readyState === "loading") {
  window.addEventListener("DOMContentLoaded", () => { renderNav(); route(); });
} else {
  renderNav();
  route();
}
