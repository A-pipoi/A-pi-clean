import { init, use } from "echarts/core";
import { LineChart, PieChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkAreaComponent,
  MarkLineComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { decorateEmpty } from "./workbench-polish.mjs";
import { installPieHoverDetail } from "./pie-hover-detail.mjs";
use([
  LineChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkAreaComponent,
  MarkLineComponent,
  CanvasRenderer,
]);
/** @typedef {{name:string,points:{date:string,value:number}[],color?:string,dashed?:boolean}} ChartSeries */
/** @typedef {{kind:'line'|'pie',title:string,precision?:number,quietZoom?:boolean,series?:ChartSeries[],bands?:{start:string,end:string,color:string}[],segments?:{name:string,value:number,color?:string}[]}} ChartModel */
/** @type {Map<HTMLElement,{chart:import('echarts/core').ECharts,host:HTMLElement,model:ChartModel,observer:ResizeObserver}>} */
const instances = new Map();
/** Repaint theme without discarding the user's current zoom.
 * @param {Pick<import('echarts/core').ECharts,'getOption'|'setOption'>} chart
 * @param {ChartModel} model */
export function repaintFinancialChart(chart, model) {
  const option = financialChartOption(model);
  const zoom = chart.getOption().dataZoom;
  if (Array.isArray(zoom) && zoom.length && "dataZoom" in option)
    option.dataZoom = zoom;
  chart.setOption(
    /** @type {import('echarts/core').EChartsCoreOption} */ (option),
    { notMerge: true },
  );
}
const palette = [
  "#6856c9",
  "#148359",
  "#be6b23",
  "#367db5",
  "#b64a7a",
  "#458d85",
];
/** @param {string} value */
export function chartDate(value) {
  const compact = value.replaceAll("-", "");
  if (!/^\d{8}$/.test(compact)) throw new Error("Invalid chart date");
  const day = `${compact.slice(0, 4)}-${compact.slice(4, 6)}-${compact.slice(6, 8)}`;
  if (new Date(day).toISOString().slice(0, 10) !== day)
    throw new Error("Invalid chart date");
  return day;
}
/** Real calendar union, never equal-index alignment, interpolation or forward fill.
 * @param {ChartSeries[]} series */
export function alignedChartSeries(series) {
  const parsed = series.map((row) => {
    const values = new Map();
    for (const point of row.points) {
      const day = chartDate(point.date);
      if (!Number.isFinite(point.value) || values.has(day))
        throw new Error("Invalid or duplicate chart point");
      values.set(day, point.value);
    }
    return { row, values };
  });
  const dates = [
    ...new Set(parsed.flatMap((row) => [...row.values.keys()])),
  ].sort();
  return {
    dates,
    series: parsed.map(({ row, values }) => ({
      ...row,
      data: dates.map((day) => values.get(day) ?? null),
    })),
  };
}
/** @param {ChartModel} model */
export function financialChartOption(model) {
  const fontFamily =
    typeof getComputedStyle === "function"
      ? getComputedStyle(document.body).fontFamily
      : "sans-serif";
  const dark =
    typeof document !== "undefined" &&
    document.documentElement.dataset.theme === "dark";
  const themeTokens =
    typeof getComputedStyle === "function"
      ? getComputedStyle(document.documentElement)
      : null;
  /** @param {string} name @param {string} fallback */
  const token = (name, fallback) =>
    themeTokens?.getPropertyValue(name).trim() || fallback;
  const colors = [
    token("--chart-primary", dark ? "#c2a6f4" : palette[0]),
    token("--chart-secondary", dark ? "#90c6d7" : palette[3]),
    token("--chart-green", dark ? "#8dd5b4" : palette[1]),
    token("--chart-yellow", dark ? "#e6cf7d" : palette[2]),
    token("--chart-pink", dark ? "#e2a4c5" : palette[4]),
  ];
  const text = token("--text", dark ? "#e0e8f5" : "#334155"),
    grid = token("--chart-grid", dark ? "#373b4b" : "#e6e8ef");
  const base = {
    animation: false,
    backgroundColor: "transparent",
    textStyle: {
      color: text,
      fontFamily,
      fontSize: 14,
    },
    color: colors,
    tooltip: { renderMode: "richText", confine: true },
    legend: {
      type: "scroll",
      textStyle: { color: text },
      top: 4,
      left: 0,
      right: 72,
      padding: [0, 0, 0, 0],
    },
  };
  if (model.kind === "pie") {
    const data = (model.segments || []).filter(
      (row) => row.value > 0 && Number.isFinite(row.value),
    );
    return {
      ...base,
      legend: { show: false },
      // Hover detail is rendered in a reserved sibling surface below the ring.
      // Keep ECharts' canvas overlay off so it cannot obscure the center label.
      tooltip: { show: false },
      series: [
        {
          type: "pie",
          radius: ["68%", "88%"],
          center: ["50%", "50%"],
          label: { show: false },
          labelLine: { show: false },
          emphasis: { scale: false, label: { show: false } },
          data: data.map((row) => ({
            name: row.name,
            value: row.value,
            itemStyle: row.color ? { color: row.color } : undefined,
          })),
        },
      ],
    };
  }
  const aligned = alignedChartSeries(model.series || []);
  return {
    ...base,
    tooltip: {
      ...base.tooltip,
      trigger: "axis",
      axisPointer: { type: "cross" },
    },
    grid: {
      left: 12,
      right: 14,
      top: 36,
      bottom: model.quietZoom ? 28 : 48,
      containLabel: true,
    },
    xAxis: {
      type: "category",
      data: aligned.dates,
      boundaryGap: false,
      axisLabel: { color: text },
      axisLine: { lineStyle: { color: grid } },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { color: text },
      splitLine: {
        lineStyle: {
          color: token("--chart-grid-quiet", "rgba(128,128,128,.12)"),
        },
      },
    },
    dataZoom: model.quietZoom
      ? [
          {
            type: "inside",
            filterMode: "none",
            zoomOnMouseWheel: true,
            moveOnMouseMove: true,
          },
        ]
      : [
          {
            type: "inside",
            filterMode: "none",
            zoomOnMouseWheel: true,
            moveOnMouseMove: true,
          },
          {
            type: "slider",
            bottom: 4,
            height: 14,
            filterMode: "none",
            borderColor: "transparent",
            backgroundColor: "rgba(128,128,128,.08)",
            fillerColor: "rgba(109,92,231,.18)",
            handleSize: 0,
            showDetail: false,
            brushSelect: false,
          },
        ],
    series: aligned.series.map((row, index) => ({
      name: row.name,
      type: "line",
      data: row.data,
      connectNulls: false,
      showSymbol: row.data.length < 80,
      lineStyle: {
        width: 2,
        type: row.dashed ? "dashed" : "solid",
        color: row.color || colors[index % colors.length],
      },
      itemStyle: { color: row.color || colors[index % colors.length] },
      areaStyle:
        index === 0 && !row.dashed
          ? {
              color: {
                type: "linear",
                x: 0,
                y: 0,
                x2: 0,
                y2: 1,
                colorStops: [
                  {
                    offset: 0,
                    color: token("--chart-area-top", "rgba(109,92,231,.08)"),
                  },
                  {
                    offset: 1,
                    color: token("--chart-area-bottom", "rgba(109,92,231,0)"),
                  },
                ],
              },
            }
          : undefined,
      markLine:
        index === 0
          ? {
              silent: true,
              symbol: "none",
              label: { show: false },
              data: (model.bands || [])
                .filter((b) => b.start && b.end)
                .flatMap((b) => {
                  const covered = aligned.dates.filter(
                    (day) =>
                      day >= chartDate(b.start) && day <= chartDate(b.end),
                  );
                  return covered.length === 1
                    ? [
                        {
                          xAxis: covered[0],
                          lineStyle: {
                            color: b.color,
                            width: 8,
                            type: "solid",
                          },
                        },
                      ]
                    : [];
                }),
            }
          : undefined,
      markArea:
        index === 0
          ? {
              silent: true,
              data: (model.bands || [])
                .filter((b) => b.start && b.end)
                .flatMap((b) => {
                  const covered = aligned.dates.filter(
                    (day) =>
                      day >= chartDate(b.start) && day <= chartDate(b.end),
                  );
                  return covered.length
                    ? [
                        [
                          { xAxis: covered[0], itemStyle: { color: b.color } },
                          { xAxis: covered.at(-1) },
                        ],
                      ]
                    : [];
                }),
            }
          : undefined,
    })),
  };
}
/** @param {HTMLElement} anchor @param {ChartModel} model */
export function renderFinancialChart(anchor, model) {
  const option = financialChartOption(model);
  for (const [element, entry] of instances)
    if (!element.isConnected) {
      entry.observer.disconnect();
      entry.chart.dispose();
      instances.delete(element);
    }
  let entry = instances.get(anchor);
  if (entry && JSON.stringify(entry.model) === JSON.stringify(model)) {
    repaintFinancialChart(entry.chart, model);
    entry.chart.resize();
    return entry.chart;
  }
  if (!entry) {
    const host = document.createElement("div");
    host.className = "financial-chart";
    if (anchor instanceof HTMLCanvasElement) {
      anchor.hidden = true;
      anchor.parentElement?.classList.add("chart-native-migrated");
      anchor.after(host);
    } else anchor.append(host);
    const plot = document.createElement("div");
    plot.className =
      model.kind === "pie" ? "financial-chart-pie" : "financial-chart-plot";
    plot.setAttribute("role", "img");
    plot.setAttribute("aria-label", model.title);
    host.append(plot);
    const chart = init(plot, undefined, { renderer: "canvas" });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(plot);
    entry = { chart, host, model, observer };
    instances.set(anchor, entry);
  }
  entry.model = model;
  entry.chart.setOption(
    /** @type {import('echarts/core').EChartsCoreOption} */ (option),
    { notMerge: true },
  );
  entry.host
    .querySelectorAll(
      ".financial-chart-controls,details,.financial-chart-empty",
    )
    .forEach((node) => node.remove());
  if (anchor.id) document.getElementById(anchor.id + "-chart-data")?.remove();
  if (model.kind === "line") {
    const controls = document.createElement("div");
    controls.className = "financial-chart-controls";
    let start = 0,
      end = 100;
    const apply = () =>
      entry?.chart.dispatchAction({ type: "dataZoom", start, end });
    entry.chart.off("datazoom");
    entry.chart.on("datazoom", () => {
      const ranges = /** @type {{start?:number,end?:number}[]} */ (
        entry?.chart.getOption().dataZoom || []
      );
      start = ranges[0]?.start ?? 0;
      end = ranges[0]?.end ?? 100;
    });
    for (const [name, action] of /** @type {[string,()=>void][]} */ ([
      [
        "放大",
        () => {
          const middle = (start + end) / 2,
            width = Math.max(2, (end - start) * 0.8);
          start = Math.max(0, middle - width / 2);
          end = Math.min(100, middle + width / 2);
          apply();
        },
      ],
      [
        "缩小",
        () => {
          const middle = (start + end) / 2,
            width = Math.min(100, (end - start) * 1.25);
          start = Math.max(0, middle - width / 2);
          end = Math.min(100, middle + width / 2);
          apply();
        },
      ],
      [
        "向前",
        () => {
          const shift = Math.min(start, (end - start) * 0.2);
          start -= shift;
          end -= shift;
          apply();
        },
      ],
      [
        "向后",
        () => {
          const shift = Math.min(100 - end, (end - start) * 0.2);
          start += shift;
          end += shift;
          apply();
        },
      ],
      [
        "全部",
        () => {
          start = 0;
          end = 100;
          apply();
        },
      ],
    ])) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary-button";
      button.textContent = name;
      button.addEventListener("click", action);
      controls.append(button);
    }
    entry.host.append(controls);
  }
  const details = document.createElement("details"),
    summary = document.createElement("summary");
  summary.textContent = "查看图表数据";
  details.append(summary);
  const table = document.createElement("table");
  table.className = "interactive-chart-table";
  const body = document.createElement("tbody");
  const aligned =
    model.kind === "line" ? alignedChartSeries(model.series || []) : null;
  const head = document.createElement("thead"),
    tr = document.createElement("tr");
  for (const value of aligned
    ? ["交易日", ...aligned.series.map((row) => row.name)]
    : ["标的", "金额"]) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = value;
    tr.append(th);
  }
  head.append(tr);
  table.append(head);
  const rows = aligned
    ? aligned.dates.map((day, i) => [
        day,
        ...aligned.series.map((row) =>
          row.data[i] === null ? "—" : String(row.data[i]),
        ),
      ])
    : (model.segments || []).map((row) => [row.name, String(row.value)]);
  for (const values of rows) {
    const row = document.createElement("tr");
    for (const value of values) {
      const td = document.createElement("td");
      const numeric = Number(value);
      td.textContent =
        model.precision !== undefined &&
        value !== "" &&
        Number.isFinite(numeric)
          ? String(
              Number(
                numeric.toFixed(Math.max(0, Math.min(8, model.precision))),
              ),
            )
          : value;
      if (td.textContent !== value) td.title = value;
      row.append(td);
    }
    body.append(row);
  }
  table.append(body);
  details.append(table);
  if (model.kind === "pie") {
    details.className = "financial-chart-data";
    if (anchor.id) details.id = anchor.id + "-chart-data";
    anchor.after(details);
    installPieHoverDetail(anchor, entry.chart, model.segments || []);
  } else entry.host.append(details);
  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "financial-chart-empty";
    decorateEmpty(
      empty,
      model.title,
      anchor.id === "exposureChart" ? "paper" : "backtest",
    );
    if (model.kind === "line") entry.host.prepend(empty);
    else details.prepend(empty);
  }
  entry.host
    .querySelector(".financial-chart-plot,.financial-chart-pie")
    ?.toggleAttribute("hidden", !rows.length);
  return entry.chart;
}
/** @param {HTMLElement} anchor */
export function clearFinancialChart(anchor) {
  if (anchor.id) document.getElementById(anchor.id + "-chart-data")?.remove();
  if (anchor.id) document.getElementById(anchor.id + "-hover-detail")?.remove();
  const entry = instances.get(anchor);
  if (entry) {
    entry.observer.disconnect();
    entry.chart.dispose();
    entry.host.remove();
    instances.delete(anchor);
  }
}
if (typeof MutationObserver !== "undefined") {
  new MutationObserver(() => {
    for (const [anchor, entry] of instances) {
      if (!anchor.isConnected) {
        entry.observer.disconnect();
        entry.chart.dispose();
        instances.delete(anchor);
      } else repaintFinancialChart(entry.chart, entry.model);
    }
  }).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme", "lang"],
  });
}
