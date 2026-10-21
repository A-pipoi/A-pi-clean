import { setSafeHtml } from "./safe-html.mjs";
import { apiFetch as fetch } from "./api-client.mjs";
import { requiredElement, toError } from "./runtime-utils.mjs";
import { APiFinancialStatements } from "./financial-statements-panel.mjs";
import { APiGovernanceActions } from "./research-governance-actions.mjs";
import { APiStockPicker } from "./stock-picker.mjs";
/** runtime config with explicit services and live state accessors. */
/** @param {import('../types/services').RuntimeConfigServices} deps */
export function createRuntimeConfig(deps) {
  function renderRuntimeConfig() {
    const seed = Array.isArray(deps.runtimeConfig.seed_universe)
      ? deps.runtimeConfig.seed_universe
      : [];
    deps.runtimeSeedSymbols = seed
      .map((item) => item.symbol || item.ts_code || "")
      .filter(Boolean);
    const dataSourceSelect = requiredElement("dataSourceSelect");
    if (dataSourceSelect) {
      const previous = dataSourceSelect.value;
      setSafeHtml(
        dataSourceSelect,
        (deps.runtimeConfig.data_sources || [])
          .map(
            (item) =>
              `<option value="${deps.escapeHtml(item.key)}">${deps.escapeHtml(item.label)}</option>`,
          )
          .join("") || '<option value="">Tushare 配置不可用</option>',
      );
      dataSourceSelect.value = (deps.runtimeConfig.data_sources || []).some(
        (item) => item.key === previous,
      )
        ? previous
        : dataSourceSelect.options[0]?.value || "";
    }
    const statementSelect = requiredElement("statementSymbolSelect");
    if (statementSelect) {
      const previous = statementSelect.value;
      setSafeHtml(
        statementSelect,
        seed
          .map(
            (item) =>
              `<option value="${deps.escapeHtml(item.ts_code)}">${deps.escapeHtml(item.name || item.symbol)} · ${deps.escapeHtml(item.symbol)}</option>`,
          )
          .join("") || '<option value="">Tushare 股票池不可用</option>',
      );
      statementSelect.value = seed.some((item) => item.ts_code === previous)
        ? previous
        : statementSelect.options[0]?.value || "";
      const refreshButton = requiredElement("refreshStatementsButton");
      if (refreshButton) refreshButton.disabled = !statementSelect.value;
    }
    const indexSelect = requiredElement("indexUniverseSelect");
    if (indexSelect) {
      const previous = indexSelect.value;
      setSafeHtml(
        indexSelect,
        (deps.runtimeConfig.index_universes || [])
          .map(
            (item) =>
              `<option value="${deps.escapeHtml(item.ts_code)}">${deps.escapeHtml(item.name)} · ${deps.escapeHtml(item.ts_code)}</option>`,
          )
          .join("") || '<option value="">Tushare 指数不可用</option>',
      );
      indexSelect.value = (deps.runtimeConfig.index_universes || []).some(
        (item) => item.ts_code === previous,
      )
        ? previous
        : indexSelect.options[0]?.value || "";
    }
    const hidden = requiredElement("symbolInput");
    if (hidden) APiStockPicker?.setDefaultStocks(seed, deps.runtimeSeedSymbols);
    const note = requiredElement("universeConfigNote");
    if (note)
      note.textContent = `${deps.runtimeConfig.universe_note || "默认股票池由 Tushare stock_basic 返回"}${deps.runtimeConfig.stale ? " · 当前配置来自缓存" : ""} 可搜索全市场股票或加载历史指数成分股，回测池最多 ${deps.MAX_RESEARCH_SYMBOLS} 只；大股票池实时回测耗时较长。`;
    deps.applySignalSnapshot(
      deps.latestWorkspaceSnapshot?.signals || [],
      deps.latestPaperState || deps.latestWorkspaceSnapshot?.paper,
    );
    deps.applyLocale(deps.activeLocale);
  }

  async function loadRuntimeConfig() {
    try {
      const response = await fetch("/api/config");
      const data = await response.json();
      if (!response.ok || !data.ok)
        throw new Error(data.error || `HTTP ${response.status}`);
      deps.runtimeConfig = data;
      deps.MAX_RESEARCH_SYMBOLS =
        Number(data.max_research_symbols) || deps.MAX_RESEARCH_SYMBOLS;
      renderRuntimeConfig();
      if (requiredElement("statementSymbolSelect")?.value)
        void APiFinancialStatements?.load(false);
      return true;
    } catch (errorCaught) {
      const error = toError(errorCaught);
      deps.runtimeConfig = {
        seed_universe: [],
        index_universes: [],
        data_sources: [],
        stale: true,
      };
      renderRuntimeConfig();
      deps.showToast(`Tushare 动态配置加载失败 · ${error.message}`);
      return false;
    }
  }

  /** @type {import('../types/services').SharedServices['initBacktestUniverseSelector']} */
  function initBacktestUniverseSelector() {
    const symbolInput = requiredElement("symbolInput");
    const panel = symbolInput?.closest(".config-panel");
    const symbolLabel = symbolInput?.closest("label");
    if (
      !panel ||
      !symbolLabel ||
      document.getElementById("backtestUniverseMode")
    )
      return;

    const modeLabel = document.createElement("label");
    modeLabel.className = "backtest-universe-control";
    modeLabel.append(document.createTextNode("股票池口径"));
    const wrap = document.createElement("span");
    wrap.className = "select-wrap";
    const select = document.createElement("select");
    select.id = "backtestUniverseMode";
    select.setAttribute("aria-label", "回测股票池口径");
    setSafeHtml(
      select,
      '<option value="pit_index">沪深300历史成分 · PIT</option><option value="fixed">固定/自选股票池 · 非 PIT</option>',
    );
    wrap.append(select);
    modeLabel.append(wrap);
    const note = document.createElement("p");
    note.id = "backtestUniverseNote";
    note.className = "backtest-universe-note";
    symbolLabel.before(modeLabel);
    modeLabel.after(note);

    const fixedControls = [
      symbolLabel,
      requiredElement("allUniverseButton"),
      document.querySelector(".index-universe-row"),
      requiredElement("indexUniverseMeta"),
    ].filter((element) => element instanceof HTMLElement);
    const sync = () => {
      const fixed = select.value === "fixed";
      fixedControls.forEach((element) => {
        element.hidden = !fixed;
      });
      note.textContent = fixed
        ? "固定/自选池不会按历史指数成员变更；用今天的成分回测过去会有前视选择偏差。"
        : "按 Tushare 沪深300历史月度快照逐日取样，要求上市至少60日；不会用当前成分回填历史。";
    };
    select.addEventListener("change", sync);
    sync();
  }

  /** @type {import('../types/services').SharedServices['syncResearchBudgetControls']} */
  function syncResearchBudgetControls() {
    const state = deps.researchBudgetState;
    const exhausted = state && Number(state.remaining) <= 0;
    const runButton = requiredElement("configRunButton");
    const overviewRunButton = requiredElement("runButton");
    const sensitivityButton = requiredElement("sensitivityButton");
    if (runButton && runButton.dataset.running !== "true")
      runButton.disabled = Boolean(exhausted);
    if (overviewRunButton && overviewRunButton.dataset.running !== "true")
      overviewRunButton.disabled = Boolean(exhausted);
    if (sensitivityButton && sensitivityButton.dataset.running !== "true")
      sensitivityButton.disabled = Boolean(exhausted);
    let note = document.getElementById("researchBudgetBlockedNote");
    if (!exhausted) {
      note?.remove();
      return;
    }
    const host = runButton?.parentElement;
    if (!host) return;
    if (!note) {
      note = document.createElement("div");
      note.id = "researchBudgetBlockedNote";
      note.className = "research-budget-blocked";
      note.setAttribute("role", "status");
      runButton.after(note);
    }
    note.replaceChildren();
    const copy = document.createElement("span");
    copy.textContent = `本研究周期预算已用尽（${state.used}/${state.budget}）。回测与敏感性扫描已暂停；归档后才会开启新周期，历史记录会保留。`;
    const archive = document.createElement("button");
    archive.type = "button";
    archive.className = "secondary-button";
    archive.textContent = "查看归档与新周期";
    archive.addEventListener("click", () =>
      APiGovernanceActions?.openArchive(),
    );
    note.append(copy, archive);
  }
  return {
    renderRuntimeConfig,
    loadRuntimeConfig,
    initBacktestUniverseSelector,
    syncResearchBudgetControls,
  };
}
