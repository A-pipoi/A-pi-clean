/** @type {{topN(): number; buildBacktestPreset: typeof buildBacktestPreset} | null} */
export let APiResearchControls = null;
/** @param {unknown} value */
export function validateInitialCash(value) {
  const cash = Number(value);
  if (!Number.isFinite(cash) || cash <= 0 || cash > 100000000)
    throw new Error("初始资金必须大于 0 且不超过 1 亿元");
  return cash;
}
const MIN_RESEARCH_TOP_N = 1;
const MAX_RESEARCH_TOP_N = 10;
const DEFAULT_RESEARCH_TOP_N = 5;

/** @param {unknown} value @param {number} [fallback] */
export function normalizeResearchTopN(
  value,
  fallback = DEFAULT_RESEARCH_TOP_N,
) {
  const parsed = Number(value);
  if (
    Number.isInteger(parsed) &&
    parsed >= MIN_RESEARCH_TOP_N &&
    parsed <= MAX_RESEARCH_TOP_N
  ) {
    return parsed;
  }
  return fallback;
}

/**
 * @typedef {import('../types/domain').StrategyInfo} StrategyPreset
 * @typedef {{ doc?: Document, strategyCatalog?: StrategyPreset[], seedSymbols?: string[], researchOnly?: boolean, researchTopN?: unknown }} BacktestOptions
 */
/** @param {BacktestOptions} [options] */
export function buildBacktestPreset({
  doc = globalThis.document,
  strategyCatalog = [],
  seedSymbols = [],
  researchOnly = true,
  researchTopN,
} = {}) {
  /** @param {string} id @param {string} [fallback] */
  const read =
    /** @param {string} id @param {string} [fallback] */
    (id, fallback = "") =>
      /** @type {HTMLInputElement | HTMLSelectElement | null} */ (
        doc?.getElementById(id)
      )?.value || fallback;
  const strategy = read("strategySelect", "baseline");
  /** @type {Record<string,{lookback:number,top_n:number}>} */
  const presets = {
    baseline: { lookback: 20, top_n: 5 },
    baseline_price_audited: { lookback: 20, top_n: 5 },
    baseline_full_audited: { lookback: 20, top_n: 5 },
    baseline_cost_aware: { lookback: 20, top_n: 5 },
    momentum: { lookback: 12, top_n: 5 },
    defensive: { lookback: 30, top_n: 5 },
    rulebook: { lookback: 20, top_n: 5 },
    multi_signal: { lookback: 20, top_n: 5 },
    inverse_volatility: { lookback: 20, top_n: 5 },
    volatility_targeted: { lookback: 20, top_n: 5 },
    linear_ml: { lookback: 20, top_n: 5 },
    lightgbm: { lookback: 60, top_n: 5 },
  };
  const registered = strategyCatalog.find((item) => item.key === strategy);
  const strategyParams = registered
    ? {
        lookback: registered.lookback ?? 20,
        top_n: registered.top_n ?? DEFAULT_RESEARCH_TOP_N,
      }
    : presets[strategy] || presets.baseline;
  const symbolText = read("symbolInput", seedSymbols.join(","));
  const symbols = [
    ...new Set(
      String(symbolText)
        .split(/[,，\s]+/)
        .map((symbol) => symbol.trim())
        .filter(Boolean),
    ),
  ];
  const universeMode = read("backtestUniverseMode", "pit_index");
  /** @type {import('../types/services').ResearchPreset} */
  const payload = {
    strategy,
    data_source: read("dataSourceSelect", "tushare"),
    universe_mode: universeMode,
    start_date: read("backtestStart", "2024-01-02"),
    end_date: read("backtestEnd"),
    adjust: "",
    rebalance: read("rebalanceSelect", "weekly"),
    cost_profile: read("costSelect", "standard"),
    initial_cash: validateInitialCash(
      /** @type {HTMLInputElement | null} */ (
        doc?.getElementById("backtestInitialCash")
      )?.value ?? "1000000",
    ),
    ...strategyParams,
    ...(researchOnly ? readResearchEligibility(doc) : {}),
  };
  // Fixed catalog strategies must keep audited lookback/top_n.
  // #backtestTopN is display-only sync for those presets and must not override.
  if (researchOnly && !registered && !(strategy in presets)) {
    payload.top_n = normalizeResearchTopN(
      researchTopN ?? read("backtestTopN", String(DEFAULT_RESEARCH_TOP_N)),
      payload.top_n,
    );
  }
  if (universeMode === "fixed" && symbols.length) payload.symbols = symbols;
  return payload;
}

/** @param {Document} doc */
export function readResearchEligibility(doc) {
  const enabled =
    /** @type {HTMLSelectElement | null} */ (
      doc?.getElementById("backtestDataQualityPolicy")
    )?.value === "enabled";
  if (!enabled) return {};
  const minimum = Number(
    /** @type {HTMLInputElement | null} */ (
      doc.getElementById("backtestMinListingSessions")
    )?.value,
  );
  if (!Number.isInteger(minimum) || minimum < 0 || minimum > 1000)
    throw new Error("上市交易日门槛必须是 0–1000 的整数");
  return {
    data_quality_policy: true,
    min_listing_sessions: minimum,
    exclude_nonstandard_audit:
      /** @type {HTMLSelectElement | null} */ (
        doc.getElementById("backtestExcludeAudit")
      )?.value === "enabled",
  };
}

/** @param {Document} doc @param {Element} anchor */
function installResearchEligibility(doc, anchor) {
  if (doc.getElementById("backtestDataQualityPolicy")) return;
  /** @param {string} zh @param {string} en */
  const t = (zh, en) =>
    globalThis.window?.localeText?.(zh) ||
    (doc.documentElement.lang.startsWith("en") ? en : zh);
  /** @param {string} id @param {string} text */
  const label = (id, text) => {
    const node = doc.createElement("label");
    node.htmlFor = id;
    node.textContent = text;
    return node;
  };
  /** @param {string} id */
  const select = (id) => {
    const node = doc.createElement("select");
    node.id = id;
    node.setAttribute("aria-describedby", "researchEligibilityNote");
    for (const [value, text] of [
      ["disabled", t("不启用", "Disabled")],
      ["enabled", t("启用", "Enabled")],
    ]) {
      const option = doc.createElement("option");
      option.value = value;
      option.textContent = text;
      node.append(option);
    }
    return node;
  };
  const policyLabel = label(
    "backtestDataQualityPolicy",
    t("研究数据规则", "Research eligibility"),
  );
  const policy = select("backtestDataQualityPolicy");
  policyLabel.append(policy);
  const ageLabel = label(
    "backtestMinListingSessions",
    t("已完成上市交易日", "Completed listing sessions"),
  );
  const age = doc.createElement("input");
  age.id = "backtestMinListingSessions";
  age.type = "number";
  age.min = "0";
  age.max = "1000";
  age.step = "1";
  age.value = "120";
  age.setAttribute("aria-describedby", "researchEligibilityNote");
  ageLabel.append(age);
  const auditLabel = label(
    "backtestExcludeAudit",
    t("排除非标审计", "Exclude nonstandard audits"),
  );
  const audit = select("backtestExcludeAudit");
  audit.value = "disabled";
  auditLabel.append(audit);
  const note = doc.createElement("small");
  note.id = "researchEligibilityNote";
  note.className = "backtest-topn-note";
  note.textContent = t(
    "仅影响回测，不改纸面策略",
    "Applies only to backtests and sensitivity research, not paper strategy. Audit filtering blocks when historical availability evidence is missing.",
  );
  const paramsHost = doc.getElementById("backtestStrategyParams") || anchor;
  if (paramsHost === anchor)
    anchor.after(policyLabel, ageLabel, auditLabel, note);
  else paramsHost.append(policyLabel, ageLabel, auditLabel, note);
  const update = () => {
    age.disabled = audit.disabled = policy.value !== "enabled";
  };
  policy.addEventListener("change", update);
  update();
}

/** Reorder strategy params into a balanced 2-col grid; helpers become titles. */
function balanceStrategyParams(doc = globalThis.document) {
  const section = doc.getElementById("backtestStrategySection");
  const params = doc.getElementById("backtestStrategyParams");
  if (!section || !params || params.dataset.balanced === "1") return;
  params.dataset.balanced = "1";
  params.classList.add("strategy-params-balanced");

  const strategyLabel = doc.getElementById("strategySelect")?.closest("label");
  const policy = doc
    .getElementById("backtestDataQualityPolicy")
    ?.closest("label");
  const age = doc
    .getElementById("backtestMinListingSessions")
    ?.closest("label");
  const audit = doc.getElementById("backtestExcludeAudit")?.closest("label");
  const cash = doc.getElementById("backtestInitialCash")?.closest("label");
  const topn = doc.getElementById("backtestTopN")?.closest("label");
  const rebalance = doc.getElementById("rebalanceSelect")?.closest("label");
  const cost = doc.getElementById("costSelect")?.closest("label");
  const note = doc.getElementById("researchEligibilityNote");
  const topnNote = params.querySelector(
    ".backtest-topn-note:not(#researchEligibilityNote)",
  );

  const tip = (note && note.textContent) || "仅影响回测，不改纸面策略";
  for (const el of [policy, age, audit, cash, topn]) {
    if (el && !el.title) el.title = tip;
  }
  if (topnNote?.textContent) {
    if (topn) topn.title = topnNote.textContent.trim();
    topnNote.remove();
  }
  if (note) {
    note.className = "strategy-params-note";
    note.hidden = true;
  }
  if (strategyLabel) strategyLabel.dataset.span = "full";

  const ordered = [strategyLabel, policy, age, audit, topn, cash, cost].filter(
    (node) => node != null,
  );

  for (const node of ordered) node.remove();
  for (const grid of [
    ...section.querySelectorAll(":scope > .config-section__grid"),
  ]) {
    if (grid === params) continue;
    if (![...grid.children].length) grid.remove();
  }
  params.replaceChildren(...ordered);
  if (note) {
    note.remove();
    params.append(note);
  }
  const rebalanceSlot = doc.getElementById("universeRebalanceSlot");
  if (rebalance && rebalanceSlot) {
    rebalance.style.gridColumn = "";
    rebalanceSlot.replaceWith(rebalance);
    rebalance.classList.add("universe-rebalance-label");
    // Hide visible label text so it matches the range control footprint.
    const select = rebalance.querySelector("select");
    if (select instanceof HTMLSelectElement) {
      if (!select.getAttribute("aria-label")) {
        select.setAttribute("aria-label", "调仓频率");
      }
      const wrap = rebalance.querySelector(".select-wrap");
      for (const node of [...rebalance.childNodes]) {
        if (node.nodeType === 3 && (node.textContent || "").trim()) {
          const hidden = doc.createElement("span");
          hidden.className = "visually-hidden";
          hidden.textContent = (node.textContent || "").trim();
          node.replaceWith(hidden);
        }
      }
      if (wrap && !rebalance.querySelector(":scope > .visually-hidden")) {
        const hidden = doc.createElement("span");
        hidden.className = "visually-hidden";
        hidden.textContent = "调仓频率";
        rebalance.insertBefore(hidden, wrap);
      }
    }
  }
}

export function installBacktestResearchControls(doc = globalThis.document) {
  if (!doc) return null;
  const strategySelect = /** @type {HTMLSelectElement | null} */ (
    doc.getElementById("strategySelect")
  );
  const strategyLabel = strategySelect?.closest("label");
  if (!strategyLabel) return null;
  installResearchEligibility(doc, strategyLabel);
  if (!doc.getElementById("backtestInitialCash")) {
    const label = doc.createElement("label");
    label.textContent = "初始资金（元）";
    label.htmlFor = "backtestInitialCash";
    const input = doc.createElement("input");
    input.id = "backtestInitialCash";
    input.type = "number";
    input.min = "0.01";
    input.max = "100000000";
    input.step = "0.01";
    input.value = "1000000";
    input.required = true;
    input.style.textAlign = "center";
    label.append(input);
    const paramsHost =
      doc.getElementById("backtestStrategyParams") || strategyLabel;
    if (paramsHost === strategyLabel) strategyLabel.after(label);
    else paramsHost.append(label);
  }

  let select = /** @type {HTMLSelectElement | null} */ (
    doc.getElementById("backtestTopN")
  );
  if (!select) {
    const translate =
      /** @param {string} text */
      (text) => globalThis.window?.localeText?.(text) || text;
    const label = doc.createElement("label");
    label.className = "backtest-topn-control";
    label.append(doc.createTextNode(translate("研究持仓数")));

    const wrap = doc.createElement("span");
    wrap.className = "select-wrap";
    select = doc.createElement("select");
    select.id = "backtestTopN";
    select.setAttribute("aria-label", translate("仅影响研究回测的持仓数量"));
    for (
      let count = MIN_RESEARCH_TOP_N;
      count <= MAX_RESEARCH_TOP_N;
      count += 1
    ) {
      const option = doc.createElement("option");
      option.value = String(count);
      option.textContent = `${count} ${translate("只")}`;
      select.append(option);
    }
    select.value = String(DEFAULT_RESEARCH_TOP_N);
    wrap.append(select);
    label.append(wrap);
    const note = doc.createElement("small");
    note.className = "backtest-topn-note";
    note.textContent = translate(
      "只影响研究回测；纸面交易继续使用既有策略参数。",
    );
    const paramsHost =
      doc.getElementById("backtestStrategyParams") || strategyLabel;
    if (paramsHost === strategyLabel) {
      strategyLabel.after(label);
      label.after(note);
    } else {
      paramsHost.append(label, note);
    }
  }

  /** @type {Record<string,{lookback:number,top_n:number}>} */
  const localPresets = {
    baseline: { lookback: 20, top_n: 5 },
    baseline_price_audited: { lookback: 20, top_n: 5 },
    baseline_full_audited: { lookback: 20, top_n: 5 },
    baseline_cost_aware: { lookback: 20, top_n: 5 },
    momentum: { lookback: 12, top_n: 5 },
    defensive: { lookback: 30, top_n: 5 },
    rulebook: { lookback: 20, top_n: 5 },
    multi_signal: { lookback: 20, top_n: 5 },
    inverse_volatility: { lookback: 20, top_n: 5 },
    volatility_targeted: { lookback: 20, top_n: 5 },
    linear_ml: { lookback: 20, top_n: 5 },
    lightgbm: { lookback: 60, top_n: 5 },
  };

  function syncTopNToStrategy() {
    if (!select || !strategySelect) return;
    const key = strategySelect.value || "baseline";
    // Prefer live catalog from window if present; else local fallbacks.
    const catalog =
      /** @type {Array<{key:string,top_n?:number,lookback?:number}> | undefined} */ (
        globalThis.window?.APiStrategyCatalog
      ) || [];
    const registered = catalog.find((item) => item.key === key);
    const preset = registered
      ? {
          lookback: registered.lookback ?? 20,
          top_n: registered.top_n ?? DEFAULT_RESEARCH_TOP_N,
        }
      : localPresets[key];
    if (preset) {
      select.value = String(normalizeResearchTopN(preset.top_n));
      select.disabled = true;
      select.title =
        globalThis.window?.localeText?.(
          "固定策略使用目录预设持仓数，不能用研究持仓数覆盖",
        ) || "Fixed strategy uses catalog top_n";
    } else {
      select.disabled = false;
      select.title = "";
    }
  }
  strategySelect?.addEventListener("change", syncTopNToStrategy);
  syncTopNToStrategy();

  const controls = Object.freeze({
    topN: () => normalizeResearchTopN(select.value),
    buildBacktestPreset: (options = {}) =>
      buildBacktestPreset({
        ...options,
        doc,
        // Still pass for unknown strategies; catalog strategies ignore override.
        researchTopN: select.value,
      }),
  });
  balanceStrategyParams(doc);
  APiResearchControls = controls;
  return controls;
}

if (typeof document !== "undefined") {
  document.addEventListener("a-pi:balance-strategy-params", () =>
    balanceStrategyParams(document),
  );
  const install = () => installBacktestResearchControls(document);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install, { once: true });
  } else {
    install();
  }
}
