import { requiredElement } from "./runtime-utils.mjs";
import { renderFinancialChart } from "./chart-loader.mjs";
/** @type {import('../types/services').ChartsServices} */
let services;
let resizeBound = false;
let selectedFold = 0;
/** @param {{date:string,equity:number}[]} curve @param {{date:string,equity:number}[]} benchmark */
export function alignBenchmark(curve, benchmark) {
  const byDate = new Map(
    benchmark.map((p) => [p.date.replaceAll("-", ""), Number(p.equity)]),
  );
  const aligned = curve.map((p) => byDate.get(p.date.replaceAll("-", "")));
  // Missing observations are not invented or forward-filled.
  if (
    !aligned.length ||
    aligned.some((v) => v === undefined || !Number.isFinite(v) || v <= 0)
  )
    return null;
  return normalizeEquityValues(/** @type {number[]} */ (aligned));
}

/** @param {number[]} values */
export function normalizeEquityValues(values) {
  const base = values[0] || 1;
  return values.map((value) => value / base);
}

/** @param {HTMLCanvasElement} canvas @param {number[]} values @param {number[]|null} secondary @param {import('../types/module-state').ChartOptions} options */
function drawLine(canvas, values, secondary, options) {
  const dates = options.dates || [];
  /** @type {import('./chart-engine.mjs').ChartSeries[]} */
  const series = [
    {
      name: "策略净值",
      points: values.map((value, index) => ({ date: dates[index], value })),
    },
  ];
  if (secondary)
    series.push({
      name: "沪深300",
      points: secondary.map((value, index) => ({ date: dates[index], value })),
      dashed: true,
    });
  renderFinancialChart(canvas, {
    kind: "line",
    title: canvas.getAttribute("aria-label") || "净值走势",
    series,
    bands: options.bands,
  });
}
/** @param {HTMLCanvasElement} canvas @param {string} text */
function drawEmptyChart(canvas, text) {
  renderFinancialChart(canvas, { kind: "line", title: text, series: [] });
}
export function drawCharts() {
  if (!services) return;
  const curve = services
    .getCurve()
    .filter((point) => Number.isFinite(Number(point.equity)));
  const values = curve.map((point) => Number(point.equity));
  const dates = curve.map((point) => point.date);
  services.updateAxis(curve);
  const equityChart = requiredElement("equityChart");
  if (values.length > 1) {
    drawLine(equityChart, normalizeEquityValues(values), null, {
      dates,
      rawValues: values,
      tooltipId: "equityChartTooltip",
    });
  } else if (equityChart && equityChart.offsetParent !== null) {
    drawEmptyChart(equityChart, "等待 Tushare 真实回测数据");
  }
  const backtestChart = requiredElement("backtestChart");
  const result = services.getBacktestResult();
  const backtestCurve = (result?.equity_curve || []).filter((p) =>
    Number.isFinite(p.equity),
  );
  const folds = result?.strategy_validation?.fold_ranges || [];
  let legend = document.getElementById("backtestValidationLegend");
  let selector = /** @type {HTMLSelectElement|null} */ (
    document.getElementById("backtestFoldSelect")
  );
  if (!legend && backtestChart) {
    legend = document.createElement("p");
    legend.id = "backtestValidationLegend";
    legend.className = "backtest-chart-legend";
    const label = document.createElement("label");
    label.className = "backtest-fold-control";
    label.textContent = "验证折叠 ";
    label.htmlFor = "backtestFoldSelect";
    selector = document.createElement("select");
    selector.id = "backtestFoldSelect";
    selector.addEventListener("change", () => {
      selectedFold = Number(selector?.value || 0);
      drawCharts();
    });
    label.append(selector);
    backtestChart.parentElement?.before(label, legend);
  }
  if (selector) {
    selector.replaceChildren();
    folds.forEach((fold, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = `第 ${index + 1} 折 · ${fold.test_start} → ${fold.test_end}`;
      selector?.append(option);
    });
    selectedFold = Math.min(selectedFold, Math.max(0, folds.length - 1));
    selector.value = String(selectedFold);
    selector.hidden = !folds.length;
  }
  const fold = folds[selectedFold];
  const secondary = alignBenchmark(
    backtestCurve,
    result?.benchmark_curve || [],
  );
  if (legend)
    legend.textContent = `蓝色：训练 · 黄色：embargo · 紫色：样本外验证（非独立密封测试） · 虚线：沪深300${secondary ? "" : "（暂不可用/覆盖不完整）"} · 密封区间 ${result?.dataset_metadata?.sealed_test_start || "未提供"} → ${result?.dataset_metadata?.sealed_test_end || "未提供"}，不绘制未验收数据`;
  if (backtestCurve.length > 1) {
    const bands = fold
      ? [
          {
            start: fold.train_start,
            end: fold.train_end,
            color: "rgba(100,170,220,.15)",
          },
          {
            start: fold.embargo_start || "",
            end: fold.embargo_end || "",
            color: "rgba(240,187,98,.18)",
          },
          {
            start: fold.test_start,
            end: fold.test_end,
            color: "rgba(150,120,220,.17)",
          },
        ]
      : [];
    drawLine(
      backtestChart,
      normalizeEquityValues(backtestCurve.map((p) => p.equity)),
      secondary,
      {
        dates: backtestCurve.map((p) => p.date),
        rawValues: backtestCurve.map((p) => p.equity),
        bands,
        tooltipId: "backtestChartTooltip",
      },
    );
  } else if (backtestChart && backtestChart.offsetParent !== null) {
    drawEmptyChart(backtestChart, "运行 Tushare 回测后显示");
  }
}

/** @param {import('../types/services').ChartsServices} dependencies */
export function initCharts(dependencies) {
  services = dependencies;
  if (!resizeBound) {
    window.addEventListener("resize", drawCharts);
    resizeBound = true;
  }
}

export const APiCharts = { init: initCharts, draw: drawCharts };
