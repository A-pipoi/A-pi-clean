/** Presentation only: preserves original controls, ids, data attributes and values. */
const ns = "http://www.w3.org/2000/svg";
/** @param {string} path */
export function lineIcon(path) {
  const svg = document.createElementNS(ns, "svg");
  for (const [key, value] of Object.entries({
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    "stroke-width": "1.8",
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
    "aria-hidden": "true",
    focusable: "false",
  }))
    svg.setAttribute(key, value);
  const item = document.createElementNS(ns, "path");
  item.setAttribute("d", path);
  svg.append(item);
  return svg;
}
const vocabulary = {
  zh: [
    "数据",
    "区间与频率",
    "成本与执行",
    "连接详情",
    "查看因子研究",
    "前往回测实验",
    "查看纸面账户",
    "空数据不会生成模拟结果。请先检查数据连接和研究门禁。",
  ],
  en: [
    "Data",
    "Period & frequency",
    "Costs & execution",
    "Connection details",
    "View factor research",
    "Open backtest",
    "View paper account",
    "No synthetic results. Check data connectivity and research gates first.",
  ],
  fr: [
    "Données",
    "Période et fréquence",
    "Coûts et exécution",
    "Détails de connexion",
    "Recherche de facteurs",
    "Ouvrir le backtest",
    "Compte simulé",
    "Aucun résultat synthétique. Vérifiez les données et les contrôles de recherche.",
  ],
  ja: [
    "データ",
    "期間と頻度",
    "コストと執行",
    "接続の詳細",
    "因子研究を表示",
    "バックテストへ",
    "ペーパー口座を表示",
    "合成結果は表示しません。データ接続と研究ゲートを確認してください。",
  ],
};
/** @param {number} index */
function text(index) {
  const key = document.documentElement.lang.slice(0, 2);
  return (vocabulary[/** @type {keyof typeof vocabulary} */ (key)] ||
    vocabulary.zh)[index];
}
/** @param {number} index */
function actionText(index) {
  const words = {
    zh: [
      "选择股票",
      "编辑因子表达式",
      "查看回测配置",
      "查看纸面记录",
      "检查筛选与数据连接",
    ],
    en: [
      "Select a stock",
      "Edit factor expression",
      "View backtest configuration",
      "View paper records",
      "Inspect filters and connection",
    ],
    fr: [
      "Choisir une action",
      "Modifier l’expression",
      "Voir la configuration",
      "Voir les ordres simulés",
      "Vérifier les filtres et données",
    ],
    ja: [
      "銘柄を選択",
      "因子式を編集",
      "設定を確認",
      "ペーパー記録を確認",
      "フィルターと接続を確認",
    ],
  };
  return (words[
    /** @type {keyof typeof words} */ (
      document.documentElement.lang.slice(0, 2)
    )
  ] || words.zh)[index];
}
/** @param {HTMLElement} container @param {string} message @param {'research'|'backtest'|'paper'|'connection'} target */
export function decorateEmpty(container, message, target) {
  if (container.querySelector(".empty-state")) return;
  if (/^(?:正在|加载中|loading|chargement|読み込み)/i.test(message.trim()))
    return;
  const box = document.createElement("div");
  box.className = "empty-state";
  const art = document.createElement("span");
  art.className = "empty-state-art";
  art.append(
    lineIcon(
      "M4 7c0-5 16-5 16 0v10c0 5-16 5-16 0V7Zm0 0c0 5 16 5 16 0M4 12c0 5 16 5 16 0",
    ),
  );
  const title = document.createElement("strong");
  title.textContent = message;
  const description = document.createElement("p");
  const selecting = /选择|select|sélection|選択/i.test(message);
  const statement = container.classList.contains("statement-empty");
  const mining = container.classList.contains("factor-mining-empty");
  const focusId =
    statement && selecting
      ? "statementSymbolSelect"
      : mining
        ? "factorExpressionInput"
        : target === "backtest"
          ? "configRunButton"
          : null;
  description.textContent = focusId || target === "paper" ? "" : text(7);
  const action = document.createElement("button");
  action.type = "button";
  action.className = "secondary-button";
  action.textContent = text(
    target === "research"
      ? 4
      : target === "backtest"
        ? 5
        : target === "paper"
          ? 6
          : 3,
  );
  if (focusId || target === "paper" || target === "research")
    action.textContent = actionText(
      statement && selecting
        ? 0
        : mining
          ? 1
          : target === "backtest"
            ? 2
            : target === "paper"
              ? 3
              : 4,
    );
  action.addEventListener("click", () => {
    if (focusId) {
      if (target === "backtest")
        document
          .querySelector('.nav-item[data-section="backtest"]')
          ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      const control = document.getElementById(focusId);
      control?.scrollIntoView?.({ block: "center", behavior: "instant" });
      control?.focus();
    } else if (target === "paper") {
      document
        .querySelector('.nav-item[data-section="paper"]')
        ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      document.getElementById("paperRefreshButton")?.focus();
    } else if (target === "research") {
      document
        .querySelector('[data-factor-filter="all"]')
        ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      document.getElementById("trackerDetailsButton")?.click();
    } else if (target === "connection")
      document.getElementById("trackerDetailsButton")?.click();
    else
      document
        .querySelector(
          /** @type {string} */ (`.nav-item[data-section="${target}"]`),
        )
        ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  box.append(art, title, description, action);
  container.replaceChildren(box);
}
export function installWorkbenchPolish() {
  queueMicrotask(() => ensureControlLabels());
  const navPaths = {
    research: "M3 12h4l3-6 4 12 3-6h4",
    backtest: "M4 3v17h17M7 15l4-5 4 3 5-7",
    risk: "m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z",
    paper: "M4 7h16v14H4V7Zm4 0V3h8v4M4 12h16M10 12v3h4v-3",
  };
  for (const [section, path] of Object.entries(navPaths))
    document
      .querySelector(`.nav-item[data-section="${section}"] .nav-icon`)
      ?.replaceChildren(lineIcon(path));
  const paths = [
    "M3 6h17v14H3V6Zm0 0V3h14v3M15 12h5v4h-5v-4Z",
    "m3 17 6-6 4 4 8-10M15 5h6v6",
    "M4 5l15 15M11 20h8v-8",
    "M3 8h17l-4-4M21 16H4l4 4",
  ];
  document.querySelectorAll(".metric-card").forEach((card, index) => {
    const chip = document.createElement("span");
    chip.className = "metric-icon-chip";
    chip.append(lineIcon(paths[index] || paths[0]));
    card.prepend(chip);
  });
  const panel = document.querySelector(".config-panel");
  if (panel) {
    // Prefer the sectioned experiment IA (①②③). Empty fieldsets would become
    // stray grid cells and leave a skinny left strip beside blank columns.
    const sectioned = Boolean(panel.querySelector(".config-section"));
    const groups = [
      [
        "dataSourceSelect",
        "strategySelect",
        "symbolInput",
        "allUniverseButton",
        "indexUniverseSelect",
        "indexUniverseMeta",
      ],
      ["backtestStart", "rebalanceSelect"],
      [
        "costSelect",
        "configRunButton",
        "sensitivityButton",
        "backtestProgress",
        "sensitivityResult",
      ],
    ];
    if (!sectioned)
      groups.forEach((ids, index) => {
        const group = document.createElement("fieldset");
        group.className = "config-group";
        const legend = document.createElement("legend");
        legend.textContent = text(index);
        group.append(legend);
        for (const id of ids) {
          let item = document.getElementById(id);
          while (item?.parentElement && item.parentElement !== panel)
            item = item.parentElement;
          if (item && item.parentElement === panel) group.append(item);
        }
        if (group.childElementCount > 1) panel.append(group);
      });
    const guide = document.createElement("details");
    guide.className = "research-workflow-guide";
    const summary = document.createElement("summary");
    summary.textContent = "研究流程与策略入口";
    const workflow = document.createElement("p");
    workflow.textContent =
      "先检查数据链路，再配置研究实验；预算用完时查看台账与归档说明。归档不会恢复已曝光密封样本的未知性。";
    const strategies = document.createElement("p");
    strategies.textContent =
      "预设策略仅是起点；复杂规则可在策略编辑器中配置，模型策略以依赖与验收状态为准，不代表自动具备盈利能力。";
    guide.append(summary, workflow, strategies);
    // Sit in「② 策略与参数」标题行右侧；勿作为 config-panel 独立栅格单元。
    const strategySection = document.getElementById("backtestStrategySection");
    const head =
      strategySection?.querySelector(".config-section__head") || null;
    if (head) {
      if (!head.contains(guide)) head.append(guide);
    } else {
      const pageHeading = document.querySelector("#backtest .page-heading");
      if (pageHeading) pageHeading.append(guide);
      else panel.append(guide);
    }
    // Controls installed later retain their handlers and values, but join the same groups.
    if (!sectioned) {
      const regroup = () => {
        for (const [id, index] of [
          ["backtestInitialCash", 2],
          ["backtestUniverseMode", 0],
        ]) {
          const input = document.getElementById(String(id));
          const label = input?.closest("label");
          const group = panel.querySelectorAll(".config-group")[Number(index)];
          if (label && group && label.parentElement !== group)
            group.prepend(label);
        }
      };
      new MutationObserver(regroup).observe(panel, {
        childList: true,
        subtree: true,
      });
    }
  }
  const parameterUnits = [
    ["factorMiningSeed", "ID", "可复现随机种子 / Reproducible random seed"],
    ["factorMiningGenerations", "代 / gen", "进化轮数 / Evolution rounds"],
    [
      "factorMiningPopulation",
      "只 / items",
      "每代候选数 / Candidates per generation",
    ],
    [
      "factorMiningTopN",
      "只 / items",
      "保留上限 / Maximum retained candidates",
    ],
  ];
  for (const [id, unit, hint] of parameterUnits) {
    const input = document.getElementById(id);
    if (input) {
      input.title = hint;
      const suffix = document.createElement("small");
      suffix.className = "parameter-unit";
      suffix.textContent = unit;
      input.after(suffix);
    }
  }
  const pill = document.getElementById("servicePill");
  if (pill) {
    pill.classList.add("status-chip");
    pill.tabIndex = 0;
    pill.setAttribute("role", "button");
    pill.setAttribute("aria-label", text(3));
    pill.addEventListener("click", () =>
      document.getElementById("trackerDetailsButton")?.click(),
    );
    pill.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        pill.click();
      }
    });
  }
  const targets = {
    factorTableBody: "research",
    holdingsBody: "paper",
    paperOrdersBody: "paper",
  };
  const decoratePanels = () => {
    document
      .querySelectorAll("div.statement-empty,div.factor-mining-empty")
      .forEach((node) => {
        if (node instanceof HTMLElement && !node.querySelector(".empty-state"))
          decorateEmpty(node, node.textContent || "", "connection");
      });
  };
  decoratePanels();
  new MutationObserver(decoratePanels).observe(
    document.getElementById("appShell") || document.body,
    { childList: true, subtree: true },
  );
  for (const [id, target] of Object.entries(targets)) {
    const body = document.getElementById(id);
    if (!body) continue;
    const refresh = () => {
      const cell = body.querySelector("tr:only-child > td:only-child[colspan]");
      if (cell instanceof HTMLElement)
        decorateEmpty(
          cell,
          cell.textContent || "",
          /** @type {'research'|'paper'} */ (target),
        );
    };
    refresh();
    new MutationObserver(refresh).observe(body, {
      childList: true,
      subtree: true,
    });
  }
  // Financial values are always the actual formatted source, never interpolated.
}

/** Label icon-only controls for a11y (design-system P1). */
export function ensureControlLabels(root = document) {
  root.querySelectorAll("button").forEach((btn) => {
    const text = (btn.textContent || "").replace(/\s+/g, " ").trim();
    if (text) return;
    if (btn.getAttribute("aria-label") || btn.title) return;
    if (btn.classList.contains("stock-chip-remove")) {
      btn.setAttribute("aria-label", "移除标的");
      btn.title = "移除标的";
      return;
    }
    if (btn.classList.contains("range-tab")) {
      btn.setAttribute("aria-label", btn.dataset.range || "时间范围");
      return;
    }
    const parent = btn.closest("[data-filter], .filter-row, .category-tags");
    if (parent) {
      btn.setAttribute(
        "aria-label",
        btn.dataset.category || btn.dataset.filter || "筛选",
      );
      btn.title = btn.getAttribute("aria-label") || "筛选";
    }
  });
  root
    .querySelectorAll(".chart-controls button, .chart-zoom-tools button")
    .forEach((btn) => {
      const t = (btn.textContent || "").trim();
      if (t && !btn.getAttribute("aria-label"))
        btn.setAttribute("aria-label", t);
    });
}

/** P3: ensure strategy params balanced after late mounts; quiet chart zoom chips. */
export function installP3Polish(doc = document) {
  try {
    const params = doc.getElementById("backtestStrategyParams");
    params &&
      params.dataset.balanced !== "1" &&
      doc.dispatchEvent(new CustomEvent("a-pi:balance-strategy-params"));
  } catch (_) {}
  doc
    .querySelectorAll(
      ".chart-zoom-controls, .mini-chart-zoom, [data-chart-zoom]",
    )
    .forEach((el) => {
      if (el instanceof HTMLElement) {
        el.style.opacity = "0.72";
        el.style.gap = "4px";
      }
    });
  // Hide duplicate remember hint (kept in DOM for aria)
  const hint = doc.getElementById("rememberHint");
  if (hint) hint.setAttribute("hidden", "");
}
if (typeof document !== "undefined") {
  const run = () => installP3Polish(document);
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", run, { once: true });
  else run();
}
