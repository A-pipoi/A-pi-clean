/** In-page tabs + sidebar grouping. Does not change sidebar-dock chrome/interaction. */
import { APiCharts } from "./charts.mjs";

/**
 * @param {ParentNode} parent
 * @param {{id:string,label:string,nodes:Element[]}[]} tabs
 * @param {string} [storageKey]
 */
function mountTabs(parent, tabs, storageKey = "") {
  if (!tabs.length || parent.querySelector(":scope > .workbench-tabs")) return;
  const bar = document.createElement("div");
  bar.className = "workbench-tabs";
  bar.setAttribute("role", "tablist");
  const panels = document.createElement("div");
  panels.className = "workbench-tab-panels";
  /** @type {HTMLButtonElement[]} */
  const buttons = [];
  /** @type {HTMLElement[]} */
  const panelEls = [];
  const initial =
    (storageKey && sessionStorage.getItem(storageKey)) || tabs[0].id;

  tabs.forEach((tab, index) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "workbench-tab";
    btn.id = `tab-${tab.id}`;
    btn.setAttribute("role", "tab");
    btn.dataset.tabId = tab.id;
    btn.textContent = tab.label;
    buttons.push(btn);
    bar.append(btn);

    const panel = document.createElement("div");
    panel.className = "workbench-tab-panel";
    panel.id = `panel-${tab.id}`;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", btn.id);
    tab.nodes.forEach((node) => {
      if (node) panel.append(node);
    });
    panelEls.push(panel);
    panels.append(panel);

    btn.addEventListener("click", () => activate(index));
  });

  /** @param {number} index */
  function activate(index) {
    buttons.forEach((btn, i) => {
      const on = i === index;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
      btn.tabIndex = on ? 0 : -1;
      panelEls[i].hidden = !on;
      panelEls[i].classList.toggle("is-active", on);
    });
    if (storageKey) sessionStorage.setItem(storageKey, tabs[index].id);
    requestAnimationFrame(() => APiCharts?.draw?.());
  }

  const start = Math.max(
    0,
    tabs.findIndex((tab) => tab.id === initial),
  );
  parent.append(bar, panels);
  activate(start < 0 ? 0 : start);
  return { activate, buttons, panelEls, tabs };
}

/** @param {Document} root */
function regroupSidebar(root) {
  const candidate = root.querySelector(".sidebar .nav-list");
  if (
    !(candidate instanceof HTMLElement) ||
    candidate.dataset.iaGrouped === "1"
  )
    return;
  const nav = candidate;
  nav.dataset.iaGrouped = "1";

  /** @type {[string, string[]][]} */
  const groups = [
    ["监控", ["overview"]],
    ["研究", ["research", "factorMining"]],
    ["验证", ["backtest", "risk"]],
    ["执行", ["paper"]],
  ];
  const items = [...nav.querySelectorAll(".nav-item")];
  const bySection = Object.fromEntries(
    items.map((item) => [item.dataset.section, item]),
  );
  nav.replaceChildren();
  for (const [label, sections] of groups) {
    const heading = root.createElement("div");
    heading.className = "nav-group-label";
    heading.textContent = label;
    nav.append(heading);
    for (const section of sections) {
      const item = bySection[section];
      if (item) nav.append(item);
    }
  }

  const system = root.createElement("div");
  system.className = "nav-group-label nav-group-label--system";
  system.textContent = "系统";
  nav.append(system);

  /** @param {string} section @param {string} label */
  function ensureNav(section, label) {
    if (nav.querySelector(`[data-section="${section}"]`)) return;
    const btn = root.createElement("button");
    btn.type = "button";
    btn.className = "nav-item";
    btn.dataset.section = section;
    btn.setAttribute("aria-label", label);
    btn.title = label;
    const icon = root.createElement("span");
    icon.className = "nav-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = "◇";
    const lab = root.createElement("span");
    lab.className = "nav-label";
    lab.textContent = label;
    btn.append(icon, lab);
    nav.append(btn);
  }
  ensureNav("governance", "研究治理");
  ensureNav("ops", "数据与运维");
}

/** Move node into host; drop empty workbench-disclosure parents left behind.
 * @param {Element | null | undefined} host
 * @param {Element | null | undefined} node
 */
function adoptInto(host, node) {
  if (!host || !node) return;
  const shell = node.closest("details.workbench-disclosure");
  host.append(node);
  if (
    shell &&
    !shell.querySelector(
      ".panel, table, canvas, input, select, .live-ic-panel, .research-stat-grid, .factor-pipeline-status",
    )
  ) {
    shell.remove();
  } else if (shell) {
    const leftovers = [...shell.children].filter(
      (n) => n.tagName !== "SUMMARY",
    );
    if (!leftovers.length) shell.remove();
  }
}

/** @param {Document} root */
function ensureSyntheticPages(root) {
  // Always mount under .page-wrap so showSection page-section toggles stay in-flow.
  // Do NOT use outer <main class="main-content"> — that parks pages beside the wrap (blank UI).
  const candidateWrap =
    root.querySelector(".page-wrap") ||
    root.getElementById("overview")?.parentElement;
  if (!candidateWrap) return;
  const wrap = candidateWrap;

  /** @param {string} id @param {string} title @param {string} desc */
  function ensurePage(id, title, desc) {
    let page = root.getElementById(id);
    // Existing non-page nodes (e.g. #governance panel inside backtest) must not
    // be treated as a route target — that blanks the shell when activated.
    if (page && !page.classList.contains("page-section")) {
      if (!page.id.endsWith("Panel")) page.id = `${id}Panel`;
      page = null;
    }
    if (page) {
      if (!page.querySelector(`[data-ia-body="${id}"]`)) {
        const body = root.createElement("div");
        body.className = "ia-page-body";
        body.dataset.iaBody = id;
        page.append(body);
      }
      if (page.parentElement !== wrap) wrap.append(page);
      return page;
    }
    page = root.createElement("section");
    page.className = "page-section";
    page.id = id;
    const heading = root.createElement("div");
    heading.className = "page-heading compact";
    const copy = root.createElement("div");
    const over = root.createElement("div");
    over.className = "overline";
    over.textContent = "SYSTEM";
    const h1 = root.createElement("h1");
    h1.textContent = title;
    const p = root.createElement("p");
    p.textContent = desc;
    copy.append(over, h1, p);
    heading.append(copy);
    const body = root.createElement("div");
    body.className = "ia-page-body";
    body.dataset.iaBody = id;
    page.append(heading, body);
    wrap.append(page);
    return page;
  }

  const gov = ensurePage(
    "governance",
    "研究治理",
    "预算、研究边界、密封测试与归档",
  );
  const ops = ensurePage(
    "ops",
    "数据与运维",
    "PIT 预热、扩展数据核查与纸面维护",
  );
  const govBody = gov.querySelector("[data-ia-body='governance']") || gov;
  const opsBody = ops.querySelector("[data-ia-body='ops']") || ops;

  const governance =
    root.querySelector("#backtest .governance-section") ||
    root.querySelector(".governance-section") ||
    root.getElementById("governancePanel");
  if (governance && !govBody.contains(governance)) {
    if (governance.id === "governance") governance.id = "governancePanel";
    govBody.append(governance);
  }

  const dataSection = root.getElementById("backtestDataSection");
  if (dataSection && !opsBody.contains(dataSection)) {
    const wrap = root.createElement("div");
    wrap.className = "panel";
    wrap.append(dataSection);
    opsBody.append(wrap);
    // leave a stub on backtest config
    const stub = root.createElement("p");
    stub.className = "config-note ia-moved-note";
    stub.append("数据与 PIT 预热已移至侧栏「数据与运维」。");
    const jump = root.createElement("button");
    jump.type = "button";
    jump.className = "text-button";
    jump.dataset.sectionLink = "ops";
    jump.textContent = "打开运维 →";
    stub.append(jump);
    const config = root.getElementById("backtestConfigPanel");
    config?.querySelector(".panel-kicker")?.after(stub);
  }

  const catchup = root.querySelector("#paper .paper-catchup-tools");
  const runDetails = root.querySelector("#paper .paper-run-details");
  if (catchup && !opsBody.contains(catchup)) {
    const wrap = root.createElement("div");
    wrap.className = "panel";
    const h2 = root.createElement("h2");
    h2.textContent = "纸面运行维护";
    wrap.append(h2);
    wrap.append(catchup);
    if (runDetails) wrap.append(runDetails);
    opsBody.append(wrap);
  }
}

/** @param {Document} root */
function installBacktestTabs(root) {
  const page = root.getElementById("backtest");
  if (!page || page.dataset.iaTabs === "1") return;
  const layout = page.querySelector(".backtest-layout");
  const config = root.getElementById("backtestConfigPanel");
  const results = root.getElementById("backtestResultsPanel");
  if (!layout || !config || !results) return;
  page.dataset.iaTabs = "1";

  // Attach staging host first so moved controls remain document-connected
  // (getElementById / requiredElement fail on detached subtrees under Trusted Types apps).
  const staging = root.createElement("div");
  staging.className = "ia-staging";
  staging.hidden = true;
  page.append(staging);

  const sensitivityHost = root.createElement("div");
  sensitivityHost.className = "panel ia-sensitivity-panel";
  const sensTitle = root.createElement("h2");
  sensTitle.textContent = "参数敏感性";
  sensitivityHost.append(sensTitle);
  staging.append(sensitivityHost);
  const sensBtn = root.getElementById("sensitivityButton");
  const sensResult = root.getElementById("sensitivityResult");
  if (sensBtn) sensitivityHost.append(sensBtn);
  if (sensResult) sensitivityHost.append(sensResult);

  const compareHost = root.createElement("div");
  compareHost.className = "panel ia-compare-panel";
  compareHost.id = "experimentComparison";
  const cmpTitle = root.createElement("h2");
  cmpTitle.textContent = "回测对比";
  const hint = root.createElement("p");
  hint.className = "ia-tab-hint";
  hint.textContent = "在研究治理的台账中勾选至少 2 个已保存回测，再点击对比。";
  const note = root.createElement("p");
  note.id = "ledgerCompareNote";
  note.className = "datum-note";
  const slot = root.createElement("div");
  slot.dataset.iaCompareSlot = "1";
  compareHost.append(cmpTitle, hint, note, slot);
  staging.append(compareHost);

  const heading = page.querySelector(".page-heading");
  const host = root.createElement("div");
  host.className = "ia-tab-host";
  if (heading) heading.after(host);
  else page.prepend(host);

  // Pull config/results out while still connected, then remove old layout shell
  mountTabs(
    host,
    [
      { id: "bt-config", label: "实验配置", nodes: [config] },
      { id: "bt-result", label: "结果", nodes: [results] },
      { id: "bt-compare", label: "对比", nodes: [compareHost] },
      { id: "bt-sens", label: "敏感性", nodes: [sensitivityHost] },
    ],
    "a_pi_tab_backtest",
  );
  layout?.remove();
  staging.remove();
}

/** @param {Document} root */
function installMiningTabs(root) {
  const page = root.getElementById("factorMining");
  const panel = page?.querySelector(".factor-mining-panel");
  if (!page || !panel || page.dataset.iaTabs === "1") return;
  page.dataset.iaTabs = "1";

  const heading = panel.querySelector(".factor-mining-heading");
  const universe = panel.querySelector(".factor-mining-universe");
  const workbench = panel.querySelector(".factor-mining-workbench");
  const arena = panel.querySelector(
    "#portfolioArena, .factor-evaluation-report",
  );
  const output = panel.querySelector(".factor-mining-output");
  const expression = workbench?.querySelector(".factor-expression-area");
  const guardrails = workbench?.querySelector(".factor-mining-guardrails");
  const compositions = [
    ...(expression?.querySelectorAll(":scope > details.factor-composition") ||
      workbench?.querySelectorAll("details.factor-composition") ||
      []),
  ];
  const compose = compositions[0];
  const deep = compositions[1];
  // Detach composition blocks before moving the expression area into Search tab
  compositions.forEach((node) => node.remove());

  // Pull compositions out of expression area flow for their own tabs
  const searchNodes = [heading, universe, expression, guardrails].filter(
    (node) => node != null,
  );
  const composeNodes = [compose].filter((node) => node != null);
  const externalNodes = [deep, arena].filter((node) => node != null);
  const batchNodes = [output].filter((node) => node != null);

  const bridge = root.createElement("div");
  bridge.className = "ia-tab-stack factor-registry-bridge";
  const bridgeCard = root.createElement("div");
  bridgeCard.className = "panel";
  const bridgeTitle = root.createElement("h3");
  bridgeTitle.textContent = "因子库（与研究共用）";
  const bridgeP = root.createElement("p");
  bridgeP.className = "muted";
  bridgeP.textContent =
    "启用、冻结与注册状态统一在「研究 → 因子库」维护；挖掘页不另建副本。";
  const bridgeBtn = root.createElement("button");
  bridgeBtn.type = "button";
  bridgeBtn.className = "secondary-button";
  bridgeBtn.textContent = "打开研究 · 因子库";
  bridgeBtn.addEventListener("click", () => {
    try {
      sessionStorage.setItem("a_pi_tab_research", "rs-registry");
    } catch (_) {}
    const researchNav = root.querySelector(
      '.nav-item[data-section="research"]',
    );
    if (researchNav instanceof HTMLElement) researchNav.click();
  });
  bridgeCard.append(bridgeTitle, bridgeP, bridgeBtn);
  bridge.append(bridgeCard);

  const host = root.createElement("div");
  host.className = "ia-tab-host";
  page.append(host);
  mountTabs(
    host,
    [
      { id: "fm-search", label: "搜索与评估", nodes: searchNodes },
      { id: "fm-compose", label: "组合研究", nodes: composeNodes },
      { id: "fm-registry", label: "因子库", nodes: [bridge] },
      { id: "fm-external", label: "外部候选", nodes: externalNodes },
      { id: "fm-batch", label: "批次记录", nodes: batchNodes },
    ].filter((tab) => tab.nodes.length),
    "a_pi_tab_mining",
  );
  panel.remove();
}

/** @param {Document} root */
function installResearchTabs(root) {
  const page = root.getElementById("research");
  if (!page || page.dataset.iaTabs === "1") return;
  page.dataset.iaTabs = "1";
  const heading = page.querySelector(".page-heading");
  const host = root.createElement("div");
  host.className = "ia-tab-host";
  if (heading) heading.after(host);
  else page.append(host);

  const monitor = root.createElement("div");
  monitor.className = "ia-tab-stack";
  const registryHost = root.createElement("div");
  registryHost.className = "ia-tab-stack";
  const financialHost = root.createElement("div");
  financialHost.className = "ia-tab-stack";

  mountTabs(
    host,
    [
      { id: "rs-monitor", label: "样本外监控", nodes: [monitor] },
      { id: "rs-registry", label: "因子库", nodes: [registryHost] },
      { id: "rs-financial", label: "财务数据", nodes: [financialHost] },
    ],
    "a_pi_tab_research",
  );

  const stats = page.querySelector(".research-stat-grid");
  const status = page.querySelector(".factor-pipeline-status");
  const live = page.querySelector("#liveICPanel, .live-ic-panel");
  const registry = page.querySelector(".factor-panel");
  const financial = page.querySelector(".financial-statement-panel");
  [stats, status, live].filter(Boolean).forEach((n) => adoptInto(monitor, n));
  adoptInto(registryHost, registry);
  adoptInto(financialHost, financial);
  // Drop any orphan empty disclosures still under research
  page.querySelectorAll("details.workbench-disclosure").forEach((details) => {
    const body = [...details.children].filter((n) => n.tagName !== "SUMMARY");
    if (
      !body.length ||
      body.every((n) => !(n.textContent || "").trim() && !n.children.length)
    ) {
      details.remove();
    }
  });
}

/** @param {Document} root */
function installRiskTabs(root) {
  const page = root.getElementById("risk");
  if (!page || page.dataset.iaTabs === "1") return;
  page.dataset.iaTabs = "1";
  const heading = page.querySelector(".page-heading");
  const host = root.createElement("div");
  host.className = "ia-tab-host";
  if (heading) heading.after(host);
  else page.append(host);

  const limitsHost = root.createElement("div");
  limitsHost.className = "ia-tab-stack";
  const optHost = root.createElement("div");
  optHost.className = "ia-tab-stack";
  mountTabs(
    host,
    [
      { id: "rk-limits", label: "限额监控", nodes: [limitsHost] },
      { id: "rk-opt", label: "组合优化", nodes: [optHost] },
    ],
    "a_pi_tab_risk",
  );
  const layout = page.querySelector(".risk-layout");
  const optimizer = page.querySelector(".optimizer-panel");
  if (layout) adoptInto(limitsHost, layout);
  adoptInto(optHost, optimizer);
  page.querySelectorAll("details.workbench-disclosure").forEach((details) => {
    const body = [...details.children].filter((n) => n.tagName !== "SUMMARY");
    if (
      !body.length ||
      body.every(
        (n) =>
          !(n.textContent || "").trim() &&
          n.children.length === 0 &&
          !n.querySelector("table, .panel, form, input, select, textarea"),
      )
    ) {
      details.remove();
    }
  });
}

/** @param {Document} root */
function installPaperTabs(root) {
  const page = root.getElementById("paper");
  if (!page || page.dataset.iaTabs === "1") return;
  page.dataset.iaTabs = "1";
  const heading = page.querySelector(".page-heading");
  const host = root.createElement("div");
  host.className = "ia-tab-host";
  if (heading) heading.after(host);
  else page.prepend(host);

  const trading = root.createElement("div");
  trading.className = "ia-tab-stack";
  const researchHost = root.createElement("div");
  researchHost.className = "ia-tab-stack";
  researchHost.dataset.iaPaperResearch = "1";

  // Mount empty panels first while connected
  mountTabs(
    host,
    [
      { id: "pp-trading", label: "委托与持仓", nodes: [trading] },
      { id: "pp-research", label: "研究账户", nodes: [researchHost] },
    ],
    "a_pi_tab_paper",
  );

  const kpis = page.querySelector(".paper-kpis");
  const orders = page.querySelector(".orders-panel");
  const research = page.querySelector(".research-paper-panel");
  if (kpis) trading.append(kpis);
  if (orders) trading.append(orders);
  if (research) researchHost.append(research);

  const obs = new MutationObserver(() => {
    const panel = page.querySelector(
      ":scope > .research-paper-panel, .research-paper-panel",
    );
    if (panel && !researchHost.contains(panel)) researchHost.append(panel);
  });
  obs.observe(page, { childList: true, subtree: true });
}

/** @param {Document} root */
function cleanupHeaders(root) {
  root.querySelectorAll(".page-heading p").forEach((p) => {
    const text = (p.textContent || "").trim();
    if (text.length > 36) {
      p.title = text;
      // keep as-is if already shortened by copy pass
    }
  });
}

/** @param {Document} [root] */
export function installWorkbenchIA(root = document) {
  regroupSidebar(root);
  ensureSyntheticPages(root);
  installBacktestTabs(root);
  installMiningTabs(root);
  installResearchTabs(root);
  installRiskTabs(root);
  installPaperTabs(root);
  cleanupHeaders(root);

  // section-link buttons for moved notes
  root.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const link = target.closest("[data-section-link]");
    if (!link) return;
    const section = link.getAttribute("data-section-link");
    if (!section) return;
    root
      .querySelector(`.nav-item[data-section="${section}"]`)
      ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  root.querySelectorAll("details.workbench-disclosure").forEach((details) => {
    const body = [...details.children].filter((n) => n.tagName !== "SUMMARY");
    if (
      !body.length ||
      body.every(
        (n) =>
          !(n.textContent || "").trim() &&
          n.children.length === 0 &&
          !n.querySelector("table, .panel, canvas, input, select"),
      )
    ) {
      details.remove();
    }
  });
}
