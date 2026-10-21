import { setSafeHtml } from "./safe-html.mjs";
import { apiFetch as fetch } from "./api-client.mjs";
import { requiredElement, toError } from "./runtime-utils.mjs";
/** @type {import('../types/services').StockPickerServices} */
let services;
/** @type {import('../types/module-state').StockPickerRefs | null} */
let refs = null;
let initialized = false;
/** @type {import('../types/module-state').StockPickerState} */
const state = {
  selected: [],
  results: [],
  total: 0,
  activeIndex: -1,
  timer: undefined,
  requestId: 0,
  controller: null,
  source: "search",
  offset: 0,
  hasMore: false,
  loadingMore: false,
  loadMoreError: "",
  skipFocusLoad: false,
};

/** @param {unknown} value */
export function normalizeManualStock(value) {
  const raw = String(value || "")
    .trim()
    .toUpperCase()
    .replace(/\s/g, "");
  const parts = raw.split(".");
  const code = parts[0];
  if (
    !/^\d{6}$/.test(code) ||
    (parts[1] && !["SH", "SZ", "BJ"].includes(parts[1]))
  )
    return null;
  const market =
    parts[1] ||
    (code.startsWith("4") || code.startsWith("8") || code.startsWith("9")
      ? "BJ"
      : code.startsWith("5") || code.startsWith("6")
        ? "SH"
        : "SZ");
  return {
    ts_code: `${code}.${market}`,
    symbol: code,
    name: "手动代码",
    industry: "",
  };
}

/** @param {import('../types/domain').Stock[]} current @param {import('../types/domain').Stock[]} incoming */
export function appendUniqueStockRows(current, incoming) {
  const known = new Set(current.map((item) => item.ts_code).filter(Boolean));
  /** @type {import('../types/domain').Stock[]} */
  const additions = [];
  incoming.forEach((item) => {
    if (!item.ts_code || known.has(item.ts_code)) return;
    known.add(item.ts_code);
    additions.push(item);
  });
  return [...current, ...additions];
}

/** @param {unknown} value */
function splitStockTokens(value) {
  return String(value || "")
    .split(/[,，\s]+/)
    .map((token) => token.trim())
    .filter(Boolean);
}

/** @param {string} tsCode */
function stockAlreadySelected(tsCode) {
  return state.selected.some((item) => item.ts_code === tsCode);
}

function renderSelectedStocks() {
  if (!refs) return;
  refs.hidden.value = state.selected.map((item) => item.ts_code).join(", ");
  setSafeHtml(
    refs.chips,
    state.selected
      .map(
        (item, index) => `
    <span class="stock-chip">
      <span class="stock-chip-copy"><strong>${services.escapeHtml(item.name || item.ts_code)}</strong><small>${services.escapeHtml(item.ts_code)}</small></span>
      <button type="button" class="stock-chip-remove" data-remove-stock="${index}" aria-label="移除 ${services.escapeHtml(item.name || item.ts_code)}">×</button>
    </span>
  `,
      )
      .join(""),
  );
  const maxSymbols = services.getMaxSymbols();
  const locale = services.getLocale();
  refs.meta.textContent = services.localeText(
    state.total
      ? `${state.total.toLocaleString(locale)} 只 A 股可搜索 · 已选 ${state.selected.length}/${maxSymbols}`
      : `已选 ${state.selected.length}/${maxSymbols}`,
  );
  services.applyLocale(locale);
}

/** @param {boolean} open */
function setStockPickerOpen(open) {
  if (!refs) return;
  refs.suggestions.hidden = !open;
  refs.input.setAttribute("aria-expanded", String(open));
}

function stockPickerPagerCopy() {
  const locale = services.getLocale();
  const formatter = new Intl.NumberFormat(locale);
  const range = `${formatter.format(state.results.length)} / ${formatter.format(state.total)}`;
  const completeCount = formatter.format(state.total);
  /** @type {Record<string,{loading:string;more:string;retry:string;complete:string}>} */
  const copy = {
    "zh-CN": {
      loading: "正在加载更多股票…",
      more: `加载更多 · ${range}`,
      retry: `重试加载 · ${range}`,
      complete: `已全部加载 · ${completeCount} 只股票`,
    },
    "en-US": {
      loading: "Loading more stocks…",
      more: `Load more · ${range}`,
      retry: `Retry loading · ${range}`,
      complete: `All ${completeCount} stocks loaded`,
    },
    "fr-FR": {
      loading: "Chargement des actions…",
      more: `Charger la suite · ${range}`,
      retry: `Réessayer · ${range}`,
      complete: `${completeCount} actions chargées`,
    },
    "ja-JP": {
      loading: "銘柄を読み込み中…",
      more: `さらに読み込む · ${range}`,
      retry: `再試行 · ${range}`,
      complete: `${completeCount} 銘柄を読み込み済み`,
    },
  };
  return copy[locale] || copy["zh-CN"];
}

/** @param {string} message */
function renderStockSuggestions(message = "") {
  if (!refs) return;
  const locale = services.getLocale();
  const query = refs.input.value.trim();
  const header = `<div class="stock-suggestions-head"><strong>${services.localeText(query ? "搜索结果" : "全市场候选")}</strong><span>${state.total ? services.escapeHtml(services.localeText(`${state.total.toLocaleString(locale)} 只股票`)) : ""}</span></div>`;
  if (message) {
    setSafeHtml(
      refs.suggestions,
      `${header}<div class="stock-suggestions-empty">${services.escapeHtml(message)}</div>`,
    );
    setStockPickerOpen(true);
    return;
  }
  if (!state.results.length) {
    setSafeHtml(
      refs.suggestions,
      `${header}<div class="stock-suggestions-empty">${services.escapeHtml(services.localeText("未找到匹配股票，可尝试名称、6 位代码或拼音首字母。"))}</div>`,
    );
    setStockPickerOpen(true);
    return;
  }
  const pagerCopy = stockPickerPagerCopy();
  const pager =
    state.source === "universe" && state.total
      ? state.hasMore || state.loadingMore || state.loadMoreError
        ? `<div class="stock-suggestions-more"><button type="button" data-stock-load-more ${state.loadingMore ? "disabled" : ""}>${services.escapeHtml(state.loadingMore ? pagerCopy.loading : state.loadMoreError ? pagerCopy.retry : pagerCopy.more)}</button>${state.loadMoreError ? `<small role="status">${services.escapeHtml(state.loadMoreError)}</small>` : ""}</div>`
        : `<div class="stock-suggestions-more complete" role="status">${services.escapeHtml(pagerCopy.complete)}</div>`
      : "";
  setSafeHtml(
    refs.suggestions,
    header +
      state.results
        .map((item, index) => {
          const selected = stockAlreadySelected(
            item.ts_code || item.symbol || "",
          );
          return `<button type="button" role="option" aria-selected="${selected}" class="stock-suggestion${index === state.activeIndex ? " active" : ""}" data-stock-index="${index}" ${selected ? "disabled" : ""}>
      <span class="stock-suggestion-main"><strong>${services.escapeHtml(item.name)}</strong><small>${services.escapeHtml(item.ts_code)}${item.industry ? ` · ${services.escapeHtml(item.industry)}` : ""}</small></span>
      <span class="stock-suggestion-action">${services.escapeHtml(services.localeText(selected ? "已加入" : "加入"))}</span>
    </button>`;
        })
        .join("") +
      pager,
  );
  setStockPickerOpen(true);
}

/** @param {string} query */
async function loadStockSuggestions(query = "") {
  if (!refs) return;
  const normalizedQuery = String(query || "").trim();
  if (!normalizedQuery) return loadAllUniverseSuggestions();
  const requestId = ++state.requestId;
  if (state.controller) state.controller.abort();
  state.controller = new AbortController();
  state.source = "search";
  state.results = [];
  state.total = 0;
  state.offset = 0;
  state.hasMore = false;
  state.loadingMore = false;
  state.loadMoreError = "";
  state.activeIndex = -1;
  renderStockSuggestions("正在加载全市场股票列表…");
  try {
    const response = await fetch(
      `/api/stocks/search?q=${encodeURIComponent(normalizedQuery)}&limit=10`,
      { signal: state.controller.signal },
    );
    const data = await response.json();
    if (requestId !== state.requestId) return;
    if (!response.ok || !data.ok)
      throw new Error(data.error || `HTTP ${response.status}`);
    state.results = data.results || [];
    state.total = Number(data.total || data.match_count || 0);
    renderStockSuggestions();
  } catch (errorCaught) {
    const error = toError(errorCaught);
    if (error.name === "AbortError" || requestId !== state.requestId) return;
    state.results = [];
    renderStockSuggestions(`股票列表暂时不可用：${error.message}`);
  }
}

async function loadAllUniverseSuggestions() {
  if (!refs) return;
  const requestId = ++state.requestId;
  if (state.controller) state.controller.abort();
  state.controller = new AbortController();
  state.source = "universe";
  state.results = [];
  state.total = 0;
  state.offset = 0;
  state.hasMore = false;
  state.loadingMore = false;
  state.loadMoreError = "";
  state.activeIndex = -1;
  renderStockSuggestions("正在加载全部 A 股候选列表…");
  try {
    const response = await fetch(
      "/api/stocks/universe?mode=all&offset=0&limit=100",
      { signal: state.controller.signal },
    );
    const data = await response.json();
    if (requestId !== state.requestId) return;
    if (!response.ok || !data.ok)
      throw new Error(data.error || `HTTP ${response.status}`);
    state.results = data.results || [];
    state.total = Number(data.total || 0);
    state.offset = state.results.length;
    state.hasMore =
      typeof data.has_more === "boolean"
        ? data.has_more
        : state.offset < state.total;
    renderStockSuggestions();
  } catch (errorCaught) {
    const error = toError(errorCaught);
    if (error.name === "AbortError" || requestId !== state.requestId) return;
    state.results = [];
    renderStockSuggestions(`全市场股票列表暂时不可用：${error.message}`);
  }
}

async function loadMoreUniverseSuggestions() {
  if (
    !refs ||
    state.source !== "universe" ||
    state.loadingMore ||
    (!state.hasMore && !state.loadMoreError)
  )
    return;
  const requestId = state.requestId;
  const offset = state.offset;
  const scrollTop = refs.suggestions.scrollTop;
  const controller = new AbortController();
  state.controller = controller;
  state.loadingMore = true;
  state.loadMoreError = "";
  renderStockSuggestions();
  refs.suggestions.scrollTop = scrollTop;
  try {
    const response = await fetch(
      `/api/stocks/universe?mode=all&offset=${offset}&limit=100`,
      { signal: controller.signal },
    );
    const data = await response.json();
    if (requestId !== state.requestId || state.source !== "universe") return;
    if (!response.ok || !data.ok)
      throw new Error(data.error || `HTTP ${response.status}`);
    const rows = Array.isArray(data.results) ? data.results : [];
    if (!rows.length && offset < Number(data.total || state.total))
      throw new Error("本页没有返回股票数据，请重试");
    state.results = appendUniqueStockRows(state.results, rows);
    state.offset = offset + rows.length;
    state.total = Number(data.total || state.total);
    state.hasMore =
      rows.length > 0 &&
      (typeof data.has_more === "boolean"
        ? data.has_more
        : state.offset < state.total);
  } catch (errorCaught) {
    const error = toError(errorCaught);
    if (
      error.name === "AbortError" ||
      requestId !== state.requestId ||
      state.source !== "universe"
    )
      return;
    state.loadMoreError = error.message || "加载失败";
  } finally {
    if (requestId === state.requestId && state.source === "universe") {
      state.loadingMore = false;
      renderStockSuggestions();
      refs.suggestions.scrollTop = scrollTop;
    }
  }
}

async function loadHistoricalIndexUniverse() {
  const button = requiredElement("indexUniverseButton");
  const indexCode = requiredElement("indexUniverseSelect")?.value || "";
  if (!indexCode) {
    services.showToast("Tushare 指数配置尚未加载");
    return;
  }
  const asOf = requiredElement("indexUniverseDate")?.value || "";
  if (button) {
    button.disabled = true;
    button.textContent = services.localeText("加载中");
  }
  try {
    const query = new URLSearchParams({ index_code: indexCode });
    if (asOf) query.set("as_of", asOf);
    const response = await fetch(`/api/index/universe?${query.toString()}`);
    const data = await response.json();
    if (!response.ok || !data.ok)
      throw new Error(data.error || `HTTP ${response.status}`);
    const rankedMembers = [...(data.members || [])].sort(
      (a, b) =>
        (Number(b.weight) || 0) - (Number(a.weight) || 0) ||
        String(a.ts_code).localeCompare(String(b.ts_code)),
    );
    state.selected = rankedMembers
      .slice(0, services.getMaxSymbols())
      .map((item) => ({
        ts_code: item.ts_code,
        symbol: item.symbol,
        name: item.ts_code,
        industry: "",
      }));
    state.total = Number(data.count || 0);
    renderSelectedStocks();
    const meta = requiredElement("indexUniverseMeta");
    if (meta)
      meta.textContent = `${data.index_name} · ${data.trade_date} · ${data.count} 只，已按历史指数权重载入前 ${Math.min(services.getMaxSymbols(), data.count)} 只`;
    services.showToast(
      `${data.index_name} ${data.trade_date} 历史成分股已载入`,
    );
  } catch (errorCaught) {
    const error = toError(errorCaught);
    services.showToast(`历史成分股加载失败 · ${error.message}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = services.localeText("加载历史成分股");
    }
  }
}

/** @type {import('../types/services').SharedServices['addStockToPicker']} */
export function addStockToPicker(item) {
  if (!services) return false;
  const normalized = normalizeManualStock(item.ts_code || item.symbol);
  if (!normalized) {
    services.showToast("股票代码格式不正确");
    return false;
  }
  const selected = {
    ...normalized,
    name: item.name || normalized.name,
    industry: item.industry || "",
  };
  if (stockAlreadySelected(selected.ts_code)) {
    services.showToast("这只股票已经在股票池中");
    return false;
  }
  const maxSymbols = services.getMaxSymbols();
  if (state.selected.length >= maxSymbols) {
    services.showToast(`股票池最多选择 ${maxSymbols} 只股票`);
    return false;
  }
  state.selected.push(selected);
  renderSelectedStocks();
  if (!refs) return false;
  refs.input.value = "";
  state.results = [];
  setStockPickerOpen(false);
  state.skipFocusLoad = document.activeElement !== refs.input;
  refs.input.focus();
  return true;
}

/** @param {unknown} value */
function addManualStocks(value) {
  const tokens = splitStockTokens(value);
  if (!tokens.length) return;
  let invalid = false;
  tokens.forEach((token) => {
    const normalized = normalizeManualStock(token);
    if (normalized) addStockToPicker(normalized);
    else invalid = true;
  });
  if (invalid) services.showToast("请输入 6 位 A 股代码，或从下拉候选中选择");
}

function mountStockPicker() {
  const hidden = requiredElement("symbolInput");
  const host = hidden?.closest(".select-wrap");
  if (
    !(hidden instanceof HTMLInputElement) ||
    !(host instanceof HTMLElement) ||
    host.dataset.enhanced === "true"
  )
    return;
  const initial = splitStockTokens(hidden.value)
    .map(normalizeManualStock)
    .filter((item) => item !== null);
  state.selected = initial;
  host.dataset.enhanced = "true";
  host.className = "stock-picker-host";
  setSafeHtml(host, "");
  hidden.type = "hidden";
  hidden.setAttribute("aria-hidden", "true");
  hidden.tabIndex = -1;
  const chips = document.createElement("div");
  chips.className = "stock-chip-list";
  chips.id = "stockChipList";
  const searchField = document.createElement("div");
  searchField.className = "stock-search-field";
  const icon = document.createElement("span");
  icon.className = "stock-search-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = "⌕";
  const input = document.createElement("input");
  input.id = "stockSearchInput";
  input.type = "text";
  input.placeholder = "搜索股票名称、代码或拼音";
  input.autocomplete = "off";
  input.setAttribute("aria-label", "搜索股票名称、代码或拼音");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-controls", "stockSuggestions");
  input.setAttribute("aria-expanded", "false");
  const browse = requiredElement("allUniverseButton");
  if (browse instanceof HTMLButtonElement) {
    browse.classList.add("stock-search-browse");
    browse.classList.remove("universe-button");
    browse.type = "button";
    browse.title = "浏览全部 A 股候选（不清空已选）";
    if (!browse.textContent?.trim()) browse.textContent = "浏览全部 A 股";
    searchField.append(icon, input, browse);
  } else {
    searchField.append(icon, input);
  }
  const meta = document.createElement("div");
  meta.className = "stock-picker-meta";
  const suggestions = document.createElement("div");
  suggestions.id = "stockSuggestions";
  suggestions.className = "stock-suggestions";
  suggestions.setAttribute("role", "listbox");
  suggestions.hidden = true;
  host.append(chips, searchField, hidden, meta, suggestions);
  refs = { host, hidden, chips, input, meta, suggestions };
  renderSelectedStocks();
  browse?.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    // Browse opens the full-A candidate dropdown only — never clear state.selected.
    void loadAllUniverseSuggestions();
  });
  requiredElement("indexUniverseButton")?.addEventListener(
    "click",
    loadHistoricalIndexUniverse,
  );
  input.addEventListener("focus", () => {
    if (state.skipFocusLoad) {
      state.skipFocusLoad = false;
      return;
    }
    loadStockSuggestions(input.value.trim());
  });
  input.addEventListener("input", () => {
    clearTimeout(state.timer);
    state.timer = setTimeout(
      () => loadStockSuggestions(input.value.trim()),
      220,
    );
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      setStockPickerOpen(false);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (suggestions.hidden) return;
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      state.activeIndex = Math.max(
        -1,
        Math.min(state.results.length - 1, state.activeIndex + step),
      );
      renderStockSuggestions();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const active = state.results[state.activeIndex];
      if (active) addStockToPicker(active);
      else addManualStocks(input.value);
    }
  });
  chips.addEventListener("click", (event) => {
    const button = services.closestHTMLElement(event, "[data-remove-stock]");
    if (!button) return;
    state.selected.splice(Number(button.dataset.removeStock), 1);
    renderSelectedStocks();
  });
  suggestions.addEventListener("click", (event) => {
    if (services.closestHTMLElement(event, "[data-stock-load-more]")) {
      loadMoreUniverseSuggestions();
      return;
    }
    const button = services.closestHTMLElement(event, "[data-stock-index]");
    if (button)
      addStockToPicker(state.results[Number(button.dataset.stockIndex)]);
  });
  suggestions.addEventListener("scroll", () => {
    if (
      !state.loadMoreError &&
      suggestions.scrollHeight -
        suggestions.scrollTop -
        suggestions.clientHeight <=
        44
    )
      loadMoreUniverseSuggestions();
  });
  suggestions.addEventListener("mousedown", (event) => {
    if (
      services.closestHTMLElement(
        event,
        "[data-stock-index], [data-stock-load-more]",
      )
    )
      event.preventDefault();
  });
  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Node && host.contains(event.target)))
      setStockPickerOpen(false);
  });
}

/** @param {import('../types/services').StockPickerServices} dependencies */
export function initStockPicker(dependencies) {
  services = dependencies;
  if (initialized) return;
  mountStockPicker();
  initialized = true;
}

/** @param {import('../types/domain').Stock[]} seed @param {string[]} fallbackSymbols */
export function setDefaultStocks(seed, fallbackSymbols) {
  if (state.selected.length || !fallbackSymbols.length) return;
  state.selected = seed.map((item) => ({
    ts_code: item.ts_code,
    symbol: item.symbol,
    name: item.name || item.symbol,
    industry: item.industry || "",
  }));
  if (refs) renderSelectedStocks();
  else {
    const hidden = requiredElement("symbolInput");
    if (hidden) hidden.value = fallbackSymbols.join(", ");
  }
}

export const APiStockPicker = {
  init: initStockPicker,
  add: addStockToPicker,
  renderSelected: renderSelectedStocks,
  setDefaultStocks,
};
