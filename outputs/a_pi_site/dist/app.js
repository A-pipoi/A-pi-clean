import { setSafeHtml } from "./safe-html.mjs";
import { getAuthClient, setSessionExpiredHandler } from "./api-client.mjs";
import { installTheme } from "./theme.mjs";
import { installIcons } from "./icons.mjs";
import { installWorkbenchPolish } from "./workbench-polish.mjs";
import { installSidebarDock } from "./sidebar-dock.mjs";
import { installWorkbenchIA } from "./workbench-ia.mjs";
import {
  installWorkbenchDisclosure,
  displayedStatusDetails,
} from "./workbench-disclosure.mjs";
import { createInitialPassword } from "./initial-password.mjs";
import { createRegistrationView } from "./registration-view.mjs";
import { createAccountSecurity, securityText } from "./account-security.mjs";
import { requiredElement } from "./runtime-utils.mjs";
import { createAppState, connectState } from "./app-state.mjs";
import "./feature-store-panel.mjs";
import { APiBacktestRunner } from "./backtest-runner.mjs";
import { APiCharts } from "./charts.mjs";
import { APiExperimentComparison } from "./experiment-comparison.mjs";
import { APiFactorMiningPanel } from "./factor-mining-panel.mjs";
import { APiFactorRegistration } from "./factor-registration.mjs";
import { APiFinancialStatements } from "./financial-statements-panel.mjs";
import { APiGovernanceActions } from "./research-governance-actions.mjs";
import { APiLocaleDictionary } from "./locale-dictionary.js";
import { APiLocaleRuntime } from "./i18n.js";
import { APiMarketTicker } from "./market-ticker.mjs";
import { APiNotifications } from "./notification-center.mjs";
import { APiPaperPage } from "./paper-page.mjs";
import { APiPortfolioOptimizer } from "./portfolio-optimizer.js";
import { APiResearchControls } from "./backtest-research-controls.mjs";
import { APiRiskView } from "./risk-view.js";
import { APiStockPicker } from "./stock-picker.mjs";
import { APiStrategySearch } from "./strategy-search.mjs";
import { createMarketView } from "./market-view.mjs";
import { createPortfolioView } from "./portfolio-view.mjs";
import { createDataHealth } from "./data-health.mjs";
import { createWorkspacePresenter } from "./workspace-presenter.mjs";
import { createFactorRegistryView } from "./factor-registry-view.mjs";
import { createResearchGovernance } from "./research-governance.mjs";
import { createWorkspaceController } from "./workspace-controller.mjs";
import { createBacktestResults } from "./backtest-results.mjs";
import { createAppShell } from "./app-shell.mjs";
import { createRuntimeConfig } from "./runtime-config.mjs";
import { createServiceHealth } from "./service-health.mjs";
import { createLiveICPanel } from "./live-ic-panel.mjs";
import { createRulebookEditor } from "./rulebook-editor.mjs";
import { installPasswordReveal } from "./password-reveal.mjs";
const authView = requiredElement("authView");
installTheme();
installIcons();
installWorkbenchPolish();
installSidebarDock();
installWorkbenchDisclosure();
const appShell = requiredElement("appShell");
const sessionKey = "a_pi_session";
localStorage.removeItem(sessionKey);
sessionStorage.removeItem(sessionKey);
const authClient = getAuthClient();
installPasswordReveal(document, localeText);
const liveICPanel = createLiveICPanel();
const rulebookEditor = createRulebookEditor();
setSessionExpiredHandler((reason) => {
  appShellRuntime.exitWorkspace();
  sessionStorage.setItem("a_pi_forced_login", reason || "expired");
  window.location.reload();
});
const sectionNames = {
  overview: "组合总览",
  research: "因子研究",
  factorMining: "因子挖掘",
  backtest: "回测实验",
  risk: "风险控制",
  paper: "纸面交易",
  governance: "研究治理",
  ops: "数据与运维",
};

const localeStorageKey = "a_pi_locale";
const localeDictionary = APiLocaleDictionary;
const localeRuntime = APiLocaleRuntime.create(
  localeDictionary,
  localeStorageKey,
);
const state = createAppState(localeRuntime.locale());
const registrationView = createRegistrationView({
  client: authClient,
  locale: () => state.activeLocale,
});

const marketViewRuntime = createMarketView(
  connectState(state, ["activeLocale"], {
    applyLocale,
    dataSourceClass,
    dataSourceLabel,
    escapeHtml,
    formatNumber,
    localeText,
    setText,
    showToast,
  }),
);

const portfolioViewRuntime = createPortfolioView(
  connectState(
    state,
    [
      "latestBacktestValues",
      "latestEquityCurve",
      "latestRiskAlertDetails",
      "latestRiskLimits",
      "latestWorkspaceSnapshot",
      "notificationState",
      "riskViewRuntime",
      "runtimeConfig",
    ],
    {
      escapeHtml,
      formatNumber,
      formatPercent,
      localeText,
      getCurrentUsername: () => authClient.current()?.username || "anonymous",
      setText,
      updateEquityAxis,
      visibleEquityCurve,
    },
  ),
);

const dataHealthRuntime = createDataHealth(
  connectState(state, ["latestWorkspaceSnapshot", "notificationState"], {
    localeText,
    setText,
  }),
);

const workspacePresenterRuntime = createWorkspacePresenter(
  connectState(
    state,
    [
      "connectionState",
      "latestBacktestRestored",
      "latestBacktestResult",
      "latestPaperState",
      "latestRiskLimits",
      "latestScheduler",
      "latestWorkspaceSnapshot",
      "notificationState",
      "workspaceBuilding",
      "workspaceLoadError",
    ],
    {
      applyBacktestResult,
      applyMarketSnapshot,
      applyPortfolioSnapshot,
      applyResearchSnapshot,
      applyRiskSnapshot,
      applySignalSnapshot,
      beijingDateTime,
      dataSourceLabel,
      escapeHtml,
      formatPercent,
      localeText,
      renderDataHealth,
      setText,
      updateConnectionStatus,
    },
  ),
);

const factorRegistryViewRuntime = createFactorRegistryView(
  connectState(
    state,
    ["activeFactorFilter", "activeLocale", "latestResearchRows"],
    {
      applyLocale,
      escapeHtml,
      formatPercent,
      setText,
    },
  ),
);

const researchGovernanceRuntime = createResearchGovernance(
  connectState(
    state,
    ["latestBacktestResult", "researchBudgetState", "restoringBacktestUrl"],
    {
      escapeHtml,
      formatPercent,
      localeText,
      restoreBacktestResult,
      setText,
      showToast,
      syncResearchBudgetControls,
    },
  ),
);

const workspaceControllerRuntime = createWorkspaceController(
  connectState(
    state,
    [
      "activeLocale",
      "latestWorkspaceSnapshot",
      "notificationState",
      "workspaceBuilding",
      "workspaceCatalog",
      "workspaceCatalogLoading",
      "workspaceLoadError",
      "workspaceLoading",
      "workspaceRetryAttempt",
      "workspaceRetryDelaySeconds",
      "workspaceRetryTimer",
    ],
    {
      applyLocale,
      applyWorkspaceSnapshot,
      closestHTMLElement,
      escapeHtml,
      loadFactorDefinitions,
      loadGovernance,
      loadOperationsStatus,
      localeText,
      renderDataHealth,
      renderDataProbeStatus,
      setText,
      showToast,
    },
  ),
);

const backtestResultsRuntime = createBacktestResults(
  connectState(
    state,
    [
      "backtestRestoreRequests",
      "latestBacktestRestored",
      "latestBacktestResult",
      "latestBacktestValues",
      "latestEquityCurve",
      "latestPaperState",
      "latestRiskLimits",
      "latestWorkspaceSnapshot",
      "notificationState",
      "restoringBacktestUrl",
    ],
    {
      applyPortfolioSnapshot,
      applyRiskSnapshot,
      dataSourceLabel,
      escapeHtml,
      formatPercent,
      formatNumber,
      loadAdjustmentHealth,
      loadOperationsStatus,
      localeText,
      renderGuardrails,
      setText,
      updateEquityAxis,
      visibleEquityCurve,
    },
  ),
);

const appShellServices = connectState(
  state,
  /** @type {const} */ ([
    "activeLocale",
    "connectionState",
    "latestRiskAlertDetails",
    "latestScheduler",
    "latestWorkspaceSnapshot",
    "notificationState",
    "toastTimer",
  ]),
  {
    applyLocale,
    get appShell() {
      return appShell;
    },
    get authView() {
      return authView;
    },
    get breadcrumb() {
      return breadcrumb;
    },
    escapeHtml,
    fetchPaperState,
    formatNumber,
    formatPercent,
    localeText,
    get navItems() {
      return navItems;
    },
    get sectionNames() {
      return sectionNames;
    },
    get sections() {
      return sections;
    },
    get sessionKey() {
      return sessionKey;
    },
    dismissRiskAlert: (/** @type {string} */ id) =>
      state.riskViewRuntime?.dismissAlert(id),
    setText,
  },
);
const appShellRuntime = createAppShell(
  /** @type {import('../types/services').AppShellServices} */ (
    appShellServices
  ),
);

const runtimeConfigRuntime = createRuntimeConfig(
  connectState(
    state,
    [
      "activeLocale",
      "latestPaperState",
      "latestWorkspaceSnapshot",
      "MAX_RESEARCH_SYMBOLS",
      "researchBudgetState",
      "runtimeConfig",
      "runtimeSeedSymbols",
    ],
    {
      applyLocale,
      applySignalSnapshot,
      escapeHtml,
      showToast,
    },
  ),
);

const serviceHealthRuntime = createServiceHealth(
  connectState(
    state,
    [
      "connectionState",
      "latestWorkspaceSnapshot",
      "MAX_RESEARCH_SYMBOLS",
      "notificationState",
    ],
    {
      renderDataHealth,
      renderDataProbeStatus,
      setText,
      showToast,
      updateConnectionStatus,
    },
  ),
);

/** @type {import('../types/services').SharedServices['localeText']} */
function localeText(value) {
  return localeRuntime.translate(value);
}

/** @type {import('../types/services').SharedServices['applyLocale']} */
function applyLocale(locale = state.activeLocale) {
  state.activeLocale = localeRuntime.apply(locale);
  navItems.forEach((item) => {
    const section = item.dataset.section || "overview";
    const sectionKey = /** @type {keyof typeof sectionNames} */ (
      Object.hasOwn(sectionNames, section) ? section : "overview"
    );
    const label = localeText(sectionNames[sectionKey]);
    item.setAttribute("aria-label", label);
    item.title = label;
  });
  registrationView.refresh();
  requiredElement("authOtpLabel").textContent = securityText(
    state.activeLocale,
    "loginOtp",
  );
  requiredElement("authOtpHint").textContent = securityText(
    state.activeLocale,
    "loginOtpHint",
  );
  requiredElement("userMenuButton").setAttribute(
    "aria-label",
    securityText(state.activeLocale, "title"),
  );
  APiStrategySearch?.rerenderStrategyOptions();
}

const languageSelect = requiredElement("languageSelect");
const switchLocale =
  /** @param {Event} event */
  (event) => {
    if (!(event.currentTarget instanceof HTMLSelectElement)) return;
    const nextLocale = event.currentTarget.value;

    const changed = nextLocale !== state.activeLocale;
    applyLocale(nextLocale);
    const session = authClient.current();
    if (session?.must_change_password)
      initialPassword.activate(session.username);
    registrationView.refresh();
    if (changed) showToast(localeText("语言已切换"));
  };
languageSelect?.addEventListener("change", switchLocale);
/** @type {import('../types/services').SharedServices['beijingDateTime']} */
function beijingDateTime(value) {
  if (!value) return "—";
  const date = new Date(typeof value === "number" ? value : String(value));
  if (Number.isNaN(date.getTime()))
    return String(value).replace("T", " ").slice(0, 19);
  const zoneLabel =
    {
      "zh-CN": "北京时间",
      "en-US": "China Standard Time",
      "fr-FR": "heure de Chine",
      "ja-JP": "中国標準時",
    }[state.activeLocale] || "北京时间";
  return `${new Intl.DateTimeFormat(state.activeLocale, { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(date)} ${zoneLabel}`;
}

/** @param {string} email */
function enterWorkspace(email, silent = false) {
  return appShellRuntime.enterWorkspace(email, silent);
}

const authErrorMessages = {
  invalid_credentials: "用户名或密码错误，请重试。",
  unauthorized: "会话已过期，请重新登录。",
  too_many_attempts: "尝试次数过多，请 15 分钟后重试。",
  auth_not_configured: "尚未创建用户，请联系管理员在本机创建账号。",
  auth_network_error: "无法连接认证服务，请检查后端后重试。",
  auth_invalid_session: "认证服务响应异常，请检查后端版本。",
};
/** @param {string} code */
function showAuthError(code) {
  if (code === "totp_required" || code === "totp_invalid") {
    const error = requiredElement("authError");
    error.setAttribute("data-locale-skip", "");
    error.textContent = securityText(state.activeLocale, code);
    error.hidden = false;
    requiredElement("authOtp").focus();
    return;
  }
  const message =
    authErrorMessages[/** @type {keyof typeof authErrorMessages} */ (code)] ||
    authErrorMessages.auth_network_error;
  const error = requiredElement("authError");
  error.removeAttribute("data-locale-skip");
  error.textContent = localeText(message);
  error.hidden = false;
}
async function exitWorkspace() {
  sessionStorage.setItem("a_pi_forced_login", "signed-out");
  appShellRuntime.exitWorkspace();
  try {
    await authClient.logout();
  } catch {
    /* Offline still clears local identity. */
  }
  window.location.reload();
}
const initialPassword = createInitialPassword({
  fetch: authClient.fetch,
  locale: () => state.activeLocale,
  logout: exitWorkspace,
});
requiredElement("authForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = requiredElement("authSubmit");
  if (button.disabled) return;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  requiredElement("authError").hidden = true;
  try {
    if (authClient.current()?.must_change_password) {
      await initialPassword.submit();
      return;
    }
    const session = await authClient.login(
      requiredElement("authUsername").value,
      requiredElement("authPassword").value,
      requiredElement("rememberSession").checked,
      requiredElement("authOtp").value.trim(),
    );
    requiredElement("authPassword").value = "";
    requiredElement("authOtp").value = "";
    if (session?.must_change_password) {
      sessionStorage.removeItem("a_pi_forced_login");
      initialPassword.activate(session.username);
      registrationView.refresh();
      return;
    }
    sessionStorage.removeItem("a_pi_forced_login");
    window.location.reload();
  } catch (error) {
    showAuthError(
      error instanceof Error ? error.message : "auth_network_error",
    );
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
});
requiredElement("logoutButton").addEventListener("click", exitWorkspace);
requiredElement("helpLink").addEventListener("click", (event) => {
  event.preventDefault();
  openUtilityDialog(
    "访问帮助",
    "<p>账号由管理员通过本机用户管理工具创建。忘记密码时请联系管理员重置；不提供访客入口。</p>",
  );
});
requiredElement("helpButton")?.addEventListener("click", openHelpDialog);
const accountSecurity = createAccountSecurity({
  fetch: authClient.fetch,
  session: authClient.current,
  locale: () => state.activeLocale,
  logout: exitWorkspace,
  forget: authClient.forget,
});
requiredElement("userMenuButton").addEventListener("click", () =>
  accountSecurity.open(),
);

installWorkbenchIA();
const navItems = [...document.querySelectorAll(".nav-item")];
const sections = [...document.querySelectorAll(".page-section")];
const breadcrumb = document.getElementById("breadcrumb-current");

/** @type {import('../types/services').SharedServices['showSection']} */
function showSection(id) {
  return appShellRuntime.showSection(id);
}

/** @type {import('../types/services').SharedServices['closestHTMLElement']} */
function closestHTMLElement(event, selector) {
  const target = event.target;
  const match = target instanceof Element ? target.closest(selector) : null;
  return match instanceof HTMLElement ? match : null;
}

navItems.forEach((item) =>
  item.addEventListener("click", () =>
    showSection(item.dataset.section || "overview"),
  ),
);
document.addEventListener("click", (event) => {
  const alertDetailsButton = closestHTMLElement(
    event,
    "[data-risk-alert-index]",
  );
  if (alertDetailsButton) {
    openRiskAlertDetails(Number(alertDetailsButton.dataset.riskAlertIndex));
    return;
  }
  const link = closestHTMLElement(event, "[data-section-link]");
  if (link) showSection(link.dataset.sectionLink || "overview");
  if (closestHTMLElement(event, "[data-close-utility]")) closeUtilityDialog();
  if (closestHTMLElement(event, "[data-utility-logout]")) {
    closeUtilityDialog();
    exitWorkspace();
  }
});

/** @type {import('../types/services').SharedServices['showToast']} */
function showToast(message) {
  return appShellRuntime.showToast(message);
}

/** @type {import('../types/services').SharedServices['updateConnectionStatus']} */
function updateConnectionStatus() {
  return appShellRuntime.updateConnectionStatus();
}

/** @param {string} label @param {unknown} value */
function dialogRow(label, value, tone = "") {
  return appShellRuntime.dialogRow(label, value, tone);
}

/** @param {number} index */
function openRiskAlertDetails(index) {
  return appShellRuntime.openRiskAlertDetails(index);
}

/** @type {import('../types/services').SharedServices['openUtilityDialog']} */
function openUtilityDialog(title, body) {
  return appShellRuntime.openUtilityDialog(title, body);
}

/** @type {import('../types/services').SharedServices['closeUtilityDialog']} */
function closeUtilityDialog() {
  return appShellRuntime.closeUtilityDialog();
}

function openHelpDialog() {
  return appShellRuntime.openHelpDialog();
}

function openConnectionDialog() {
  return appShellRuntime.openConnectionDialog();
}

function openExposureDialog() {
  return appShellRuntime.openExposureDialog();
}

function openRunDetailsDialog() {
  return appShellRuntime.openRunDetailsDialog();
}

requiredElement("runButton").addEventListener("click", async () => {
  const result = await APiBacktestRunner?.run(requiredElement("runButton"));
  if (result) showSection("backtest");
});
requiredElement("configRunButton").addEventListener("click", () =>
  APiBacktestRunner?.run(requiredElement("configRunButton")),
);
requiredElement("sensitivityButton")?.addEventListener("click", () =>
  APiBacktestRunner?.sensitivity(requiredElement("sensitivityButton")),
);
requiredElement("factorButton").addEventListener("click", () =>
  APiFactorRegistration?.open(),
);
document.querySelectorAll("[data-factor-filter]").forEach((button) =>
  button.addEventListener("click", () => {
    state.activeFactorFilter = button.dataset.factorFilter || "all";
    document
      .querySelectorAll("[data-factor-filter]")
      .forEach((item) => item.classList.toggle("active", item === button));
    renderFactorTable();
  }),
);
requiredElement("trackerDetailsButton")?.addEventListener(
  "click",
  openConnectionDialog,
);
requiredElement("exposureDetailsButton")?.addEventListener(
  "click",
  openExposureDialog,
);
requiredElement("todayRunDetailsButton")?.addEventListener(
  "click",
  openRunDetailsDialog,
);
requiredElement("utilityDialogClose")?.addEventListener(
  "click",
  closeUtilityDialog,
);
requiredElement("utilityDialog")?.addEventListener("close", () =>
  appShellRuntime.handleUtilityDialogClosed(),
);
requiredElement("utilityDialog")?.addEventListener("click", (event) => {
  if (event.target === event.currentTarget) closeUtilityDialog();
});
requiredElement("exportButton").addEventListener("click", () => {
  const exported = displayedStatusDetails();
  const blob = new Blob([JSON.stringify(exported, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "a-pi-status-details.json";
  link.click();
  URL.revokeObjectURL(url);
  showToast("状态详情已导出");
});

/** @type {import('../types/services').SharedServices['dataSourceLabel']} */
function dataSourceLabel(source, stale = false) {
  const normalized = String(source || "").toLowerCase();
  if (stale || normalized.includes("cache")) return "缓存";
  if (normalized.includes("mixed")) return "混合";
  if (normalized.includes("live") || normalized === "tushare") return "实时";
  if (normalized === "market_closed") return "休市";
  return "未知";
}

/** @type {import('../types/services').SharedServices['dataSourceClass']} */
function dataSourceClass(source, stale = false) {
  const label = dataSourceLabel(source, stale);
  return label === "实时"
    ? "source-live"
    : label === "缓存"
      ? "source-cache"
      : label === "混合"
        ? "source-mixed"
        : "source-unknown";
}

function renderRuntimeConfig() {
  return runtimeConfigRuntime.renderRuntimeConfig();
}

async function loadRuntimeConfig() {
  return runtimeConfigRuntime.loadRuntimeConfig();
}

/** @type {import('../types/services').SharedServices['backtestPreset']} */
function backtestPreset({ researchOnly = true } = {}) {
  const build = APiResearchControls?.buildBacktestPreset;
  if (typeof build !== "function") throw new Error("回测研究配置模块尚未加载");
  return build({
    doc: document,
    strategyCatalog: APiStrategySearch?.strategyCatalog() || [],
    seedSymbols: state.runtimeSeedSymbols,
    researchOnly,
  });
}

/** @type {import('../types/services').SharedServices['initBacktestUniverseSelector']} */
function initBacktestUniverseSelector() {
  return runtimeConfigRuntime.initBacktestUniverseSelector();
}

/** @type {import('../types/services').SharedServices['syncResearchBudgetControls']} */
function syncResearchBudgetControls() {
  return runtimeConfigRuntime.syncResearchBudgetControls();
}

/** @type {import('../types/services').SharedServices['formatPercent']} */
function formatPercent(value, digits = 1) {
  return Number.isFinite(value)
    ? `${(Number(value) * 100).toFixed(digits)}%`
    : "—";
}

/** @type {import('../types/services').SharedServices['formatNumber']} */
function formatNumber(value, digits = 2) {
  return Number.isFinite(value)
    ? Number(value).toLocaleString(state.activeLocale, {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      })
    : "—";
}

/** @param {import('../types/domain').Stock | undefined} index @param {string} valueId @param {string} changeId */
function applyIndexValue(index, valueId, changeId) {
  return marketViewRuntime.applyIndexValue(index, valueId, changeId);
}

/** @param {import('../types/domain').Stock[]} indices */
function applyIndexCollection(indices, latestTradeDate = "") {
  return marketViewRuntime.applyIndexCollection(indices, latestTradeDate);
}

/** @param {import('../types/domain').Stock | undefined} index */
function applyShanghaiIndex(index) {
  return marketViewRuntime.applyShanghaiIndex(index);
}

async function loadMarketIndex(showFailure = false) {
  return marketViewRuntime.loadMarketIndex(showFailure);
}

/** @type {import('../types/services').SharedServices['setText']} */
function setText(id, value) {
  const element = document.getElementById(id);
  if (element)
    element.textContent =
      typeof value === "string" ? localeText(value) : String(value ?? "");
}

/** @type {import('../types/services').SharedServices['applyMarketSnapshot']} */
function applyMarketSnapshot(market) {
  return marketViewRuntime.applyMarketSnapshot(market);
}

/** @type {import('../types/services').SharedServices['visibleEquityCurve']} */
function visibleEquityCurve() {
  if (!state.latestEquityCurve.length || state.activeEquityRange === "ALL")
    return state.latestEquityCurve;
  /** @type {Record<string,number>} */
  const daysByRange = { "1M": 31, "6M": 183, "1Y": 366 };
  const days = daysByRange[state.activeEquityRange] || 183;
  const lastDate = new Date(
    `${state.latestEquityCurve[state.latestEquityCurve.length - 1].date}T00:00:00`,
  );
  if (Number.isNaN(lastDate.getTime())) return state.latestEquityCurve;
  const cutoff = new Date(lastDate.getTime() - days * 24 * 60 * 60 * 1000);
  const filtered = state.latestEquityCurve.filter(
    (point) => new Date(`${point.date}T00:00:00`) >= cutoff,
  );
  return filtered.length >= 2 ? filtered : state.latestEquityCurve.slice(-2);
}

/** @param {string} id @param {import('../types/domain').EquityPoint[]} curve */
function updateAxisLabels(id, curve) {
  const axis = document.getElementById(id);
  if (!axis || !curve.length) return;
  const labels = [0, 0.2, 0.4, 0.6, 0.8, 1].map(
    (ratio) =>
      curve[
        Math.min(curve.length - 1, Math.floor((curve.length - 1) * ratio))
      ]?.date?.slice(5) || "—",
  );
  setSafeHtml(
    axis,
    labels.map((label) => `<span>${escapeHtml(label)}</span>`).join(""),
  );
}

/** @type {import('../types/services').SharedServices['updateEquityAxis']} */
function updateEquityAxis(curve) {
  updateAxisLabels("equityAxisLabels", curve);
  updateAxisLabels(
    "backtestAxisLabels",
    state.latestBacktestResult?.equity_curve || [],
  );
}

/** @type {import('../types/services').SharedServices['applyPortfolioSnapshot']} */
function applyPortfolioSnapshot(portfolio) {
  return portfolioViewRuntime.applyPortfolioSnapshot(portfolio);
}

function renderFactorTable() {
  return factorRegistryViewRuntime.renderFactorTable();
}

/** @type {import('../types/services').SharedServices['applyResearchSnapshot']} */
function applyResearchSnapshot(research) {
  return factorRegistryViewRuntime.applyResearchSnapshot(research);
}

/** @type {import('../types/services').SharedServices['loadFactorDefinitions']} */
async function loadFactorDefinitions() {
  return factorRegistryViewRuntime.loadFactorDefinitions();
}

/** @type {import('../types/services').SharedServices['applySignalSnapshot']} */
function applySignalSnapshot(signals, paper = null) {
  return portfolioViewRuntime.applySignalSnapshot(signals, paper);
}

/** @type {import('../types/services').SharedServices['renderAllocationDonut']} */
function renderAllocationDonut(positions, equity, marketValue) {
  return portfolioViewRuntime.renderAllocationDonut(
    positions,
    equity,
    marketValue,
  );
}

/** @type {import('../types/services').SharedServices['renderGuardrails']} */
function renderGuardrails(snapshot = {}) {
  return workspacePresenterRuntime.renderGuardrails(snapshot);
}

/** @type {import('../types/services').SharedServices['applyRiskSnapshot']} */
function applyRiskSnapshot(paper, portfolio, riskLimits = null) {
  return portfolioViewRuntime.applyRiskSnapshot(paper, portfolio, riskLimits);
}

function calculateDataHealth(
  snapshot = state.latestWorkspaceSnapshot,
  health = state.notificationState.health,
) {
  return dataHealthRuntime.calculateDataHealth(snapshot, health);
}

/** @type {import('../types/services').SharedServices['renderDataHealth']} */
function renderDataHealth(
  snapshot = state.latestWorkspaceSnapshot,
  health = state.notificationState.health,
) {
  return dataHealthRuntime.renderDataHealth(snapshot, health);
}

/** @type {import('../types/services').SharedServices['applyWorkspaceSnapshot']} */
function applyWorkspaceSnapshot(data) {
  return workspacePresenterRuntime.applyWorkspaceSnapshot(data);
}

/** @type {import('../types/services').SharedServices['renderDataProbeStatus']} */
function renderDataProbeStatus() {
  return workspacePresenterRuntime.renderDataProbeStatus();
}

/** @type {import('../types/services').SharedServices['loadGovernance']} */
async function loadGovernance(showFailure = false) {
  return researchGovernanceRuntime.loadGovernance(showFailure);
}

/** @param {import('../types/domain').RiskAnalytics | null} report */
function renderBacktestRiskAnalytics(report) {
  return backtestResultsRuntime.renderBacktestRiskAnalytics(report);
}

/** @param {string} id @param {unknown} value @param {string} status */
function setStatusText(id, value, status = "") {
  return researchGovernanceRuntime.setStatusText(id, value, status);
}

/** @type {import('../types/services').SharedServices['loadOperationsStatus']} */
async function loadOperationsStatus() {
  return researchGovernanceRuntime.loadOperationsStatus();
}

/** @type {import('../types/services').SharedServices['loadAdjustmentHealth']} */
async function loadAdjustmentHealth(symbols) {
  return researchGovernanceRuntime.loadAdjustmentHealth(symbols);
}

/** @type {import('../types/services').SharedServices['loadWorkspace']} */
async function loadWorkspace(showFailure = false) {
  return workspaceControllerRuntime.loadWorkspace(showFailure);
}

/** @param {number} delaySeconds */
function scheduleWorkspaceRetry(delaySeconds) {
  return workspaceControllerRuntime.scheduleWorkspaceRetry(delaySeconds);
}

/** @param {boolean} open */
function setWorkspaceMenuOpen(open) {
  return workspaceControllerRuntime.setWorkspaceMenuOpen(open);
}

/** @param {import('../types/domain').WorkspaceCatalog} data */
function renderWorkspaceCatalog(data) {
  return workspaceControllerRuntime.renderWorkspaceCatalog(data);
}

async function loadWorkspaceCatalog() {
  return workspaceControllerRuntime.loadWorkspaceCatalog();
}

/** @param {string} workspaceId */
async function selectWorkspace(workspaceId) {
  return workspaceControllerRuntime.selectWorkspace(workspaceId);
}

function initWorkspaceSelector() {
  return workspaceControllerRuntime.initWorkspaceSelector();
}

/** @type {import('../types/services').SharedServices['applyBacktestResult']} */
function applyBacktestResult(data, { restored = false } = {}) {
  return backtestResultsRuntime.applyBacktestResult(data, { restored });
}

/** @type {import('../types/services').SharedServices['restoreBacktestResult']} */
async function restoreBacktestResult(url) {
  return backtestResultsRuntime.restoreBacktestResult(url);
}

/** @param {{month: string, return: number, start_date?: string, end_date?: string}[]} rows */
function renderMonthlyReturns(rows) {
  return backtestResultsRuntime.renderMonthlyReturns(rows);
}

/** @param {import('../types/domain').BacktestResult} data */
async function loadBenchmarkReports(data) {
  return backtestResultsRuntime.loadBenchmarkReports(data);
}

/** @type {import('../types/services').SharedServices['escapeHtml']} */
function escapeHtml(value) {
  return String(value).replace(
    /[&<>"']/g,
    (character) =>
      /** @type {Record<string,string>} */ ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;",
      })[character] || character,
  );
}

/** @type {import('../types/services').SharedServices['fetchPaperState']} */
function fetchPaperState(showSuccess = false) {
  return APiPaperPage?.fetch(showSuccess) ?? Promise.resolve(false);
}

document.querySelectorAll(".range-tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    document
      .querySelectorAll(".range-tab")
      .forEach((item) => item.classList.remove("active"));
    tab.classList.add("active");
    state.activeEquityRange = tab.dataset.range || "6M";
    updateEquityAxis(visibleEquityCurve());
    const selectedCurve = visibleEquityCurve();
    setText(
      "portfolioDateRange",
      selectedCurve.length > 1
        ? `${selectedCurve[0].date} → ${selectedCurve[selectedCurve.length - 1].date}`
        : "等待真实回测",
    );
    showToast(`净值区间已切换至 ${tab.dataset.range}`);
    APiCharts?.draw();
  }),
);

/** @type {import('../types/services').SharedServices['checkServiceHealth']} */
async function checkServiceHealth(showFailure = false) {
  return serviceHealthRuntime.checkServiceHealth(showFailure);
}

window.addEventListener("load", async () => {
  applyLocale(state.activeLocale);
  APiFactorRegistration?.init({
    openDialog: openUtilityDialog,
    closeDialog: closeUtilityDialog,
    showGovernance: () => showSection("backtest"),
    toast: showToast,
    reloadWorkspace: () => loadWorkspace(true),
    registered: (factor) => {
      if (!factor) return;
      const key = String(factor.key || "");
      state.latestResearchRows = [
        {
          factor: factor.name,
          name: key,
          factor_type: factor.factor_type,
          symbol: "—",
          value: null,
          sample_days: null,
          trade_date: null,
          status: "已注册 · 待真实行情计算",
          source: "因子注册表",
        },
        ...state.latestResearchRows.filter(
          (row) => row.name !== key && row.factor !== factor.name,
        ),
      ];
      state.activeFactorFilter = "all";
      document
        .querySelectorAll("[data-factor-filter]")
        .forEach((item) =>
          item.classList.toggle("active", item.dataset.factorFilter === "all"),
        );
      renderFactorTable();
    },
  });
  APiGovernanceActions?.init({
    localeText,
    loadGovernance,
    toast: showToast,
    formatNumber,
  });
  APiExperimentComparison?.init({
    toast: showToast,
    escapeHtml,
    formatPercent,
    formatNumber,
  });
  APiBacktestRunner?.init({
    localeText,
    setText,
    backtestPreset,
    applyResult: applyBacktestResult,
    loadGovernance,
    syncBudgetControls: syncResearchBudgetControls,
    formatNumber,
    dataSourceLabel,
    toast: showToast,
  });
  APiNotifications?.init({
    notificationState: state.notificationState,
    escapeHtml,
    getLocale: () => state.activeLocale,
    applyLocale,
    paperStatusLabel: (status) =>
      APiPaperPage?.statusLabel(status) || String(status),
    localeText,
    showToast,
    showSection,
    checkServiceHealth,
    loadWorkspace,
    fetchPaperState,
    closestHTMLElement,
  });
  APiPaperPage?.init({
    setPaperState: (paper, scheduler, riskLimits) => {
      state.latestPaperState = paper;
      state.notificationState.paper = paper;
      state.connectionState.sqlite = true;
      state.latestScheduler = scheduler || state.latestScheduler;
      state.latestRiskLimits =
        APiRiskView.normalizeLimits(riskLimits || paper?.risk_limits) ||
        state.latestRiskLimits;
      updateConnectionStatus();
      APiNotifications?.render();
    },
    getWorkspace: () => state.latestWorkspaceSnapshot,
    getRiskLimits: () => state.latestRiskLimits,
    getLocale: () => state.activeLocale,
    dataSourceLabel,
    setText,
    formatNumber,
    formatPercent,
    escapeHtml,
    beijingDateTime,
    renderAllocationDonut,
    applySignalSnapshot,
    applyRiskSnapshot,
    applyLocale,
    localeText,
    showToast,
    backtestPreset,
  });
  APiCharts?.init({
    getBacktestResult: () => state.latestBacktestResult,
    getCurve: visibleEquityCurve,
    updateAxis: updateEquityAxis,
    escapeHtml,
    formatNumber,
  });
  APiPortfolioOptimizer?.init({ translate: localeText });
  APiStockPicker?.init({
    getMaxSymbols: () => state.MAX_RESEARCH_SYMBOLS,
    getLocale: () => state.activeLocale,
    escapeHtml,
    localeText,
    applyLocale,
    showToast,
    closestHTMLElement,
  });
  APiStrategySearch?.init({
    sectionNames,
    escapeHtml,
    getLocale: () => state.activeLocale,
    localeText,
    applyLocale,
    initBacktestUniverseSelector,
    showSection,
    showToast,
    addStockToPicker: (item) => APiStockPicker?.add(item) ?? false,
    loadGovernance,
    closestHTMLElement,
  });
  initWorkspaceSelector();
  requiredElement("strategySelect")?.addEventListener("change", () => {
    APiStrategySearch?.syncStrategyModeControls();
    loadGovernance(false);
  });
  requiredElement("researchLedgerEntries")?.addEventListener(
    "click",
    async (event) => {
      const button = closestHTMLElement(event, "[data-backtest-result]");
      if (!(button instanceof HTMLButtonElement) || button.disabled) return;
      const originalLabel = button.textContent;
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      button.textContent = "正在加载…";
      showSection("backtest");
      setText("backtestStatus", "正在恢复历史回测…");
      try {
        const restored = await restoreBacktestResult(
          button.dataset.backtestResult || "",
        );
        if (restored) {
          document
            .querySelector("#backtest .backtest-results")
            ?.scrollIntoView({
              behavior: window.matchMedia("(prefers-reduced-motion: reduce)")
                .matches
                ? "auto"
                : "smooth",
              block: "start",
            });
        } else {
          showToast("历史回测结果不可用，详情见回测页审计状态");
        }
      } finally {
        button.disabled = false;
        button.removeAttribute("aria-busy");
        button.textContent = originalLabel;
      }
    },
  );
  APiFinancialStatements?.init({
    escapeHtml,
    getLocale: () => state.activeLocale,
    getLatestTradeDate: () =>
      state.latestWorkspaceSnapshot?.latest_trade_date || "",
    localeText,
    applyLocale,
    showToast,
  });
  applyLocale(state.activeLocale);
  if (
    sessionStorage.getItem("a_pi_forced_login") === "reauthentication_required"
  )
    sessionStorage.removeItem("a_pi_forced_login");
  if (sessionStorage.getItem("a_pi_forced_login")) {
    if (sessionStorage.getItem("a_pi_forced_login") === "expired")
      showAuthError("unauthorized");
    return;
  }
  try {
    const session = await authClient.boot();
    if (!session) return;
    if (session.must_change_password) {
      initialPassword.activate(session.username);
      registrationView.refresh();
      return;
    }
    enterWorkspace(session.username, true);
  } catch {
    showAuthError("auth_network_error");
    return;
  }
  loadRuntimeConfig();
  loadWorkspaceCatalog();
  APiStrategySearch?.loadStrategies();
  APiCharts?.draw();
  checkServiceHealth(false);
  loadWorkspace(false);
  loadGovernance(false);
  loadMarketIndex(false);
  APiMarketTicker.loadRealtimeMarketTicker();
  loadFactorDefinitions();
  liveICPanel.refresh();
  rulebookEditor.refresh();
  APiFactorMiningPanel?.init({
    localeText,
    escapeHtml,
    setText,
    showToast,
    loadFactorDefinitions,
    closestHTMLElement,
  });
  APiFactorMiningPanel?.load();
  fetchPaperState(false);
  window.setInterval(() => checkServiceHealth(false), 10000);
  window.setInterval(() => loadMarketIndex(false), 60000);
  window.setInterval(() => APiMarketTicker.loadRealtimeMarketTicker(), 30000);
  window.setInterval(() => loadWorkspace(false), 10 * 60 * 1000);
  applyLocale(state.activeLocale);
});

setInterval(() => {
  const paperSection = requiredElement("paper");
  if (paperSection && paperSection.classList.contains("active"))
    fetchPaperState(false);
}, 30000);
