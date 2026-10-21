import { renderFinancialChart } from "./chart-loader.mjs";
import { setSafeHtml } from "./safe-html.mjs";
import { requiredElement } from "./runtime-utils.mjs";
import { APiCharts } from "./charts.mjs";
import { APiRiskView } from "./risk-view.js";
/** portfolio view with explicit services and live state accessors. */
/** @param {import('../types/services').PortfolioViewServices} deps */
export function createPortfolioView(deps) {
  /** @type {import('../types/services').SharedServices['applyPortfolioSnapshot']} */
  function applyPortfolioSnapshot(portfolio) {
    if (!portfolio) return;
    const metrics = portfolio.metrics || {};
    const finalEquity = Number(portfolio.final_equity);
    deps.setText(
      "portfolioEquity",
      Number.isFinite(finalEquity)
        ? `¥ ${deps.formatNumber(finalEquity)}`
        : "—",
    );
    deps.setText("annualReturn", deps.formatPercent(metrics.annualized_return));
    deps.setText("maxDrawdown", deps.formatPercent(metrics.max_drawdown));
    deps.setText(
      "turnover",
      Number.isFinite(Number(metrics.turnover))
        ? `${Number(metrics.turnover).toFixed(2)}×`
        : "—",
    );
    deps.setText(
      "portfolioReturn",
      Number.isFinite(finalEquity)
        ? deps.formatPercent(
            finalEquity /
              (Number(portfolio.equity_curve?.[0]?.equity) || 1000000) -
              1,
          )
        : "—",
    );
    const curve = portfolio.equity_curve || [];
    deps.setText(
      "drawdownStatus",
      Number.isFinite(Number(metrics.max_drawdown)) ? "真实回测" : "等待数据",
    );
    deps.setText("recoveryDays", `恢复期 ${portfolio.recovery_days ?? "—"} 天`);
    deps.latestEquityCurve = curve.filter(
      (point) => point && point.date && Number.isFinite(Number(point.equity)),
    );
    deps.latestBacktestValues = deps.latestEquityCurve.map((point) =>
      Number(point.equity),
    );
    const visibleCurve = deps.visibleEquityCurve();
    deps.setText(
      "portfolioDateRange",
      visibleCurve.length > 1
        ? `${visibleCurve[0].date} → ${visibleCurve[visibleCurve.length - 1].date}`
        : "等待真实回测",
    );
    const lastLabel = document.querySelector(".chart-last");
    if (lastLabel && Number.isFinite(finalEquity))
      lastLabel.textContent = `最新 ¥ ${(finalEquity / 1000000).toFixed(3)}M`;
    deps.updateEquityAxis(deps.visibleEquityCurve());
    APiCharts?.draw();
  }

  /** @type {import('../types/services').SharedServices['applySignalSnapshot']} */
  function applySignalSnapshot(signals, paper = null) {
    const body = requiredElement("holdingsBody");
    if (!body) return;
    const signalRows = Array.isArray(signals) ? signals : [];
    const signalBySymbol = new Map(
      signalRows.map((row) => [String(row.symbol || "").toUpperCase(), row]),
    );
    const identityBySymbol = new Map(
      (deps.runtimeConfig.seed_universe || []).map((item) => [
        String(item.ts_code || "").toUpperCase(),
        item,
      ]),
    );
    const knownIndustry =
      /** @param {unknown} value */
      (value) =>
        value && !["未分类", "未知行业"].includes(String(value).trim());
    const identityFor =
      /** @param {string} symbol @param {import('../types/domain').Stock} row @param {import('../types/domain').Stock} position */
      (symbol, row = {}, position = {}) => {
        const identity = identityBySymbol.get(symbol) || {};
        return {
          name:
            [position.name, row.name, identity.name].find(
              (value) => value && String(value).trim() !== symbol,
            ) || symbol,
          industry:
            [position.industry, row.industry, identity.industry].find(
              knownIndustry,
            ) || "",
        };
      };
    const heldRows = (paper?.positions || []).map((position) => {
      const symbol = String(position.symbol || "").toUpperCase();
      const signal = signalBySymbol.get(symbol) || {};
      return {
        ...signal,
        ...identityFor(symbol, signal, position),
        symbol,
        trade_date: position.quote_date || signal.trade_date || "",
        signal: "持仓",
      };
    });
    const heldSymbols = new Set(heldRows.map((row) => row.symbol));
    const rows = [
      ...heldRows,
      ...signalRows
        .filter(
          (row) => !heldSymbols.has(String(row.symbol || "").toUpperCase()),
        )
        .map((row) => ({
          ...row,
          ...identityFor(String(row.symbol || "").toUpperCase(), row),
          trade_date:
            row.trade_date ||
            deps.latestWorkspaceSnapshot?.latest_trade_date ||
            "",
        })),
    ];
    const paperEquity = Number(paper?.equity);
    const hasPaper = Number.isFinite(paperEquity) && paperEquity > 0;
    const paperWeights = new Map(
      (paper?.positions || []).map((position) => [
        String(position.symbol || "").toUpperCase(),
        Number(position.market_value) / paperEquity,
      ]),
    );
    setSafeHtml(
      body,
      rows.length
        ? rows
            .map((row) => {
              const signalClass = row.signal === "强势" ? "buy" : "hold";
              const weight = hasPaper
                ? paperWeights.get(String(row.symbol || "").toUpperCase()) || 0
                : null;
              const tradeDate = /^\d{4}-\d{2}-\d{2}$/.test(
                String(row.trade_date || ""),
              )
                ? row.trade_date
                : "—";
              return `<tr><td><strong>${deps.escapeHtml(row.name || row.symbol)}</strong><small>${deps.escapeHtml(row.symbol)}</small></td><td>${deps.escapeHtml(row.industry || deps.localeText("行业待确认"))}</td><td class="num">${hasPaper ? deps.formatPercent(weight) : "—"}</td><td>${deps.escapeHtml(tradeDate)}</td><td><span class="signal-tag ${signalClass}">${deps.escapeHtml(row.signal || "观察")}</span></td></tr>`;
            })
            .join("")
        : '<tr><td colspan="5">当前无纸面持仓或 Tushare 策略信号</td></tr>',
    );
  }

  /** @type {import('../types/services').SharedServices['renderAllocationDonut']} */
  function renderAllocationDonut(positions, equity, marketValue) {
    const donut = requiredElement("allocationDonut");
    if (!donut) return;
    const valid =
      Number.isFinite(equity) &&
      equity > 0 &&
      Number.isFinite(marketValue) &&
      marketValue >= 0;
    const segments = valid
      ? (positions || []).map((position) => ({
          name: position.symbol || deps.localeText("标的"),
          value: Math.max(0, Number(position.market_value) || 0),
        }))
      : [];
    if (valid && equity > marketValue)
      segments.push({
        name: deps.localeText("现金"),
        value: equity - marketValue,
      });
    renderFinancialChart(donut, {
      kind: "pie",
      title: deps.localeText("当前暴露"),
      segments,
    });
  }

  /** @type {import('../types/services').SharedServices['applyRiskSnapshot']} */
  function applyRiskSnapshot(paper, portfolio, riskLimits = null) {
    if (!deps.riskViewRuntime) {
      deps.riskViewRuntime = APiRiskView.create({
        setText: deps.setText,
        localeText: deps.localeText,
        escapeHtml: deps.escapeHtml,
        getRiskLimits: () => deps.latestRiskLimits,
        setRiskLimits: (value) => {
          deps.latestRiskLimits = value;
        },
        getWorkspaceSnapshot: () => deps.latestWorkspaceSnapshot,
        getHealth: () => deps.notificationState.health,
        getCurrentUsername: deps.getCurrentUsername,
        setAlertDetails: (value) => {
          deps.latestRiskAlertDetails = value;
        },
      });
    }
    return deps.riskViewRuntime.apply(paper, portfolio, riskLimits);
  }
  return {
    applyPortfolioSnapshot,
    applySignalSnapshot,
    renderAllocationDonut,
    applyRiskSnapshot,
  };
}
