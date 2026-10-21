import { setSafeHtml } from "./safe-html.mjs";
import { apiFetch as fetch } from "./api-client.mjs";
import { requiredElement, toError } from "./runtime-utils.mjs";
/** @type {import('../types/services').StrategySearchServices} */
let services;
/** @type {import('../types/domain').StrategyInfo[]} */
let strategyCatalog = [];
/** @type {import('../types/module-state').SearchItem[]} */
let globalSearchStrategies = [];
/** @type {import('../types/module-state').SearchItem[]} */
let globalSearchPages = [];
/** @type {{input: HTMLInputElement | null, results: HTMLElement | null}} */
let globalSearchRefs = { input: null, results: null };
/** @type {import('../types/module-state').SearchState} */
const globalSearchState = {
  results: [],
  activeIndex: -1,
  timer: undefined,
  requestId: 0,
  controller: null,
};
let initialized = false;

/** @param {unknown} value */
export function searchText(value) {
  return String(value || "")
    .trim()
    .toLocaleLowerCase();
}

/** @param {import('../types/domain').StrategyInfo} item @param {string} locale @param {(value: unknown) => string} translate */
export function strategyLabel(
  item,
  locale = "zh-CN",
  translate = (value) => String(value ?? ""),
) {
  /** @type {Record<string, Record<string, string>>} */
  const labels = {
    baseline_cost_aware: {
      "en-US": "Financial / low-turnover baseline (prospective research)",
      "fr-FR": "Base financière à faible rotation (recherche prospective)",
      "ja-JP": "財務・低回転ベースライン（前向き研究）",
    },
    baseline: {
      "en-US": "Cross-sectional momentum",
      "fr-FR": "Momentum cross-sectionnel",
      "ja-JP": "クロスセクション・モメンタム",
    },
    momentum: {
      "en-US": "Dual-horizon trend",
      "fr-FR": "Tendance à double horizon",
      "ja-JP": "デュアルホライズン・トレンド",
    },
    defensive: {
      "en-US": "Low-volatility defensive",
      "fr-FR": "Défensif faible volatilité",
      "ja-JP": "低ボラティリティ防御",
    },
    lightgbm: {
      "en-US": "Walk-forward LightGBM (research only)",
      "fr-FR": "LightGBM walk-forward (recherche)",
      "ja-JP": "LightGBMウォークフォワード（研究専用）",
    },
  };
  return labels[item.key]?.[locale] || translate(item.label);
}

/** @param {Partial<import('../types/module-state').SearchItem>} item @param {string} query */
export function localSearchMatches(item, query) {
  const haystack = searchText(
    `${item.title} ${item.meta} ${item.section || ""} ${item.value || ""}`,
  );
  return !query || haystack.includes(searchText(query));
}

function renderStrategyOptions() {
  const select = requiredElement("strategySelect");
  if (!services || !select || !strategyCatalog.length) return;
  const available = strategyCatalog.filter((item) => item.available !== false);
  const previous = available.some((item) => item.key === select.value)
    ? select.value
    : available[0]?.key || strategyCatalog[0].key;
  setSafeHtml(
    select,
    strategyCatalog
      .map((item) => {
        const unavailable = item.available === false;
        const label = unavailable
          ? `${strategyLabel(item, services.getLocale(), services.localeText)} · ${item.unavailable_reason || "不可用"}`
          : strategyLabel(item, services.getLocale(), services.localeText);
        return `<option value="${services.escapeHtml(item.key)}"${unavailable ? " disabled" : ""}>${services.escapeHtml(label)}</option>`;
      })
      .join(""),
  );
  select.value = previous;
  syncStrategyModeControls();
  globalSearchStrategies = available.map((item) => ({
    type: "策略",
    title: `${strategyLabel(item, services.getLocale(), services.localeText)} · ${item.key}-v1`,
    meta: `${item.description} · 固定研究预设`,
    section: "backtest",
    value: item.key,
    icon: "策",
  }));
}

export function syncStrategyModeControls() {
  const lightgbmSelected = [
    "lightgbm",
    "multi_signal",
    "baseline_cost_aware",
    "baseline_price_audited",
    "baseline_full_audited",
  ].includes(requiredElement("strategySelect")?.value || "");
  const sensitivity = requiredElement("sensitivityButton");
  if (!sensitivity) return;
  sensitivity.disabled = lightgbmSelected;
  sensitivity.title = lightgbmSelected
    ? "隔离研究策略的模型、特征或规则口径固定，不开放参数扫描"
    : "";
}

export async function loadStrategies() {
  if (!services) return;
  try {
    const response = await fetch("/api/strategies");
    const data = await response.json();
    if (!response.ok || !data.ok)
      throw new Error(data.error || `HTTP ${response.status}`);
    strategyCatalog = Array.isArray(data.strategies) ? data.strategies : [];
    renderStrategyOptions();
    services.loadGovernance(false);
  } catch (errorCaught) {
    const error = toError(errorCaught);
    strategyCatalog = [];
    globalSearchStrategies = [];
    const select = requiredElement("strategySelect");
    if (select)
      setSafeHtml(
        select,
        `<option value="">${services.escapeHtml(services.localeText("真实策略暂不可用"))}</option>`,
      );
  }
}

/** @param {string} message */
function renderGlobalSearch(message = "") {
  const { input, results } = globalSearchRefs;
  if (!input || !results) return;
  const query = input.value.trim();
  const items = globalSearchState.results;
  input.setAttribute("aria-expanded", "true");
  results.hidden = false;
  if (!items.length) {
    setSafeHtml(
      results,
      `<div class="global-search-status">${message ? services.escapeHtml(message) : "<strong>没有找到匹配结果</strong><span>可搜索股票名称、6 位代码、拼音、策略或页面。</span>"}</div>`,
    );
    services.applyLocale(services.getLocale());
    return;
  }
  const sourceLabel = items.some((item) => item.type === "股票")
    ? '<span class="global-search-source">Tushare 股票库</span>'
    : "";
  const status = message
    ? `<div class="global-search-status compact">${services.escapeHtml(message)}</div>`
    : "";
  setSafeHtml(
    results,
    status +
      `<div class="global-search-head"><strong>${query ? "搜索结果" : "快速访问"}</strong>${sourceLabel}</div>` +
      items
        .map(
          (item, index) => `
    <button type="button" role="option" aria-selected="${index === globalSearchState.activeIndex}" class="global-search-item${index === globalSearchState.activeIndex ? " active" : ""}" data-global-index="${index}">
      <span class="global-search-marker ${item.type === "股票" ? "stock" : item.type === "策略" ? "strategy" : "page"}">${services.escapeHtml(item.icon || "搜")}</span>
      <span class="global-search-copy"><strong>${services.escapeHtml(item.title)}</strong><small>${services.escapeHtml(item.meta || "")}</small></span>
      <span class="global-search-kind">${services.escapeHtml(item.type)}</span>
    </button>`,
        )
        .join(""),
  );
  services.applyLocale(services.getLocale());
  services.initBacktestUniverseSelector();
}

function closeGlobalSearch() {
  const { input, results } = globalSearchRefs;
  if (!input || !results) return;
  results.hidden = true;
  input.setAttribute("aria-expanded", "false");
  globalSearchState.activeIndex = -1;
}

/** @param {string} query */
async function loadGlobalSearch(query = "") {
  const { input } = globalSearchRefs;
  if (!input) return;
  const normalizedQuery = searchText(query);
  const localItems = [...globalSearchStrategies, ...globalSearchPages].filter(
    (item) => localSearchMatches(item, normalizedQuery),
  );
  const requestId = ++globalSearchState.requestId;
  globalSearchState.controller?.abort();
  globalSearchState.controller = new AbortController();
  globalSearchState.activeIndex = -1;
  if (!normalizedQuery) {
    globalSearchState.results = [
      ...globalSearchStrategies,
      ...globalSearchPages,
    ];
    renderGlobalSearch();
    return;
  }
  globalSearchState.results = localItems;
  renderGlobalSearch("正在搜索 A-pi 功能和 Tushare 股票库…");
  try {
    const response = await fetch(
      `/api/stocks/search?q=${encodeURIComponent(query.trim())}&limit=8`,
      { signal: globalSearchState.controller.signal },
    );
    const data = await response.json();
    if (requestId !== globalSearchState.requestId) return;
    if (!response.ok || !data.ok)
      throw new Error(data.error || `HTTP ${response.status}`);
    const stockItems = /** @type {import('../types/domain').Stock[]} */ (
      data.results || []
    ).map((item) => ({
      type: "股票",
      title: `${item.name}（${item.symbol}）`,
      meta: `${item.ts_code}${item.industry ? ` · ${item.industry}` : ""}${data.stale ? " · 缓存数据" : ""}`,
      icon: "股",
      ts_code: item.ts_code,
      symbol: item.symbol,
      name: item.name,
      industry: item.industry || "",
      section: "backtest",
    }));
    globalSearchState.results = [...stockItems, ...localItems];
    renderGlobalSearch();
  } catch (errorCaught) {
    const error = toError(errorCaught);
    if (
      error.name === "AbortError" ||
      requestId !== globalSearchState.requestId
    )
      return;
    globalSearchState.results = localItems;
    renderGlobalSearch(
      localItems.length
        ? `股票实时搜索暂不可用：${error.message}`
        : `搜索暂不可用：${error.message}`,
    );
  }
}

/** @param {import('../types/module-state').SearchItem | null} item */
function selectGlobalSearchResult(item) {
  if (!item) return;
  closeGlobalSearch();
  if (globalSearchRefs.input) globalSearchRefs.input.value = "";
  if (item.type === "股票") {
    services.showSection("backtest");
    if (services.addStockToPicker(item))
      services.showToast(`已将 ${item.name} 加入股票池`);
    return;
  }
  if (item.type === "策略") {
    services.showSection("backtest");
    const strategySelect = requiredElement("strategySelect");
    if (strategySelect) strategySelect.value = item.value || "baseline";
    services.showToast(`已打开策略：${item.title}`);
    return;
  }
  services.showSection(item.section || "overview");
  services.showToast(`已打开页面：${item.title}`);
}

function bindGlobalSearch() {
  const { input, results } = globalSearchRefs;
  if (!input || !results) return;
  input.addEventListener("focus", () => void loadGlobalSearch(input.value));
  input.addEventListener("input", () => {
    clearTimeout(globalSearchState.timer);
    globalSearchState.timer = setTimeout(
      () => void loadGlobalSearch(input.value),
      220,
    );
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeGlobalSearch();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (results.hidden) return;
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      const total = globalSearchState.results.length;
      globalSearchState.activeIndex = total
        ? (globalSearchState.activeIndex + step + total) % total
        : -1;
      renderGlobalSearch();
      results
        .querySelector(".global-search-item.active")
        ?.scrollIntoView({ block: "nearest" });
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      selectGlobalSearchResult(
        globalSearchState.results[globalSearchState.activeIndex] ||
          globalSearchState.results[0],
      );
    }
  });
  results.addEventListener("click", (event) => {
    const item = services.closestHTMLElement(event, "[data-global-index]");
    if (item)
      selectGlobalSearchResult(
        globalSearchState.results[Number(item.dataset.globalIndex)],
      );
  });
  document.addEventListener("click", (event) => {
    if (!services.closestHTMLElement(event, ".global-search"))
      closeGlobalSearch();
  });
}

/** @param {import('../types/services').StrategySearchServices} dependencies */
export function initStrategySearch(dependencies) {
  services = dependencies;
  if (initialized) return;
  globalSearchPages = Object.entries(services.sectionNames).map(
    ([section, title]) => ({
      type: "页面",
      title,
      meta: "A-pi 工作台页面",
      section,
      icon: "页",
    }),
  );
  globalSearchRefs = {
    input: requiredElement("globalSearchInput"),
    results: requiredElement("globalSearchResults"),
  };
  bindGlobalSearch();
  initialized = true;
}

export const APiStrategySearch = {
  init: initStrategySearch,
  loadStrategies,
  syncStrategyModeControls,
  rerenderStrategyOptions: renderStrategyOptions,
  strategyCatalog: () => strategyCatalog,
};
