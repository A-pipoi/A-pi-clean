import { setSafeHtml } from "./safe-html.mjs";
import { apiFetch as fetch } from "./api-client.mjs";
import { requiredElement, toError } from "./runtime-utils.mjs";
/** market view with explicit services and live state accessors. */
/** @param {import('../types/services').MarketViewServices} deps */
export function createMarketView(deps) {
  /** @param {import('../types/domain').Stock | undefined} index @param {string} valueId @param {string} changeId */
  function applyIndexValue(index, valueId, changeId) {
    if (!index) {
      deps.setText(valueId, "—");
      deps.setText(changeId, "暂无数据");
      return;
    }
    const close = Number(index?.close);
    const change = Number(index?.change_pct);
    deps.setText(valueId, deps.formatNumber(close));
    const changeElement = document.getElementById(changeId);
    if (changeElement) {
      changeElement.textContent = Number.isFinite(change)
        ? `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`
        : "—";
      changeElement.className =
        "datum-note " +
        (Number.isFinite(change) && change > 0
          ? "rise"
          : Number.isFinite(change) && change < 0
            ? "fall"
            : "neutral");
    }
  }

  /** @param {import('../types/domain').Stock[]} indices */
  function applyIndexCollection(indices, latestTradeDate = "") {
    const byCode = Object.fromEntries(
      (indices || []).map((index) => [index.ts_code, index]),
    );
    applyIndexValue(
      byCode["000001.SH"],
      "shanghaiIndexValue",
      "shanghaiIndexChange",
    );
    applyIndexValue(
      byCode["399001.SZ"],
      "shenzhenIndexValue",
      "shenzhenIndexChange",
    );
    applyIndexValue(
      byCode["399006.SZ"],
      "chinextIndexValue",
      "chinextIndexChange",
    );
    const tradeDate =
      latestTradeDate ||
      Object.values(byCode)
        .map((index) => index.trade_date)
        .sort()
        .at(-1) ||
      "";
    const values = Object.values(byCode);
    const hasCache = values.some(
      (index) => index.stale || index.source === "cache",
    );
    const hasLive = values.some((index) => index.source === "tushare");
    const sourceLabel = hasCache ? (hasLive ? "混合" : "缓存") : "实时";
    deps.setText(
      "marketDateLabel",
      tradeDate
        ? `最近交易日 / ${tradeDate} · ${sourceLabel}`
        : `Tushare · ${sourceLabel}`,
    );
    const status = requiredElement("marketDataStatus");
    if (status) {
      setSafeHtml(
        status,
        `<span class="pulse-dot"></span><span class="market-data-source ${deps.dataSourceClass(hasCache ? (hasLive ? "tushare_mixed" : "cache") : "tushare_live", hasCache && !hasLive)}">${sourceLabel} · ${Object.keys(byCode).length}/3 指数</span>`,
      );
      status.title = status.textContent || "";
    }
    deps.applyLocale(deps.activeLocale);
  }

  /** @param {import('../types/domain').Stock | undefined} index */
  function applyShanghaiIndex(index) {
    applyIndexCollection(index ? [index] : [], index?.trade_date || "");
  }

  async function loadMarketIndex(showFailure = false) {
    try {
      const response = await fetch("/api/market/indices");
      const data = await response.json();
      if (!response.ok || !data.ok || !data.indices?.length)
        throw new Error(data.error || `HTTP ${response.status}`);
      applyIndexCollection(data.indices, data.latest_trade_date);
    } catch (errorCaught) {
      const error = toError(errorCaught);
      applyIndexCollection([], "");
      deps.setText("shanghaiIndexChange", "暂不可用");
      const status = requiredElement("marketDataStatus");
      if (status) {
        const label = `${deps.localeText("行情数据")} · ${deps.localeText("暂不可用")}`;
        setSafeHtml(
          status,
          `<span class="pulse-dot"></span><span>${deps.escapeHtml(label)}</span>`,
        );
        status.title = status.textContent || "";
      }
      deps.applyLocale(deps.activeLocale);
      if (showFailure) deps.showToast(`上证指数加载失败 · ${error.message}`);
    }
  }

  /** @type {import('../types/services').SharedServices['applyMarketSnapshot']} */
  function applyMarketSnapshot(market) {
    const index = market?.index;
    if (index)
      applyIndexCollection(market?.indices || [index], index.trade_date || "");
    const breadth = market?.breadth;
    if (breadth) {
      deps.setText(
        "marketTurnoverValue",
        `${deps.formatNumber(Number(breadth.turnover_billion), 0)} 亿`,
      );
      deps.setText("marketBreadthValue", `${breadth.up} / ${breadth.down}`);
      deps.setText(
        "marketBreadthLabel",
        `平 ${breadth.flat} · ${breadth.trade_date}`,
      );
    } else {
      deps.setText("marketTurnoverValue", "—");
      deps.setText("marketBreadthValue", "—");
      deps.setText("marketBreadthLabel", "Tushare 暂无");
    }
    const status = requiredElement("marketDataStatus");
    if (status && index) {
      const values = market?.indices || [index];
      const hasCache = values.some(
        (item) => item.stale || item.source === "cache",
      );
      const hasLive = values.some((item) => item.source === "tushare");
      const source = hasCache
        ? hasLive
          ? "tushare_mixed"
          : "cache"
        : "tushare_live";
      setSafeHtml(
        status,
        `<span class="pulse-dot"></span><span class="market-data-source ${deps.dataSourceClass(source, hasCache && !hasLive)}">${deps.dataSourceLabel(source, hasCache && !hasLive)} · ${values.length}/3 指数</span>`,
      );
      status.title = status.textContent || "";
    }
    deps.setText(
      "homeRunDate",
      index?.trade_date
        ? `Tushare · ${index.trade_date}`
        : "Tushare · 等待最新交易日",
    );
    deps.applyLocale(deps.activeLocale);
  }
  return {
    applyIndexValue,
    applyIndexCollection,
    applyShanghaiIndex,
    loadMarketIndex,
    applyMarketSnapshot,
  };
}
