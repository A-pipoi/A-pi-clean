import { setSafeHtml } from "./safe-html.mjs";
import { requiredElement } from "./runtime-utils.mjs";
import { APiNotifications } from "./notification-center.mjs";
import { APiPaperPage, paperSchedulerStatus } from "./paper-page.mjs";
import { APiRiskView } from "./risk-view.js";
import { defaultBacktestDisplay } from "./backtest-default-display.mjs";
import {
  presentationState,
  applyPresentationState,
} from "./presentation-state.mjs";
/** workspace presenter with explicit services and live state accessors. */
/** @param {import('../types/services').WorkspacePresenterServices} deps */
export function createWorkspacePresenter(deps) {
  /** @type {import('../types/services').SharedServices['renderGuardrails']} */
  function renderGuardrails(snapshot = {}) {
    const host = requiredElement("guardrailList");
    if (!host) return;
    const portfolio = snapshot.portfolio || {};
    const validation =
      portfolio.strategy_validation || snapshot.strategy_validation;
    const audit = portfolio.audit || snapshot.audit || [];
    const warnings = [
      ...new Set([
        ...(snapshot.warnings || []),
        ...(portfolio.data_quality?.warnings || []),
        ...(snapshot.data_quality?.warnings || []),
      ]),
    ];
    const checks = validation?.checks?.length ? validation.checks : audit;
    const rows = checks
      .filter((item) => item && item.status !== "不适用")
      .map((item) => ({
        label: item.name || "真实数据校验",
        status: item.status || "未知",
        evidence: item.evidence || "",
      }));
    if (warnings.length)
      rows.push({
        label: "数据质量提示",
        status: "提示",
        evidence: warnings.join("；"),
      });
    if (!rows.length) {
      setSafeHtml(
        host,
        '<div class="audit-row"><span class="audit-warn">…</span><span>正在读取真实 Tushare 校验结果</span><b>加载中</b></div>',
      );
      deps.setText("guardrailStatus", "等待真实校验");
      return;
    }
    setSafeHtml(
      host,
      rows
        .map((item) => {
          const passed = item.status === "通过";
          const neutral = item.status === "不适用";
          const iconClass = passed ? "audit-check" : "audit-warn";
          const rowClass = passed ? "passed" : neutral ? "neutral" : "warning";
          return `<div class="audit-row ${rowClass}" title="${deps.escapeHtml(item.evidence)}"><span class="${iconClass}">${passed ? "✓" : neutral ? "–" : "!"}</span><span>${deps.escapeHtml(item.label)}</span><b>${deps.escapeHtml(item.status)}</b></div>`;
        })
        .join(""),
    );
    const validationPassed =
      validation?.passed === true && warnings.length === 0;
    const validationFailed = validation?.passed === false;
    deps.setText(
      "guardrailStatus",
      validationPassed
        ? "验证通过"
        : validationFailed
          ? "需要关注"
          : warnings.length
            ? `数据有 ${warnings.length} 项提示`
            : "已完成真实校验",
    );
  }

  /** @type {import('../types/services').SharedServices['applyWorkspaceSnapshot']} */
  function applyWorkspaceSnapshot(data) {
    deps.latestWorkspaceSnapshot = data;
    deps.latestScheduler = data.scheduler || deps.latestScheduler;
    const snapshotLimits = APiRiskView.normalizeLimits(
      data.risk_limits || data.paper?.risk_limits,
    );
    if (snapshotLimits) deps.latestRiskLimits = snapshotLimits;
    deps.notificationState.workspace = data;
    deps.connectionState.python = true;
    deps.connectionState.tushare =
      deps.notificationState.health?.dataReachable === true;
    deps.updateConnectionStatus();
    APiNotifications?.render();
    const research = data.research || {};
    const portfolioMetrics = data.portfolio?.metrics || {};
    const workspaceGate =
      data.portfolio?.validation_gate ||
      research.validation_gate ||
      data.validation_gate ||
      {};
    deps.setText("authFactorCount", research.factor_count ?? "—");
    deps.setText(
      "authBaselineSharpe",
      workspaceGate.conclusion_ready === true
        ? deps.localeText("验证通过")
        : deps.localeText(workspaceGate.status || "待敏感性检验"),
    );
    deps.setText(
      "authTradeDate",
      data.latest_trade_date || research.latest_trade_date || "—",
    );
    const feedWarningCount = (data.warnings || []).length;
    const warningCount = [
      ...new Set([
        ...(data.warnings || []),
        ...(data.portfolio?.data_quality?.warnings || []),
      ]),
    ].length;
    const servicePill = requiredElement("servicePill");
    const datasetSource =
      data.portfolio?.dataset_metadata?.data_source || data.source;
    const datasetStale =
      data.snapshot_cached ||
      data.portfolio?.dataset_metadata?.cache_used ||
      data.portfolio?.data_freshness?.fresh === false;
    const datasetUsesCache =
      String(datasetSource).toLowerCase() === "tushare_cache" ||
      (String(datasetSource).toLowerCase() === "tushare" && datasetStale);
    const datasetLabel = deps.dataSourceLabel(
      datasetSource,
      Boolean(datasetUsesCache),
    );
    if (servicePill && !deps.notificationState.health)
      setSafeHtml(
        servicePill,
        '<span class="pulse-dot"></span>Python 在线 · 数据探针检查中',
      );
    const hasLiveSnapshot = Boolean(
      data.latest_trade_date && data.source === "tushare",
    );
    deps.setText(
      "dataHealthLabel",
      hasLiveSnapshot
        ? warningCount
          ? `${warningCount} 项数据提示`
          : `${datasetLabel} 数据`
        : "等待 Tushare 数据",
    );
    deps.renderDataHealth(data, deps.notificationState.health);
    deps.applyMarketSnapshot(data.market);
    if (data.snapshot_cached) {
      const marketStatus = requiredElement("marketDataStatus");
      if (marketStatus)
        marketStatus.textContent = `Tushare 缓存快照 · ${Math.ceil(Number(data.snapshot_age_seconds || 0) / 60)} 分钟前生成`;
    }
    deps.applyPortfolioSnapshot(data.portfolio);
    deps.setText("holdingsChangeHeader", "行情日期");
    const backtestEnd = requiredElement("backtestEnd");
    if (backtestEnd && data.latest_trade_date && !backtestEnd.value)
      backtestEnd.value = data.latest_trade_date;
    if (data.portfolio && !deps.latestBacktestResult) {
      const metrics = data.portfolio.metrics || {};
      const conclusionReady = workspaceGate.conclusion_ready === true;
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
      deps.setText(
        "backtestDataset",
        `${datasetLabel} · ${data.portfolio.dataset || "Tushare"} · Python`,
      );
      deps.setText(
        "backtestRunCount",
        `${research.run_count ?? research.runs ?? "—"} / ${research.run_budget ?? research.budget ?? "—"} 次`,
      );
      deps.setText(
        "backtestStatus",
        conclusionReady
          ? "验证通过 · Tushare 快照已加载"
          : deps.localeText(workspaceGate.status || "待敏感性检验"),
      );
      deps.setText(
        "backtestAudit",
        conclusionReady
          ? "验证通过 · 参数敏感性已通过"
          : deps.localeText("验证完成，待敏感性检验；指标不作结论"),
      );
      const dataset = String(data.portfolio.dataset || "Tushare");
      deps.setText("experimentId", `#${dataset.slice(-8).toUpperCase()}`);
    }
    deps.applyResearchSnapshot(data.research || {});
    const workspacePaper = data.paper || {};
    const cachedPaper = deps.latestPaperState || {};
    /** @param {import('../types/domain').PaperState} paper */
    const paperStamp = (paper) =>
      String(paper.as_of || paper.updated_at || paper.quote_date || "");
    const account =
      paperStamp(workspacePaper) >= paperStamp(cachedPaper) &&
      Object.keys(workspacePaper).length
        ? workspacePaper
        : cachedPaper && Object.keys(cachedPaper).length
          ? cachedPaper
          : workspacePaper;
    deps.applySignalSnapshot(data.signals, account);
    APiPaperPage?.apply(account, data.scheduler || null, data.risk_limits);
    deps.applyRiskSnapshot(account, data.portfolio || {}, data.risk_limits);
    deps.setText(
      "todayMarketStatus",
      data.market?.index ? (feedWarningCount ? "有提示" : "已同步") : "不可用",
    );
    deps.setText(
      "todayBacktestStatus",
      data.portfolio?.equity_curve?.length ? "已计算" : "等待",
    );
    const scheduler = data.scheduler || {};
    deps.setText(
      "todayPaperStatus",
      scheduler.last_error ? "有错误" : paperSchedulerStatus(scheduler),
    );
    renderGuardrails(data);
    renderDataProbeStatus();
    defaultBacktestDisplay?.applyWorkspace();
  }

  /** @type {import('../types/services').SharedServices['renderDataProbeStatus']} */
  function renderDataProbeStatus() {
    const health = deps.notificationState.health;
    if (!health?.online) return;
    const probe = health.probe || {};
    const status = probe.status || "pending";
    const label =
      status === "fresh"
        ? "数据可达 · 新鲜"
        : status === "stale"
          ? "数据可达 · 待更新"
          : status === "freshness_unknown"
            ? "数据可达 · 新鲜度未确认"
            : status === "unsupported"
              ? "旧版后端 · 需重启"
              : status === "unreachable"
                ? "数据不可达"
                : status === "unconfigured"
                  ? "Token 未配置"
                  : "正在验证数据";
    const pill = requiredElement("servicePill");
    if (pill) {
      applyPresentationState(
        pill,
        presentationState(health, {
          ready: Boolean(deps.latestWorkspaceSnapshot?.latest_trade_date),
          building: deps.workspaceBuilding,
          error: deps.workspaceLoadError,
        }),
      );
      pill.textContent = deps.localeText("连接详情");
      pill.setAttribute("aria-label", `Python 在线 · ${label}`);
      pill.title = [
        deps.workspaceBuilding && "工作区生成中",
        deps.workspaceLoadError && "工作区重试中",
        probe.checked_at && `探针 ${deps.beijingDateTime(probe.checked_at)}`,
        probe.latest_trade_date && `最新日线 ${probe.latest_trade_date}`,
        probe.expected_trade_date && `应有交易日 ${probe.expected_trade_date}`,
        probe.error,
        deps.workspaceLoadError,
      ]
        .filter(Boolean)
        .join(" · ");
    }
    deps.setText(
      "dataHealthLabel",
      deps.workspaceLoadError
        ? `${deps.workspaceLoadError}；将自动重试`
        : deps.workspaceBuilding
          ? "正在获取真实 Tushare 工作区快照"
          : probe.error || label,
    );
    deps.renderDataHealth(deps.latestWorkspaceSnapshot, health);
  }
  return { renderGuardrails, applyWorkspaceSnapshot, renderDataProbeStatus };
}
