import { apiFetch } from "./api-client.mjs";

/** @typedef {{ok?:boolean,error?:string,configuration_error?:string|null,version_mismatch?:boolean,enabled?:boolean,configuration?:{enabled?:boolean,strategy?:string,rebalance?:string,config_hash?:string,code_hash?:string,archive_id?:string,version_started_at?:string},paper?:import('../types/domain').PaperState,scheduler?:import('../types/domain').Scheduler,cycle?:import('../types/domain').PaperState,orders?:NonNullable<import('../types/domain').PaperState['orders']>,pages?:number,total?:number}} ResearchResponse */
let initialized = false;
let busy = false;
let page = 1;
let pages = 1;
let enabled = false;
let configured = false;
let upgraded = false;
let versionMismatch = false;
let configHash = "";
/** @type {HTMLElement} */ let panel;
/** @type {HTMLParagraphElement} */ let status;
/** @type {HTMLDivElement} */ let balances;
/** @type {HTMLDivElement} */ let execution;
/** @type {HTMLButtonElement} */ let enable;
/** @type {HTMLButtonElement} */ let pause;
/** @type {HTMLButtonElement} */ let run;
/** @type {HTMLButtonElement} */ let refresh;
/** @type {HTMLButtonElement} */ let upgrade;
/** @type {HTMLButtonElement} */ let newVersion;
/** @type {(() => void) | null} */ let layoutResearchActions = null;
/** @type {HTMLButtonElement} */ let previous;
/** @type {HTMLButtonElement} */ let next;
/** @type {HTMLTableSectionElement} */ let tableBody;
/** @type {HTMLSpanElement} */ let pageLabel;

/** @param {string} zh @param {string} en @param {string} ja */
function text(zh, en, ja) {
  const lang = document.documentElement.lang;
  return lang.startsWith("en") ? en : lang.startsWith("ja") ? ja : zh;
}

function updateButtons() {
  enable.disabled = busy || enabled || versionMismatch;
  layoutResearchActions?.();
  newVersion.disabled = busy || !configured || !versionMismatch;
  pause.disabled = busy || !configured;
  run.disabled = busy || !enabled;
  refresh.disabled = busy;
  upgrade.disabled = busy || !configured || upgraded;
  previous.disabled = busy || page <= 1;
  next.disabled = busy || page >= pages;
  panel.setAttribute("aria-busy", String(busy));
}

/** @param {ResearchResponse} data */
function render(data) {
  enabled = data.enabled === true;
  const config = data.configuration;
  configured = Boolean(config);
  versionMismatch = data.version_mismatch === true;
  configHash = config?.config_hash || "";
  upgraded =
    config?.strategy === "baseline_cost_aware" &&
    config?.enabled === true &&
    !data.configuration_error;
  status.textContent = enabled
    ? text(
        "研究账户已启用 · 未验收",
        "Research enabled · Unvalidated · Not independent holdout evidence",
        "研究口座は有効 · 未検証 · 独立した封印検証の証拠ではありません",
      )
    : text(
        "研究账户未启用或已暂停",
        "Research disabled or paused; formal acceptance gates remain unchanged.",
        "研究口座は未有効または停止中。正式口座の封印ゲートは維持されます。",
      );
  if (data.configuration_error) status.textContent = data.configuration_error;
  if (versionMismatch)
    status.textContent = text(
      "版本已变 · 请新建",
      "Execution version changed. Create a new research version; old records are retained and cannot validate the new version.",
      "実行バージョンが変更されました。旧記録を保持して新しい研究バージョンを作成してください。",
    );
  if (config?.archive_id && !enabled && !versionMismatch)
    status.textContent = text(
      "新版本已建 · 已暂停 · 未验收",
      "New research version is paused. Enable it to accumulate new observations; unvalidated.",
      "新しい研究バージョンは停止中です。有効化して新しい観測を蓄積してください。未検証です。",
    );
  const paper = data.paper;
  const currency = new Intl.NumberFormat(
    document.documentElement.lang || "zh-CN",
    { style: "currency", currency: "CNY" },
  );
  balances.replaceChildren();
  if (paper) {
    const entries = [
      [text("权益", "Equity", "純資産"), currency.format(Number(paper.equity))],
      [text("可用现金", "Cash", "現金"), currency.format(Number(paper.cash))],
      [text("持仓", "Positions", "保有"), String(paper.positions?.length || 0)],
      [
        text("策略 / 调仓", "Strategy / rebalance", "戦略 / リバランス"),
        `${config?.strategy || "—"} / ${config?.rebalance || "—"}`,
      ],
    ];
    for (const [label, value] of entries) {
      const item = document.createElement("div");
      item.className = "research-balance-item";
      const name = document.createElement("span");
      name.className = "research-balance-label";
      name.textContent = label;
      const amount = document.createElement("strong");
      amount.className = "research-balance-value";
      amount.textContent = value;
      item.append(name, amount);
      balances.append(item);
    }
  } else {
    balances.textContent = text(
      "启用后创建独立的 100 万元模拟账户，不复制现有持仓。",
      "Enabling creates a separate CNY 1,000,000 simulation without copying existing holdings.",
      "有効化すると既存保有をコピーせず、独立した100万元の模擬口座を作成します。",
    );
  }
  const latest = data.scheduler?.maintenance?.latest;
  const result = data.cycle || data.scheduler?.last_result;
  execution.replaceChildren();
  const schedule = document.createElement("span");
  schedule.className = "research-execution-schedule";
  schedule.textContent = text(
    "维护窗口: 交易日 16:00–16:10",
    "Maintenance: trading days 16:00–16:10",
    "自動維持 · 取引日16:00–16:10（北京時間）。毎日の売買ではありません",
  );
  execution.append(schedule);
  if (config?.code_hash) {
    const version = document.createElement("span");
    version.className = "research-execution-reason";
    version.textContent = `${text("研究版本", "Research version", "研究バージョン")}: ${config.code_hash.slice(0, 12)}${config.version_started_at ? ` · ${config.version_started_at}` : ""}${config.archive_id ? ` · ${text("旧记录保留编号", "Archive ID", "旧記録ID")}: ${config.archive_id}` : ""}`;
    execution.append(version);
  }
  if (latest) {
    const lastRun = document.createElement("span");
    lastRun.className = "research-execution-latest";
    lastRun.textContent = `${text("最近维护", "Last maintenance", "最終維持")}: ${latest.run_date} · ${latest.status} · ${latest.attempts}`;
    execution.append(lastRun);
  }
  const reason = result?.no_trade_reason || result?.skip_reason;
  if (reason) {
    const reasonLine = document.createElement("span");
    reasonLine.className = "research-execution-reason";
    reasonLine.textContent = `${text("未调仓原因", "No-rebalance reason", "リバランスなしの理由")}: ${reason}`;
    execution.append(reasonLine);
  }
  updateButtons();
}

/** @param {string} url @param {RequestInit} [options] @returns {Promise<ResearchResponse>} */
async function request(url, options) {
  const response = await apiFetch(url, options);
  const data = await response.json();
  if (!response.ok || data.ok !== true)
    throw Error(data.error || `HTTP ${response.status}`);
  return data;
}

async function orders() {
  const data = await request(`/api/paper/research/orders?page=${page}`);
  pages = data.pages || 1;
  pageLabel.textContent = `${page}/${pages} · ${data.total || 0}`;
  tableBody.replaceChildren();
  for (const order of data.orders || []) {
    const row = document.createElement("tr");
    for (const value of [
      order.order_id,
      order.symbol,
      order.side,
      order.quantity,
      order.filled_quantity,
      order.status,
    ]) {
      const cell = document.createElement("td");
      cell.textContent = String(value ?? "—");
      row.append(cell);
    }
    tableBody.append(row);
  }
  if (!tableBody.children.length) {
    const noticeRow = document.createElement("tr");
    noticeRow.className = "research-paper-isolation-row";
    const noticeCell = document.createElement("td");
    noticeCell.colSpan = 6;
    noticeCell.textContent = text(
      "未验收研究纸面委托，与正式账户隔离",
      "Unvalidated simulated orders, isolated from the formal account",
      "未検証の研究模擬注文。正式口座から独立しています",
    );
    noticeRow.append(noticeCell);
    tableBody.append(noticeRow);

    const emptyRow = document.createElement("tr");
    emptyRow.className = "research-paper-empty-row";
    const emptyCell = document.createElement("td");
    emptyCell.colSpan = 6;
    emptyCell.textContent = text(
      "暂无研究纸面委托",
      "No research paper orders",
      "研究模擬注文はありません",
    );
    emptyRow.append(emptyCell);
    tableBody.append(emptyRow);
  }
}

/** @param {string} [action] @param {number} [requestedPage] */
export async function refreshResearchPaper(action, requestedPage) {
  const previousPage = page;
  if (!initialized || busy) return;
  if (
    typeof requestedPage === "number" &&
    Number.isInteger(requestedPage) &&
    requestedPage >= 1
  )
    page = requestedPage;
  let payload = {};
  if (action === "pause") {
    if (
      !window.confirm(
        text(
          "暂停研究账户？暂停后将停止自动维护与委托，可稍后重新启用。",
          "Pause the research account? Automatic maintenance and orders will stop until re-enabled.",
          "研究口座を一時停止しますか？",
        ),
      )
    )
      return;
  }
  if (action === "new-version") {
    if (!versionMismatch || !configHash) return;
    const confirmed = window.confirm(
      text(
        "建立新研究版本？旧账户的委托、维护、持仓和净值记录将完整保留在独立版本目录，不会迁入新账户。新账户沿用冻结策略、因子和调仓频率，以100万元模拟资金从零开始，初始暂停，需要另行启用。旧标签、历史回放和验收结论不作为新版本验收；正式账户不受影响。",
        "Create a new research version? Archive the complete old account without migrating orders, positions or equity. Preserve frozen strategy, factors and rebalance frequency. Start a paused CNY 1,000,000 simulation; enable separately. Old labels, replays and acceptance do not validate the new version. The formal account is unchanged.",
        "新しい研究バージョンを作成しますか？旧口座の全記録を保存し、新口座には移しません。凍結した戦略・因子・頻度を保持し、100万元の模擬資金で停止状態から開始します。旧結果は新バージョンの検証に使用せず、正式口座は変更しません。",
      ),
    );
    if (!confirmed) return;
    payload = { expected_config_hash: configHash, confirm_new_version: true };
  }
  busy = true;
  updateButtons();
  try {
    const data = await request(
      action ? `/api/paper/research/${action}` : "/api/paper/research",
      action
        ? {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          }
        : undefined,
    );
    render(data);
    if (data.configuration) await orders();
  } catch (error) {
    page = previousPage;
    status.textContent = `${text("研究账户操作未完成：", "Research operation incomplete: ", "研究操作は未完了：")}${error instanceof Error ? error.message : String(error)}`;
    if (action) status.focus();
  } finally {
    busy = false;
    updateButtons();
  }
}

export function initResearchPaper() {
  if (initialized) return;
  const host = document.querySelector("#paper .paper-kpis");
  if (!host) return;
  panel = document.createElement("section");
  status = document.createElement("p");
  balances = document.createElement("div");
  balances.className = "research-balance-strip";
  execution = document.createElement("div");
  execution.className = "research-execution";
  enable = document.createElement("button");
  pause = document.createElement("button");
  run = document.createElement("button");
  refresh = document.createElement("button");
  upgrade = document.createElement("button");
  newVersion = document.createElement("button");
  newVersion.dataset.slot = "research-new-version";
  previous = document.createElement("button");
  next = document.createElement("button");
  tableBody = document.createElement("tbody");
  pageLabel = document.createElement("span");
  panel.className = "panel research-paper-panel";
  const heading = document.createElement("div");
  heading.className = "panel-heading";
  const title = document.createElement("h2");
  title.textContent = text(
    "独立研究纸面账户",
    "Separate research paper account",
    "独立した研究模擬口座",
  );
  const actions = document.createElement("div");
  actions.className = "heading-actions";
  const buttons = [enable, pause, run, refresh, upgrade, newVersion];
  const labels = [
    text("启用研究账户", "Enable research", "研究口座を有効化"),
    text("暂停研究", "Pause research", "研究を停止"),
    text("运行研究维护", "Run research maintenance", "研究維持を実行"),
    text("刷新研究账户", "Refresh research", "研究口座を更新"),
    text(
      "升级财务低换手版本",
      "Upgrade financial baseline",
      "財務ベースラインへ更新",
    ),
    text("建立新研究版本", "Create research version", "新研究バージョンを作成"),
  ];
  const handlers = [
    "enable",
    "pause",
    "cycle",
    undefined,
    "upgrade-financial",
    "new-version",
  ];
  const buttonClasses = [
    "primary-button",
    "danger-button",
    "secondary-button",
    "secondary-button",
    "secondary-button",
    "danger-button",
  ];
  buttons.forEach((button, index) => {
    button.type = "button";
    button.className = buttonClasses[index] || "secondary-button";
    button.textContent = labels[index];
    button.addEventListener(
      "click",
      () => void refreshResearchPaper(handlers[index]),
    );
  });
  layoutResearchActions = function layoutResearchActions() {
    actions.className = "heading-actions research-paper-actions";
    actions.replaceChildren();
    buttons.forEach((button, index) => {
      button.className = buttonClasses[index] || "secondary-button";
      actions.append(button);
    });
  };
  layoutResearchActions();

  heading.append(title, actions);
  const upgradeHint = document.createElement("p");
  upgradeHint.className = "research-upgrade-hint";
  upgradeHint.textContent = text(
    "升级仅限无委托、维护或净值记录且财务因子可用的账户；不回填历史，效果尚未验证。",
    "Upgrade requires no prior orders, maintenance or equity records and available financial factors; no backfill, results unverified.",
    "注文・維持・純資産の記録がなく、財務因子が利用可能な口座のみ更新できます。過去データは補完せず、効果は未検証です。",
  );
  status.setAttribute("role", "status");
  status.tabIndex = -1;
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = text(
    "查看研究委托（每页20条）",
    "Research orders (20 per page)",
    "研究注文を表示（1ページ20件）",
  );
  const scroll = document.createElement("div");
  scroll.className = "table-scroll";
  const table = document.createElement("table");
  table.setAttribute(
    "aria-label",
    text(
      "未验收研究纸面委托，与正式账户隔离",
      "Unvalidated simulated orders, isolated from the formal account",
      "未検証の研究模擬注文。正式口座から独立しています",
    ),
  );
  const head = document.createElement("thead");
  const row = document.createElement("tr");
  for (const label of [
    "ID",
    text("标的", "Symbol", "銘柄"),
    text("方向", "Side", "方向"),
    text("数量", "Quantity", "数量"),
    text("成交", "Filled", "約定"),
    text("状态", "Status", "状態"),
  ]) {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = label;
    row.append(cell);
  }
  head.append(row);
  table.append(head, tableBody);
  scroll.append(table);
  const pager = document.createElement("nav");
  pager.className = "order-pagination";
  pager.setAttribute(
    "aria-label",
    text("研究委托分页", "Research order pagination", "研究注文のページ"),
  );
  previous.type = next.type = "button";
  previous.textContent = text("上一页", "Previous", "前へ");
  next.textContent = text("下一页", "Next", "次へ");
  previous.addEventListener("click", () => {
    if (page <= 1) return;
    const target = page - 1;
    void refreshResearchPaper(undefined, target);
  });
  next.addEventListener("click", () => {
    if (page >= pages) return;
    const target = page + 1;
    void refreshResearchPaper(undefined, target);
  });
  pager.append(previous, pageLabel, next);
  details.append(summary, scroll, pager);
  panel.append(heading, upgradeHint, status, balances, execution, details);
  host.before(panel);
  initialized = true;
  void refreshResearchPaper();
}
