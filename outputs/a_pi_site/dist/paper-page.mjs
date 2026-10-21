import { setSafeHtml } from "./safe-html.mjs";
import { apiFetch as fetch } from "./api-client.mjs";
import { requiredElement, toError } from "./runtime-utils.mjs";
import { openPaperOrderDialog } from "./paper-order-dialog.mjs";
import { createManualPaperCatchup } from "./paper-manual-catchup.mjs";
import {
  initResearchPaper,
  refreshResearchPaper,
} from "./research-paper-page.mjs";
/** @type {import('../types/services').PaperPageServices} */
let services;
let initialized = false;
let executionInProgress = false;
/** @type {import('../types/domain').PaperState} */
let latestPaper = {};
/** @type {ReturnType<typeof createManualPaperCatchup>} */
let manualCatchupUI = null;

/** @param {import('../types/domain').Scheduler | null} scheduler */
export function paperSchedulerOnline(scheduler) {
  return scheduler?.scheduler_running ?? scheduler?.running ?? false;
}

/** @param {import('../types/domain').Scheduler | null} scheduler */
export function paperSchedulerStatus(scheduler) {
  if (scheduler?.execution_in_progress) return "执行中";
  if (scheduler?.enabled === false) return "已停用";
  return paperSchedulerOnline(scheduler) ? "调度在线 · 等待执行" : "待启动";
}

/** @param {import('../types/domain').Scheduler | null} scheduler */
export function paperRunExplanation(scheduler) {
  const audit = scheduler?.maintenance?.latest;
  const result = audit?.result || scheduler?.last_result;
  /** @type {Record<string, string>} */
  const states = {
    running: "执行中",
    pending: "待执行",
    retry_pending: "等待重试",
    retrying: "重试中",
    completed: "已完成",
    success: "已完成",
    succeeded: "已完成",
    failed: "失败",
    skipped: "已跳过",
    missed_window: "错过执行窗口",
    no_trade: "未交易",
    not_rebalance_day: "未到调仓日",
    risk_rejected: "委托被风控拒绝",
    traded: "已有纸面成交",
    blocked: "校验阻止交易",
    waiting_data: "等待当日数据",
    market_closed: "A 股休市",
    exchange_closed: "交易所休市",
    maintenance_only: "已完成账户维护，未交易",
    orders_pending: "委托等待后续交易日执行",
    outside_window: "超出执行窗口",
    already_running: "当前账号正在执行",
    maintenance_not_due: "维护已结束或等待重试",
    daily_cycle_completed: "当日循环已完成",
    config_changed: "账号配置已变化",
    unfinished_cycle_conflict: "存在未完成的占用记录，请人工复核",
    recovering_or_retrying: "正在恢复或重试",
    window_expired: "执行窗口已结束",
    governance_blocked: "交易门禁未通过",
    waiting_next_session: "等待下一交易日",
    daily_maintenance_completed: "每日维护已完成",
  };
  /** @param {string | null | undefined} value */
  const label = (value) => states[String(value).toLowerCase()] || value;
  const parts = [];
  if (scheduler?.execution_in_progress) parts.push("纸面循环执行中。");
  else if (scheduler?.enabled === false) parts.push("自动调度已关闭。");
  else if (!scheduler) parts.push("调度状态待加载。");
  else if (paperSchedulerOnline(scheduler)) parts.push("调度在线 · 等待执行。");
  else parts.push("调度线程未运行。");
  if (scheduler?.last_error)
    parts.push(`最近执行错误：${scheduler.last_error}。`);
  if (result) {
    const reason = result.no_trade_reason || result.skip_reason;
    if (result.outcome) parts.push(`最近结果：${label(result.outcome)}。`);
    if (reason) parts.push(`无交易原因：${reason}。`);
    else if (result.generated_orders === 0)
      parts.push("本次未生成委托，接口未提供无交易原因。");
    else if (result.generated_orders != null)
      parts.push(
        `本次生成 ${result.generated_orders} 个委托信号，成交以委托状态为准。`,
      );
    const rebalance = result.rebalance_schedule;
    if (rebalance) {
      const elapsed =
        rebalance.elapsed_trading_days ?? rebalance.trading_days_since_previous;
      const details = [];
      if (rebalance.interval_trading_days != null)
        details.push(`每 ${rebalance.interval_trading_days} 个交易日`);
      if (elapsed != null) details.push(`已间隔 ${elapsed} 个交易日`);
      if (rebalance.next_rebalance_date)
        details.push(`下次 ${rebalance.next_rebalance_date}`);
      if (rebalance.next_rebalance_in_trading_days != null)
        details.push(
          `距离下次 ${rebalance.next_rebalance_in_trading_days} 个交易日`,
        );
      if (rebalance.due != null)
        details.push(rebalance.due ? "已到调仓日" : "未到调仓日");
      if (details.length) parts.push(`调仓：${details.join(" · ")}。`);
    }
    if (result.next_rebalance) {
      const next = result.next_rebalance;
      if (next.remaining_trading_days != null)
        parts.push(
          `下次调仓：剩余 ${next.remaining_trading_days} 个交易日${next.anchor_date ? ` · 起算日 ${next.anchor_date}` : ""}。`,
        );
      if (next.reason) parts.push(`调仓依据：${next.reason}。`);
    }
    if (result.maintenance_stages) {
      /** @type {Record<string, string>} */
      const names = {
        quotes: "行情更新",
        equity: "权益估值",
        reconciliation: "账户核对",
        pending_orders: "待执行委托",
        rebalance: "调仓检查",
        corporate_actions: "公司行动处理",
        record_entitlements: "权益登记",
        entitlements: "权益登记",
        reconcile_positions: "持仓核对",
        record_quotes: "行情更新",
        record_equity: "权益估值",
        process_pending_orders: "待执行委托处理",
        strategy_validation: "策略验证",
        risk_checks: "风险检查",
      };
      const stages = Object.entries(result.maintenance_stages).map(
        ([key, stage]) => {
          const value =
            typeof stage === "object" && stage !== null
              ? stage
              : { status: stage };
          return `${names[key] || key}：${label(String(value.status || "状态未提供"))}${value.reason ? `（${String(value.reason)}）` : ""}`;
        },
      );
      if (stages.length) parts.push(`每日维护：${stages.join(" · ")}。`);
    }
  }
  if (audit) {
    parts.push(
      `每日运行审计：${audit.run_date || "日期未提供"} · ${label(audit.status) || "状态未提供"} · 尝试 ${audit.attempts ?? "—"} 次。`,
    );
    if (audit.reason_code)
      parts.push(`审计原因：${label(audit.reason_code)}。`);
    if (audit.started_at) parts.push(`开始：${audit.started_at}。`);
    if (audit.finished_at) parts.push(`结束：${audit.finished_at}。`);
    if (audit.next_attempt_at)
      parts.push(`后端计划重试：${audit.next_attempt_at}。`);
  } else parts.push("每日运行审计：暂无记录。");
  if (scheduler?.maintenance?.missed_window)
    parts.push("已错过自动执行窗口；请查看审计原因，页面不会自动补跑。");
  return parts.join(" ");
}

/** @param {import('../types/domain').PaperState} result */
export function paperCycleMessage(result) {
  if (
    result.skipped ||
    ["outside_window", "maintenance_not_due", "already_running"].includes(
      result.outcome || "",
    )
  )
    return `策略日循环已跳过 · ${result.skip_reason || result.no_trade_reason || "接口未提供原因"}`;
  if (result.no_trade_reason)
    return `策略日循环完成 · ${result.no_trade_reason}`;
  return `策略日循环完成 · ${result.generated_orders ?? 0} 个委托信号，成交以委托状态为准`;
}

/** @type {import('../types/services').SharedServices['paperStatusLabel']} */
export function paperStatusLabel(status) {
  return (
    {
      NEW: "待成交",
      PARTIALLY_FILLED: "部分成交",
      FILLED: "已完成",
      REJECTED: "已拒绝",
      CANCELLED: "已撤销",
    }[String(status)] ||
    String(status || "") ||
    "未知"
  );
}

/** @param {import('../types/domain').PaperState | null} paper @param {import('../types/domain').Scheduler | null} scheduler @param {import('../types/domain').Workspace | null} workspace */
export function paperTradingBlocked(paper, scheduler = null, workspace = null) {
  const quality =
    paper?.data_quality ||
    scheduler?.last_result?.data_quality ||
    workspace?.portfolio?.data_quality ||
    {};
  const freshness =
    paper?.data_freshness ||
    scheduler?.last_result?.data_freshness ||
    workspace?.portfolio?.data_freshness ||
    {};
  const allowed =
    paper?.paper_trading_allowed === true ||
    scheduler?.last_result?.paper_trading_allowed === true ||
    workspace?.portfolio?.paper_trading_allowed === true;
  return (
    !allowed || quality.limits_available === false || freshness.fresh === false
  );
}

/** @param {unknown} source */
function paperSourceLabel(source) {
  const value = String(source || "manual");
  if (value.startsWith("strategy:"))
    return `${services.localeText("策略")} · ${value.slice("strategy:".length)}`;
  return services.localeText("手工委托");
}

/** @type {import('../types/services').SharedServices['fetchPaperState']} */
export async function fetchPaperState(showSuccess = false) {
  if (!services) return false;
  try {
    const response = await fetch("/api/paper/state");
    const data = await response.json();
    if (!response.ok || !data.ok)
      throw new Error(data.error || `HTTP ${response.status}`);
    const paperState = {
      ...(data.paper || {}),
      data_quality: data.data_quality ?? data.paper?.data_quality,
      data_freshness: data.data_freshness ?? data.paper?.data_freshness,
      paper_trading_allowed:
        data.paper_trading_allowed ?? data.paper?.paper_trading_allowed,
    };
    applyPaperState(paperState, data.scheduler || null, data.risk_limits);
    await loadOrderPage();
    void refreshResearchPaper();
    if (showSuccess) services.showToast("纸面账户已刷新");
    return true;
  } catch (errorCaught) {
    const error = toError(errorCaught);
    const status = requiredElement("paperStatus");
    if (status)
      setSafeHtml(
        status,
        `<span class="pulse-dot"></span>纸面账户刷新失败 · ${services.escapeHtml(error.message)}`,
      );
    const homePaperStatus = requiredElement("homePaperStatus");
    if (homePaperStatus)
      setSafeHtml(
        homePaperStatus,
        `<span class="ui-decoration"></span>刷新失败`,
      );
    if (showSuccess)
      services.showToast(`纸面交易服务未连接 · ${error.message}`);
    return false;
  }
}

/** @param {import('../types/domain').PaperState} paper @param {import('../types/domain').Scheduler | null} scheduler @param {import('../types/domain').RiskLimits | null} riskLimits */
export function applyPaperState(paper, scheduler = null, riskLimits = null) {
  if (!services) return;
  latestPaper = paper;
  manualCatchupUI?.update(scheduler);
  services.setPaperState(paper, scheduler, riskLimits);
  const workspace = services.getWorkspace();
  const equity = Number(paper.equity);
  const cash = Number(paper.cash);
  const marketValue = Number(paper.market_value);
  const positionCount = (paper.positions || []).length;
  const freshness =
    paper?.data_freshness ||
    scheduler?.last_result?.data_freshness ||
    workspace?.portfolio?.data_freshness ||
    {};
  const paperBlocked = paperTradingBlocked(paper, scheduler, workspace);
  executionInProgress = scheduler?.execution_in_progress === true;
  const runExplanation = requiredElement("paperRunExplanation");
  if (runExplanation)
    runExplanation.textContent = paperRunExplanation(scheduler);
  const cycleButton = requiredElement("paperCycleButton");
  cycleButton.disabled =
    executionInProgress ||
    cycleButton.dataset.running === "true" ||
    cycleButton.dataset.manualRunning === "true";
  cycleButton.textContent = services.localeText(
    cycleButton.disabled ? "纸面循环执行中…" : "手动运行一次",
  );
  cycleButton.setAttribute("aria-busy", String(cycleButton.disabled));
  const datasetSource =
    paper.data_source ||
    workspace?.portfolio?.dataset_metadata?.data_source ||
    freshness.source ||
    "unknown";
  const sourceLabel = services.dataSourceLabel(
    datasetSource,
    String(datasetSource).toLowerCase() === "tushare_cache",
  );
  const exposure =
    equity > 0 && Number.isFinite(marketValue) ? marketValue / equity : null;
  services.setText("paperEquity", `¥ ${services.formatNumber(equity)}`);
  services.setText("paperCash", `¥ ${services.formatNumber(cash)}`);
  services.setText("paperReturn", services.formatPercent(paper.return_pct));
  services.setText("paperPositions", String(positionCount));
  services.setText(
    "paperQuality",
    `${sourceLabel} Tushare · ${freshness.latest_trade_date || paper.latest_trade_date || "—"}`,
  );
  services.setText("homePaperEquity", `¥ ${services.formatNumber(equity)}`);
  services.setText("homePaperCash", `¥ ${services.formatNumber(cash)}`);
  services.setText("homePaperPositions", `${positionCount} 只`);
  services.setText("homePaperReturn", services.formatPercent(paper.return_pct));
  const homePaperReturn = requiredElement("homePaperReturn");
  homePaperReturn?.classList.toggle(
    "positive",
    Number.isFinite(Number(paper.return_pct)) && Number(paper.return_pct) > 0,
  );
  homePaperReturn?.classList.toggle(
    "negative",
    Number.isFinite(Number(paper.return_pct)) && Number(paper.return_pct) < 0,
  );
  services.setText(
    "homePaperExposure",
    Number.isFinite(exposure) ? `${(Number(exposure) * 100).toFixed(1)}%` : "—",
  );
  const homeExposureBar = requiredElement("homePaperExposureBar");
  if (homeExposureBar)
    homeExposureBar.style.width = `${Math.min(100, Math.max(0, (exposure || 0) * 100))}%`;
  const status = requiredElement("paperStatus");
  const paperOnline = scheduler?.last_error ? false : true;
  if (status) status.classList.toggle("is-blocked", paperBlocked);
  if (status)
    setSafeHtml(
      status,
      scheduler?.execution_in_progress
        ? '<span class="pulse-dot"></span>纸面循环执行中'
        : scheduler?.last_error
          ? '<span class="pulse-dot"></span>纸面循环有错误'
          : paperBlocked
            ? `<span class="pulse-dot"></span>委托已锁定 · ${paperBlocked && freshness.source === "market_closed" ? "A股休市" : sourceLabel + "数据未通过校验"}`
            : paperSchedulerOnline(scheduler) && scheduler?.enabled !== false
              ? '<span class="pulse-dot"></span>调度线程在线 · 等待执行'
              : '<span class="pulse-dot"></span>纸面账户已连接',
    );
  const homePaperStatus = requiredElement("homePaperStatus");
  if (homePaperStatus)
    setSafeHtml(
      homePaperStatus,
      `<span class="ui-decoration"></span>${paperOnline ? "已连接" : "有错误"}`,
    );
  const newOrderButton = requiredElement("newOrderButton");
  if (newOrderButton) {
    newOrderButton.disabled = paperBlocked;
    newOrderButton.title = paperBlocked
      ? "实时行情、stk_limit 涨跌停约束和数据新鲜度校验通过后才能委托"
      : "新建纸面委托";
  }
  services.setText(
    "exposureValue",
    equity > 0 && Number.isFinite(marketValue)
      ? `${((marketValue / equity) * 100).toFixed(1)}%`
      : "—",
  );
  const allocation = requiredElement("allocationList");
  const positions = paper.positions || [];
  if (allocation) {
    setSafeHtml(
      allocation,
      positions.length
        ? positions
            .map((position, index) => {
              const ratio =
                equity > 0 ? Number(position.market_value) / equity : 0;
              return `<div><span class="color-key k${(index % 4) + 1}"></span><span>${services.escapeHtml(position.symbol)}</span><strong>${(ratio * 100).toFixed(1)}%</strong></div>`;
            })
            .join("")
        : '<div><span class="color-key k4"></span><span>暂无纸面持仓</span><strong>—</strong></div>',
    );
  }
  services.renderAllocationDonut(positions, equity, marketValue);
  services.applySignalSnapshot(workspace?.signals || [], paper);
  const body = requiredElement("paperOrdersBody");
  if (!body) {
    services.applyLocale(services.getLocale());
    return;
  }
  renderOrderRows(orderRows ?? (paper.orders || []).slice(0, 20));
  services.applyRiskSnapshot(
    paper,
    workspace?.portfolio || {},
    services.getRiskLimits(),
  );
}

function createPaperOrder() {
  openPaperOrderDialog({
    translate: services.localeText,
    allowed: () => !requiredElement("newOrderButton").disabled,
    submit: async ({ symbol, side, quantity }, idempotencyKey) => {
      const response = await fetch("/api/paper/order", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify({
          symbol,
          side,
          quantity,
          idempotency_key: idempotencyKey,
        }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok)
        throw new Error(data.error || `HTTP ${response.status}`);
      const paperState = {
        ...(data.paper || {}),
        data_quality: data.data_quality,
        data_freshness: data.data_freshness,
        paper_trading_allowed: data.paper_trading_allowed,
      };
      applyPaperState(paperState, data.scheduler || null, data.risk_limits);
      orderPage = 1;
      await loadOrderPage();
      services.showToast(
        data.idempotent_replay
          ? "已返回同一幂等委托"
          : `${side === "BUY" ? "买入" : "卖出"}纸面委托已处理`,
      );
    },
  });
}

async function runPaperCycle() {
  const button = requiredElement("paperCycleButton");
  if (
    button.dataset.running === "true" ||
    button.dataset.manualRunning === "true" ||
    executionInProgress
  )
    return;
  button.dataset.running = "true";
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.textContent = services.localeText("策略运行中…");
  try {
    const response = await fetch("/api/paper/cycle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await response.json();
    if (!response.ok || !data.ok)
      throw new Error(data.error || `HTTP ${response.status}`);
    const paperState = {
      ...latestPaper,
      ...(data.state || {}),
      data_quality: data.data_quality ?? latestPaper.data_quality,
      data_freshness: data.data_freshness ?? latestPaper.data_freshness,
      paper_trading_allowed:
        data.paper_trading_allowed ?? latestPaper.paper_trading_allowed,
    };
    applyPaperState(paperState, data.scheduler || null, data.risk_limits);
    orderPage = 1;
    await loadOrderPage();
    services.showToast(paperCycleMessage(data));
  } catch (errorCaught) {
    const error = toError(errorCaught);
    services.showToast(`策略日循环失败 · ${error.message}`);
  } finally {
    button.dataset.running = "false";
    button.disabled = executionInProgress;
    button.setAttribute("aria-busy", String(executionInProgress));
    button.textContent = services.localeText(
      executionInProgress ? "纸面循环执行中…" : "手动运行一次",
    );
  }
}

/** @param {import('../types/services').PaperPageServices} dependencies */
export function initPaperPage(dependencies) {
  services = dependencies;
  if (initialized) return;
  initResearchPaper();
  manualCatchupUI = createManualPaperCatchup({
    translate: services.localeText,
    refresh: () => fetchPaperState(false),
    onBusy: (busy) => {
      const button = requiredElement("paperCycleButton");
      button.dataset.manualRunning = String(busy);
      button.disabled =
        busy || executionInProgress || button.dataset.running === "true";
    },
    apply: (data) =>
      applyPaperState(
        {
          ...latestPaper,
          ...(data.state || {}),
          data_quality: data.data_quality ?? latestPaper.data_quality,
          data_freshness: data.data_freshness ?? latestPaper.data_freshness,
          paper_trading_allowed:
            data.paper_trading_allowed ?? latestPaper.paper_trading_allowed,
        },
        data.scheduler || null,
      ),
  });
  requiredElement("newOrderButton")?.addEventListener(
    "click",
    createPaperOrder,
  );
  requiredElement("paperRefreshButton")?.addEventListener("click", () =>
    fetchPaperState(true),
  );
  requiredElement("paperCycleButton")?.addEventListener("click", runPaperCycle);
  initialized = true;
}

export const APiPaperPage = {
  init: initPaperPage,
  fetch: fetchPaperState,
  apply: applyPaperState,
  statusLabel: paperStatusLabel,
};

/** @type {NonNullable<import('../types/domain').PaperState['orders']> | null} */
let orderRows = null;
let orderPage = 1,
  orderPages = 1,
  orderTotal = 0,
  orderLoading = false,
  loadedOrderPage = 1,
  orderRequest = 0;
/** @type {HTMLElement|null} */
let orderPager = null;
/** @param {NonNullable<import('../types/domain').PaperState['orders']>} orders */
function renderOrderRows(orders) {
  const body = requiredElement("paperOrdersBody");
  if (!body) return;
  setSafeHtml(
    body,
    orders.length
      ? orders
          .map((order) => {
            const buy = order.side === "BUY";
            const statusClass =
              order.status === "FILLED"
                ? "filled"
                : order.status === "REJECTED"
                  ? "blocked"
                  : "partial";
            return `<tr><td><code>${services.escapeHtml(order.order_id)}</code></td><td><strong>${services.escapeHtml(order.symbol)}</strong></td><td class="${buy ? "rise" : "fall"}">${buy ? "买入" : "卖出"}</td><td class="num">${services.formatNumber(Number(order.quantity), 0)}</td><td class="num">${services.formatNumber(Number(order.filled_quantity), 0)}</td><td><span class="order-status ${statusClass}">${paperStatusLabel(order.status)}</span></td><td>${services.escapeHtml(paperSourceLabel(order.source))}</td><td>${services.escapeHtml(services.beijingDateTime(order.created_at))}</td></tr>`;
          })
          .join("")
      : '<tr><td colspan="8">暂无纸面委托</td></tr>',
  );
}
async function loadOrderPage() {
  if (!services) return;
  const request = ++orderRequest;
  const focusedAction = orderPager?.contains(document.activeElement)
    ? document.activeElement?.getAttribute("data-page-action")
    : null;
  orderLoading = true;
  renderOrderPager();
  try {
    const response = await fetch(
      "/api/paper/orders?page=" + orderPage + "&page_size=20",
      { cache: "no-store", signal: AbortSignal.timeout(15000) },
    );
    const data = await response.json();
    if (request !== orderRequest) return;
    if (!response.ok || !data.ok || !Array.isArray(data.orders))
      throw Error(data.error || "历史委托分页不可用");
    orderRows = data.orders.slice(0, 20);
    orderPage = data.page;
    loadedOrderPage = orderPage;
    orderPages = data.pages;
    orderTotal = data.total;
    renderOrderRows(orderRows || []);
  } catch (caught) {
    if (request !== orderRequest) return;
    orderPage = loadedOrderPage;
    services.showToast("历史委托加载失败 · " + toError(caught).message);
  } finally {
    if (request === orderRequest) {
      orderLoading = false;
      renderOrderPager();
      if (focusedAction) {
        const focused =
          orderPager?.querySelector(
            `button[data-page-action="${focusedAction}"]:not(:disabled)`,
          ) || orderPager?.querySelector("button:not(:disabled)");
        if (focused instanceof HTMLElement) focused.focus();
      }
    }
  }
}
function renderOrderPager() {
  if (!orderPager) {
    orderPager = document.createElement("nav");
    orderPager.className = "order-pagination";
    orderPager.setAttribute("aria-label", "纸面委托分页");
    requiredElement("paperOrdersBody")?.closest("table")?.after(orderPager);
  }
  orderPager.replaceChildren();
  const label = document.createElement("span");
  label.textContent = orderLoading
    ? "正在读取委托…"
    : "第 " + orderPage + " / " + orderPages + " 页 · 共 " + orderTotal + " 条";
  const previous = document.createElement("button"),
    next = document.createElement("button");
  previous.type = next.type = "button";
  previous.dataset.pageAction = "previous";
  next.dataset.pageAction = "next";
  previous.textContent = "上一页";
  next.textContent = "下一页";
  previous.disabled = orderLoading || orderPage <= 1;
  next.disabled = orderLoading || orderPage >= orderPages;
  previous.addEventListener("click", () => {
    if (orderPage <= 1) return;
    orderPage--;
    void loadOrderPage();
  });
  next.addEventListener("click", () => {
    if (orderPage >= orderPages) return;
    orderPage++;
    void loadOrderPage();
  });
  orderPager.append(label, previous, next);
}
