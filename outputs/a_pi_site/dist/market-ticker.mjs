import { apiFetch as fetch } from "./api-client.mjs";
import { requiredElement, toError } from "./runtime-utils.mjs";
// @ts-check

/** @typedef {{ts_code?: unknown, change_pct?: unknown, close?: unknown, trade_time?: unknown}} MarketQuote */
/** @typedef {{indices?: MarketQuote[], source?: string, is_live?: boolean, latest_trade_time?: string, market_phase?: string}} MarketTickerPayload */

/** @type {Readonly<Record<string,string>>} */
const TICKER_NAMES = Object.freeze({
  "399006.SZ": "创业板指",
  "000300.SH": "沪深300",
  "000688.SH": "科创50",
  "000001.SH": "上证指数",
  "399001.SZ": "深证成指",
  "000905.SH": "中证500",
  "000016.SH": "上证50",
  "000010.SH": "上证180",
  "000698.SH": "科创100",
  "000852.SH": "中证1000",
  "000903.SH": "中证100",
  "000906.SH": "中证800",
  "000922.CSI": "中证红利",
  "399005.SZ": "中小100",
  "399102.SZ": "创业板综",
  "399330.SZ": "深证100",
  "399673.SZ": "创业板50",
  "399303.SZ": "国证2000",
  "399975.SZ": "证券公司",
  "399986.SZ": "中证银行",
});

let loading = false;
/** @type {ResizeObserver | null} */
let resizeObserver = null;

/** @type {import('../types/services').SharedServices['translate']} */
function translate(value) {
  return (
    globalThis.window?.localeText?.(String(value ?? "")) || String(value ?? "")
  );
}

/** @param {MarketQuote[]} quotes */
export function selectStableMarketTickerQuotes(quotes) {
  const priority = Object.keys(TICKER_NAMES);
  const seen = new Map();
  for (const quote of quotes) {
    const code = String(quote.ts_code || "").toUpperCase();
    if (code && !seen.has(code)) seen.set(code, quote);
  }
  return [...seen.values()].sort((a, b) => {
    const aa = String(a.ts_code).toUpperCase(),
      bb = String(b.ts_code).toUpperCase();
    const ar = priority.indexOf(aa),
      br = priority.indexOf(bb);
    return (
      (ar < 0 ? priority.length : ar) - (br < 0 ? priority.length : br) ||
      aa.localeCompare(bb, "en")
    );
  });
}

function updateMarketTickerMotion() {
  const viewport = document.querySelector(".market-ticker-window");
  const track = requiredElement("marketTickerTrack");
  if (!viewport || !track) return;
  const overflow = Math.max(0, track.scrollWidth - viewport.clientWidth);
  track.style.setProperty("--ticker-overflow", `${overflow}px`);
  track.style.setProperty(
    "--ticker-duration",
    `${Math.max(14, overflow / 18)}s`,
  );
  track.style.animationName = overflow > 2 ? "market-ticker-bounce" : "none";
}

/** @param {MarketTickerPayload} data */
function renderRealtimeMarketTicker(data) {
  const root = requiredElement("marketTicker");
  const track = requiredElement("marketTickerTrack");
  const status = requiredElement("marketTickerStatus");
  const stamp = requiredElement("marketTickerTime");
  if (!root || !track || !status || !stamp) return;

  const seen = new Set();
  const quotes = (Array.isArray(data.indices) ? data.indices : []).filter(
    (quote) => {
      const code = String(quote?.ts_code || "").toUpperCase();
      const change = Number(quote?.change_pct);
      const price = Number(quote?.close);
      if (
        !TICKER_NAMES[code] ||
        seen.has(code) ||
        !Number.isFinite(change) ||
        !Number.isFinite(price) ||
        price <= 0 ||
        !quote.trade_time
      )
        return false;
      seen.add(code);
      return true;
    },
  );
  if (
    !quotes.length ||
    data.source !== "tushare_rt_idx_k" ||
    data.is_live !== true
  ) {
    throw new Error("实时行情接口没有返回可验证的 Tushare 最新指数报价");
  }

  const displayedQuotes = selectStableMarketTickerQuotes(quotes);
  track.replaceChildren(
    ...displayedQuotes.map((quote) => {
      const item = document.createElement("span");
      item.className = "market-ticker-item";
      const name = document.createElement("span");
      name.textContent = translate(
        TICKER_NAMES[String(quote.ts_code).toUpperCase()],
      );
      const change = document.createElement("strong");
      const value = Number(quote.change_pct);
      change.className = value > 0 ? "rise" : value < 0 ? "fall" : "neutral";
      change.textContent = `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
      item.append(name, change);
      return item;
    }),
  );

  const latest = String(data.latest_trade_time || "");
  const latestDate = latest.slice(0, 10);
  const latestClock =
    latest.match(/\b(\d{2}:\d{2})(?::\d{2})?\b/)?.[1] || "--:--";
  const shanghaiToday = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
  }).format(new Date());
  const phase =
    latestDate === shanghaiToday ? data.market_phase || "交易中" : "休市";
  status.textContent = translate(phase);
  stamp.textContent =
    latestDate === shanghaiToday ? latestClock : latestDate.slice(5);
  root.dataset.state = phase === "交易中" ? "live" : "closed";
  root.title = `${translate("Tushare 实时行情")} · ${latest} · ${displayedQuotes.map((quote) => quote.ts_code).join(", ")}`;
  requestAnimationFrame(updateMarketTickerMotion);
}

/** @param {string} message */
function showRealtimeTickerError(message = "") {
  const root = requiredElement("marketTicker");
  const track = requiredElement("marketTickerTrack");
  const status = requiredElement("marketTickerStatus");
  const stamp = requiredElement("marketTickerTime");
  if (!root || !track || !status || !stamp) return;
  track.replaceChildren();
  root.dataset.state = "error";
  root.title = message || translate("实时行情暂不可用");
  status.textContent = translate("实时行情暂不可用");
  stamp.textContent = "";
}

async function loadRealtimeMarketTicker() {
  if (loading || document.visibilityState === "hidden") return;
  loading = true;
  try {
    const response = await fetch("/api/market/ticker", {
      cache: "no-store",
      signal: AbortSignal.timeout(20000),
    });
    const data = await response.json();
    if (!response.ok || !data.ok)
      throw new Error(data.error || `HTTP ${response.status}`);
    renderRealtimeMarketTicker(data);
  } catch (errorCaught) {
    const error = toError(errorCaught);
    showRealtimeTickerError(
      error instanceof Error ? error.message : String(error),
    );
  } finally {
    loading = false;
  }
}

if (typeof window !== "undefined" && typeof ResizeObserver !== "undefined") {
  resizeObserver = new ResizeObserver(updateMarketTickerMotion);
  window.addEventListener(
    "load",
    () => {
      const pause = document.querySelector("#marketTickerPause");
      pause?.addEventListener("click", () => {
        const paused = pause.getAttribute("aria-pressed") !== "true";
        pause.setAttribute("aria-pressed", String(paused));
        pause.setAttribute(
          "aria-label",
          paused ? "继续行情滚动" : "暂停行情滚动",
        );
        pause.textContent = paused ? "继续" : "暂停";
        requiredElement("marketTicker").classList.toggle("is-paused", paused);
      });
      const viewport = document.querySelector(".market-ticker-window");
      const track = requiredElement("marketTickerTrack");
      if (viewport) resizeObserver?.observe(viewport);
      if (track) resizeObserver?.observe(track);
    },
    { once: true },
  );
}

export const APiMarketTicker = Object.freeze({
  loadRealtimeMarketTicker,
  selectStableMarketTickerQuotes,
});
