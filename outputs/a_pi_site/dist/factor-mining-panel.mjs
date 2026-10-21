import { appendSafeHtml, setSafeHtml } from "./safe-html.mjs";
import { apiFetch as fetch } from "./api-client.mjs";
import { requiredElement, toError } from "./runtime-utils.mjs";
import {
  initPortfolioArena,
  portfolioMarkup,
  updatePortfolioFactorOptions,
} from "./portfolio-arena.mjs";
/** @type {import('../types/services').FactorMiningPanelServices} */
let dependencies;
/** @type {import('../types/domain').FactorMiningSnapshot | null} */
let latestFactorMining = null;
let factorMiningWorking = false;
let initialized = false;
let researchPollTimer = 0;
/** @type {{model_id: string; version: string}[]} */
let externalDeepAvailable = [];
let externalDeepWorking = false;
let externalDeepUnconfirmed = false;

function researchButtons() {
  return [
    requiredElement("factorMineButton"),
    requiredElement("factorEvaluateButton"),
    requiredElement("factorTemplateCensusButton"),
    requiredElement("factorComposeButton"),
  ];
}

/** @param {import('../types/domain').MiningCensus} census */
export function miningCensusMarkup(census) {
  const translate = dependencies?.localeText || String;
  const label = (/** @type {string} */ source) =>
    escapeMarkup(translate(source));
  const number = (/** @type {number | null | undefined} */ value) =>
    typeof value === "number" && Number.isFinite(value)
      ? value.toFixed(3)
      : "—";
  const families = (census.family_ranking || [])
    .map(
      (row) =>
        `<li>${escapeMarkup(row.family)} · ICIR ${number(row.mean_icir)} · ${label("已评估 / 失败")} ${Number(row.evaluated)}/${Number(row.failed)}</li>`,
    )
    .join("");
  const rows = (census.templates || [])
    .map(
      (row) =>
        `<tr><th scope="row">${escapeMarkup(row.name || row.expression || "—")}</th><td>${escapeMarkup(row.category || "—")}</td><td>${number(row.mean_ic)}</td><td>${number(row.icir)}</td><td>${number(row.coverage)}</td><td>${Number(row.n_tested || 0)}</td><td>${escapeMarkup(row.error || translate(row.status === "evaluated" ? "已评估" : "未完成"))}</td></tr>`,
    )
    .join("");
  return `<details class="factor-evaluation-report"><summary>${label("模板普查报告")} · N_tested ${Number(census.n_tested || 0)}</summary><div class="factor-report-content"><p>${label("各表达式独立计数，汇总报告不重复计数；不使用密封样本。")}</p><ul>${families}</ul><div class="factor-report-table-wrap"><table class="factor-report-table"><thead><tr><th>${label("模板")}</th><th>${label("因子家族")}</th><th>Rank IC</th><th>ICIR</th><th>${label("覆盖率")}</th><th>N_tested</th><th>${label("状态 / 原因")}</th></tr></thead><tbody>${rows}</tbody></table></div></div></details>`;
}

/** @param {number} status @param {string} code @param {string | null} retryAfter */
export function factorMiningFailureFeedback(status, code, retryAfter) {
  /** @type {Record<string, string>} */
  const messages = {
    operation_already_active:
      "已有研究操作正在执行，首次准备数据可能需要数十分钟。请勿重复提交。",
    operation_global_capacity: "服务正在处理其他研究任务，请稍后重试。",
    backtest_already_active: "当前账户已有后台任务，请等待任务结束。",
  };
  const source = status === 429 ? messages[code] : undefined;
  const translate = dependencies?.localeText || ((value) => value);
  if (!source) return null;
  const seconds = Number(retryAfter);
  return (
    translate(source) +
    (Number.isFinite(seconds) && seconds > 0
      ? ` ${translate("建议等待秒数：")}${Math.ceil(seconds)}`
      : "")
  );
}

/** Poll reads only; an ambiguous response must never automatically resubmit. */
/** @param {string | null} jobId */
function pollResearchStatus(jobId = null) {
  window.clearTimeout(researchPollTimer);
  const initialRuns = new Set(
    (latestFactorMining?.runs || []).map((run) => run.run_id),
  );
  const deadline = Date.now() + 30 * 60 * 1000;
  const poll = async () => {
    const snapshot = await loadFactorMining(true);
    const job = snapshot?.latest_job;
    const release = () => {
      factorMiningWorking = false;
      researchButtons().forEach((button) => {
        button.disabled = false;
      });
    };
    if (
      jobId &&
      job?.job_id === jobId &&
      ["failed", "cancelled", "interrupted"].includes(job.status)
    ) {
      setText(
        "factorMiningFeedback",
        `${dependencies.localeText("后台研究已停止：")}${job.error || job.status}`,
      );
      release();
      return;
    }
    if (jobId && job?.job_id === jobId && job.status === "completed") {
      setText(
        "factorMiningFeedback",
        dependencies.localeText("研究结果已确认并写入台账。"),
      );
      release();
      return;
    }
    if (
      snapshot?.runs?.some(
        (/** @type {{run_id: string}} */ run) => !initialRuns.has(run.run_id),
      )
    ) {
      setText(
        "factorMiningFeedback",
        dependencies.localeText("研究结果已确认并写入台账。"),
      );
      release();
      return;
    }
    if (Date.now() < deadline)
      researchPollTimer = window.setTimeout(poll, 5000);
    else {
      setText(
        "factorMiningFeedback",
        dependencies.localeText(
          "自动查询已停止；结果尚未确认，请刷新台账后再决定是否重试。",
        ),
      );
      release();
    }
  };
  researchPollTimer = window.setTimeout(poll, 5000);
}

/** @type {import('../types/services').SharedServices['escapeHtml']} */
function escapeHtml(value) {
  return dependencies.escapeHtml(String(value ?? ""));
}

/** @type {import('../types/services').SharedServices['setText']} */
function setText(id, value) {
  dependencies.setText(id, value);
}

/** @param {unknown} value */
function escapeMarkup(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

/** @param {string} status */
export function factorMiningStatusLabel(status = "") {
  return (
    {
      awaiting_acceptance: "等待 sealed 验收",
      acceptance_claimed: "验收处理中 / 已占用一次机会",
      acceptance_passed: "至少一个因子通过",
      acceptance_needs_review: "未发现校正后显著因子",
      acceptance_failed: "验收失败，机会已记录",
      candidate: "候选 · 未通过 sealed",
      census_report: "模板普查报告",
      multi_seed_report: "多种子汇总报告",
      evaluation_in_progress: "结果写入中",
      evaluation_failed: "评估失败 · 已计数",
      accepted: "验收通过 · 待提升",
      "overfitting-suspect": "校正未通过",
      "duplicate-discovery": "与现有因子重复",
      rejected: "未通过",
    }[status] ||
    status ||
    "状态待定"
  );
}

/** @param {string} status */
export function factorMiningTagClass(status = "") {
  if (status === "accepted" || status === "acceptance_passed") return "is-pass";
  if (
    status === "overfitting-suspect" ||
    status === "acceptance_needs_review" ||
    status === "acceptance_claimed"
  )
    return "is-warn";
  if (
    status === "duplicate-discovery" ||
    status === "rejected" ||
    status === "acceptance_failed"
  )
    return "is-fail";
  return "";
}

/** @param {import('../types/domain').FactorCorrelationMatrix | undefined} report */
export function factorCorrelationMatrixMarkup(report, escape = escapeMarkup) {
  if (!report) return "";
  if (
    report.sealed_dates_used !== false ||
    report.scope !== "enabled_registry_plus_current_candidate_training_only" ||
    !Array.isArray(report.keys) ||
    !Array.isArray(report.matrix) ||
    !report.keys.length ||
    report.keys.length > 64 ||
    report.matrix.length !== report.keys.length ||
    report.matrix.some(
      (row) => !Array.isArray(row) || row.length !== report.keys.length,
    )
  )
    return '<p class="factor-report-muted">相关性矩阵暂不可展示：训练范围或矩阵结构未通过校验。</p>';
  const keys = report.keys;
  const label = (/** @type {string} */ key) =>
    escape(key === "__candidate__" ? "当前候选" : key);
  const count = (/** @type {unknown} */ value) =>
    typeof value === "number" && Number.isSafeInteger(value) && value >= 0
      ? String(value)
      : "—";
  const tables = /** @type {const} */ (["pearson", "spearman"])
    .map((metric) => {
      const title = metric === "pearson" ? "Pearson" : "Spearman";
      const rows = keys
        .map(
          (key, index) =>
            `<tr><th scope="row">${label(key)}</th>${keys
              .map((_, column) => {
                const cell = report.matrix[index][column];
                const value = cell?.[metric];
                const numeric =
                  typeof value === "number" &&
                  Number.isFinite(value) &&
                  Math.abs(value) <= 1;
                return `<td><span>${numeric ? value.toFixed(3) : "—"}</span><small>${count(cell?.observations)} 天 · ${count(cell?.paired_samples)} 对</small></td>`;
              })
              .join("")}</tr>`,
        )
        .join("");
      return `<details class="factor-matrix-section"><summary>${title} 相关性矩阵</summary><div class="factor-report-table-wrap factor-matrix-scroll" tabindex="0" role="region" aria-label="${title} 相关性矩阵，可横向滚动"><table class="factor-report-table factor-correlation-matrix"><caption>${title} · 每日截面相关性的平均值</caption><thead><tr><th scope="col">因子</th>${keys.map((key) => `<th scope="col">${label(key)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div></details>`;
    })
    .join("");
  return `<section class="factor-report-cross"><h4>训练集因子相关性</h4><p>${escape(report.start || "—")} 至 ${escape(report.end || "—")} · 当前候选与已启用注册因子；不是全部历史因子库。</p><p>每日至少 3 对有效样本；每格注明有效天数与累计样本对数。常量或缺失样本显示 —，不补零。仅供训练诊断，不代表密封验收。</p>${tables}</section>`;
}

/** @param {import('../types/domain').FactorCandidate} candidate
 * @param {(value: unknown) => string} escape
 * @param {'training' | 'sealed'} phase
 */
export function factorEvaluationReportMarkup(
  candidate,
  escape = escapeMarkup,
  phase = "sealed",
) {
  const report = candidate && candidate.evaluation;
  if (!report || typeof report !== "object") return "";
  const horizons = report.horizons || {};
  const decisionMarkup =
    phase === "sealed" && typeof candidate.ic_passed === "boolean"
      ? `<section class="factor-report-cross"><h4>IC 与组合联合验收</h4><p>IC 校正：${candidate.ic_passed ? "通过" : "未通过"} · 训练组合门槛：${candidate.portfolio_passed ? "通过" : "未通过"}</p><p>${candidate.metric_conflict ? "两种口径存在冲突，已留痕；" : ""}${escape(candidate.decision_note || "")}</p></section>`
      : "";
  const format =
    /** @param {unknown} value @param {number} digits */
    (value, digits = 4) =>
      value == null || !Number.isFinite(Number(value))
        ? "—"
        : Number(value).toFixed(digits);
  const formatP =
    /** @param {unknown} value */
    (value) =>
      value == null || !Number.isFinite(Number(value))
        ? "—"
        : Number(value).toExponential(2);
  const rows = Array.from({ length: 20 }, (_, index) => String(index + 1))
    .filter((key) => horizons[key])
    .map((key) => {
      const item = horizons[key];
      const hitRate =
        item.monthly_hit_rate == null
          ? "—"
          : (Number(item.monthly_hit_rate) * 100).toFixed(1) + "%";
      return (
        "<tr><th>" +
        key +
        '日</th><td class="num">' +
        format(item.mean_ic) +
        '</td><td class="num">' +
        format(item.normal_ic && item.normal_ic.mean_ic) +
        '</td><td class="num">' +
        format(item.icir, 3) +
        '</td><td class="num">' +
        hitRate +
        '</td><td class="num">' +
        format(item.long_short_return, 3) +
        '</td><td class="num">' +
        formatP(item.raw_p_value) +
        '</td><td class="num">' +
        formatP(item.dsr_p_value) +
        '</td><td class="num">' +
        formatP(phase === "training" ? null : candidate.bh_adjusted_p_value) +
        "</td></tr>"
      );
    })
    .join("");
  const fiveDay = horizons["5"] || {};
  const curve = report.ic_decay_curve;
  const curvePoints = (curve?.curve || []).filter(
    (point) =>
      Number.isFinite(point.mean_ic) &&
      point.horizon >= 1 &&
      point.horizon <= 20,
  );
  const amplitude = Math.max(
    0.001,
    ...curvePoints.map((point) => Math.abs(Number(point.mean_ic))),
  );
  const curveGraphic =
    curvePoints.length > 1
      ? `<svg class="factor-ic-curve" viewBox="0 0 360 120" role="img" aria-label="1–20 日平均 IC 衰减曲线"><path d="M 12 60 H 348" class="factor-ic-baseline"/><polyline fill="none" stroke="currentColor" stroke-width="2" points="${curvePoints.map((point) => `${12 + ((point.horizon - 1) * 336) / 19},${60 - (Number(point.mean_ic) / amplitude) * 48}`).join(" ")}"/></svg>`
      : "";
  const curveMarkup = curve
    ? `<section class="factor-report-cross"><h4>1–20 日 IC 衰减</h4>${curveGraphic}<p>${curve.half_life != null ? `已观测半衰期 ${Number(curve.half_life)} 日` : curve.status === "right_censored" ? "观测范围内尚未达到半衰点，不做外推" : "样本不足，无法估计半衰期"}</p><p>半衰期从绝对 IC 峰值之后计算；完整期限数据见上表。</p></section>`
    : "";
  const registry = report.registry_diagnostics;
  const layersMarkup = Object.entries(report.layered_diagnostics || {})
    .map(([count, diagnostic]) => {
      const last = diagnostic.curve?.at(-1);
      return `<section class="factor-report-cross"><h4>${escape(count)} 层训练集理论检验</h4><p>样本 ${Number(diagnostic.observations || 0)} 段 · 缺失跳过 ${Number(diagnostic.skipped_blocks || 0)} 段 · 层序相关 ${format(diagnostic.monotonicity_spearman)}</p>${
        diagnostic.status === "available"
          ? `<div class="factor-report-table-wrap"><table class="factor-report-table"><thead><tr><th>成本情景</th><th>期末理论多空净值</th></tr></thead><tbody><tr><th>未扣成本</th><td>${format(last?.long_short_gross_nav)}</td></tr>${Object.entries(
              last?.long_short_net_nav || {},
            )
              .map(
                ([rate, nav]) =>
                  `<tr><th>日单边换手 ${format(Number(rate) * 100, 0)}%</th><td>${format(nav)}</td></tr>`,
              )
              .join("")}</tbody></table></div>`
          : "<p>样本不足，不展示模拟收益。</p>"
      }<p>次日开盘、非重叠持有；成本情景不等于真实换手。理论多空不代表 A 股可成交回测，未模拟融券、涨跌停、最小佣金与市场冲击。</p></section>`;
    })
    .join("");
  const registryMarkup = registry
    ? `<section class="factor-report-cross"><h4>训练集增量诊断</h4><p>与已启用因子最大相关性 ${format(registry.max_corr_with_registry, 3)} · 残差 Rank IC ${format(registry.incremental_rank_ic)} · ${Number(registry.incremental_observations || 0)} 期</p><div class="factor-report-table-wrap"><table class="factor-report-table"><thead><tr><th>已有因子</th><th>Pearson</th><th>Spearman</th><th>样本</th></tr></thead><tbody>${(registry.correlations || []).map((item) => `<tr><th>${escape(item.factor_key)}</th><td>${format(item.pearson)}</td><td>${format(item.spearman)}</td><td>${Number(item.observations || 0)}</td></tr>`).join("") || '<tr><td colspan="4">没有可对齐的注册表样本。</td></tr>'}</tbody></table></div><p>仅训练集诊断，不代表封存验收或交易收益。</p></section>`
    : "";
  const decay = fiveDay.decay_monitor;
  const decayMarkup = decay
    ? `<section class="factor-report-cross"><h4>IC 衰减监控（已保存研究样本）</h4><p>${escape(decay.status === "alert" ? "持续衰减报警：请人工复核" : decay.status === "unproven_baseline" ? "历史基线非正，不能宣称因子有效" : decay.status === "insufficient_data" ? `观测不足 ${decay.observations ?? 0}/${decay.required ?? 80}` : "当前保存窗口未触发持续衰减报警")} · 截至 ${escape(decay.as_of || "—")}</p><p>20 日滚动 IC 对比不重叠的前段 60 日基线，持续三次才报警；不等于实时监控，不自动停用策略。</p></section>`
    : "";
  const sizeSlice =
    (fiveDay.environment_slices && fiveDay.environment_slices.market_cap) || {};
  const sizeGroups = sizeSlice.groups || {};
  /** @type {Record<string,string>} */
  const sizeLabels = { small: "小盘", mid: "中盘", large: "大盘" };
  const sizeMarkup = Object.keys(sizeGroups).length
    ? '<div class="factor-report-sizes">' +
      Object.entries(sizeGroups)
        .map(
          ([name, item]) =>
            "<span>" +
            escape(sizeLabels[name] || name) +
            " · IC " +
            format(item.mean_rank_ic) +
            " · " +
            Number(item.observations || 0) +
            " 期</span>",
        )
        .join("") +
      "</div>"
    : '<p class="factor-report-muted">' +
      escape(sizeSlice.reason || "市值分组样本不足。") +
      "</p>";
  const industry =
    (fiveDay.environment_slices && fiveDay.environment_slices.industry) || {};
  const industryGroups = industry.groups || {};
  const industryMarkup = Object.keys(industryGroups).length
    ? '<div class="factor-report-sizes">' +
      Object.entries(industryGroups)
        .map(
          ([name, item]) =>
            "<span>" +
            escape(name) +
            " · IC " +
            format(item.mean_rank_ic) +
            " · " +
            Number(item.observations || 0) +
            " 期</span>",
        )
        .join("") +
      "</div>"
    : '<p class="factor-report-muted">' +
      escape(
        industry.reason ||
          (industry.status === "available"
            ? "已按通过验证的 PIT 行业时间分组。"
            : "行业 PIT 数据不可用。"),
      ) +
      "</p>";
  const annual =
    (report.annual_distribution_drift &&
      report.annual_distribution_drift.years) ||
    {};
  const annualRows = Object.entries(annual)
    .map(([year, item]) => {
      const q = item.quantiles || {};
      const drift = item.drift_vs_previous_year || {};
      return (
        "<tr><th>" +
        escape(year) +
        '</th><td class="num">' +
        Number(item.observations || 0) +
        '</td><td class="num">' +
        format(q.p10) +
        '</td><td class="num">' +
        format(q.p50) +
        '</td><td class="num">' +
        format(q.p90) +
        '</td><td class="num">' +
        (item.compared_to ? format(drift.p50) : "—") +
        "</td></tr>"
      );
    })
    .join("");
  const coverage =
    report.coverage == null
      ? "—"
      : (Number(report.coverage) * 100).toFixed(1) + "%";
  const comparison = candidate.cross_universe_reference;
  let comparisonMarkup = "";
  if (comparison && typeof comparison === "object") {
    const crossFiveDay =
      (comparison.horizons && comparison.horizons["5"]) || {};
    comparisonMarkup =
      comparison.status === "available"
        ? '<section class="factor-report-cross"><h4>中证500跨宇宙参考（不参与筛选或验收）</h4><p>' +
          escape(comparison.period_start || "—") +
          " 至 " +
          escape(comparison.period_end || "—") +
          " · 5 日 Rank IC " +
          format(crossFiveDay.mean_rank_ic) +
          " · ICIR " +
          format(crossFiveDay.icir, 3) +
          " · " +
          Number(crossFiveDay.observations || 0) +
          " 期</p></section>"
        : '<section class="factor-report-cross"><h4>中证500跨宇宙参考</h4><p>本次未能计算：' +
          escape(comparison.reason || "数据暂不可用") +
          "。该参考不影响沪深300验收结论。</p></section>";
  }
  return (
    '<details class="factor-evaluation-report"><summary>' +
    escape(candidate.expression || "候选因子") +
    (phase === "training" ? " · 查看训练报告" : " · 查看密封验收报告") +
    '</summary><div class="factor-report-content"><p class="factor-report-muted">' +
    (phase === "training"
      ? "仅训练样本；不代表密封验收通过。BH q 仅在独立验收报告中展示。"
      : "密封验收样本；请结合候选验收状态判断，不自动提升或交易。") +
    '</p><div class="factor-report-metrics"><span>覆盖率 ' +
    coverage +
    "</span><span>Top 20% 换手 " +
    format(report.top_quintile_turnover, 3) +
    "</span><span>因子自相关 " +
    format(report.factor_autocorrelation, 3) +
    "</span><span>N_tested " +
    Number(report.n_tested || 0) +
    '</span></div><div class="factor-report-table-wrap"><table class="factor-report-table"><thead><tr><th>期限</th><th class="num">Rank IC</th><th class="num">Normal IC</th><th class="num">ICIR</th><th class="num">月度胜率</th><th class="num">多空收益</th><th class="num">原始 p</th><th class="num">DSR p</th><th class="num">BH q</th></tr></thead><tbody>' +
    (rows || '<tr><td colspan="9">当前窗口没有足够的前向收益样本。</td></tr>') +
    "</tbody></table></div>" +
    decisionMarkup +
    comparisonMarkup +
    '<div class="factor-report-environments"><section><h4>5 日市值分层</h4>' +
    sizeMarkup +
    "</section><section><h4>行业切片</h4>" +
    industryMarkup +
    "</section></div>" +
    decayMarkup +
    (curveMarkup && report.portfolio
      ? '<div class="portfolio-ic-layout">' +
        curveMarkup +
        "<div>" +
        portfolioMarkup(report.portfolio) +
        "</div></div>"
      : curveMarkup + portfolioMarkup(report.portfolio)) +
    registryMarkup +
    factorCorrelationMatrixMarkup(report.registry_correlation_matrix, escape) +
    layersMarkup +
    '<section class="factor-report-annual"><h4>年度分布漂移</h4><div class="factor-report-table-wrap"><table class="factor-report-table"><thead><tr><th>年份</th><th class="num">样本</th><th class="num">p10</th><th class="num">p50</th><th class="num">p90</th><th class="num">p50 年变动</th></tr></thead><tbody>' +
    (annualRows || '<tr><td colspan="6">暂无年度分布数据。</td></tr>') +
    '</tbody></table></div></section><p class="factor-report-muted">' +
    escape(report.note || "") +
    "</p></div></details>"
  );
}

/** @param {import('../types/domain').FactorMiningSnapshot} data */
function renderFactorMining(data) {
  latestFactorMining = data;
  updatePortfolioFactorOptions(data);
  const composeHost = requiredElement("factorComposeFactors");
  const selected = new Set(selectedCompositionKeys());
  const compositionFactors = data.composition_factors || [];
  const signature = JSON.stringify(compositionFactors);
  if (composeHost.dataset.signature !== signature) {
    setSafeHtml(composeHost, compositionOptionsMarkup(compositionFactors));
    composeHost.dataset.signature = signature;
    composeHost.querySelectorAll("input").forEach((input) => {
      if (input instanceof HTMLInputElement)
        input.checked = selected.has(input.value);
    });
  }
  updateCompositionState();
  const templateSelect = requiredElement("factorTemplateSelect");
  if (templateSelect && templateSelect.dataset.loaded !== "true") {
    const options = (data.expression_templates || [])
      .map(
        (item, index) =>
          `<option value="${index}">${escapeHtml(item.name)} · ${escapeHtml(item.category)}</option>`,
      )
      .join("");
    appendSafeHtml(templateSelect, options);
    templateSelect.dataset.loaded = "true";
  }
  const sealed = data.sealed_test || {};
  const acceptance = data.sealed_factor_acceptance;
  const universe = data.research_universe || {};
  setText(
    "factorMiningUniverseName",
    `固定验收宇宙 · ${universe.index_name || "沪深300历史成分"}（${universe.index_code || "000300.SH"}）`,
  );
  const universeDetails = universe.snapshot_count
    ? `按历史月度快照 ${universe.snapshot_start || "—"} 至 ${universe.snapshot_end || "—"} 逐日取样 · 上市满 ${universe.min_listing_days ?? 60} 日 · ${universe.sample_symbol_count ?? "—"} 只进入样本。中证500仅作报告参考，不参与筛选或验收。`
    : `首次创建密封快照时加载沪深300历史月度成分 · 上市满 ${universe.min_listing_days ?? 60} 日 · 中证500仅作报告参考，不参与筛选或验收。`;
  setText("factorMiningUniverseDetails", universeDetails);
  const sealedLabel = sealed.sealed
    ? `#${sealed.id} · ${sealed.sealed_start || "—"} → ${sealed.sealed_end || "—"}`
    : "尚未创建";
  setText(
    "factorMiningStatus",
    sealed.sealed
      ? acceptance
        ? factorMiningStatusLabel(acceptance.status)
        : "密封窗口已就绪"
      : "等待密封快照",
  );
  setText(
    "factorMiningTrainRange",
    sealed.sealed_start
      ? `≤ ${sealed.sealed_start} 前一交易日`
      : "创建快照后确定",
  );
  setText("factorMiningSealedRange", sealedLabel);
  setText(
    "factorMiningTestedTotal",
    String(data.sealed_tested_individuals ?? 0),
  );
  setText(
    "factorMiningAcceptanceGate",
    acceptance
      ? "本周期已打开，不可重试"
      : sealed.sealed
        ? "每周期仅一次"
        : "创建快照后开放",
  );
  const pit = data.pit_quality || {};
  const financialLabel = !sealed.sealed
    ? "财务 PIT 待首次封存"
    : pit.financial === "available"
      ? "财务 PIT 可用"
      : "财务 PIT 暂无覆盖";
  const sizeLabel =
    pit.size === "verified"
      ? `市值 PIT 可用${pit.size_coverage == null ? "" : ` ${Math.round(Number(pit.size_coverage) * 100)}%`}`
      : !sealed.sealed
        ? "市值 PIT 待首次封存"
        : "市值中性化未开放";
  const industryLabel =
    pit.industry === "verified"
      ? "行业 PIT 已验证"
      : "行业中性化锁定（来源无可信可得时间）";
  setText(
    "factorPitQuality",
    `PIT 质量：${financialLabel} · ${sizeLabel} · ${industryLabel}。财务/收盘数据从下一交易日生效。`,
  );

  const runsHost = requiredElement("factorMiningRuns");
  if (runsHost) {
    const runs = data.runs || [];
    setSafeHtml(
      runsHost,
      runs.length
        ? runs
            .slice(0, 8)
            .map((run) => {
              const result = run.result || {};
              const candidates = result.candidates || [];
              const evalRows = (acceptance?.result?.included_runs || []).some(
                (item) => item.run_id === run.run_id,
              )
                ? acceptance?.result?.candidates || []
                : [];
              const canAccept =
                Number(sealed.id) === Number(run.sealed_test_id) &&
                !acceptance &&
                candidates.length > 0;
              let candidateMarkup = candidates
                .map((candidate) => {
                  const evaluated = evalRows.find(
                    (item) =>
                      item.factor_key === candidate.factor_key ||
                      item.factor_keys?.includes(candidate.factor_key || ""),
                  );
                  const status = evaluated?.status || "candidate";
                  const ic =
                    candidate.mean_rank_ic == null
                      ? "—"
                      : Number(candidate.mean_rank_ic).toFixed(4);
                  const dsr =
                    evaluated?.dsr_p_value == null
                      ? ""
                      : `DSR p ${Number(evaluated.dsr_p_value).toExponential(2)} · BH ${Number(evaluated.bh_adjusted_p_value).toExponential(2)}`;
                  return `<div class="factor-mining-candidate"><strong><span class="factor-mining-tag ${factorMiningTagClass(status)}" title="${escapeHtml(factorMiningStatusLabel(status))}">${escapeHtml(factorMiningStatusLabel(status))}</span> ${escapeHtml(candidate.factor_key || "表达式候选")}</strong><code>${escapeHtml(candidate.expression || "")}</code><div class="factor-mining-row-meta"><span>训练 Rank IC ${ic}</span><span>适应度 ${candidate.fitness == null ? "—" : Number(candidate.fitness).toFixed(4)}</span><span>${escapeHtml(dsr || `${candidate.nodes ?? "—"} 节点 · 深度 ${candidate.depth ?? "—"}`)}</span></div>${factorEvaluationReportMarkup(candidate, escapeHtml, "training")}</div>`;
                })
                .join("");
              if (acceptance && acceptance.run_id === run.run_id)
                candidateMarkup += evalRows
                  .map((candidate) =>
                    factorEvaluationReportMarkup(candidate, escapeHtml),
                  )
                  .join("");
              const action = canAccept
                ? `<button type="button" data-factor-accept="${escapeHtml(run.run_id)}" ${factorMiningWorking ? "disabled" : ""}>验收 sealed 窗口全部批次</button>`
                : "";
              const reportMarkup = result.census
                ? miningCensusMarkup(result.census)
                : result.multi_seed
                  ? `<p>${escapeHtml(dependencies.localeText("多种子汇总报告"))} · ${result.multi_seed.seeds.map(Number).join(" / ")} · N_tested ${Number(result.multi_seed.n_tested)} · ${escapeHtml(dependencies.localeText("汇总报告不重复计数。"))}</p>`
                  : result.composition
                    ? compositionResultMarkup(result.composition)
                    : "";
              return `<article class="factor-mining-row"><div class="factor-mining-row-main"><strong>批次 ${escapeHtml(run.run_id.slice(0, 10))} <span class="factor-mining-tag ${factorMiningTagClass(run.status)}" title="${escapeHtml(factorMiningStatusLabel(run.status))}">${escapeHtml(factorMiningStatusLabel(run.status))}</span></strong><div class="factor-mining-row-meta"><span>N_tested ${Number(run.n_tested || 0)}</span><span>seed ${result.seed ?? "—"}</span><span>训练至 ${escapeHtml(result.training_end || "—")}</span><span>快照 #${escapeHtml(run.sealed_test_id)}</span></div>${reportMarkup}${candidateMarkup || (reportMarkup ? "" : '<p class="factor-mining-empty">该批次没有通过训练阶段的候选。</p>')}</div>${action}</article>`;
            })
            .join("")
        : '<div class="factor-mining-empty">暂无挖掘批次。可先评估表达式，或运行遗传搜索。</div>',
    );
  }

  const libraryHost = requiredElement("factorExpressionLibrary");
  if (libraryHost) {
    const factors = data.factor_library || [];
    setSafeHtml(
      libraryHost,
      factors.length
        ? factors
            .slice(0, 30)
            .map((factor) => {
              const report = factor.metadata?.acceptance || {};
              const training = factor.metadata?.training || {};
              const pitDependencySet = new Set([
                "pe",
                "pb",
                "roe",
                "netprofit_yoy",
                "tr_yoy",
                "debt_to_assets",
                "market_cap",
                "float_market_cap",
                "industry",
              ]);
              const pitBlocked = (training.dependencies || []).some((item) =>
                pitDependencySet.has(item),
              );
              const canPromote =
                factor.status === "accepted" &&
                !data.promotion_block_reason &&
                !pitBlocked;
              const promote = canPromote
                ? `<button type="button" data-factor-promote="${escapeHtml(factor.key)}" ${factorMiningWorking ? "disabled" : ""}>提升到策略管线</button>`
                : "";
              const detailBase =
                report.bh_adjusted_p_value == null
                  ? `${training.mean_rank_ic == null ? "等待验收" : `训练 Rank IC ${Number(training.mean_rank_ic).toFixed(4)}`}`
                  : `校正 p ${Number(report.bh_adjusted_p_value).toExponential(2)} · sealed #${factor.sealed_test_id}`;
              const detail = pitBlocked
                ? `${detailBase} · PIT 依赖仅供研究，尚未接入策略/纸面运行时`
                : detailBase;
              return `<article class="factor-mining-row"><div class="factor-mining-row-main"><strong>${escapeHtml(factor.name)} <span class="factor-mining-tag ${factorMiningTagClass(factor.status || "")}" title="${escapeHtml(factorMiningStatusLabel(factor.status || ""))}">${escapeHtml(factorMiningStatusLabel(factor.status || ""))}</span></strong><code>${escapeHtml(factor.expression)}</code><div class="factor-mining-row-meta"><span>${escapeHtml(detail)}</span><span>${escapeHtml(factor.data_version || "")}</span></div>${factorEvaluationReportMarkup(training, escapeHtml, "training")}</div>${promote}</article>`;
            })
            .join("")
        : '<div class="factor-mining-empty">表达式因子在训练评估后进入候选库；未通过密封验收的候选不会进入策略。</div>',
    );
  }
  const feedback = requiredElement("factorMiningFeedback");
  if (
    feedback &&
    data.promotion_block_reason &&
    (data.factor_library || []).some((item) => item.status === "accepted")
  ) {
    feedback.textContent = `${data.promotion_block_reason}。已通过因子保留在研究库中，不会自动改变纸面策略。`;
  }
}

/** @param {boolean} quiet */
export async function loadFactorMining(quiet = false) {
  if (!dependencies) return;
  try {
    const response = await fetch("/api/factor-mining", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok || !data.ok)
      throw new Error(data.error || `HTTP ${response.status}`);
    renderFactorMining(data);
    requiredElement("factorMiningCancelButton").hidden = !data.active_job;
    requiredElement("factorMiningCancelButton").disabled =
      data.active_job?.status === "cancelling";
    if (data.active_job) {
      const job = data.active_job;
      setText(
        "factorMiningFeedback",
        job.status === "cancelling"
          ? dependencies.localeText(
              "已请求取消，等待安全检查点；已消耗配额不退回。",
            )
          : `${dependencies.localeText("后台研究进行中：")}${dependencies.localeText(job.stage)} · ${job.progress_current ?? 0}/${job.progress_total ?? "—"} · ${job.requested_start || ""} — ${job.requested_end || ""}`,
      );
      researchButtons().forEach((button) => {
        button.disabled = true;
      });
      if (!quiet) pollResearchStatus(job.job_id);
    } else if (!factorMiningWorking) {
      researchButtons().forEach((button) => {
        button.disabled = false;
      });
    }
    updateCompositionState();
    return data;
  } catch (errorCaught) {
    const error = toError(errorCaught);
    if (!quiet) {
      setText("factorMiningStatus", "研究接口不可用");
      setText("factorMiningFeedback", `无法读取因子挖掘台账：${error.message}`);
    }
  }
}

/** @param {string} path @param {Record<string, unknown>} payload @param {string} busyLabel */
/** @param {string} path @param {object} payload @param {EventTarget | null} button @param {string} busyLabel */
async function submitFactorMining(path, payload, button, busyLabel) {
  if (!(button instanceof HTMLElement) || factorMiningWorking) return;
  factorMiningWorking = true;
  const buttons = researchButtons();
  const previous = button?.textContent;
  buttons.forEach((item) => {
    item.disabled = true;
  });
  if (button) button.textContent = busyLabel;
  let responseReceived = false;
  let backgroundJob = false;
  setText(
    "factorMiningFeedback",
    "正在使用固定数据快照运行研究，请不要关闭此页面…",
  );
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    responseReceived = true;
    if (!response.ok || !data.ok) {
      const feedback = factorMiningFailureFeedback(
        response.status,
        data.code || data.error,
        response.headers.get("Retry-After"),
      );
      if (feedback) {
        setText("factorMiningFeedback", feedback);
        dependencies.showToast(feedback);
        if (
          data.error === "operation_already_active" ||
          data.code === "operation_already_active"
        )
          pollResearchStatus();
        return;
      }
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    if (response.status === 202 && data.job_id) {
      setText(
        "factorMiningFeedback",
        dependencies.localeText("研究已进入后台，正在准备数据；请勿重复提交。"),
      );
      backgroundJob = true;
      pollResearchStatus(data.job_id);
      return;
    }
    renderFactorMining(data);
    setText(
      "factorMiningFeedback",
      `已保存研究批次 ${data.run?.run_id?.slice(0, 10) || ""} · N_tested ${data.run?.n_tested ?? "—"}。尚未打开 sealed 测试集。`,
    );
    dependencies.showToast("因子研究已完成并写入台账");
  } catch (errorCaught) {
    const error = toError(errorCaught);
    if (!responseReceived || error instanceof SyntaxError) {
      const message = dependencies.localeText(
        "请求结果尚未确认，服务端可能仍在执行。正在查询研究状态，请勿重复提交。",
      );
      setText("factorMiningFeedback", message);
      dependencies.showToast(message);
      pollResearchStatus();
    } else {
      setText("factorMiningFeedback", `因子研究未完成：${error.message}`);
      dependencies.showToast(`因子研究失败 · ${error.message}`);
    }
  } finally {
    if (!backgroundJob) factorMiningWorking = false;
    buttons.forEach((item) => {
      item.disabled = backgroundJob;
    });
    if (button && previous) button.textContent = previous;
    await loadFactorMining(true);
    updateCompositionState();
  }
}

/** @param {{key: string; name?: string}[]} factors */
export function compositionOptionsMarkup(factors) {
  return (
    factors
      .map(
        (factor) =>
          `<label class="factor-compose-option"><input type="checkbox" value="${escapeMarkup(factor.key)}" data-factor-compose-key><span>${escapeMarkup(factor.name || factor.key)}</span></label>`,
      )
      .join("") ||
    '<p class="factor-expression-help">没有已启用且支持组合的因子，请先检查因子研究注册表。</p>'
  );
}

/** @param {{weighting?: string; weights?: Record<string, number>}} composition */
export function compositionResultMarkup(composition) {
  const rows = Object.entries(composition.weights || {})
    .map(
      ([key, value]) =>
        `<tr><th scope="row">${escapeMarkup(key)}</th><td>${typeof value === "number" && Number.isFinite(value) ? value.toFixed(4) : "—"}</td></tr>`,
    )
    .join("");
  return `<details class="factor-evaluation-report"><summary>组合权重 · ${composition.weighting === "icir" ? "训练 ICIR" : "等权"}</summary><div class="factor-report-content"><p class="factor-report-muted">成分先做截面标准化，权重按绝对值之和归一化；负权重表示反向。仅训练研究，不代表可交易或验收通过。</p><div class="factor-report-table-wrap"><table class="factor-report-table"><thead><tr><th>成分因子</th><th>训练权重</th></tr></thead><tbody>${rows || '<tr><td colspan="2">本批次未保存组合权重。</td></tr>'}</tbody></table></div></div></details>`;
}

function selectedCompositionKeys() {
  return Array.from(
    requiredElement("factorComposeFactors").querySelectorAll("input:checked"),
  )
    .filter((input) => input instanceof HTMLInputElement)
    .map((input) => /** @type {HTMLInputElement} */ (input).value);
}

function updateCompositionState() {
  const count = selectedCompositionKeys().length;
  setText(
    "factorComposeStatus",
    count >= 2 && count <= 8
      ? `已选 ${count} 个，预计消耗 ${count + 1} 次研究。`
      : `已选 ${count} 个，请选择 2–8 个因子。`,
  );
  requiredElement("factorComposeButton").disabled =
    count < 2 ||
    count > 8 ||
    factorMiningWorking ||
    Boolean(latestFactorMining?.active_job);
  requiredElement("factorComposeGroup").setAttribute(
    "aria-invalid",
    String(count > 8),
  );
}

function bindEvents() {
  requiredElement("factorDeepRefreshButton").addEventListener("click", () => {
    void loadExternalDeep();
  });
  requiredElement("factorDeepCandidate").addEventListener(
    "change",
    updateExternalDeepControls,
  );
  requiredElement("factorDeepImportButton").addEventListener("click", () => {
    void importExternalDeep();
  });
  requiredElement("factorComposeFactors").addEventListener(
    "change",
    updateCompositionState,
  );
  requiredElement("factorComposeButton").addEventListener("click", (event) => {
    const keys = selectedCompositionKeys();
    if (keys.length < 2 || keys.length > 8) {
      updateCompositionState();
      requiredElement("factorComposeStatus").focus();
      return;
    }
    void submitFactorMining(
      "/api/factor-mining/evaluate",
      {
        mode: "compose",
        factor_keys: keys,
        weighting: requiredElement("factorComposeWeighting").value,
      },
      event.currentTarget,
      "组合评估中…",
    );
  });
  requiredElement("factorTemplateCensusButton").addEventListener(
    "click",
    (event) => {
      void submitFactorMining(
        "/api/factor-mining/evaluate",
        { mode: "template_batch" },
        event.currentTarget,
        dependencies.localeText("模板普查进行中…"),
      );
    },
  );
  requiredElement("factorMiningCancelButton")?.addEventListener(
    "click",
    async () => {
      const job = latestFactorMining?.active_job;
      if (!job) return;
      try {
        const response = await fetch("/api/factor-mining/cancel", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ job_id: job.job_id }),
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        setText(
          "factorMiningFeedback",
          dependencies.localeText(
            "已请求取消，等待安全检查点；已消耗配额不退回。",
          ),
        );
      } catch (errorCaught) {
        dependencies.showToast(
          `${dependencies.localeText("取消请求未确认：")}${toError(errorCaught).message}`,
        );
      }
    },
  );
  requiredElement("factorMineButton")?.addEventListener("click", (event) => {
    const numeric =
      /** @param {string} id @param {number} fallback */
      (id, fallback) => {
        const input = document.getElementById(id);
        return input instanceof HTMLInputElement
          ? Number(input.value || fallback)
          : fallback;
      };
    const seed = numeric("factorMiningSeed", 20260927);
    const count = Number(requiredElement("factorMiningSeedCount").value);
    const generations = numeric("factorMiningGenerations", 10);
    const population = numeric("factorMiningPopulation", 50);
    const inputIds = [
      "factorMiningSeed",
      "factorMiningGenerations",
      "factorMiningPopulation",
      "factorMiningTopN",
    ];
    if (
      inputIds.some((id) => {
        const input = document.getElementById(id);
        return input instanceof HTMLInputElement && !input.reportValidity();
      }) ||
      !Number.isInteger(count) ||
      count < 1 ||
      count > 4
    )
      return;
    if (generations * population * count > 2000) {
      setText(
        "factorMiningFeedback",
        dependencies.localeText(
          "所有种子合计最多评估 2,000 个个体，请减少代数、种群或种子数量。",
        ),
      );
      return;
    }
    void submitFactorMining(
      "/api/factor-mining/run",
      {
        seed,
        ...(count > 1
          ? {
              seeds: Array.from(
                { length: count },
                (_, index) => (seed + index) % 2147483648,
              ),
            }
          : {}),
        fitness_mode: requiredElement("factorMiningFitnessMode").value,
        generations,
        population_size: population,
        top_n: numeric("factorMiningTopN", 10),
      },
      event.currentTarget,
      "遗传搜索进行中…",
    );
  });
  requiredElement("factorEvaluateButton")?.addEventListener(
    "click",
    (event) => {
      void submitFactorMining(
        "/api/factor-mining/evaluate",
        { expression: requiredElement("factorExpressionInput")?.value || "" },
        event.currentTarget,
        "表达式评估中…",
      );
    },
  );
  requiredElement("factorTemplateLoadButton")?.addEventListener("click", () => {
    const index = Number(requiredElement("factorTemplateSelect")?.value);
    const template = latestFactorMining?.expression_templates?.[index];
    if (!template) {
      dependencies.showToast("请先选择一个基础因子模板");
      return;
    }
    const input = requiredElement("factorExpressionInput");
    input.value = template.expression || "";
    input.focus();
  });
  requiredElement("factorMiningRuns")?.addEventListener(
    "click",
    async (event) => {
      const button = dependencies.closestHTMLElement(
        event,
        "[data-factor-accept]",
      );
      if (!(button instanceof HTMLButtonElement) || factorMiningWorking) return;
      const runId = button.dataset.factorAccept;
      if (
        !window.confirm(
          "将汇总当前 sealed 窗口全部已保存的搜索/表达式批次候选，并一次性打开密封测试集。N_tested 按本窗口累计计算；每个研究周期只允许一次，失败或中断也不能重试。继续吗？",
        )
      )
        return;
      factorMiningWorking = true;
      button.disabled = true;
      button.textContent = "sealed 验收中…";
      setText(
        "factorMiningFeedback",
        "正在一次性读取密封区间并执行多重检验校正…",
      );
      try {
        const response = await fetch("/api/factor-mining/accept", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ run_id: runId }),
        });
        const data = await response.json();
        if (!response.ok || !data.ok)
          throw new Error(data.error || `HTTP ${response.status}`);
        const result = data.acceptance?.result || {};
        setText(
          "factorMiningFeedback",
          `sealed 验收已汇总 ${result.included_runs?.length ?? 0} 个批次 / N_tested ${result.N_tested ?? "—"}：${result.effective_discoveries ?? 0} 个校正后有效发现。策略配置未自动改变。`,
        );
        dependencies.showToast("密封因子验收已写入研究台账");
      } catch (errorCaught) {
        const error = toError(errorCaught);
        setText(
          "factorMiningFeedback",
          `sealed 验收被拒绝或失败：${error.message}`,
        );
        dependencies.showToast(`密封验收未通过 · ${error.message}`);
      } finally {
        factorMiningWorking = false;
        await loadFactorMining();
      }
    },
  );
  requiredElement("factorExpressionLibrary")?.addEventListener(
    "click",
    async (event) => {
      const button = dependencies.closestHTMLElement(
        event,
        "[data-factor-promote]",
      );
      if (!(button instanceof HTMLButtonElement) || factorMiningWorking) return;
      const key = button.dataset.factorPromote;
      if (
        !window.confirm(
          "提升会修改当前策略因子快照，并要求重新运行策略验证和密封策略验收。仅继续研究用途，不会自动进行纸面或实盘交易。继续吗？",
        )
      )
        return;
      button.disabled = true;
      try {
        const response = await fetch("/api/factor-mining/promote", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ factor_key: key }),
        });
        const data = await response.json();
        if (!response.ok || !data.ok)
          throw new Error(data.error || `HTTP ${response.status}`);
        dependencies.showToast("因子已提升到研究策略管线，请重新执行策略验证");
        await dependencies.loadFactorDefinitions();
      } catch (errorCaught) {
        const error = toError(errorCaught);
        dependencies.showToast(`因子提升失败 · ${error.message}`);
      } finally {
        await loadFactorMining();
      }
    },
  );
}

function updateExternalDeepControls() {
  const selected = requiredElement("factorDeepCandidate").value;
  requiredElement("factorDeepImportButton").disabled =
    externalDeepWorking ||
    externalDeepUnconfirmed ||
    !externalDeepAvailable.some((item) => item.model_id === selected);
  requiredElement("factorDeepRefreshButton").disabled = externalDeepWorking;
  requiredElement("factorDeepCandidate").disabled = externalDeepWorking;
}

async function loadExternalDeep() {
  if (externalDeepWorking) return;
  externalDeepWorking = true;
  updateExternalDeepControls();
  setText("factorDeepStatus", "正在读取当前账号的研究候选…");
  try {
    const response = await fetch("/api/mining/external-deep", {
      cache: "no-store",
    });
    const data =
      /** @type {{ok?: boolean; available?: {model_id: string; version: string; status: string; enabled: boolean}[]; imported?: {version: string}[]}} */ (
        await response.json()
      );
    if (!response.ok || !data.ok) throw new Error(`HTTP ${response.status}`);
    externalDeepAvailable = (
      Array.isArray(data.available) ? data.available : []
    ).filter(
      (item) =>
        item.status === "research_only" &&
        item.enabled === false &&
        /^[A-Za-z0-9_-]{1,64}$/.test(item.model_id) &&
        /^[0-9a-f]{64}$/.test(item.version),
    );
    const imported = new Set(
      (Array.isArray(data.imported) ? data.imported : []).map(
        (item) => item.version,
      ),
    );
    const select = requiredElement("factorDeepCandidate");
    const previous = select.value;
    setSafeHtml(
      select,
      '<option value="">选择研究候选</option>' +
        externalDeepAvailable
          .map(
            (item) =>
              `<option value="${escapeMarkup(item.model_id)}">${escapeMarkup(item.model_id)} · ${imported.has(item.version) ? "已导入，仍禁用" : "待导入"}</option>`,
          )
          .join(""),
    );
    if (externalDeepAvailable.some((item) => item.model_id === previous))
      select.value = previous;
    externalDeepUnconfirmed = false;
    setText(
      "factorDeepStatus",
      externalDeepAvailable.length
        ? `当前账号 ${externalDeepAvailable.length} 个研究候选。已导入的候选仍保持禁用。`
        : "当前账号没有已校验候选，请先在独立训练环境执行分数校验与离线入库。",
    );
  } catch (errorCaught) {
    externalDeepAvailable = [];
    setText(
      "factorDeepStatus",
      `无法读取研究候选：${toError(errorCaught).message}。可点击读取候选重试，不会自动提交导入。`,
    );
  } finally {
    externalDeepWorking = false;
    updateExternalDeepControls();
  }
}

async function importExternalDeep() {
  if (externalDeepWorking || externalDeepUnconfirmed) return;
  const item = externalDeepAvailable.find(
    (row) => row.model_id === requiredElement("factorDeepCandidate").value,
  );
  if (!item) return;
  externalDeepWorking = true;
  updateExternalDeepControls();
  setText("factorDeepStatus", "正在校验不可变版本并导入研究候选…");
  try {
    const response = await fetch("/api/mining/external-deep/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_id: item.model_id, version: item.version }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      const reason =
        response.status === 409
          ? "研究快照边界或来源门禁不满足"
          : response.status === 404
            ? "当前账号找不到此候选"
            : response.status === 400
              ? "版本、数据快照或候选结构校验未通过"
              : response.status === 429
                ? `操作配额或并发限制，请等待 ${response.headers.get("Retry-After") || "建议的"} 秒后先读取候选`
                : `HTTP ${response.status}`;
      throw new Error(reason);
    }
    if (
      data.enabled !== false ||
      data.status !== "research_only" ||
      data.version !== item.version
    )
      throw new Error("响应状态需要核对");
    setText(
      "factorDeepStatus",
      "研究候选已导入，仍未启用；未创建密封验收，离线训练次数未合并到正式研究预算。",
    );
  } catch (errorCaught) {
    externalDeepUnconfirmed = true;
    setText(
      "factorDeepStatus",
      `导入未确认：${toError(errorCaught).message}。请先点击读取候选核对，系统不会自动重复导入。`,
    );
  } finally {
    externalDeepWorking = false;
    updateExternalDeepControls();
  }
}

/** @param {import('../types/services').FactorMiningPanelServices} services */
export function initFactorMiningPanel(services) {
  dependencies = services;
  initPortfolioArena();
  if (!initialized) {
    bindEvents();
    initialized = true;
  }
}

export const APiFactorMiningPanel = {
  init: initFactorMiningPanel,
  load: loadFactorMining,
};
