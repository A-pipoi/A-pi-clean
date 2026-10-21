import { setSafeHtml } from "./safe-html.mjs";
import { apiFetch as fetch } from "./api-client.mjs";
import { requiredElement, toError } from "./runtime-utils.mjs";
import { APiCharts } from "./charts.mjs";
import { APiNotifications } from "./notification-center.mjs";
import { initializeDefaultBacktestDisplay } from "./backtest-default-display.mjs";
import { createBacktestTrades } from "./backtest-trades.mjs";
import { createBacktestFactorAudit } from "./backtest-factor-audit.mjs";
/** backtest results with explicit services and live state accessors. */
/** @param {import('../types/services').BacktestResultsServices} deps */
export function createBacktestResults(deps) {
  const defaultDisplay = initializeDefaultBacktestDisplay(deps);
  const trades = createBacktestTrades();
  const factorAudit = createBacktestFactorAudit();

  function revealBacktestResultBody() {
    const empty = document.getElementById("backtestResultEmpty");
    const body = document.getElementById("backtestResultBody");
    if (empty) empty.hidden = true;
    if (body) body.hidden = false;
  }

  /** @param {import('../types/domain').RiskAnalytics | null} report */
  function renderBacktestRiskAnalytics(report) {
    const riskLayout = document.querySelector("#risk .risk-layout");
    if (!riskLayout) return;
    let panel = document.getElementById("backtestRiskAnalytics");
    if (!panel) {
      panel = document.createElement("section");
      panel.id = "backtestRiskAnalytics";
      panel.className = "panel risk-analytics-panel";
      setSafeHtml(
        panel,
        '<div class="panel-heading"><div><h2>回测风险统计</h2><p>仅为历史样本诊断；VaR、相关性和压力区间不等同于未来风险保证。</p></div><span class="status-chip neutral" id="riskAnalyticsStatus">等待回测</span></div><div id="riskAnalyticsBody"></div>',
      );
      riskLayout.after(panel);
    }
    const body = document.getElementById("riskAnalyticsBody");
    if (!body) return;
    if (!report || report.status !== "available") {
      body.textContent = report
        ? `风险统计不可用：${report.status || "旧结果未保存该数据"}`
        : "运行一次新回测后显示 VaR、相关矩阵和样本压力统计；历史旧产物不会补造数据。";
      deps.setText(
        "riskAnalyticsStatus",
        report?.status === "insufficient_data" ? "样本不足" : "无风险产物",
      );
      return;
    }
    deps.setText(
      "riskAnalyticsStatus",
      `${report.observations || 0} 个日收益样本`,
    );
    const metricRows = [
      ["年化波动率", report.annualized_volatility],
      ["历史 VaR 95%（单日损失）", report.historical_var_95],
      ["历史 ES 95%（尾部均损）", report.historical_expected_shortfall_95],
      ["最大回撤", report.max_drawdown],
    ]
      .map(
        ([label, value]) =>
          `<div><dt>${deps.escapeHtml(label)}</dt><dd>${value !== null && value !== undefined && Number.isFinite(Number(value)) ? deps.formatPercent(Number(value)) : "—"}</dd></div>`,
      )
      .join("");
    const horizons = Object.entries(report.stress_horizons || {})
      .map(([key, value]) => {
        if (!value)
          return `<tr><th>${deps.escapeHtml(key)}</th><td>样本不足</td><td>—</td></tr>`;
        return `<tr><th>${deps.escapeHtml(key.replace("worst_", "最差 ").replace("d", " 日"))}</th><td class="num">${deps.formatPercent(Number(value.return))}</td><td>${deps.escapeHtml(value.start)} → ${deps.escapeHtml(value.end)}</td></tr>`;
      })
      .join("");
    const periods = Object.entries(report.historical_periods || {})
      .map(
        ([name, value]) =>
          `<span class="risk-period-tag" data-state="${value.status === "available" ? "available" : "unavailable"}">${deps.escapeHtml(name)} · ${value.status === "available" && value.max_drawdown != null ? deps.formatPercent(Number(value.max_drawdown)) : "样本不覆盖"}</span>`,
      )
      .join("");
    const correlation = report.correlation || {};
    const symbols = (correlation.symbols || []).slice(0, 8);
    const matrix = symbols.length
      ? `<div class="risk-correlation-wrap"><table class="risk-correlation"><thead><tr><th>相关系数</th>${symbols.map((symbol) => `<th>${deps.escapeHtml(symbol)}</th>`).join("")}</tr></thead><tbody>${symbols
          .map(
            (left) =>
              `<tr><th>${deps.escapeHtml(left)}</th>${symbols
                .map((right) => {
                  const value = correlation.matrix?.[left]?.[right];
                  const available =
                    value !== null &&
                    value !== undefined &&
                    Number.isFinite(Number(value));
                  const alpha = available
                    ? Math.min(0.22, Math.abs(Number(value)) * 0.22).toFixed(3)
                    : "0";
                  return `<td class="num" style="--corr-alpha:${alpha}">${available ? Number(value).toFixed(2) : "—"}</td>`;
                })
                .join("")}</tr>`,
          )
          .join("")}</tbody></table></div>`
      : "<p>没有足够成交标的计算相关矩阵。</p>";
    setSafeHtml(
      body,
      `<dl class="risk-analytics-metrics">${metricRows}</dl><div class="risk-analytics-detail"><section><h3>历史最差区间</h3><table class="risk-stress-table"><tbody>${horizons}</tbody></table><div class="risk-periods">${periods}</div></section><section><h3>成交标的相关性</h3>${matrix}<small>只展示成交额靠前的至多 8 只；少于 20 个共同日收益的组合标为 —。</small></section></div><p class="risk-analytics-basis">${deps.escapeHtml(report.basis || "")} · ${deps.escapeHtml(report.limits?.correlation || "")}</p>`,
    );
  }

  /** @param {import('../types/domain').BacktestResult} data */
  function applyBacktestResult(data, { restored = false } = {}) {
    revealBacktestResultBody();
    trades.render(data);
    factorAudit.render(data);
    deps.latestBacktestResult = data;
    deps.latestBacktestRestored = restored;
    deps.notificationState.backtest = restored ? null : data;
    APiNotifications?.render();
    const metrics = data.metrics || {};
    const validationGate =
      data.validation_gate || data.research?.validation_gate || {};
    const conclusionReady = validationGate.conclusion_ready === true;
    const portfolio = {
      dataset: data.dataset,
      final_equity: data.final_equity,
      metrics,
      equity_curve: data.equity_curve || [],
      recovery_days: metrics.recovery_days,
    };
    defaultDisplay.showCandidate(data);
    deps.setText(
      "backtestAnnual",
      conclusionReady
        ? deps.formatPercent(metrics.annualized_return)
        : deps.localeText("待敏感性检验"),
    );
    deps.setText(
      "backtestSharpe",
      conclusionReady && Number.isFinite(metrics.sharpe)
        ? Number(metrics.sharpe).toFixed(2)
        : deps.localeText("待敏感性检验"),
    );
    deps.setText(
      "backtestDrawdown",
      conclusionReady
        ? deps.formatPercent(metrics.max_drawdown)
        : deps.localeText("待敏感性检验"),
    );
    const backtestSource = data.dataset_metadata?.data_source || "tushare";
    const universeSummary =
      data.parameters?.research_universe ||
      data.dataset_metadata?.research_universe ||
      {};
    const universeLabel =
      universeSummary.pit_membership === true
        ? `${universeSummary.index_name || "指数"} 历史成分 PIT`
        : universeSummary.pit_membership === false
          ? "固定股票池 · 非 PIT"
          : "股票池未记录";
    deps.setText(
      "backtestDataset",
      `${deps.dataSourceLabel(backtestSource, backtestSource === "tushare_cache")} Tushare · ${universeLabel}`,
    );
    renderMonthlyReturns(data.monthly_returns || []);
    deps.setText(
      "backtestBenchmark",
      deps.localeText("正在加载 Tushare 基准…"),
    );
    if (!restored)
      deps.setText(
        "backtestRunCount",
        `${data.research?.run_count ?? data.research?.runs ?? "—"} / ${data.research?.budget ?? "—"} 次`,
      );
    deps.setText(
      "backtestSplit",
      data.strategy_validation
        ? `${data.strategy_validation.folds ?? "—"} 折验证`
        : "—",
    );
    deps.setText(
      "backtestEmbargo",
      data.strategy_validation
        ? `${data.strategy_validation.embargo ?? "—"} 日`
        : "—",
    );
    deps.setText(
      "experimentId",
      data.research?.experiment_id
        ? `#${data.research.experiment_id}`
        : `#${String(data.dataset || "Tushare").slice(-6)}`,
    );
    deps.setText(
      "backtestStatus",
      conclusionReady
        ? "成本后 · 验证通过"
        : deps.localeText(validationGate.status || "待敏感性检验"),
    );
    const validation = data.strategy_validation;
    if (validation) {
      const qualityWarning = (data.data_quality?.warnings || []).length > 0;
      deps.setText(
        "backtestAudit",
        validation.passed
          ? conclusionReady
            ? qualityWarning
              ? `流程通过 · 数据质量有警告`
              : `通过 · ${validation.folds} 个验证折叠`
            : deps.localeText("验证完成，待敏感性检验；指标不作结论")
          : "未通过 · 需要补充验证",
      );
    } else {
      const applicable = (data.audit || []).filter(
        (item) => item.status !== "不适用",
      );
      const passed = applicable.filter((item) => item.status === "通过").length;
      deps.setText("backtestAudit", `${passed}/${applicable.length} 项通过`);
    }
    const capacity = data.capacity_report || {};
    const capacityMax = Number(capacity.max_participation);
    const industryRows = Array.isArray(data.industry_trade_report?.rows)
      ? data.industry_trade_report.rows
      : [];
    const industryReport = data.industry_trade_report || {};
    const unknownIndustryFills = industryRows
      .filter((item) => String(item.industry || "").trim() === "未知行业")
      .reduce((sum, item) => sum + (Number(item.fills) || 0), 0);
    const hasUnknownIndustryRow = industryRows.some(
      (item) => String(item.industry || "").trim() === "未知行业",
    );
    const reportedIndustryFills =
      Number(industryReport.classified_fills ?? 0) +
      Number(industryReport.unclassified_fills ?? 0);
    const expectedFills = Number(data.fills) || reportedIndustryFills;
    const allFillsAreUnknown =
      expectedFills > 0 &&
      (unknownIndustryFills >= expectedFills ||
        (hasUnknownIndustryRow &&
          unknownIndustryFills === 0 &&
          industryRows.length === 1));
    const legacyIndustryInvalid =
      allFillsAreUnknown &&
      (!Number.isFinite(Number(industryReport.classified_fills)) ||
        Number(industryReport.classified_fills) === 0);
    const auditHost = requiredElement("backtestAudit");
    if (auditHost && (Number.isFinite(capacityMax) || industryRows.length)) {
      const capacityText = Number.isFinite(capacityMax)
        ? `${deps.localeText("成交容量")} ${deps.formatPercent(capacityMax)}`
        : `${deps.localeText("成交容量")} —`;
      const industryText = legacyIndustryInvalid
        ? `行业归因无效 · ${unknownIndustryFills || expectedFills}/${expectedFills} 笔历史成交未分类，旧快照不可用于分析`
        : industryRows.length
          ? `${deps.localeText("行业成交")} ${industryRows
              .slice(0, 2)
              .map(
                (item) =>
                  `${item.industry} ${deps.formatPercent(Number(item.share))}`,
              )
              .join(" / ")}`
          : `${deps.localeText("行业成交")} —`;
      auditHost.textContent = `${auditHost.textContent} · ${capacityText} · ${industryText}`;
    }
    const brinson = data.brinson_report || {};
    const contribution = data.security_contribution;
    if (auditHost && contribution) {
      const value =
        contribution.status === "available"
          ? `收益贡献已对账 · 净损益 ¥${deps.formatNumber(contribution.net_pnl)} · 费用 ¥${deps.formatNumber(contribution.fees)}`
          : "收益贡献待复核，不能作为完整归因";
      auditHost.textContent = `${auditHost.textContent} · ${value}`;
    }
    if (auditHost && brinson.status) {
      const linked = brinson.linked || {};
      const brinsonText =
        brinson.status === "available"
          ? `严格 PIT Brinson · 配置 ${deps.formatPercent(Number(linked.allocation) || 0)} · 选股 ${deps.formatPercent(Number(linked.selection) || 0)} · 交互 ${deps.formatPercent(Number(linked.interaction) || 0)}`
          : `严格 PIT Brinson 未启用 · ${brinson.reason || "行业/基准时点数据不完整"}`;
      auditHost.textContent = `${auditHost.textContent} · ${brinsonText}`;
    }
    const modelFit = data.factor_pipeline?.latest_fit;
    if (auditHost && data.strategy === "lightgbm" && modelFit) {
      auditHost.textContent = `${auditHost.textContent} · LightGBM ${modelFit.status || "—"} · 样本 ${modelFit.training_samples || 0} · 特征仓库 ${modelFit.data_version || "sealed"}`;
    }
    if (!restored)
      deps.renderGuardrails({
        warnings: data.data_quality?.warnings || [],
        portfolio: {
          ...portfolio,
          audit: data.audit,
          strategy_validation: data.strategy_validation,
          data_quality: data.data_quality,
        },
      });
    renderBacktestRiskAnalytics(data.risk_analytics || null);
    if (!restored)
      deps.applyRiskSnapshot(
        deps.latestPaperState || deps.latestWorkspaceSnapshot?.paper || {},
        portfolio,
        deps.latestRiskLimits,
      );
    APiCharts?.draw();
    defaultDisplay.applyWorkspace();
    if (!restored) {
      loadBenchmarkReports(data);
      deps.loadAdjustmentHealth(data.parameters?.symbols);
      deps.loadOperationsStatus();
    } else {
      deps.setText("backtestStatus", "已恢复历史回测 · 非今日实时结果");
      deps.setText("backtestBenchmark", "历史基准需重新校验");
      loadBenchmarkReports(data);
    }
  }

  /** @type {import('../types/services').SharedServices['restoreBacktestResult']} */
  async function restoreBacktestResult(url) {
    if (!/^\/api\/backtest\/BT-[\w-]+$/.test(String(url || ""))) return false;
    const inFlight = deps.backtestRestoreRequests.get(url);
    if (inFlight) return inFlight;
    deps.restoringBacktestUrl = url;
    const request = (async () => {
      try {
        const response = await fetch(url, { cache: "no-store" });
        const job = await response.json();
        if (
          !response.ok ||
          !job.ok ||
          job.status !== "completed" ||
          !job.result?.metrics
        )
          throw new Error(job.error || "历史回测产物不可用");
        applyBacktestResult(
          {
            ...job.result,
            job_id: job.job_id,
            completed_at: job.completed_at || job.created_at,
          },
          { restored: true },
        );
        return true;
      } catch (errorCaught) {
        const error = toError(errorCaught);
        deps.setText("backtestStatus", "历史回测恢复失败");
        deps.setText("backtestAudit", `历史回测恢复失败：${error.message}`);
        return false;
      }
    })();
    deps.backtestRestoreRequests.set(url, request);
    try {
      return await request;
    } finally {
      deps.backtestRestoreRequests.delete(url);
      deps.restoringBacktestUrl =
        deps.backtestRestoreRequests.keys().next().value || null;
    }
  }

  /** @param {{month: string, return: number, start_date?: string, end_date?: string}[]} rows */
  function renderMonthlyReturns(rows) {
    const host = requiredElement("backtestMonthly");
    if (!host) return;
    if (!Array.isArray(rows) || !rows.length) {
      host.textContent = "—";
      return;
    }
    setSafeHtml(
      host,
      rows
        .map((item) => {
          const value = Number(item.return);
          const cls = value >= 0 ? "positive" : "negative";
          return `<span class="ui-decoration ${cls}" title="${deps.escapeHtml(item.start_date || "")} → ${deps.escapeHtml(item.end_date || "")}">${deps.escapeHtml(item.month || "—")} ${Number.isFinite(value) ? deps.formatPercent(value) : "—"}</span>`;
        })
        .join(""),
    );
  }

  let benchmarkRequest = 0;
  /** @param {import('../types/domain').BacktestResult} data */
  async function loadBenchmarkReports(data) {
    const request = ++benchmarkRequest;
    const host = requiredElement("backtestBenchmark");
    if (!host) return;
    const curve = Array.isArray(data.equity_curve)
      ? data.equity_curve.filter(
          (item) => item && item.date && Number.isFinite(Number(item.equity)),
        )
      : [];
    if (curve.length < 2) {
      host.textContent = "—";
      return;
    }
    const start = curve[0].date;
    const end = curve[curve.length - 1].date;
    try {
      const codes = ["000300.SH", "000905.SH"];
      const reports = await Promise.all(
        codes.map(async (code) => {
          const params = new URLSearchParams({
            ts_code: code,
            start_date: start,
            end_date: end,
            experiment_id: String(data.research?.experiment_id || ""),
            strategy_initial: String(
              Number(data.parameters?.initial_cash) || 1000000,
            ),
            strategy_final: String(Number(data.final_equity) || 0),
          });
          const response = await fetch(
            `/api/reports/benchmark?${params.toString()}`,
            { cache: "no-store" },
          );
          const payload = await response.json();
          if (!response.ok || !payload.ok)
            throw new Error(payload.error || `HTTP ${response.status}`);
          const report = payload.benchmark_report || {};
          const label = payload.benchmark?.name || code;
          if (
            request === benchmarkRequest &&
            code === "000300.SH" &&
            deps.latestBacktestResult === data
          ) {
            data.benchmark_curve = (payload.benchmark?.rows || [])
              .map(
                /** @param {{trade_date?:string,date?:string,close?:number}} row */
                (row) => ({
                  date: String(row.date || row.trade_date || "").replace(
                    /^(\d{4})(\d{2})(\d{2})$/,
                    "$1-$2-$3",
                  ),
                  equity: Number(row.close),
                }),
              )
              .filter(
                /** @param {{date:string,equity:number}} p */
                (p) => p.date && Number.isFinite(p.equity) && p.equity > 0,
              );
            APiCharts.draw();
          }
          return `${label} ${Number.isFinite(Number(report.excess_return)) ? deps.formatPercent(Number(report.excess_return)) : "—"}`;
        }),
      );
      if (request !== benchmarkRequest) return;
      host.textContent = reports.join(" · ");
    } catch (errorCaught) {
      if (request !== benchmarkRequest) return;
      const error = toError(errorCaught);
      host.textContent = deps.localeText("基准暂不可用");
    }
  }
  return {
    applyBacktestResult,
    restoreBacktestResult,
    renderMonthlyReturns,
    loadBenchmarkReports,
    renderBacktestRiskAnalytics,
  };
}
