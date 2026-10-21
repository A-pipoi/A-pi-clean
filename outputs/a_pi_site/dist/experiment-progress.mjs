/** Shared experiment progress host for backtest jobs and PIT feature warm-up. */

/** @typedef {"backtest" | "feature_warmup"} ExperimentProgressKind */

const KIND_LABEL = {
  backtest: { zh: "回测任务", en: "Backtest" },
  feature_warmup: { zh: "特征预热", en: "Feature warm-up" },
};

/** @param {ExperimentProgressKind | string} kind */
function kindLabel(kind) {
  const row =
    kind === "feature_warmup" ? KIND_LABEL.feature_warmup : KIND_LABEL.backtest;
  const lang = document.documentElement?.lang || "zh-CN";
  return lang.startsWith("zh") ? row.zh : row.en;
}

/**
 * @param {{
 *   kind?: ExperimentProgressKind | string,
 *   message?: string,
 *   value?: unknown,
 *   meta?: string,
 *   error?: boolean,
 * }} options
 */
export function setExperimentProgress(options = {}) {
  const host = document.getElementById("backtestProgress");
  if (!host) return;
  const kind = String(options.kind || "backtest");
  const pct = Math.max(0, Math.min(100, Number(options.value) || 0));
  host.hidden = false;
  host.dataset.kind = kind;
  host.classList.toggle("is-error", Boolean(options.error));
  const kindEl = document.getElementById("backtestProgressKind");
  if (kindEl) kindEl.textContent = kindLabel(kind);
  const message = document.getElementById("backtestProgressMessage");
  if (message) message.textContent = options.message || "";
  const value = document.getElementById("backtestProgressValue");
  if (value) value.textContent = `${pct}%`;
  const meta = document.getElementById("backtestProgressMeta");
  if (meta) {
    meta.textContent =
      options.meta ||
      (kind === "feature_warmup"
        ? "PIT 特征库 · 异步预热"
        : "Tushare 实时行情 · Python 异步任务");
  }
  const bar = document.getElementById("backtestProgressBar");
  if (bar) bar.style.width = `${pct}%`;
}

/**
 * @param {{ onlyIfKind?: ExperimentProgressKind | string }} [options]
 */
export function clearExperimentProgress(options = {}) {
  const host = document.getElementById("backtestProgress");
  if (!host) return;
  if (options.onlyIfKind && host.dataset.kind !== options.onlyIfKind) return;
  host.hidden = true;
  host.classList.remove("is-error");
  delete host.dataset.kind;
  const bar = document.getElementById("backtestProgressBar");
  if (bar) bar.style.width = "0%";
}

export function experimentProgressKind() {
  return document.getElementById("backtestProgress")?.dataset.kind || "";
}
