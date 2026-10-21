import { APiCharts } from "./charts.mjs";

/** Keep summary/status/actions visible; move only secondary work into native disclosure.
 * Existing nodes are moved, never cloned, so event handlers and input values survive.
 * @param {Document} [root] */
export function installWorkbenchDisclosure(root = document) {
  const groups = [
    [".factor-mining-config", "搜索参数", false],
    [".factor-mining-output > section:first-child", "挖掘批次与执行记录", true],
    [".factor-mining-output > section:last-child", "表达式因子库", false],
    // Research factor/financial panels are owned by workbench-ia tabs — do not
    // wrap them or IA will move the body out and leave an empty disclosure shell.
    // optimizer owned by risk tab
    [".backtest-results .result-table", "回测数据、基准与审计详情", false],
  ];
  for (const [selector, label, open] of groups) {
    const node = root.querySelector(String(selector));
    if (!node || node.closest(".workbench-disclosure")) continue;
    // Skip anything already parked in IA tab hosts
    if (node.closest(".ia-tab-host, .ia-tab-stack, .workbench-tab-panel"))
      continue;
    const details = root.createElement("details");
    details.className = "workbench-disclosure";
    details.dataset.disclosureKey = String(selector);
    details.open = Boolean(open);
    const summary = root.createElement("summary");
    summary.textContent = String(label);
    node.before(details);
    details.append(summary, node);
    details.addEventListener("toggle", () => {
      if (details.open) requestAnimationFrame(() => APiCharts.draw());
    });
  }
  // Sweep empty disclosure shells left by earlier IA moves
  root.querySelectorAll("details.workbench-disclosure").forEach((details) => {
    const bodyNodes = [...details.children].filter(
      (n) => n.tagName !== "SUMMARY",
    );
    const empty =
      bodyNodes.length === 0 ||
      bodyNodes.every(
        (n) =>
          !(n.textContent || "").trim() &&
          n.children.length === 0 &&
          !n.querySelector("table, canvas, input, select, button, .panel"),
      );
    if (empty) details.remove();
  });
  /* section-return removed: sidebar already provides navigation (design-system P0). */
  root.querySelectorAll(".section-return").forEach((el) => {
    el.hidden = true;
  });
}

/** Export the displayed public state only, not the raw configuration or credentials. */
export function displayedStatusDetails() {
  /** @param {string} id */
  const read = (id) =>
    document.getElementById(id)?.textContent?.trim() || "未加载";
  return {
    product: "A-pi",
    exported_at: new Date().toISOString(),
    note: "当前界面显示的状态详情；不代表正式验收，不触发回测或交易。",
    page: document.querySelector(".page-section.active")?.id || "overview",
    default_backtest: read("portfolioStrategyIdentity"),
    current_backtest: read("backtestResultIdentity"),
    paper: {
      status: read("paperStatus"),
      equity: read("paperEquity"),
      cash: read("paperCash"),
      positions: read("paperPositions"),
      data_quality: read("paperQuality"),
      maintenance: read("paperRunExplanation"),
    },
    research: {
      budget: read("researchBudgetStatus"),
      factor_lock: read("factorLockStatus"),
      sealed_test: read("sealedTestStatus"),
      stage: read("sopStage"),
      acceptance_gate: read("factorMiningAcceptanceGate"),
      pit_quality: read("factorPitQuality"),
    },
    risk: { level: read("riskLevel"), alerts: read("riskAlertCount") },
  };
}
