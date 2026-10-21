import { setSafeHtml } from "./safe-html.mjs";
import { requiredElement } from "./runtime-utils.mjs";
import { APiCharts } from "./charts.mjs";
import { APiFactorMiningPanel } from "./factor-mining-panel.mjs";
import { APiNotifications } from "./notification-center.mjs";
import { paperSchedulerStatus } from "./paper-page.mjs";
/** app shell with explicit services and live state accessors. */
/** @param {import('../types/services').AppShellServices} deps */
export function createAppShell(deps) {
  /** @type {string | null} */
  let pendingRiskAlertId = null;
  /** @type {string[]} */
  const toastQueue = [];
  let displayingToast = false;
  /** @param {string} email */
  function enterWorkspace(email, silent = false) {
    document.documentElement.dataset.username = email;
    requiredElement("workspaceName").textContent =
      `${deps.localeText("研究工作区")} · ${email}`;
    deps.authView.classList.add("hidden");
    deps.appShell.classList.add("authenticated");
    const rawDisplayName = email || "用户";
    const isGuest = rawDisplayName.toLowerCase() === "guest";
    const displayName = isGuest ? "用户" : rawDisplayName;
    const accountLabel = document.querySelector(
      ".user-chip > span:nth-child(2)",
    );
    const accountAvatar = document.querySelector(".user-chip .avatar");
    if (accountLabel) accountLabel.textContent = displayName;
    if (accountAvatar)
      accountAvatar.textContent = displayName.slice(0, 1).toUpperCase();
    deps.setText("welcomeName", isGuest ? "量化研究员" : displayName);
    restoreRoute();
    window.requestAnimationFrame(() => APiCharts?.draw());
    if (!silent) showToast("已进入 A-pi 研究工作区");
  }

  function exitWorkspace() {
    delete document.documentElement.dataset.username;
    localStorage.removeItem(deps.sessionKey);
    sessionStorage.removeItem(deps.sessionKey);
    deps.appShell.classList.remove("authenticated");
    deps.authView.classList.remove("hidden");
    requiredElement("authUsername")?.focus();
  }

  /** @type {import('../types/services').SharedServices['showSection']} */
  function showSection(id) {
    /* empty-fix: fallback */
    const known = deps.sections.some((section) => section.id === id);
    if (!known) id = "overview";

    if (!Object.hasOwn(deps.sectionNames, id)) id = "overview";
    if (window.location.hash !== `#/${id}`) {
      const previous =
        document.querySelector(".page-section.active")?.id || "overview";
      window.history.pushState({ aPiPreviousSection: previous }, "", `#/${id}`);
    }
    deps.sections.forEach((section) =>
      section.classList.toggle("active", section.id === id),
    );
    deps.navItems.forEach((item) => {
      item.classList.toggle("active", item.dataset.section === id);
      if (item.dataset.section === id) {
        item.setAttribute("aria-current", "page");
        const rail = item.parentElement;
        if (rail && rail.scrollWidth > rail.clientWidth && rail.scrollTo) {
          rail.scrollTo({
            left:
              item.offsetLeft -
              rail.offsetLeft -
              (rail.clientWidth - item.offsetWidth) / 2,
            behavior: "auto",
          });
        }
      } else item.removeAttribute("aria-current");
    });
    if (deps.breadcrumb)
      deps.breadcrumb.textContent = deps.localeText(
        deps.sectionNames[id] || id,
      );
    if (id === "paper") deps.fetchPaperState(false);
    if (id === "factorMining") APiFactorMiningPanel?.load();
    window.requestAnimationFrame(() => APiCharts?.draw());
  }

  /** @type {import('../types/services').SharedServices['showToast']} */
  function showToast(message) {
    const text = String(message ?? "");
    APiNotifications.record(
      text,
      document.querySelector(".page-section.active")?.id || "overview",
    );
    toastQueue.push(text);
    if (toastQueue.length > 20) toastQueue.shift();
    if (displayingToast) return;
    displayNextToast();
  }

  function displayNextToast() {
    const message = toastQueue.shift();
    if (message === undefined) {
      displayingToast = false;
      return;
    }
    displayingToast = true;
    const toast = requiredElement("toast");
    requiredElement("toastText").textContent = deps.localeText(message);
    toast.classList.add("visible");
    clearTimeout(deps.toastTimer ?? undefined);
    deps.toastTimer = setTimeout(() => {
      toast.classList.remove("visible");
      displayingToast = false;
      displayNextToast();
    }, 6000);
  }

  function restoreRoute() {
    if (!deps.appShell.classList.contains("authenticated")) return;
    const id = window.location.hash.replace(/^#\/?/, "");
    const safe = Object.hasOwn(deps.sectionNames, id) ? id : "overview";
    if (safe !== id) window.history.replaceState(null, "", `#/${safe}`);
    showSection(safe);
  }
  window.addEventListener("popstate", restoreRoute);
  window.addEventListener("hashchange", restoreRoute);

  /** @type {import('../types/services').SharedServices['updateConnectionStatus']} */
  function updateConnectionStatus() {
    const connected = Object.values(deps.connectionState).filter(
      Boolean,
    ).length;
    const total = Object.keys(deps.connectionState).length;
    const allConnected = connected === total;
    const probePending =
      deps.connectionState.python &&
      deps.notificationState.health?.probe?.status === "pending";
    deps.setText(
      "trackerTitle",
      probePending
        ? "量化链路检查中"
        : allConnected
          ? "量化链路已连接"
          : "量化链路部分连接",
    );
    deps.setText(
      "trackerConnectionLabel",
      `${connected}/${total} 个连接正常 · Python、Tushare、SQLite`,
    );
    const services = [
      ["trackerTushare", "Tushare Pro", "tushare"],
      ["trackerPython", "Python", "python"],
      ["trackerSqlite", "SQLite", "sqlite"],
    ];
    services.forEach(([id, label, key]) => {
      const badge = document.getElementById(id);
      if (!badge) return;
      const online = Boolean(
        deps.connectionState[
          /** @type {keyof import('../types/domain').AppState['connectionState']} */ (
            key
          )
        ],
      );
      const pending = key === "tushare" && probePending;
      badge.classList.toggle("is-pending", pending);
      badge.classList.toggle("is-online", online);
      badge.classList.toggle("is-offline", !online && !pending);
      const state = pending ? "检查中" : online ? "已连接" : "未连接";
      badge.title = `${label} · ${state}`;
      badge.setAttribute("aria-label", `${label} · ${state}`);
    });
  }

  /** @param {string} label @param {unknown} value */
  function dialogRow(label, value, tone = "") {
    return `<div class="utility-row"><span>${deps.escapeHtml(label)}</span><strong class="${tone}">${deps.escapeHtml(value)}</strong></div>`;
  }

  /** @param {number} index */
  function openRiskAlertDetails(index) {
    const alert = deps.latestRiskAlertDetails[index];
    if (!alert) {
      showToast("提醒详情尚未加载，请刷新数据后重试");
      return;
    }
    const rows = alert.rows
      .map(([label, value, tone]) => dialogRow(label, value, tone))
      .join("");
    const body = [
      `<div class="utility-copy"><p>${deps.escapeHtml(deps.localeText(alert.description))}</p></div>`,
      `<div class="utility-grid">${rows}</div>`,
    ].join("");
    openUtilityDialog(deps.localeText("提醒详情"), body);
    pendingRiskAlertId = alert.id || null;
  }

  /** @type {import('../types/services').SharedServices['openUtilityDialog']} */
  function openUtilityDialog(title, body) {
    pendingRiskAlertId = null;
    const dialog = requiredElement("utilityDialog");
    if (!dialog) return;
    deps.setText("utilityDialogTitle", title);
    const host = requiredElement("utilityDialogBody");
    if (host) setSafeHtml(host, body);
    deps.applyLocale(deps.activeLocale);
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }

  /** @type {import('../types/services').SharedServices['closeUtilityDialog']} */
  function closeUtilityDialog() {
    const dialog = requiredElement("utilityDialog");
    if (!dialog) return;
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
    handleUtilityDialogClosed();
  }

  function handleUtilityDialogClosed() {
    const id = pendingRiskAlertId;
    pendingRiskAlertId = null;
    if (id) deps.dismissRiskAlert(id);
  }

  function openHelpDialog() {
    const health = deps.notificationState.health || {};
    const workspace = deps.latestWorkspaceSnapshot || {};
    const factors = workspace.research || {};
    const paper = deps.notificationState.paper || {};
    const scheduler = deps.latestScheduler || workspace.scheduler || {};
    const warningCount = (workspace.warnings || []).length;
    const body = [
      '<div class="utility-copy"><p>这里显示的是当前 A-pi 服务的真实状态和实际操作入口。</p></div>',
      '<div class="utility-grid">',
      dialogRow(
        "Python 服务",
        health.online ? "在线" : "未连接",
        health.online ? "utility-good" : "utility-bad",
      ),
      dialogRow(
        "Tushare",
        health.providerReady ? "Token 与依赖已就绪" : "需要检查 Token/网络",
        health.providerReady ? "utility-good" : "utility-warn",
      ),
      dialogRow("最新交易日", workspace.latest_trade_date || "尚未加载"),
      dialogRow("已加载因子", String(factors.factor_count ?? "—")),
      dialogRow("纸面持仓", `${(paper.positions || []).length} 只`),
      dialogRow(
        "策略循环",
        paperSchedulerStatus(scheduler),
        scheduler.last_error ? "utility-bad" : "utility-good",
      ),
      dialogRow(
        "数据提示",
        warningCount ? `${warningCount} 项，需要查看通知` : "无",
      ),
      "</div>",
      '<div class="utility-copy"><p>运行回测会调用 <code>/api/backtest</code>；纸面委托使用 Tushare 日线并写入本地 SQLite，不会发送真实券商订单。</p></div>',
    ].join("");
    const guide = document.getElementById("manualGuideLink");
    const link = guide?.getAttribute("href") || "";
    openUtilityDialog(
      "使用说明 · 实时状态",
      body +
        (link
          ? `<p class="manual-guide-link"><a href="${deps.escapeHtml(link)}" target="_blank" rel="noopener">打开完整使用与运维说明书</a></p>`
          : ""),
    );
  }

  function openConnectionDialog() {
    const health = deps.notificationState.health || {};
    const workspace = deps.latestWorkspaceSnapshot || {};
    const paper = deps.notificationState.paper || {};
    const scheduler = deps.latestScheduler || workspace.scheduler || {};
    const body = [
      '<div class="utility-grid">',
      dialogRow(
        "Python API",
        deps.connectionState.python ? "在线 · /api/health" : "未连接",
        deps.connectionState.python ? "utility-good" : "utility-bad",
      ),
      dialogRow(
        "Tushare Pro",
        deps.connectionState.tushare
          ? health.providerReady
            ? "可用"
            : "需检查"
          : "未确认",
        deps.connectionState.tushare && health.providerReady
          ? "utility-good"
          : "utility-warn",
      ),
      dialogRow(
        "SQLite 纸面账本",
        deps.connectionState.sqlite ? "可读写" : "未确认",
        deps.connectionState.sqlite ? "utility-good" : "utility-warn",
      ),
      dialogRow(
        "行情日期",
        workspace.latest_trade_date || paper.latest_trade_date || "—",
      ),
      dialogRow("自动循环", paperSchedulerStatus(scheduler)),
      "</div>",
      scheduler.last_error
        ? `<div class="utility-warning">最近一次纸面循环错误：${deps.escapeHtml(scheduler.last_error)}</div>`
        : "",
    ].join("");
    openUtilityDialog("连接详情 · 实时状态", body);
  }

  function openExposureDialog() {
    const paper = deps.notificationState.paper || {};
    const positions = paper.positions || [];
    const industries = paper.industry_exposure || [];
    const body = positions.length
      ? [
          '<div class="utility-copy"><p>以下持仓和行业暴露来自 SQLite 纸面账户，并使用 Tushare 元数据归类。</p></div>',
          '<div class="utility-list">',
          ...positions.map((position) =>
            dialogRow(
              position.symbol || position.ts_code || "—",
              `¥ ${deps.formatNumber(Number(position.market_value))} · ${deps.formatPercent(Number(position.market_value) / Math.max(Number(paper.equity) || 1, 1))}`,
            ),
          ),
          "</div>",
          industries.length
            ? `<div class="utility-subtitle">行业暴露</div><div class="utility-list">${industries.map((item) => dialogRow(item.industry, deps.formatPercent(item.weight))).join("")}</div>`
            : "",
        ].join("")
      : '<div class="utility-empty">当前没有真实纸面持仓，暴露数据为空。</div>';
    openUtilityDialog("当前暴露 · 纸面账户", body);
  }

  function openRunDetailsDialog() {
    const workspace = deps.latestWorkspaceSnapshot || {};
    const scheduler = deps.latestScheduler || workspace.scheduler || {};
    const metrics = workspace.portfolio?.metrics || {};
    const body = [
      '<div class="utility-grid">',
      dialogRow(
        "行情快照",
        workspace.latest_trade_date
          ? `已加载 · ${workspace.latest_trade_date}`
          : "未加载",
      ),
      dialogRow(
        "回测权益曲线",
        workspace.portfolio?.equity_curve?.length
          ? `${workspace.portfolio.equity_curve.length} 个交易日`
          : "未生成",
      ),
      dialogRow(
        "回测 Sharpe",
        Number.isFinite(Number(metrics.sharpe))
          ? Number(metrics.sharpe).toFixed(2)
          : "—",
      ),
      dialogRow("策略循环", paperSchedulerStatus(scheduler)),
      dialogRow("最后循环日", scheduler.last_cycle_date || "—"),
      dialogRow(
        "最近运行",
        scheduler.last_run_at
          ? APiNotifications?.formatTime(scheduler.last_run_at) || "—"
          : "—",
      ),
      "</div>",
      scheduler.last_result
        ? `<div class="utility-copy"><p>最近结果：${deps.escapeHtml(scheduler.last_result.skipped ? scheduler.last_result.skip_reason || "已跳过" : `${scheduler.last_result.generated_orders || 0} 个信号`)}</p></div>`
        : "",
    ].join("");
    openUtilityDialog("今日运行 · 实时状态", body);
  }
  return {
    enterWorkspace,
    exitWorkspace,
    showSection,
    showToast,
    updateConnectionStatus,
    dialogRow,
    openRiskAlertDetails,
    openUtilityDialog,
    closeUtilityDialog,
    handleUtilityDialogClosed,
    openHelpDialog,
    openConnectionDialog,
    openExposureDialog,
    openRunDetailsDialog,
  };
}
