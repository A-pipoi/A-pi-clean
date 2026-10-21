import { setSafeHtml } from "./safe-html.mjs";
import { apiFetch as fetch } from "./api-client.mjs";
import { requiredElement, toError } from "./runtime-utils.mjs";
/** @type {import('../types/services').FinancialStatementsServices} */
let services;
let latestStatements = null;
let requestId = 0;
/** @type {AbortController | null} */
let abortController = null;
let initialized = false;

/** @param {unknown} value */
export function statementNumber(value, locale = "zh-CN") {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const absolute = Math.abs(number);
  if (absolute >= 100000000) return `${(number / 100000000).toFixed(2)} 亿`;
  if (absolute >= 10000) return `${(number / 10000).toFixed(2)} 万`;
  return number.toLocaleString(locale, { maximumFractionDigits: 2 });
}

/** @param {import('../types/domain').FinancialStatements} data @param {string} symbol */
function applyFinancialStatements(data, symbol) {
  latestStatements = data;
  const meta = data?.metadata || {};
  const metaHost = requiredElement("statementMeta");
  if (metaHost) {
    const status =
      meta.status === "available"
        ? "已接入"
        : meta.status === "empty"
          ? "暂无公告数据"
          : "不可用";
    const error = (meta.errors || []).length ? ` · ${meta.errors?.[0]}` : "";
    const selector = requiredElement("statementSymbolSelect");
    const company =
      selector?.selectedOptions?.[0]?.textContent?.trim() || symbol;
    metaHost.textContent = `${company} · ${status} · ${meta.source || "Tushare"} · 截止 ${meta.as_of || "—"}${error}`;
  }
  const grid = requiredElement("statementGrid");
  if (!grid) return;
  const items = data.statements?.[symbol] || {};
  /** @type {Array<[string, string, Array<[string, string]>]>} */
  const configs = [
    [
      "income",
      "利润表",
      [
        ["营业收入", "total_revenue"],
        ["归母净利润", "n_income_attr_p"],
        ["基本每股收益", "basic_eps"],
      ],
    ],
    [
      "balancesheet",
      "资产负债表",
      [
        ["资产总计", "total_assets"],
        ["负债合计", "total_liab"],
        ["归母权益", "total_hldr_eqy_exc_min_int"],
      ],
    ],
    [
      "cashflow",
      "现金流量表",
      [
        ["经营活动现金流", "n_cashflow_act"],
        ["投资活动现金流", "n_cashflow_inv_act"],
        ["筹资活动现金流", "n_cash_flows_fnc_act"],
      ],
    ],
  ];
  const cards = configs
    .map(([key, title, fields]) => {
      const row = items[key];
      if (!row)
        return `<article class="statement-card"><h3>${title}</h3><div class="statement-empty">暂无该报表的公告日可用记录</div></article>`;
      const raw = row.raw || {};
      return `<article class="statement-card"><h3>${title}</h3><div class="statement-date">报告期 ${services.escapeHtml(row.report_date || "—")} · 公告日 ${services.escapeHtml(row.announcement_date || "—")}</div><dl>${fields.map(([label, field]) => `<div><dt>${label}</dt><dd>${statementNumber(raw[field], services.getLocale())}</dd></div>`).join("")}</dl></article>`;
    })
    .join("");
  setSafeHtml(
    grid,
    cards || '<div class="statement-empty">暂无三大报表数据</div>',
  );
  services.applyLocale(services.getLocale());
}

export async function loadFinancialStatements(showFailure = false) {
  if (!services) return false;
  const selector = requiredElement("statementSymbolSelect");
  const symbol = selector?.value || "";
  const currentRequestId = ++requestId;
  abortController?.abort();
  abortController = null;
  const refreshButton = requiredElement("refreshStatementsButton");
  const metaHost = requiredElement("statementMeta");
  const grid = requiredElement("statementGrid");
  latestStatements = null;
  if (!symbol) {
    if (refreshButton) refreshButton.disabled = true;
    if (metaHost) metaHost.textContent = services.localeText("请先选择股票");
    if (grid)
      setSafeHtml(
        grid,
        '<div class="statement-empty">选择股票后加载公告日对齐的三大报表。</div>',
      );
    return false;
  }
  const company = selector.selectedOptions?.[0]?.textContent?.trim() || symbol;
  const asOf = services.getLatestTradeDate() || "";
  const controller = new AbortController();
  abortController = controller;
  if (refreshButton) refreshButton.disabled = true;
  if (metaHost)
    metaHost.textContent = services.localeText(
      `${company} · 正在读取 Tushare 三大报表…`,
    );
  if (grid)
    setSafeHtml(
      grid,
      '<div class="statement-empty">正在加载所选公司的三大报表…</div>',
    );
  try {
    const query = new URLSearchParams({ symbols: symbol, statement: "all" });
    if (asOf) query.set("as_of", asOf);
    const response = await fetch(`/api/financials?${query.toString()}`, {
      cache: "no-store",
      signal: controller.signal,
    });
    const data = await response.json();
    if (currentRequestId !== requestId || selector.value !== symbol)
      return false;
    if (!response.ok || !data.ok)
      throw new Error(data.error || `HTTP ${response.status}`);
    if (
      !data.statements ||
      !Object.prototype.hasOwnProperty.call(data.statements, symbol) ||
      (Array.isArray(data.metadata?.symbols) &&
        !data.metadata.symbols.includes(symbol))
    ) {
      throw new Error(`返回数据与所选股票 ${symbol} 不一致`);
    }
    applyFinancialStatements(data, symbol);
    return true;
  } catch (errorCaught) {
    const error = toError(errorCaught);
    if (
      currentRequestId !== requestId ||
      controller.signal.aborted ||
      selector.value !== symbol
    )
      return false;
    if (showFailure)
      services.showToast(`三大财务报表加载失败 · ${error.message}`);
    if (metaHost)
      metaHost.textContent = services.localeText(
        `${company} · Tushare 财务报表不可用 · ${error.message}`,
      );
    if (grid)
      setSafeHtml(
        grid,
        '<div class="statement-empty">加载失败，请检查数据连接后重试。</div>',
      );
    services.applyLocale(services.getLocale());
    return false;
  } finally {
    if (currentRequestId === requestId) {
      abortController = null;
      if (refreshButton) refreshButton.disabled = false;
    }
  }
}

/** @param {import('../types/services').FinancialStatementsServices} dependencies */
export function initFinancialStatementsPanel(dependencies) {
  services = dependencies;
  if (initialized) return;
  requiredElement("statementSymbolSelect")?.addEventListener(
    "change",
    () => void loadFinancialStatements(true),
  );
  requiredElement("refreshStatementsButton")?.addEventListener(
    "click",
    () => void loadFinancialStatements(true),
  );
  initialized = true;
}

export const APiFinancialStatements = {
  init: initFinancialStatementsPanel,
  load: loadFinancialStatements,
};
