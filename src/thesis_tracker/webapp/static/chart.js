/* The price chart and the overview sparkline.
 *
 * The backend sends every path, every position (as a percentage of the plot box,
 * y measured from the top) and every string a reader sees: hover text, axis
 * labels, level labels.  This file places them.  It computes no price, rounds
 * nothing and formats nothing; the only arithmetic is stepping through the list of
 * points when a reader presses an arrow key.
 */
import { el, svg } from "./dom.js";

function legend(range) {
  const items = range.legend.map((item) => ({ cls: item.key, label: item.label }));
  for (const level of range.levels) items.push({ cls: level.shape, label: level.label });
  if (range.close_marker) items.push({ cls: "close", label: "建卡当天收盘价" });
  return el("ul", { class: "chart-legend" }, items.map((item) => el("li", {}, [
    el("span", { class: `swatch-line ${item.cls}`, "aria-hidden": "true" }),
    el("span", { text: item.label }),
  ])));
}

function drawing(range) {
  const canvas = svg("svg", {
    class: "plot-svg", viewBox: "0 0 100 100", preserveAspectRatio: "none",
    "aria-hidden": "true",
  });
  for (const tick of range.y_ticks) {
    canvas.append(svg("line", { class: "grid-line", x1: "0", x2: "100", y1: tick.y, y2: tick.y }));
  }
  for (const level of range.levels) {
    if (level.kind === "band") {
      canvas.append(svg("rect", { class: "band", x: "0", y: level.y, width: "100", height: level.height }));
      canvas.append(svg("line", { class: "level-line entry", x1: "0", x2: "100", y1: level.y, y2: level.y }));
      canvas.append(svg("line", { class: "level-line entry", x1: "0", x2: "100", y1: level.y_bottom, y2: level.y_bottom }));
    } else {
      canvas.append(svg("line", {
        class: `level-line ${level.shape}`, x1: "0", x2: "100", y1: level.y, y2: level.y,
      }));
    }
  }
  for (const name of ["sma200", "sma50"]) {
    if (range[name].available) {
      canvas.append(svg("path", { class: `series ${name}`, d: range[name].path }));
    }
  }
  canvas.append(svg("path", { class: "series price", d: range.path }));
  return canvas;
}

function labels(range) {
  const nodes = range.levels.map((level) => el("span", {
    class: "level-label", style: `--label-top:${level.label_top_px}px`,
  }, [
    el("span", { class: `glyph ${level.shape}`, "aria-hidden": "true" }),
    el("span", { text: `${level.label} ${level.value}` }),
  ]));
  if (range.close_marker) {
    const marker = range.close_marker;
    nodes.push(el("span", { class: "close-dot", style: `left:${marker.x}%;top:${marker.y}%` }));
    nodes.push(el("span", {
      class: "level-label", style: `--label-top:${marker.label_top_px}px`,
    }, [
      el("span", { class: "glyph close", "aria-hidden": "true" }),
      el("span", { text: marker.label }),
    ]));
  }
  return nodes;
}

/* The crosshair: pointer or arrow keys choose a point; the tooltip shows the
   strings the backend prepared for it. */
function interaction(range, plot, title) {
  const points = range.points;
  // The series names are the backend's legend labels, so no name is written here.
  const names = Object.fromEntries(range.legend.map((item) => [item.key, item.label]));
  const strips = points.map((point) => el("span", {
    class: "hit", style: `left:${point.x}%`, "aria-hidden": "true",
  }));
  const hits = el("div", { class: "hits" }, strips);
  const cross = el("span", { class: "cross", hidden: true });
  const dot = el("span", { class: "cross-dot", hidden: true });
  const tip = el("div", { class: "tip", hidden: true });
  const anchor = el("div", { class: "tip-anchor" }, tip);
  const readout = el("p", { class: "visually-hidden", role: "status", "aria-live": "polite" });
  let active = -1;

  const hide = () => {
    active = -1;
    for (const node of [cross, dot, tip]) node.hidden = true;
  };
  const show = (index, announce) => {
    active = index;
    const point = points[index];
    cross.style.left = `${point.x}%`;
    dot.style.left = `${point.x}%`;
    dot.style.top = `${point.y}%`;
    anchor.style.left = `${point.x}%`;
    tip.className = point.align === "end" ? "tip end" : "tip";
    const lines = [el("strong", { text: point.date }),
      el("span", { text: `${names.price} ${point.close}` })];
    if (point.sma50) lines.push(el("span", { text: `${names.sma50} ${point.sma50}` }));
    if (point.sma200) lines.push(el("span", { text: `${names.sma200} ${point.sma200}` }));
    tip.replaceChildren(...lines);
    for (const node of [cross, dot, tip]) node.hidden = false;
    if (announce) readout.textContent = `${point.date}，${names.price} ${point.close}`;
  };

  hits.addEventListener("pointermove", (event) => {
    const index = strips.indexOf(event.target);
    if (index >= 0) show(index, false);
  });
  plot.addEventListener("pointerleave", () => {
    if (document.activeElement !== plot) hide();
  });
  plot.addEventListener("blur", hide);
  plot.addEventListener("focus", () => { if (active < 0) show(points.length - 1, true); });
  plot.addEventListener("keydown", (event) => {
    const last = points.length - 1;
    const target = {
      ArrowRight: Math.min(last, active + 1), ArrowLeft: Math.max(0, active - 1),
      Home: 0, End: last,
    }[event.key];
    if (event.key === "Escape") { hide(); return; }
    if (target === undefined) return;
    event.preventDefault();
    show(target, true);
  });
  plot.setAttribute("aria-label", `${title}价格走势，用左右方向键逐日查看`);
  return [hits, cross, dot, anchor, readout];
}

function frame(chart, range, title) {
  const plot = el("div", {
    class: "plot", tabindex: "0", role: "group", "aria-roledescription": "折线图",
  });
  plot.append(drawing(range), ...labels(range), ...interaction(range, plot, title));
  const body = el("div", { class: "chart-body", style: `--plot-h:${chart.plot_height_px}px` }, [
    el("div", { class: "y-axis", "aria-hidden": "true" }, range.y_ticks.map((tick) => el("span", {
      style: `top:${tick.y}%`, text: tick.label,
    }))),
    plot,
  ]);
  const axis = el("div", { class: "x-axis", "aria-hidden": "true" }, range.x_ticks.map((tick) => el("span", {
    class: tick.align, style: `left:${tick.x}%`, text: tick.label,
  })));
  const notes = [...range.notes];
  if (chart.levels_note) notes.push(chart.levels_note);
  if (range.close_note) notes.push(range.close_note);
  notes.push(chart.caption);
  return [body, axis, el("div", { class: "chart-notes" }, notes.map((note) => el("p", { text: note })))];
}

export function priceChart(chart, title) {
  if (!chart || !chart.available) {
    return el("p", { class: "section-note", text: chart ? chart.reason : "没有价格数据。" });
  }
  const host = el("div", { class: "chart" });
  let selected = chart.default_range;
  const draw = (keepFocus) => {
    const range = chart.ranges.find((item) => item.key === selected);
    const tabs = el("div", { class: "range-tabs", role: "group", "aria-label": "价格范围" },
      chart.ranges.map((item) => {
        const button = el("button", {
          type: "button", "aria-pressed": item.key === selected ? "true" : "false",
          text: item.label,
        });
        button.addEventListener("click", () => { selected = item.key; draw(true); });
        return button;
      }));
    host.replaceChildren(el("div", { class: "chart-head" }, [tabs, legend(range)]),
      ...frame(chart, range, title));
    if (keepFocus) host.querySelector('[aria-pressed="true"]').focus();
  };
  draw(false);
  return host;
}

export function sparkline(spark) {
  if (!spark || !spark.available) {
    return el("span", { class: "spark-none", text: "—", title: spark ? spark.label : "" });
  }
  return el("span", { class: "spark", role: "img", "aria-label": spark.label }, [
    svg("svg", { viewBox: "0 0 100 100", preserveAspectRatio: "none", "aria-hidden": "true" },
      svg("path", { d: spark.path })),
    el("span", { class: "spark-dot", style: `left:${spark.end.x}%;top:${spark.end.y}%` }),
  ]);
}
