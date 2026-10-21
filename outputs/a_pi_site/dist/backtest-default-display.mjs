import { apiFetch } from "./api-client.mjs";

/** @typedef {{job_id?: string, strategy?: string, strategy_name?: string, strategy_version?: string, backtest_date?: string, result?: import('../types/domain').BacktestResult}} SavedDisplay */
/** @param {import('../types/services').BacktestResultsServices} deps */
export function createDefaultBacktestDisplay(deps) {
  /** @type {SavedDisplay | null} */
  let saved = null;
  /** @type {import('../types/domain').BacktestResult | null} */
  let candidate = null;
  let loading = false;
  let loaded = false;
  let saving = false;
  let readFailed = false;
  const doc = document;
  const t = (/** @type {string} */ text) => deps.localeText(text);
  const panel = doc.createElement("div");
  panel.className = "backtest-default-display";
  panel.setAttribute("data-slot", "backtest-default-display");
  const current = doc.createElement("p");
  current.id = "defaultBacktestIdentity";
  const identity = doc.createElement("p");
  identity.id = "backtestResultIdentity";
  const button = doc.createElement("button");
  button.type = "button";
  button.className = "secondary-button";
  button.textContent = t("设为默认展示");
  button.disabled = true;
  button.setAttribute("aria-describedby", "defaultBacktestNotice");
  const notice = doc.createElement("p");
  notice.id = "defaultBacktestNotice";
  notice.setAttribute("role", "status");
  notice.setAttribute("aria-live", "polite");
  notice.textContent = t(
    "回测成功后可手动设为默认；仅改变展示，不改变交易或验收配置。",
  );
  panel.append(current, identity, button, notice);
  doc.getElementById("backtestStatus")?.closest(".page-heading")?.after(panel);
  const overview = doc.createElement("p");
  overview.className = "backtest-default-display";
  overview.id = "portfolioStrategyIdentity";
  doc
    .getElementById("portfolioEquity")
    ?.closest(".metric-grid")
    ?.after(overview);
  function dateText(/** @type {string | undefined} */ value) {
    const date = new Date(value || "");
    return Number.isNaN(date.getTime())
      ? t("未记录")
      : new Intl.DateTimeFormat("zh-CN", {
          timeZone: "Asia/Shanghai",
          dateStyle: "short",
          timeStyle: "short",
        }).format(date);
  }
  function render() {
    current.textContent =
      readFailed && !loaded
        ? t("默认展示状态未能读取；当前结果保留，策略身份尚待核对")
        : saved
          ? `${t("默认展示")}：${saved.strategy_name} · ${t("版本")} ${saved.strategy_version} · ${t("回测日期")} ${dateText(saved.backtest_date)} · ${t("研究结果，非正式验收")}`
          : t(
              "默认展示：系统基准 baseline-v2 · 回测日期未记录（系统快照）· 非正式验收",
            );
    overview.textContent = current.textContent;
    identity.textContent = candidate
      ? `${t("本次成功结果")}：${candidate.strategy_label || candidate.strategy || t("未记录")} · ${t("版本")} ${candidate.strategy_version || t("未记录（旧产物）")} · ${t("回测日期")} ${dateText(candidate.completed_at)}`
      : t("尚未选择成功的回测结果");
    button.disabled =
      loading ||
      saving ||
      !candidate?.job_id ||
      candidate.job_id === saved?.job_id;
    button.textContent = t(
      saving
        ? "正在保存…"
        : candidate?.job_id && candidate.job_id === saved?.job_id
          ? "已设为默认展示"
          : "设为默认展示",
    );
  }
  function applySaved() {
    if (saved?.result) deps.applyPortfolioSnapshot(saved.result);
    render();
  }
  async function load() {
    if (loading || loaded) return;
    loading = true;
    render();
    try {
      const response = await apiFetch("/api/backtest/default-display", {
        cache: "no-store",
        signal: AbortSignal.timeout(15000),
      });
      const data = await response.json();
      if (!response.ok || !data.ok)
        throw new Error(data.error || `HTTP ${response.status}`);
      saved = data.display;
      loaded = true;
      readFailed = false;
      applySaved();
    } catch {
      readFailed = true;
      notice.textContent = t(
        "默认展示读取失败；保留原结果，将在下次工作区刷新时重试。",
      );
    } finally {
      loading = false;
      render();
    }
  }
  button.addEventListener("click", async () => {
    if (!candidate?.job_id || saving || loading) return;
    const jobId = candidate.job_id;
    saving = true;
    render();
    try {
      const response = await apiFetch("/api/backtest/default-display", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jobId }),
        signal: AbortSignal.timeout(15000),
      });
      const data = await response.json();
      if (!response.ok || !data.ok || !data.display?.result)
        throw new Error(data.error || `HTTP ${response.status}`);
      saved = data.display;
      loaded = true;
      applySaved();
      notice.textContent = t(
        "默认展示已保存；纸面交易、研究冻结配置和验收状态未改变。",
      );
    } catch (error) {
      notice.textContent = `${t("设置失败，原默认展示未改变")}：${error instanceof Error ? error.message : String(error)}`;
    } finally {
      saving = false;
      render();
    }
  });
  render();
  return {
    showCandidate(
      /** @type {import('../types/domain').BacktestResult} */ result,
    ) {
      candidate = result;
      render();
    },
    applyWorkspace() {
      applySaved();
      void load();
    },
  };
}

/** @type {ReturnType<typeof createDefaultBacktestDisplay> | null} */
export let defaultBacktestDisplay = null;
/** @param {import('../types/services').BacktestResultsServices} deps */
export function initializeDefaultBacktestDisplay(deps) {
  defaultBacktestDisplay = createDefaultBacktestDisplay(deps);
  return defaultBacktestDisplay;
}
