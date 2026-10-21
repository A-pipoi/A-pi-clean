import { apiFetch as fetch } from "./api-client.mjs";
import { requiredElement, toError } from "./runtime-utils.mjs";
import { mountDataExtensionPanel } from "./data-extension-panel.mjs";
import {
  setExperimentProgress,
  clearExperimentProgress,
} from "./experiment-progress.mjs";
// @ts-check

/** @type {Record<string, {title:string;empty:string;description:string;action:string;loading:string;backendOld:string;ready(rows:number,symbols:number,versions:number):string;done(written:number,total:number):string;failed(message:string):string;confirm:string;cancel:string;cancelling:string;progress(stage:string,pct:number,detail:string):string}>} */
const COPY = {
  "zh-CN": {
    title: "PIT 特征库",
    empty: "尚无已物化的价格特征",
    description: "准备特征数据（不扣预算）",
    action: "预热 PIT 特征库",
    loading: "正在获取历史成分与真实日线…",
    backendOld:
      "服务端尚未包含预热接口，请先以管理员权限重启 A-pi Python 服务。",
    ready: (rows, symbols, versions) =>
      `已存储 ${rows} 行 · ${symbols} 只股票 · ${versions} 个数据版本`,
    done: (written, total) => `本次新增 ${written} 行；库内共 ${total} 行。`,
    failed: (message) => `预热失败：${message}`,
    confirm:
      "将请求沪深300历史成分对应的真实日线（最长 5 年，可能需要数分钟），只写入本地 PIT 特征库，不会运行策略或生成订单。继续吗？",
    cancel: "取消预热",
    cancelling: "已请求取消，等待安全检查点…",
    progress: (stage, pct, detail) => {
      /** @type {Record<string, string>} */
      const names = {
        universe: "宇宙",
        bars: "日线",
        materialize: "入库",
        queued: "排队",
        completed: "完成",
        cancelled: "已取消",
        failed: "失败",
      };
      return `预热中 ${pct}% · ${names[stage] || stage}${detail ? ` · ${detail}` : ""}`;
    },
  },
  "en-US": {
    title: "PIT feature store",
    empty: "No materialized price features yet",
    description:
      "Fetches real daily bars for historical CSI 300 constituents and writes versioned features only. No backtest, budget charge, or orders.",
    action: "Prepare PIT features",
    loading: "Fetching historical constituents and real daily bars…",
    backendOld:
      "The backend needs an administrator restart to load the feature preparation API.",
    ready: (rows, symbols, versions) =>
      `${rows} rows stored · ${symbols} symbols · ${versions} data versions`,
    done: (written, total) =>
      `${written} rows added; ${total} rows in the store.`,
    failed: (message) => `Preparation failed: ${message}`,
    confirm:
      "This fetches real daily bars for historical CSI 300 constituents (up to 5 years; it may take several minutes). It only writes the local PIT feature store—no strategy run or orders. Continue?",
    cancel: "Cancel warm-up",
    cancelling: "Cancel requested; waiting for a safe checkpoint…",
    progress: (stage, pct, detail) =>
      `Warming up ${pct}% · ${stage}${detail ? ` · ${detail}` : ""}`,
  },
  "fr-FR": {
    title: "Magasin de facteurs PIT",
    empty: "Aucun facteur de prix matérialisé",
    description:
      "Récupère les cours réels des constituants historiques du CSI 300 et écrit uniquement des facteurs versionnés. Aucun backtest, budget ou ordre.",
    action: "Préparer les facteurs PIT",
    loading: "Récupération des constituants et cours réels…",
    backendOld:
      "Redémarrez le service A-pi en tant qu’administrateur pour charger cette API.",
    ready: (rows, symbols, versions) =>
      `${rows} lignes · ${symbols} titres · ${versions} versions`,
    done: (written, total) => `${written} lignes ajoutées ; ${total} au total.`,
    failed: (message) => `Échec : ${message}`,
    confirm:
      "Cette opération récupère les cours réels des constituants historiques du CSI 300 (jusqu’à 5 ans, plusieurs minutes possibles). Elle ne lance aucun backtest et ne passe aucun ordre. Continuer ?",
    cancel: "Annuler",
    cancelling: "Annulation demandée…",
    progress: (stage, pct, detail) =>
      `Préparation ${pct}% · ${stage}${detail ? ` · ${detail}` : ""}`,
  },
  "ja-JP": {
    title: "PIT 特徴量ストア",
    empty: "価格特徴量はまだありません",
    description:
      "CSI 300 の過去構成銘柄の実日足からバージョン付き特徴量を作成します。バックテスト・予算消費・注文はありません。",
    action: "PIT 特徴量を準備",
    loading: "過去構成銘柄と実日足を取得中…",
    backendOld:
      "機能準備 API を読み込むため、管理者として A-pi Python サービスを再起動してください。",
    ready: (rows, symbols, versions) =>
      `${rows} 行 · ${symbols} 銘柄 · ${versions} バージョン`,
    done: (written, total) => `${written} 行追加、合計 ${total} 行です。`,
    failed: (message) => `失敗: ${message}`,
    confirm:
      "CSI 300 の過去構成銘柄の実日足を取得します（最大 5 年、数分かかる場合があります）。ローカルの PIT 特徴量ストアだけを更新し、戦略実行や注文は行いません。続行しますか？",
    cancel: "準備をキャンセル",
    cancelling: "キャンセルを受け付けました…",
    progress: (stage, pct, detail) =>
      `準備中 ${pct}% · ${stage}${detail ? ` · ${detail}` : ""}`,
  },
};

/** @type {Record<string, {asOf:string;tradingLag(n:number):string;calendarLag(n:number):string;upToDate:string;lastRun:string;never:string;bj:string;shards(done:number,pending:number):string;resumeAt(day:string):string;catchingUp:string}>} */
const FRESHNESS_COPY = {
  "zh-CN": {
    asOf: "特征截至",
    tradingLag: (n) => `落后 ${n} 个交易日`,
    calendarLag: (n) => `落后 ${n} 天`,
    upToDate: "已是最新",
    lastRun: "上次预热",
    never: "未记录",
    bj: "（北京时间）",
    shards: (done, pending) => `已校验分片 ${done} · 待续跑 ${pending}`,
    resumeAt: (d) => `续跑自 ${d}`,
    catchingUp: "可增量追赶",
  },
  "en-US": {
    asOf: "Features through",
    tradingLag: (n) => `${n} trading day${n === 1 ? "" : "s"} behind`,
    calendarLag: (n) => `${n} day${n === 1 ? "" : "s"} behind`,
    upToDate: "up to date",
    lastRun: "Last warm-up",
    never: "not recorded",
    bj: " (Beijing time)",
    shards: (done, pending) => `${done} verified shard(s) · ${pending} pending`,
    resumeAt: (d) => `resume from ${d}`,
    catchingUp: "catch-up available",
  },
  "fr-FR": {
    asOf: "Facteurs jusqu’au",
    tradingLag: (n) => `${n} séance(s) de retard`,
    calendarLag: (n) => `${n} jour(s) de retard`,
    upToDate: "à jour",
    lastRun: "Dernière préparation",
    never: "non enregistrée",
    bj: " (heure de Pékin)",
    shards: (done, pending) => `${done} fragment(s) · ${pending} en attente`,
    resumeAt: (d) => `reprendre au ${d}`,
    catchingUp: "rattrapage possible",
  },
  "ja-JP": {
    asOf: "特徴量の基準日",
    tradingLag: (n) => `${n} 営業日遅れ`,
    calendarLag: (n) => `${n} 日遅れ`,
    upToDate: "最新",
    lastRun: "前回の準備",
    never: "記録なし",
    bj: "（北京時間）",
    shards: (done, pending) => `検証済み ${done} · 残り ${pending}`,
    resumeAt: (d) => `${d} から再開`,
    catchingUp: "追従可能",
  },
};

/** Format an ISO timestamp as "YYYY-MM-DD HH:mm" in Asia/Shanghai; "" when unusable.
 * @param {unknown} value */
export function formatBeijingTime(value) {
  const parsed = new Date(String(value || ""));
  if (!value || Number.isNaN(parsed.getTime())) return "";
  const shifted = new Date(parsed.getTime() + 8 * 3600 * 1000);
  return shifted.toISOString().slice(0, 16).replace("T", " ");
}

/**
 * Freshness line for the feature store; "" when the backend lacks the fields.
 * @param {Record<string, unknown>} store
 * @param {string} [lang]
 */
export function formatFeatureFreshness(store, lang) {
  const labels = FRESHNESS_COPY[String(lang || "")] || FRESHNESS_COPY["zh-CN"];
  const asOf = /^\d{4}-\d{2}-\d{2}/.test(String(store?.max_feature_date || ""))
    ? String(store.max_feature_date).slice(0, 10)
    : "";
  if (!asOf) return "";
  const parts = [`${labels.asOf} ${asOf}`];
  const lag =
    store.lag_days === null || store.lag_days === undefined
      ? NaN
      : Number(store.lag_days);
  if (Number.isInteger(lag) && lag >= 0)
    parts.push(
      lag === 0
        ? labels.upToDate
        : store.lag_unit === "trading_days"
          ? labels.tradingLag(lag)
          : labels.calendarLag(lag),
    );
  const last = formatBeijingTime(store.last_materialized_at);
  parts.push(
    last
      ? `${labels.lastRun} ${last}${labels.bj}`
      : `${labels.lastRun} ${labels.never}`,
  );
  const warmup = isRecord(store.warmup) ? store.warmup : null;
  if (warmup) {
    const pending = Array.isArray(warmup.pending_months)
      ? warmup.pending_months.length
      : 0;
    const done = Number(warmup.completed_shards) || 0;
    if (done || pending) parts.push(labels.shards(done, pending));
    if (pending && warmup.resume_from)
      parts.push(labels.resumeAt(String(warmup.resume_from)));
    else if (pending) parts.push(labels.catchingUp);
  }
  return parts.join(" · ");
}

/** @param {unknown} value @returns {value is Record<string, unknown>} */
function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** Chinese / English hints for stable /api/features/materialize error codes.
 * @type {Record<string, Record<string, string>>} */
const MATERIALIZE_ERROR_COPY = {
  "zh-CN": {
    invalid_feature_request: "预热请求参数无效，请检查开始/结束日期",
    range_too_long: "预热区间超过 5 年上限，请缩短日期跨度",
    universe_too_large: "PIT 股票池超过安全上限，请缩短区间",
    tushare_token_missing: "服务端未配置 TUSHARE_TOKEN，请配置后重启服务",
    quota: "Tushare 额度/频率已达上限或接口权限不足，请稍后再试",
    tushare_timeout: "连接 Tushare 超时，请检查网络后重试",
    tushare_network: "无法连接 Tushare（网络或代理异常）",
    incomplete_universe: "部分成分股未返回行情，已停止以免生成残缺特征",
    data_source_not_live: "本次行情并非实时 Tushare 拉取，已按 PIT 规则拒绝",
    data_stale: "行情落后于最新交易日，已拒绝写入",
    provider_empty: "Tushare 未返回可用数据",
    provider_shape: "Tushare 返回数据结构异常",
    feature_data_unavailable: "特征数据暂不可用",
    unknown: "未预期错误，请查看服务日志",
  },
  "en-US": {
    invalid_feature_request: "Invalid warm-up request; check the dates",
    range_too_long: "Range exceeds the 5-year limit",
    universe_too_large: "PIT universe exceeds the safety limit",
    tushare_token_missing: "TUSHARE_TOKEN is not configured on the server",
    quota: "Tushare quota/rate limit reached or permission missing",
    tushare_timeout: "Tushare request timed out",
    tushare_network: "Cannot reach Tushare (network/proxy)",
    incomplete_universe: "Some constituents returned no bars; stopped",
    data_source_not_live: "Bars were not a live Tushare fetch; rejected",
    data_stale: "Bars lag the latest trading day; rejected",
    provider_empty: "Tushare returned no usable data",
    provider_shape: "Unexpected Tushare data shape",
    feature_data_unavailable: "Feature data unavailable",
    unknown: "Unexpected error; see service logs",
  },
};

/**
 * Human-readable warm-up failure: code hint plus the original server error.
 * @param {Record<string, unknown> | null | undefined} payload
 * @param {number} status
 * @param {string} [lang]
 */
export function describeMaterializeError(payload, status, lang) {
  const copy =
    MATERIALIZE_ERROR_COPY[String(lang || "")] ||
    (String(lang || "").startsWith("zh") || !lang
      ? MATERIALIZE_ERROR_COPY["zh-CN"]
      : MATERIALIZE_ERROR_COPY["en-US"]);
  const code = typeof payload?.code === "string" ? payload.code : "";
  const raw = String(payload?.detail || payload?.error || `HTTP ${status}`);
  const hint = code ? copy[code] || copy.unknown : "";
  if (!hint) return raw;
  const zh = copy === MATERIALIZE_ERROR_COPY["zh-CN"];
  return zh
    ? `${hint}（${code}）。原始错误：${raw}`
    : `${hint} (${code}). Original error: ${raw}`;
}

/** Poll an async materialize job until terminal.
 * @param {string} jobId
 * @param {{fetchImpl?: typeof fetch, onProgress?: (job: Record<string, any>) => void, signal?: AbortSignal, intervalMs?: number}} [opts]
 */
export async function monitorFeatureJob(jobId, opts = {}) {
  const fetchImpl = opts.fetchImpl || fetch;
  const onProgress = opts.onProgress;
  const signal = opts.signal;
  const intervalMs = opts.intervalMs ?? 1200;
  const id = encodeURIComponent(jobId);
  while (true) {
    if (signal?.aborted) throw new Error("aborted");
    const response = await fetchImpl(`/api/features/jobs/${id}`, {
      cache: "no-store",
      signal: signal || AbortSignal.timeout(15000),
    });
    /** @type {Record<string, any>} */
    let job = {};
    try {
      job = await response.json();
    } catch (_e) {
      job = {};
    }
    if (!response.ok || !job.ok)
      throw new Error(
        describeMaterializeError(
          job,
          response.status,
          typeof document !== "undefined"
            ? document.documentElement.lang
            : "zh-CN",
        ),
      );
    onProgress?.(job);
    if (job.status === "completed") return job;
    if (job.status === "cancelled") {
      const err = new Error(job.error || "cancelled");
      /** @type {any} */ (err).code = "cancelled";
      throw err;
    }
    if (job.status === "failed") {
      const err = new Error(
        describeMaterializeError(
          job,
          502,
          typeof document !== "undefined"
            ? document.documentElement.lang
            : "zh-CN",
        ),
      );
      /** @type {any} */ (err).code = job.code || "unknown";
      throw err;
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

/** @param {{startDate?: string, endDate?: string}} range */
export function buildPITMaterializePayload(range = {}) {
  return {
    universe_mode: "pit_index",
    universe_index_code: "000300.SH",
    start_date: String(range.startDate || "2024-01-02"),
    end_date: String(range.endDate || ""),
    adjust: "",
    catch_up: true,
    resume: true,
  };
}

function boot() {
  const mount =
    document.getElementById("featureStoreMount") ||
    document.querySelector(".sensitivity-result");
  if (!mount || document.getElementById("featureStorePanel")) return;
  const text = () => COPY[document.documentElement.lang] || COPY["zh-CN"];
  const panel = document.createElement("section");
  panel.id = "featureStorePanel";
  panel.className = "feature-store-panel";
  panel.setAttribute("aria-labelledby", "featureStoreTitle");
  const heading = document.createElement("div");
  heading.className = "feature-store-panel__head";
  const title = document.createElement("h3");
  title.id = "featureStoreTitle";
  const state = document.createElement("span");
  state.className = "feature-store-panel__state";
  heading.append(title, state);
  const freshness = document.createElement("p");
  freshness.className = "feature-store-panel__freshness";
  freshness.hidden = true;
  const description = document.createElement("p");
  description.className = "feature-store-panel__description";
  const footer = document.createElement("div");
  footer.className = "feature-store-panel__footer";
  const button = document.createElement("button");
  button.className = "feature-store-panel__button";
  button.type = "button";
  button.disabled = true;
  const message = document.createElement("span");
  message.className = "feature-store-panel__message";
  message.setAttribute("role", "status");
  message.setAttribute("aria-live", "polite");
  const cancelButton = document.createElement("button");
  cancelButton.type = "button";
  cancelButton.className = "feature-store-panel__cancel";
  cancelButton.hidden = true;
  cancelButton.style.cssText = "margin-left:.5rem";
  footer.append(button, cancelButton, message);
  // Progress is shown on the shared #backtestProgress host (kind=feature_warmup).
  panel.append(heading, freshness, description, footer);
  let activeJobId = "";
  if (mount.id === "featureStoreMount") mount.append(panel);
  else mount.insertAdjacentElement("afterend", panel);
  const refreshExtensions = mountDataExtensionPanel(
    mount.closest(".backtest-layout")?.parentElement ||
      document.getElementById("backtest") ||
      panel,
  );

  function renderCopy() {
    const labels = text();
    title.textContent = labels.title;
    description.textContent = labels.description;
    button.textContent = labels.action;
    cancelButton.textContent = labels.cancel;
  }

  /** @param {Record<string, unknown>} job */
  function showProgress(job) {
    const labels = text();
    const pct = Math.max(0, Math.min(100, Number(job.progress) || 0));
    const detail = [
      job.symbol_count != null
        ? `${Number(job.processed_symbols) || 0}/${job.symbol_count}`
        : "",
      job.current_symbol || "",
    ]
      .filter(Boolean)
      .join(" ");
    const progressText = labels.progress(
      String(job.stage || "running"),
      pct,
      detail,
    );
    const jobMessage = typeof job.message === "string" ? job.message : "";
    setExperimentProgress({
      kind: "feature_warmup",
      message: jobMessage || progressText,
      value: pct,
      meta: detail || progressText,
    });
    message.dataset.state = "loading";
    message.textContent = jobMessage || progressText;
    cancelButton.hidden = !["queued", "running", "cancelling"].includes(
      String(job.status || ""),
    );
    cancelButton.disabled = job.status === "cancelling";
  }

  function hideProgress() {
    clearExperimentProgress({ onlyIfKind: "feature_warmup" });
    cancelButton.hidden = true;
    cancelButton.disabled = false;
    activeJobId = "";
  }

  async function refreshStatus() {
    try {
      const response = await fetch("/api/features/status", {
        cache: "no-store",
      });
      const payload = await response.json();
      if (response.status === 404) throw new Error("feature_api_not_found");
      if (!response.ok || !payload.ok)
        throw new Error(payload.error || `HTTP ${response.status}`);
      await refreshExtensions();
      const store = payload.feature_store || {};
      const labels = text();
      button.disabled = false;
      message.dataset.state = "ready";
      message.textContent = "";
      state.textContent = store.rows
        ? labels.ready(
            Number(store.rows),
            Number(store.symbols),
            Number(store.data_versions),
          )
        : labels.empty;
      const freshnessText = store.rows
        ? formatFeatureFreshness(store, document.documentElement.lang)
        : "";
      freshness.textContent = freshnessText;
      freshness.hidden = !freshnessText;
    } catch (_Caught) {
      const error = toError(_Caught);
      const labels = text();
      state.textContent =
        error.message === "feature_api_not_found"
          ? labels.backendOld
          : labels.failed(error.message);
      message.dataset.state = "error";
      message.textContent = state.textContent;
      freshness.textContent = "";
      freshness.hidden = true;
      button.disabled = true;
      return false;
    }
    return true;
  }

  cancelButton.addEventListener("click", async () => {
    if (!activeJobId || cancelButton.disabled) return;
    cancelButton.disabled = true;
    const labels = text();
    message.textContent = labels.cancelling;
    try {
      await fetch(
        `/api/features/jobs/${encodeURIComponent(activeJobId)}/cancel`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        },
      );
    } catch (_Caught) {
      cancelButton.disabled = false;
    }
  });

  button.addEventListener("click", async () => {
    const labels = text();
    if (button.disabled || !window.confirm(labels.confirm)) return;
    const startDate =
      /** @type {HTMLInputElement | null} */ (requiredElement("backtestStart"))
        ?.value || "2024-01-02";
    const endDate =
      /** @type {HTMLInputElement | null} */ (requiredElement("backtestEnd"))
        ?.value || "";
    button.disabled = true;
    message.dataset.state = "loading";
    message.textContent = labels.loading;
    try {
      const response = await fetch("/api/features/materialize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          buildPITMaterializePayload({ startDate, endDate }),
        ),
      });
      /** @type {Record<string, any>} */
      let payload = {};
      try {
        payload = await response.json();
      } catch (_parse) {
        payload = {};
      }
      if (!response.ok || !payload.ok)
        throw new Error(
          describeMaterializeError(
            payload,
            response.status,
            document.documentElement.lang,
          ),
        );
      if (response.status === 202 || payload.async) {
        activeJobId = String(payload.job_id || "");
        const job = await monitorFeatureJob(activeJobId, {
          fetchImpl: fetch,
          onProgress: showProgress,
        });
        hideProgress();
        const result = job.result || {};
        const stored = result.feature_store || {};
        message.dataset.state = "success";
        message.textContent = labels.done(
          Number(result.materialization?.written || 0),
          Number(stored.rows || 0),
        );
      } else {
        hideProgress();
        const stored = payload.feature_store || {};
        message.dataset.state = "success";
        message.textContent = labels.done(
          Number(payload.materialization?.written || 0),
          Number(stored.rows || 0),
        );
      }
      const statusOk = await refreshStatus();
      if (statusOk === false) button.disabled = true;
    } catch (errorCaught) {
      hideProgress();
      const error = toError(errorCaught);
      message.dataset.state = "error";
      message.textContent = labels.failed(
        error instanceof Error ? error.message : String(error),
      );
      button.disabled = false;
    } finally {
      if (
        message.dataset.state === "success" ||
        message.dataset.state === "ready"
      )
        button.disabled = false;
    }
  });
  new MutationObserver((changes) => {
    renderCopy();
    if (changes.some((change) => change.attributeName === "data-username")) {
      if (document.documentElement.dataset.username) void refreshStatus();
      else button.disabled = true;
    }
  }).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["lang", "data-username"],
  });
  renderCopy();
  // Imported modules initialize before session restoration. Do not treat
  // their intentional fail-closed 401 as a missing backend capability.
  if (document.documentElement.dataset.username) void refreshStatus();
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  else boot();
}
