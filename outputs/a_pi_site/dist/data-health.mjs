import { requiredElement } from "./runtime-utils.mjs";
/** data health with explicit services and live state accessors. */
/** @param {import('../types/services').DataHealthServices} deps */
export function createDataHealth(deps) {
  function calculateDataHealth(
    snapshot = deps.latestWorkspaceSnapshot,
    health = deps.notificationState.health,
  ) {
    const portfolio = snapshot?.portfolio || {};
    const metadata = portfolio.dataset_metadata || {};
    const quality = portfolio.data_quality || {};
    const freshness = portfolio.data_freshness || {};
    const probe = health?.probe || {};
    const source = String(metadata.data_source || "").toLowerCase();

    /** @type {number | null} */
    let reachable = null;
    if (
      probe.status !== "pending" &&
      typeof probe.data_reachable === "boolean"
    ) {
      reachable = probe.data_reachable ? 1 : 0;
    } else if (
      health?.online === false ||
      (health?.dataReachable === false && probe.status !== "pending")
    ) {
      reachable = 0;
    } else if (
      typeof health?.dataReachable === "boolean" &&
      probe.status !== "pending"
    ) {
      reachable = health.dataReachable ? 1 : 0;
    } else if (
      source === "tushare_live" ||
      source === "tushare_mixed" ||
      (!source && snapshot?.source === "tushare")
    ) {
      reachable = 1;
    } else if (source === "tushare_cache") {
      reachable = 0;
    }

    /** @type {number | null} */
    let fresh = null;
    if (typeof probe.data_fresh === "boolean") fresh = probe.data_fresh ? 1 : 0;
    else if (typeof freshness.fresh === "boolean")
      fresh = freshness.fresh ? 1 : 0;

    /** @type {number | null} */
    let coverage =
      typeof metadata.coverage_ratio === "number"
        ? metadata.coverage_ratio
        : NaN;
    if (!Number.isFinite(coverage)) {
      const available = metadata.available_symbols;
      const missing = metadata.missing_symbols;
      const expected =
        Array.isArray(available) && Array.isArray(missing)
          ? available.length + missing.length
          : 0;
      if (expected > 0 && Array.isArray(available))
        coverage = available.length / expected;
    }
    coverage = Number.isFinite(coverage)
      ? Math.max(0, Math.min(1, coverage))
      : null;

    const limitsAvailable =
      typeof quality.limits_available === "boolean"
        ? quality.limits_available
        : typeof metadata.limits_available === "boolean"
          ? metadata.limits_available
          : null;
    const checks = [
      reachable,
      fresh,
      coverage,
      limitsAvailable === null ? null : limitsAvailable ? 1 : 0,
    ];
    const verified = checks.filter((value) => value !== null).length;
    const score = verified
      ? Math.round(
          (checks.reduce(
            (/** @type {number} */ sum, value) => sum + (value ?? 0),
            0,
          ) *
            100) /
            checks.length,
        )
      : null;
    return {
      score,
      verified,
      quoteDate:
        probe.latest_trade_date ||
        freshness.latest_trade_date ||
        snapshot?.latest_trade_date ||
        "—",
    };
  }

  /** @type {import('../types/services').SharedServices['renderDataHealth']} */
  function renderDataHealth(
    snapshot = deps.latestWorkspaceSnapshot,
    health = deps.notificationState.health,
  ) {
    const result = calculateDataHealth(snapshot, health);
    const score = requiredElement("dataHealthStatus");
    const meta = requiredElement("dataHealthMeta");
    const bar = requiredElement("dataHealthBar");
    if (score) {
      score.textContent = result.score === null ? "—" : `${result.score}%`;
      score.title = deps.localeText(
        "数据健康度按四项核心检查等权计算：Tushare 可达性、日线新鲜度、股票池覆盖率和涨跌停数据可用性；未知项按 0 计。",
      );
      score.setAttribute(
        "aria-label",
        result.score === null
          ? "数据健康度待核验"
          : `${score.textContent} · ${score.title}`,
      );
      score.style.color =
        result.score === null
          ? ""
          : result.score >= 85
            ? "var(--lime)"
            : result.score >= 60
              ? "var(--amber)"
              : "var(--red)";
    }
    if (meta)
      meta.textContent = `${result.verified}/4 ${deps.localeText("项已核验")}`;
    if (bar) {
      bar.style.width = `${result.score ?? 0}%`;
      bar.style.backgroundColor =
        result.score === null
          ? ""
          : result.score >= 85
            ? "var(--lime)"
            : result.score >= 60
              ? "var(--amber)"
              : "var(--red)";
    }
    deps.setText("dataHealthUpdated", result.quoteDate);
  }
  return { calculateDataHealth, renderDataHealth };
}
