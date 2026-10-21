import { renderFinancialChart } from "./chart-loader.mjs";
import { setSafeHtml } from "./safe-html.mjs";
import { apiFetch as fetch } from "./api-client.mjs";
import { requiredElement, toError } from "./runtime-utils.mjs";
/** Compare saved experiments without creating a new research-budget run. */
/** @param {import('../types/services').ExperimentComparisonServices} deps */
export function createExperimentComparison(deps) {
  /** @param {HTMLElement} entries */
  function sync(entries) {
    const selected = entries.querySelectorAll(
      "input[data-compare-result]:checked",
    );
    const button = requiredElement("compareExperimentsButton");
    const note = document.getElementById("ledgerCompareNote");
    if (selected.length > 4) {
      const last = selected[selected.length - 1];
      if (last instanceof HTMLInputElement) last.checked = false;
      deps.toast("最多同时对比 4 个回测实验");
    }
    const count = entries.querySelectorAll(
      "[data-compare-result]:checked",
    ).length;
    if (button) button.disabled = count < 2 || count > 4;
    if (note)
      note.textContent = `已选 ${count}/4 组 · 至少选择 2 个已保存回测；曲线各自以首日净值归一化。`;
  }

  /** @param {import('../types/domain').BacktestResult[]} results @param {HTMLElement} host */
  function render(results, host) {
    const colors = ["#6658df", "#1a9b75", "#d27734", "#3e80bc"];
    const series = results.map((result, index) => {
      const curve = (result.equity_curve || []).filter(
        (point) =>
          Number.isFinite(Number(point.equity)) && Number(point.equity) > 0,
      );
      const base = Number(curve[0]?.equity) || 1;
      return {
        result,
        curve,
        values: curve.map((point) => Number(point.equity) / base),
        color: colors[index],
      };
    });
    /** @type {Array<[string, (item: any) => string]>} */
    const metrics = [
      [
        "年化收益",
        (item) =>
          Number.isFinite(Number(item.metrics?.annualized_return))
            ? deps.formatPercent(Number(item.metrics.annualized_return))
            : "—",
      ],
      [
        "Sharpe",
        (item) =>
          Number.isFinite(Number(item.metrics?.sharpe))
            ? Number(item.metrics.sharpe).toFixed(2)
            : "—",
      ],
      [
        "最大回撤",
        (item) =>
          Number.isFinite(Number(item.metrics?.max_drawdown))
            ? deps.formatPercent(Number(item.metrics.max_drawdown))
            : "—",
      ],
      [
        "年化换手",
        (item) =>
          Number.isFinite(Number(item.metrics?.turnover))
            ? `${Number(item.metrics.turnover).toFixed(2)}×`
            : "—",
      ],
      [
        "成交笔数",
        (item) =>
          Number.isFinite(Number(item.fills)) ? String(item.fills) : "—",
      ],
      [
        "最终权益",
        (item) =>
          Number.isFinite(Number(item.final_equity))
            ? `¥${deps.formatNumber(Number(item.final_equity))}`
            : "—",
      ],
    ];
    const head = `<tr><th>指标</th>${series.map((item) => `<th class="num">${deps.escapeHtml(`#${item.result.research?.experiment_id ?? "—"}`)} · ${deps.escapeHtml(item.result.strategy_label || item.result.strategy || "实验")}</th>`).join("")}</tr>`;
    const rows = metrics
      .map(
        ([label, getter]) =>
          `<tr><th>${label}</th>${series.map((item) => `<td class="num">${deps.escapeHtml(getter(item.result))}</td>`).join("")}</tr>`,
      )
      .join("");
    const dates = series
      .map(
        (item) =>
          `<span style="--compare-color:${item.color}">#${deps.escapeHtml(item.result.research?.experiment_id ?? "—")} · ${deps.escapeHtml(item.curve[0]?.date || "—")} → ${deps.escapeHtml(item.curve.at(-1)?.date || "—")}</span>`,
      )
      .join("");
    host.className = "experiment-comparison";
    setSafeHtml(
      host,
      `<div class="experiment-comparison-periods">${dates}</div><div class="experiment-comparison-table-wrap"><table><thead>${head}</thead><tbody>${rows}</tbody></table></div>`,
    );
    const chartHost = document.createElement("div");
    host.prepend(chartHost);
    renderFinancialChart(chartHost, {
      kind: "line",
      title: "多组回测净值比较（按真实日期对齐）",
      series: series.map((item, index) => ({
        name: `#${item.result.research?.experiment_id ?? index + 1}`,
        color: item.color,
        points: item.curve.map((point, i) => ({
          date: point.date,
          value: item.values[i],
        })),
      })),
    });
  }

  async function compareSelected() {
    const entries = requiredElement("researchLedgerEntries");
    const urls = [
      ...(entries?.querySelectorAll("[data-compare-result]:checked") || []),
    ].map((input) => input.dataset.compareResult);
    if (urls.length < 2 || urls.length > 4) return;
    const button = requiredElement("compareExperimentsButton");
    const output = document.getElementById("experimentComparison");
    if (!output) return;
    button.disabled = true;
    button.textContent = "正在读取实验…";
    output.hidden = false;
    output.textContent = "正在读取已保存回测结果…";
    try {
      const results = await Promise.all(
        urls.map(async (url) => {
          if (!url) throw new Error("所选实验缺少产物链接");
          const response = await fetch(url, { cache: "no-store" });
          const job = await response.json();
          if (
            !response.ok ||
            !job.ok ||
            job.status !== "completed" ||
            !job.result?.metrics
          ) {
            throw new Error(job.error || `实验产物不可用：${url}`);
          }
          return job.result;
        }),
      );
      render(results, output);
    } catch (errorCaught) {
      const error = toError(errorCaught);
      output.className = "experiment-comparison is-error";
      output.textContent = `实验对比失败：${error.message}`;
    } finally {
      button.textContent = "对比所选实验";
      sync(entries);
    }
  }

  /** @param {HTMLElement} entries */
  function ensureControls(entries) {
    if (entries.dataset.compareBound === "true") return;
    entries.dataset.compareBound = "true";
    const toolbar = document.createElement("div");
    toolbar.className = "ledger-compare-toolbar";
    const note = document.createElement("span");
    note.id = "ledgerCompareNote";
    note.textContent =
      "选择 2–4 个已保存回测，可对照关键指标与归一化净值；各实验区间可能不同。";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary-button";
    button.id = "compareExperimentsButton";
    button.textContent = "对比所选实验";
    button.disabled = true;
    toolbar.append(note, button);
    entries.before(toolbar);
    const output = document.createElement("div");
    output.id = "experimentComparison";
    output.className = "experiment-comparison";
    output.hidden = true;
    toolbar.after(output);
    entries.addEventListener("change", (event) => {
      if (
        event.target instanceof Element &&
        event.target.matches("[data-compare-result]")
      )
        sync(entries);
    });
    button.addEventListener("click", compareSelected);
  }

  return { ensureControls, sync, render };
}

/** @type {ReturnType<typeof createExperimentComparison> | undefined} */
let comparison;
export const APiExperimentComparison = {
  /** @param {import('../types/services').ExperimentComparisonServices} deps */
  init(deps) {
    comparison = createExperimentComparison(deps);
  },
  /** @param {HTMLElement} entries */
  ensureControls(entries) {
    comparison?.ensureControls(entries);
  },
  /** @param {HTMLElement} entries */
  sync(entries) {
    comparison?.sync(entries);
  },
};
