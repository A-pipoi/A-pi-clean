// @ts-check
import { apiFetch } from "./api-client.mjs";
import { renderFinancialChart } from "./chart-loader.mjs";

/** @type {Record<string, {title:string; refresh:string; loading:string; warning:string; failed:string; empty:string; endpoint:string; rows:string; date:string; pcr:string; iv:string; count:string; tables:string}>} */
const COPY = {
  "zh-CN": {
    title: "Tushare 扩展数据核查",
    refresh: "刷新已存数据",
    loading: "读取本地覆盖报告…",
    warning: "抽样核查，非完整覆盖",
    failed: "报告不可用：请登录后重试，或重启后端加载新接口。",
    empty: "数据管道尚未初始化。",
    endpoint: "接口",
    rows: "已存原始版本行数",
    date: "行情日期",
    pcr: "成交量 PCR / 持仓 PCR",
    iv: "近月隐波诊断",
    count: "有效合约数",
    tables: "展开接口覆盖表",
  },
  "en-US": {
    title: "Tushare extension diagnostics",
    refresh: "Refresh stored data",
    loading: "Reading local coverage…",
    warning:
      "Real samples only, not full historical coverage or factor acceptance. No sealed changes or orders.",
    failed:
      "Report unavailable. Sign in and retry, or restart the backend to load this API.",
    empty: "Data pipeline not initialized.",
    endpoint: "Endpoint",
    rows: "Stored raw revision rows",
    date: "Quote date",
    pcr: "Volume PCR / open-interest PCR",
    iv: "Near-month IV diagnostics",
    count: "Valid contracts",
    tables: "Show endpoint coverage",
  },
  "fr-FR": {
    title: "Diagnostic des données Tushare",
    refresh: "Actualiser les données stockées",
    loading: "Lecture de la couverture locale…",
    warning:
      "Échantillons réels uniquement, sans validation de couverture historique ni de facteurs. Aucun ordre ni changement des échantillons scellés.",
    failed:
      "Rapport indisponible. Connectez-vous puis réessayez, ou redémarrez le serveur.",
    empty: "Le pipeline de données n’est pas initialisé.",
    endpoint: "Interface",
    rows: "Lignes brutes versionnées",
    date: "Date du cours",
    pcr: "PCR volume / positions ouvertes",
    iv: "Diagnostic de volatilité implicite",
    count: "Contrats valides",
    tables: "Afficher la couverture des interfaces",
  },
  "ja-JP": {
    title: "Tushare 拡張データ診断",
    refresh: "保存済みデータを更新",
    loading: "ローカルの網羅率を確認中…",
    warning:
      "実データの標本のみ。履歴全体や因子の検証完了を意味しません。封印標本の変更や注文は行いません。",
    failed:
      "レポートを取得できません。ログイン後に再試行するか、サーバーを再起動してください。",
    empty: "データ処理は未初期化です。",
    endpoint: "API",
    rows: "原データの保存行数",
    date: "相場日付",
    pcr: "出来高 PCR / 建玉 PCR",
    iv: "近月のインプライド・ボラティリティ診断",
    count: "有効な契約数",
    tables: "API 網羅状況を表示",
  },
};

/** @param {unknown} value @returns {Record<string, unknown>} */
function object(value) {
  return value && typeof value === "object" && !Array.isArray(value)
    ? /** @type {Record<string, unknown>} */ (value)
    : {};
}
/** @param {unknown} value */
function number(value) {
  return typeof value === "number" && Number.isFinite(value)
    ? String(Math.round(value * 10000) / 10000)
    : "—";
}

/** Split sparse observations instead of drawing a false line across missing years.
 * @param {{date:string,value:number}[]} points
 * @returns {{date:string,value:number}[][]} */
export function splitObservedWindows(points) {
  const ordered = [...points].sort((a, b) => a.date.localeCompare(b.date));
  /** @type {{date:string,value:number}[][]} */
  const windows = [];
  let previous = NaN;
  for (const point of ordered) {
    const compact = point.date.replaceAll("-", "");
    if (!/^\d{8}$/.test(compact) || !Number.isFinite(point.value)) continue;
    const current = Date.parse(
      `${compact.slice(0, 4)}-${compact.slice(4, 6)}-${compact.slice(6, 8)}T00:00:00Z`,
    );
    if (!Number.isFinite(current)) continue;
    if (!windows.length || current - previous > 14 * 86400000) windows.push([]);
    windows.at(-1)?.push(point);
    previous = current;
  }
  return windows;
}

/** @param {unknown} value */
export function summarizeHistoricalCoverage(value) {
  const report = object(value);
  const coverage = object(report.full_history_acceptance);
  const days = Array.isArray(coverage.coverage) ? coverage.coverage : null;
  const sessions =
    typeof coverage.sessions === "number" &&
    Number.isSafeInteger(coverage.sessions) &&
    coverage.sessions > 0
      ? coverage.sessions
      : null;
  const validDays =
    days !== null &&
    sessions !== null &&
    days.length === sessions &&
    days.every(
      (day) =>
        typeof object(day).date === "string" &&
        typeof object(day).complete === "boolean",
    ) &&
    new Set(days.map((day) => object(day).date)).size === sessions;
  const periods = object(report.fund_historical_layers).periods;
  const validPeriods =
    Array.isArray(periods) &&
    periods.length > 0 &&
    periods.every((period) =>
      Array.isArray(object(period).missing_price_symbols),
    );
  return {
    sessions,
    passedDays: validDays
      ? days.filter((day) => object(day).complete === true).length
      : null,
    missingFundObservations: validPeriods
      ? periods.reduce(
          (total, period) =>
            total +
            /** @type {unknown[]} */ (object(period).missing_price_symbols)
              .length,
          0,
        )
      : null,
  };
}

/** @param {HTMLElement} parent */
export function mountDataExtensionPanel(parent) {
  const section = document.createElement("section");
  section.className = "data-extension-panel panel";
  section.id = "dataExtensionPanel";
  const heading = document.createElement("h4");
  const warning = document.createElement("p");
  const state = document.createElement("p");
  state.setAttribute("role", "status");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "feature-store-panel__button";
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  const tableBox = document.createElement("div");
  tableBox.className = "data-extension-panel__table";
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const tbody = document.createElement("tbody");
  table.append(thead, tbody);
  tableBox.append(table);
  details.append(summary, tableBox);
  const seriesBox = document.createElement("div");
  seriesBox.className = "data-extension-panel__series";
  const pcrAnchor = document.createElement("div");
  const ivAnchor = document.createElement("div");
  const pcrBody = document.createElement("div");
  const ivBody = document.createElement("div");
  const pcrBlock = document.createElement("div");
  const ivBlock = document.createElement("div");
  const pcrTitle = document.createElement("h5");
  const pcrCoverage = document.createElement("p");
  const ivTitle = document.createElement("h5");
  const ivCoverage = document.createElement("p");
  pcrBlock.id = "extensionPcrBlock";
  ivBlock.id = "extensionIvBlock";
  pcrBody.className = "data-extension-chart-body";
  ivBody.className = "data-extension-chart-body";
  pcrCoverage.className = "data-extension-chart-meta";
  ivCoverage.className = "data-extension-chart-meta";
  pcrBody.append(pcrAnchor);
  ivBody.append(ivAnchor);
  pcrBlock.append(pcrTitle, pcrCoverage, pcrBody);
  ivBlock.append(ivTitle, ivCoverage, ivBody);
  const macroDetails = document.createElement("details");
  const macroSummary = document.createElement("summary");
  const macroTableBox = document.createElement("div");
  macroTableBox.className = "data-extension-panel__table";
  macroTableBox.tabIndex = 0;
  const macroHint = document.createElement("p");
  const macroTable = document.createElement("table");
  macroTableBox.append(macroTable);
  macroDetails.append(macroSummary, macroHint, macroTableBox);
  seriesBox.className =
    "data-extension-panel__series data-extension-panel__charts";
  pcrBlock.className = "data-extension-chart-card";
  ivBlock.className = "data-extension-chart-card";
  seriesBox.append(pcrBlock, ivBlock);
  const historicalDetails = document.createElement("details");
  historicalDetails.className = "data-extension-disclosure";
  macroDetails.className = "data-extension-disclosure";
  details.className = "data-extension-disclosure";
  const historicalSummary = document.createElement("summary");
  const historicalNote = document.createElement("p");
  historicalNote.className = "data-extension-hint";
  macroHint.className = "data-extension-hint";
  const icAnchor = document.createElement("div");
  const fundAnchor = document.createElement("div");
  const historicalStatus = document.createElement("p");
  historicalStatus.className = "data-extension-hint";
  historicalDetails.id = "historicalExperimentsPanel";
  icAnchor.id = "disclosureAlignmentIcChart";
  fundAnchor.id = "fundHistoricalLayersChart";
  historicalDetails.append(
    historicalSummary,
    historicalNote,
    historicalStatus,
    icAnchor,
    fundAnchor,
  );
  section.append(
    heading,
    warning,
    button,
    state,
    seriesBox,
    macroDetails,
    historicalDetails,
    details,
  );
  parent.append(section);
  /** @type {Record<string, unknown> | null} */
  let report = null;
  let failure = false;
  const text = () => COPY[document.documentElement.lang] || COPY["zh-CN"];
  function render() {
    const copy = text();
    heading.textContent = copy.title;
    warning.textContent = copy.warning;
    button.textContent = copy.refresh;
    summary.textContent = copy.tables;
    state.textContent = failure ? copy.failed : report ? "" : copy.empty;
    thead.replaceChildren();
    tbody.replaceChildren();
    macroTable.replaceChildren();
    const extra = {
      "zh-CN": [
        "成交量 / 持仓 PCR（无量纲，仅已存窗口）",
        "近月 IV 简单均值（%，按标的汇总，非波动率指数）",
        "宏观时间序列（报告期，不是可得日）",
        "指标",
        "报告期",
        "数值",
        "横向滚动查看完整数值；各指标单位尚未核验，请勿直接横向比较。",
      ],
      "en-US": [
        "Volume / open-interest PCR (unitless, stored window)",
        "Near-month mean IV (%, by underlying, not an index)",
        "Macro series (report periods, not availability dates)",
        "Metric",
        "Period",
        "Value",
        "Scroll horizontally for full values. Metric units are not verified; do not compare across metrics.",
      ],
      "fr-FR": [
        "PCR volume / positions (sans unité, fenêtre stockée)",
        "IV moyenne proche (%, par sous-jacent, pas un indice)",
        "Séries macro (périodes, non dates de disponibilité)",
        "Indicateur",
        "Période",
        "Valeur",
        "Défilez horizontalement pour lire les valeurs. Unités non vérifiées : ne comparez pas les indicateurs.",
      ],
      "ja-JP": [
        "出来高 / 建玉 PCR（無次元、保存期間のみ）",
        "近月 IV 単純平均（%、原資産別、指数ではありません）",
        "マクロ時系列（報告期、取得可能日ではありません）",
        "指標",
        "報告期",
        "値",
        "横スクロールで数値全体を表示。各指標の単位は未確認です。指標間を直接比較しないでください。",
      ],
    }[document.documentElement.lang] || [
      "PCR series",
      "Mean IV diagnostic",
      "Macro report periods",
      "Metric",
      "Period",
      "Value",
      "Scroll horizontally. Metric units are not verified.",
    ];
    pcrTitle.textContent = extra[0];
    ivTitle.textContent = extra[1];
    macroHint.textContent = extra[6];
    macroTableBox.setAttribute("aria-label", extra[2]);
    macroSummary.textContent = extra[2];
    const language = document.documentElement.lang;
    const historicalCopy = {
      "zh-CN": [
        "披露日期 IC 对照与基金历史分层",
        "仅为回顾性诊断：end_date 是错误对齐对照；不含交易成本、可交易性验收或历史 first_seen 证明，不能用于密封批准。",
        "已计算日期 / 季度",
        "公告日对齐",
        "报告期对齐（前视诊断）",
        "全历史覆盖尚未通过",
        "基金共识分层：持有63个交易日毛收益",
        "行情对账（交易日）",
        "基金缺价样本（逐季累计，非独立股票数）",
      ],
      "en-US": [
        "Disclosure IC comparison and historical fund layers",
        "Retrospective diagnostics only. end_date is a look-ahead control; no transaction costs, tradability acceptance or historical first-seen proof. Not eligible for sealed approval.",
        "Computed dates / quarters",
        "Announcement aligned",
        "Report-period aligned (look-ahead control)",
        "Full historical coverage not accepted",
        "Fund consensus: gross 63-session buy-and-hold",
        "Market reconciliation (sessions)",
        "Unpriced fund observations (across quarters, not unique stocks)",
      ],
      "fr-FR": [
        "IC de publication et strates historiques des fonds",
        "Diagnostic rétrospectif uniquement. end_date est un contrôle biaisé ; sans frais, preuve de négociabilité ni historique de première observation. Non admissible à la validation scellée.",
        "Dates / trimestres calculés",
        "Date de publication",
        "Période comptable (contrôle biaisé)",
        "Couverture historique non validée",
        "Consensus des fonds : rendement brut sur 63 séances",
        "Rapprochement du marché (séances)",
        "Observations sans cours (par trimestre, pas titres uniques)",
      ],
      "ja-JP": [
        "開示日IC比較と基金の過去層別",
        "回顧的診断のみ。end_dateは先読み対照です。取引費用・取引可能性・過去の初回観測証拠を含まず、封印検証の承認には使用できません。",
        "計算済み日付 / 四半期",
        "開示日基準",
        "報告期基準（先読み診断）",
        "全期間の網羅性は未承認",
        "基金コンセンサス：63取引日の費用前収益",
        "相場照合（取引日）",
        "価格欠損標本（四半期累計、固有銘柄数ではありません）",
      ],
    }[language] || [
      "Historical diagnostics",
      "Retrospective only; not sealed approval.",
      "Dates / quarters",
      "ann_date",
      "end_date",
      "Coverage not accepted",
      "Fund consensus: gross 63-session buy-and-hold",
      "Market reconciliation (sessions)",
      "Unpriced fund observations (across quarters, not unique stocks)",
    ];
    historicalSummary.textContent = historicalCopy[0];
    historicalNote.textContent = historicalCopy[1];
    const labels = document.createElement("tr");
    for (const label of [copy.endpoint, copy.rows]) {
      const cell = document.createElement("th");
      cell.scope = "col";
      cell.textContent = label;
      labels.append(cell);
    }
    thead.append(labels);
    if (!report) {
      pcrBlock.hidden = true;
      ivBlock.hidden = true;
      macroDetails.hidden = true;
      historicalDetails.hidden = true;
      return;
    }
    pcrBlock.hidden = false;
    historicalDetails.hidden = false;
    ivBlock.hidden = false;
    macroDetails.hidden = false;
    const counts = object(object(report.raw_store).counts);
    for (const [endpoint, count] of Object.entries(counts)) {
      const row = document.createElement("tr");
      for (const value of [endpoint, number(count)]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      }
      tbody.append(row);
    }
    const pcr = Array.isArray(report.option_pcr) ? report.option_pcr : [];
    const pcrDays = new Set(
      pcr
        .map((item) => object(item).date)
        .filter((day) => typeof day === "string"),
    ).size;
    /** @type {Record<string,string>} */
    const sparseCopy = {
      "zh-CN": `仅存 ${pcrDays} 个期权行情日；缺失区间断线显示，未通过完整历史覆盖验收。`,
      "en-US": `${pcrDays} option quote days stored. Missing spans are disconnected; full historical coverage is unverified.`,
      "fr-FR": `${pcrDays} séances d’options enregistrées. Les périodes manquantes sont disjointes ; la couverture historique reste non validée.`,
      "ja-JP": `保存済みオプション相場は${pcrDays}日分です。欠損期間は線をつながず、全期間の網羅性は未検証です。`,
    };
    pcrCoverage.textContent = sparseCopy[language] || sparseCopy["en-US"];
    ivCoverage.textContent = "\u00a0";
    const ic = object(report.disclosure_alignment_ic);
    const icRows = Array.isArray(ic.series) ? ic.series : [];
    const fund = object(report.fund_historical_layers);
    const fundRows = Array.isArray(fund.periods) ? fund.periods : [];
    const coverage = summarizeHistoricalCoverage(report);
    historicalStatus.textContent = `${historicalCopy[2]}: ${icRows.length} / ${fundRows.length} · ${historicalCopy[7]}: ${number(coverage.passedDays)} / ${number(coverage.sessions)} · ${historicalCopy[8]}: ${number(coverage.missingFundObservations)}${coverage.passedDays === null || coverage.passedDays !== coverage.sessions ? ` · ${historicalCopy[5]}` : ""}`;
    void renderFinancialChart(icAnchor, {
      kind: "line",
      title: historicalCopy[0],
      precision: 4,
      series: ["ann_date", "end_date"].map((basis, index) => ({
        name: historicalCopy[3 + index],
        dashed: index === 1,
        points: icRows.flatMap((value) => {
          const point = object(value),
            score = point[basis + "_ic"];
          return typeof point.date === "string" &&
            typeof score === "number" &&
            Number.isFinite(score)
            ? [{ date: point.date, value: score }]
            : [];
        }),
      })),
    });
    void renderFinancialChart(fundAnchor, {
      kind: "line",
      title: historicalCopy[6],
      precision: 4,
      series: [1, 2, 3, 4, 5].map((layer) => ({
        name: `Q${layer}`,
        points: fundRows.flatMap((value) => {
          const point = object(value);
          const groups = Array.isArray(point.layers) ? point.layers : [];
          const group = object(
            groups.find((item) => object(item).layer === layer),
          );
          const result = group.gross_buy_hold_return;
          return typeof point.label_date === "string" &&
            typeof result === "number" &&
            Number.isFinite(result)
            ? [{ date: point.label_date, value: result }]
            : [];
        }),
      })),
    });
    const latest = object(pcr.at(-1));
    const iv = Array.isArray(report.near_month_iv) ? report.near_month_iv : [];
    const valid = iv.filter((value) => typeof object(value).iv === "number");
    state.textContent = `${copy.date}: ${String(latest.date || "—")} · ${copy.pcr}: ${number(latest.volume_pcr)} / ${number(latest.oi_pcr)} · ${copy.iv} · ${copy.count}: ${valid.length}/${iv.length}`;
    void renderFinancialChart(pcrAnchor, {
      kind: "line",
      title: extra[0],
      precision: 4,
      quietZoom: true,
      series: ["volume_pcr", "oi_pcr"].flatMap((key, index) => {
        const points = pcr.flatMap((item) => {
          const row = object(item);
          return typeof row[key] === "number" &&
            Number.isFinite(row[key]) &&
            typeof row.date === "string"
            ? [{ date: row.date, value: row[key] }]
            : [];
        });
        const windows = splitObservedWindows(points);
        return windows.map((window) => ({
          name:
            windows.length === 1
              ? key
              : `${key} · ${window[0].date.slice(0, 7)}–${(window.at(-1)?.date || window[0].date).slice(0, 7)}`,
          color: index === 0 ? "#6856c9" : "#148359",
          points: window,
        }));
      }),
    });
    /** @type {Map<string,Map<string,number[]>>} */
    const grouped = new Map();
    for (const item of valid) {
      const row = object(item);
      if (
        typeof row.underlying !== "string" ||
        typeof row.date !== "string" ||
        typeof row.iv !== "number"
      )
        continue;
      if (!grouped.has(row.underlying)) grouped.set(row.underlying, new Map());
      const dates = grouped.get(row.underlying);
      if (!dates) continue;
      dates.set(row.date, [...(dates.get(row.date) || []), row.iv]);
    }
    void renderFinancialChart(ivAnchor, {
      kind: "line",
      title: extra[1],
      precision: 4,
      quietZoom: true,
      series: [...grouped].map(([name, dates]) => ({
        name,
        points: [...dates]
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([date, values]) => ({
            date,
            value: (100 * values.reduce((a, b) => a + b, 0)) / values.length,
          })),
      })),
    });
    const header = document.createElement("tr");
    for (const label of extra.slice(3, 6)) {
      const th = document.createElement("th");
      th.scope = "col";
      th.textContent = label;
      header.append(th);
    }
    const macroHead = document.createElement("thead");
    macroHead.append(header);
    macroTable.append(macroHead);
    const macroBody = document.createElement("tbody");
    macroTable.append(macroBody);
    for (const [endpoint, rows] of Object.entries(
      object(report.macro_series),
    )) {
      if (!Array.isArray(rows)) continue;
      for (const item of rows.slice(-100)) {
        const row = object(item);
        const period = String(row.date || row.month || row.quarter || "—");
        for (const [metric, value] of Object.entries(row)) {
          if (typeof value !== "number") continue;
          const tr = document.createElement("tr");
          for (const text of [`${endpoint}.${metric}`, period, number(value)]) {
            const td = document.createElement("td");
            td.textContent = text;
            tr.append(td);
          }
          macroBody.append(tr);
        }
      }
    }
  }
  async function refresh() {
    button.disabled = true;
    state.textContent = text().loading;
    try {
      const response = await apiFetch("/api/data/extensions", {
        cache: "no-store",
      });
      const payload = object(await response.json());
      if (!response.ok || !payload.ok) throw new Error("report_unavailable");
      report = payload.status === "not_initialized" ? null : payload;
      failure = false;
    } catch {
      report = null;
      failure = true;
    } finally {
      button.disabled = false;
      render();
    }
  }
  button.addEventListener("click", refresh);
  new MutationObserver(render).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["lang"],
  });
  render();
  return refresh;
}
