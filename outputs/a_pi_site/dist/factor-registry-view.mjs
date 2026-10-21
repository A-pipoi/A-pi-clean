import { setSafeHtml } from "./safe-html.mjs";
import { apiFetch as fetch } from "./api-client.mjs";
import { requiredElement, toError } from "./runtime-utils.mjs";
/** factor registry view with explicit services and live state accessors. */
/** @param {import('../types/services').FactorRegistryViewServices} deps */
export function createFactorRegistryView(deps) {
  function renderFactorTable() {
    const body = requiredElement("factorTableBody");
    if (!body) return;
    const rows =
      deps.activeFactorFilter === "all"
        ? deps.latestResearchRows
        : deps.latestResearchRows.filter(
            (row) => row.factor_type === deps.activeFactorFilter,
          );
    setSafeHtml(
      body,
      rows.length
        ? rows
            .map((row) => {
              const value = row.value == null ? NaN : Number(row.value);
              const valueClass =
                Number.isFinite(value) && value < 0 ? "negative" : "positive";
              const tagClass =
                row.factor_type === "volatility"
                  ? "risk"
                  : row.factor_type === "reversal"
                    ? "flow"
                    : row.factor_type === "fundamental"
                      ? "fundamental"
                      : "momentum";
              const dateLabel = row.announcement_date
                ? `${row.announcement_date} · 公告`
                : String(row.trade_date || "—");
              const dateMeta = row.report_date
                ? `<small>报告期 ${deps.escapeHtml(row.report_date)}</small>`
                : "";
              return `<tr><td><strong>${deps.escapeHtml(row.factor)}</strong><small>${deps.escapeHtml(row.name || "")}</small></td><td><span class="category-tag ${tagClass}">${deps.escapeHtml(row.symbol || "—")}</span></td><td class="num ${valueClass}">${Number.isFinite(value) ? deps.formatPercent(value) : "—"}</td><td class="num">${deps.escapeHtml(String(row.sample_days ?? "—"))}${row.sample_days == null ? "" : " 日"}</td><td>${deps.escapeHtml(dateLabel)}${dateMeta}</td><td><span class="result-pass">${deps.escapeHtml(row.status || "可用")}</span></td><td>${deps.escapeHtml(row.source || "Tushare")}</td></tr>`;
            })
            .join("")
        : '<tr><td colspan="7">当前筛选条件没有可用的 Tushare 因子数据</td></tr>',
    );
    deps.applyLocale(deps.activeLocale);
  }

  /** @type {import('../types/services').SharedServices['applyResearchSnapshot']} */
  function applyResearchSnapshot(research) {
    if (!research) return;
    deps.setText("researchFactorCount", String(research.factor_count ?? "—"));
    deps.setText("researchValidCount", String(research.valid_count ?? "—"));
    deps.setText(
      "researchAverageMomentum",
      deps.formatPercent(research.average_momentum),
    );
    deps.setText("researchTradeDate", research.latest_trade_date || "—");
    const pipeline = research.factor_pipeline || {};
    const factorNames =
      pipeline.factor_names ||
      (pipeline.factor_definitions || [])
        .map((item) => item.name)
        .filter(Boolean);
    const pipelineText = factorNames.length
      ? `策略已绑定因子管线 ${pipeline.version || "—"} · ${factorNames.join("、")}`
      : "等待策略因子管线…";
    deps.setText("factorPipelineStatus", pipelineText);
    deps.latestResearchRows = research.rows || [];
    renderFactorTable();
    deps.applyLocale(deps.activeLocale);
  }

  /** @type {import('../types/services').SharedServices['loadFactorDefinitions']} */
  async function loadFactorDefinitions() {
    try {
      const response = await fetch("/api/factors", { cache: "no-store" });
      const data = await response.json();
      if (!response.ok || !data.ok)
        throw new Error(data.error || `HTTP ${response.status}`);
      if (!deps.latestResearchRows.length) {
        deps.latestResearchRows = (data.definitions || []).map(
          (/** @type {import('../types/domain').FactorRow} */ item) => ({
            factor: item.name,
            name: item.key,
            factor_type: item.factor_type,
            symbol: "—",
            value: null,
            sample_days: null,
            trade_date: null,
            status: "已注册 · 待真实行情计算",
            source: "因子注册表",
          }),
        );
        deps.setText(
          "researchFactorCount",
          String(deps.latestResearchRows.length),
        );
        renderFactorTable();
      }
    } catch (errorCaught) {
      const error = toError(errorCaught);
      if (!deps.latestResearchRows.length)
        deps.setText(
          "factorPipelineStatus",
          `因子注册表不可用：${error.message}`,
        );
    }
  }
  return { renderFactorTable, applyResearchSnapshot, loadFactorDefinitions };
}
