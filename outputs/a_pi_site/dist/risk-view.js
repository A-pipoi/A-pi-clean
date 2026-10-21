import { setSafeHtml } from "./safe-html.mjs";
import { requiredElement } from "./runtime-utils.mjs";
export const APiRiskView = (() => {
  /** @param {unknown} value @returns {import('../types/domain').RiskLimits | null} */
  function normalizeLimits(value) {
    const keys = [
      "max_single_weight",
      "max_industry_weight",
      "max_gross_leverage",
      "max_drawdown",
    ];
    if (!value || typeof value !== "object") return null;
    const limits = /** @type {Record<string, unknown>} */ (value);
    const isNumeric = /** @param {unknown} item */ (item) =>
      item !== null &&
      item !== undefined &&
      item !== "" &&
      Number.isFinite(Number(item));
    if (keys.some((key) => !isNumeric(limits[key]))) return null;
    const normalized = /** @type {import('../types/domain').RiskLimits} */ (
      Object.fromEntries(keys.map((key) => [key, Number(limits[key])]))
    );
    for (const key of [
      "max_annualized_volatility",
      "max_var_95",
      "max_es_95",
      "max_daily_variance",
      "max_pairwise_correlation",
    ]) {
      normalized[key] = isNumeric(limits[key]) ? Number(limits[key]) : null;
    }
    return normalized;
  }

  /** @param {import('../types/services').RiskViewServices} deps */
  function create(deps) {
    const { setText, localeText, escapeHtml } = deps;
    /** @type {import('../types/domain').RiskAlert[]} */
    let activeAlerts = [];
    let alertContext = {
      hasLimits: false,
      hasPositions: false,
      source: "",
      dismissedCount: 0,
    };

    function dismissedStorageKey() {
      const username =
        deps.getCurrentUsername()?.trim().toLowerCase() || "anonymous";
      return `a_pi_dismissed_risk_alerts:v1:${encodeURIComponent(username)}`;
    }

    function dismissedIds() {
      try {
        const value = JSON.parse(
          localStorage.getItem(dismissedStorageKey()) || "[]",
        );
        return new Set(
          Array.isArray(value)
            ? value.filter((item) => typeof item === "string")
            : [],
        );
      } catch {
        return new Set();
      }
    }

    /** @param {import('../types/domain').RiskAlert} item */
    function fingerprint(item) {
      const source = JSON.stringify([
        item.kind || "",
        item.title || "",
        item.description || "",
        item.rows || [],
      ]);
      let hash = 2166136261;
      for (let index = 0; index < source.length; index += 1) {
        hash ^= source.charCodeAt(index);
        hash = Math.imul(hash, 16777619);
      }
      return (hash >>> 0).toString(16).padStart(8, "0");
    }

    function renderAlerts() {
      deps.setAlertDetails(activeAlerts);
      setText(
        "riskAlertCount",
        alertContext.hasLimits || activeAlerts.length
          ? `${activeAlerts.length} 条`
          : "—",
      );
      const riskBadge = requiredElement("riskNavBadge");
      if (riskBadge) {
        riskBadge.textContent = String(activeAlerts.length);
        riskBadge.hidden = activeAlerts.length === 0;
        const riskNavItem = riskBadge.closest(".nav-item");
        if (riskNavItem) {
          const label = deps.localeText("风险控制");
          const accessibleLabel = activeAlerts.length
            ? `${label}，${activeAlerts.length} ${deps.localeText("待处理提醒")}`
            : label;
          riskNavItem.setAttribute("aria-label", accessibleLabel);
          riskNavItem.setAttribute("title", accessibleLabel);
        }
      }
      const alertHost = requiredElement("riskAlerts");
      if (!alertHost) return;
      if (activeAlerts.length) {
        setSafeHtml(
          alertHost,
          activeAlerts
            .map((item, index) => {
              const description =
                item.kind === "risk-limit"
                  ? "本条提醒由实际组合指标与风险限额比较生成。"
                  : "本条提示来自服务返回的数据质量/新鲜度检查。";
              const timestamp =
                item.kind === "risk-limit"
                  ? `行情日 ${item.rows.find(([label]) => label === "行情日")?.[1] || "当前"}`
                  : "服务检查结果";
              return `<div class="alert-card warning-alert"><span class="alert-symbol">!</span><div><strong>${escapeHtml(localeText(item.title))}</strong><p>${escapeHtml(localeText(description))}</p><small>${escapeHtml(timestamp)}</small></div><button type="button" data-risk-alert-index="${index}" aria-label="${escapeHtml(`${localeText("提醒详情")}：${localeText(item.title)}`)}">查看</button></div>`;
            })
            .join(""),
        );
      } else if (alertContext.dismissedCount > 0) {
        setSafeHtml(
          alertHost,
          '<div class="alert-card info-alert"><span class="alert-symbol">i</span><div><strong>提醒已处理</strong><p>已关闭的提醒不会重复显示；风险或数据状态变化后会再次提醒。</p><small>当前</small></div></div>',
        );
      } else if (!alertContext.hasLimits) {
        setSafeHtml(
          alertHost,
          '<div class="alert-card warning-alert"><span class="alert-symbol">!</span><div><strong>风险限额数据不可用</strong><p>未取得可用于风控判断的 Python 限额配置，不能生成正式结论。</p><small>当前</small></div></div>',
        );
      } else if (!alertContext.hasPositions) {
        setSafeHtml(
          alertHost,
          `<div class="alert-card info-alert"><span class="alert-symbol">i</span><div><strong>已连接真实风控数据</strong><p>当前无纸面持仓，因此没有集中度、行业暴露或杠杆超限结论。</p><small>${escapeHtml(alertContext.source || "tushare")}</small></div></div>`,
        );
      } else {
        setSafeHtml(
          alertHost,
          '<div class="alert-card info-alert"><span class="alert-symbol">i</span><div><strong>暂无超限提醒</strong><p>当前没有可由真实账户数据确认的风险告警。</p><small>当前</small></div></div>',
        );
      }
    }

    /** @param {string} id */
    function dismissAlert(id) {
      if (!id || !activeAlerts.some((item) => item.id === id)) return;
      const ids = dismissedIds();
      ids.add(id);
      try {
        localStorage.setItem(
          dismissedStorageKey(),
          JSON.stringify([...ids].slice(-200)),
        );
      } catch {
        // Keep the current session usable if persistent browser storage is unavailable.
      }
      activeAlerts = activeAlerts.filter((item) => item.id !== id);
      alertContext.dismissedCount += 1;
      renderAlerts();
    }

    /** @param {import('../types/domain').PaperState | null} paper @param {import('../types/domain').Portfolio | null} portfolio @param {import('../types/domain').RiskLimits | null} riskLimits */
    function apply(paper, portfolio, riskLimits = null) {
      const limits = normalizeLimits(
        riskLimits || paper?.risk_limits || deps.getRiskLimits(),
      );
      if (limits) deps.setRiskLimits(limits);
      const positions = paper?.positions || [];
      const equity = Number(paper?.equity);
      const marketValue = Number(paper?.market_value);
      const leverage =
        equity > 0 && Number.isFinite(marketValue)
          ? marketValue / equity
          : null;
      const concentrations = positions
        .map((item) => Number(item.market_value) / equity)
        .filter(Number.isFinite);
      const maxConcentration = concentrations.length
        ? Math.max(...concentrations)
        : null;
      const industryExposures = paper?.industry_exposure || [];
      const industryWeights = industryExposures
        .map((item) => Number(item.weight))
        .filter(Number.isFinite);
      const maxIndustry = industryWeights.length
        ? Math.max(...industryWeights)
        : null;
      const drawdownValue =
        portfolio?.metrics?.max_drawdown == null
          ? NaN
          : Number(portfolio.metrics.max_drawdown);
      const drawdown = Number.isFinite(drawdownValue)
        ? Math.abs(drawdownValue)
        : null;
      const riskEstimate =
        paper?.pre_trade_risk ||
        paper?.scheduler?.last_result?.pre_trade_risk ||
        {};
      const covarianceVolatility =
        riskEstimate.status === "available"
          ? Number(riskEstimate.annualized_volatility)
          : null;
      const parametricVar95 =
        riskEstimate.status === "available"
          ? Number(riskEstimate.var_95)
          : null;
      const expectedShortfall95 =
        riskEstimate.status === "available" ? Number(riskEstimate.es_95) : null;
      const dailyVariance =
        riskEstimate.status === "available"
          ? Number(riskEstimate.daily_variance)
          : null;
      const maxPairwiseCorrelation =
        riskEstimate.status === "available" &&
        riskEstimate.max_pairwise_correlation != null
          ? Number(riskEstimate.max_pairwise_correlation)
          : null;
      const observed = [
        [maxConcentration, limits?.max_single_weight],
        [maxIndustry, limits?.max_industry_weight],
        [leverage, limits?.max_gross_leverage],
        [drawdown, limits?.max_drawdown],
        [covarianceVolatility, limits?.max_annualized_volatility],
        [parametricVar95, limits?.max_var_95],
        [expectedShortfall95, limits?.max_es_95],
        [dailyVariance, limits?.max_daily_variance],
        [maxPairwiseCorrelation, limits?.max_pairwise_correlation],
      ].filter(
        ([value, limit]) =>
          value != null &&
          limit != null &&
          Number.isFinite(Number(value)) &&
          Number.isFinite(Number(limit)) &&
          Number(limit) > 0,
      );
      const highestRatio = observed.length
        ? Math.max(
            ...observed.map(([value, limit]) => Number(value) / Number(limit)),
          )
        : null;
      const riskLevel =
        !limits || !observed.length || !positions.length
          ? "—"
          : Number(highestRatio) > 1
            ? "高"
            : Number(highestRatio) >= 0.8
              ? "中"
              : "低";
      setText("riskLevel", riskLevel);
      setText(
        "homeConcentration",
        Number.isFinite(maxConcentration)
          ? `${(Number(maxConcentration) * 100).toFixed(1)}%`
          : "—",
      );
      setText(
        "homeLeverage",
        Number.isFinite(leverage) ? `${Number(leverage).toFixed(2)}×` : "—",
      );
      setText(
        "homeConcentrationLimit",
        limits
          ? `≤ ${(limits.max_single_weight * 100).toFixed(0)}%`
          : "等待限额",
      );
      setText(
        "homeLeverageLimit",
        limits ? `≤ ${limits.max_gross_leverage.toFixed(2)}×` : "等待限额",
      );
      const concentrationBar = requiredElement("homeConcentrationBar");
      const leverageBar = requiredElement("homeLeverageBar");
      if (concentrationBar)
        concentrationBar.style.width = `${limits && Number.isFinite(maxConcentration) ? Math.min(100, Math.max(0, (Number(maxConcentration) / limits.max_single_weight) * 100)) : 0}%`;
      if (leverageBar)
        leverageBar.style.width = `${limits && Number.isFinite(leverage) ? Math.min(100, Math.max(0, (Number(leverage) / limits.max_gross_leverage) * 100)) : 0}%`;
      const homeRiskStatus = requiredElement("homeRiskStatus");
      if (homeRiskStatus) {
        homeRiskStatus.textContent =
          riskLevel === "—" ? "等待数据" : `${riskLevel}风险`;
        homeRiskStatus.className = `status-chip ${riskLevel === "高" ? "warning" : riskLevel === "中" ? "neutral" : riskLevel === "低" ? "good" : "neutral"}`;
      }

      const riskList = requiredElement("riskLimitList");
      /** @type {[string, number | null, number | null | undefined, string][]} */
      const items = [
        ["单票集中度", maxConcentration, limits?.max_single_weight, "%"],
        ["行业集中度", maxIndustry, limits?.max_industry_weight, "%"],
        ["总杠杆", leverage, limits?.max_gross_leverage, "×"],
        ["组合回撤", drawdown, limits?.max_drawdown, "%"],
        [
          "滚动年化波动率",
          covarianceVolatility,
          limits?.max_annualized_volatility,
          "%",
        ],
        ["单日参数 VaR 95%", parametricVar95, limits?.max_var_95, "%"],
        ["单日参数 ES 95%", expectedShortfall95, limits?.max_es_95, "%"],
        ["组合日方差", dailyVariance, limits?.max_daily_variance, "variance"],
        [
          "持仓最大相关性",
          maxPairwiseCorrelation,
          limits?.max_pairwise_correlation,
          "ρ",
        ],
      ];
      if (riskList) {
        setSafeHtml(
          riskList,
          items
            .map(([label, value, limit, unit]) => {
              const hasLimit =
                limit != null &&
                Number.isFinite(Number(limit)) &&
                Number(limit) > 0;
              const hasValue = value != null && Number.isFinite(Number(value));
              const rawRatio =
                hasLimit && hasValue
                  ? Math.abs(Number(value)) / Number(limit)
                  : 0;
              const ratio = Math.min(1, rawRatio);
              const formatRiskValue =
                /** @param {number} number */
                (number, isLimit = false) =>
                  unit === "×"
                    ? `${number.toFixed(2)}×`
                    : unit === "ρ"
                      ? number.toFixed(2)
                      : unit === "variance"
                        ? number.toExponential(isLimit ? 2 : 3)
                        : `${(number * 100).toFixed(isLimit ? 2 : 1)}%`;
              const display = hasValue ? formatRiskValue(Number(value)) : "—";
              const limitDisplay = hasLimit
                ? formatRiskValue(Number(limit), true)
                : "等待限额";
              const status = !hasLimit
                ? "等待限额"
                : !hasValue
                  ? "暂无数据"
                  : rawRatio > 1
                    ? "超限"
                    : rawRatio >= 0.9
                      ? "接近"
                      : "正常";
              const statusClass =
                rawRatio > 1 || rawRatio >= 0.9 ? "limit-warn" : "limit-ok";
              return `<div class="limit-item"><div><span>${label}</span><strong>${display} <small>/ ${limitDisplay}</small></strong></div><div class="limit-track${ratio >= 0.9 ? " warn" : ""}"><span style="width:${ratio * 100}%" class="ui-decoration"></span></div><b class="${statusClass}">${status}</b></div>`;
            })
            .join(""),
        );
      }

      const snapshot = deps.getWorkspaceSnapshot();
      /** @type {import('../types/domain').RiskAlert[]} */
      const alertDetails = [];
      const quoteDate =
        paper?.data_freshness?.latest_trade_date ||
        portfolio?.data_freshness?.latest_trade_date ||
        snapshot?.latest_trade_date ||
        paper?.latest_trade_date ||
        "—";
      const riskDataSource =
        paper?.data_freshness?.source ||
        portfolio?.dataset_metadata?.data_source ||
        snapshot?.source ||
        "—";
      const pushLimitAlert =
        /** @param {string} title @param {string} metric @param {number | null} value @param {number | null | undefined} limit @param {(value: number) => string} formatter */
        (title, metric, value, limit, formatter) => {
          if (
            !limits ||
            typeof value !== "number" ||
            typeof limit !== "number" ||
            !Number.isFinite(value) ||
            !Number.isFinite(limit) ||
            value <= limit
          )
            return;
          alertDetails.push({
            kind: "risk-limit",
            title,
            description: "本条提醒由实际组合指标与风险限额比较生成。",
            rows: [
              ["触发项", metric],
              ["当前值", formatter(value)],
              ["限额", formatter(limit)],
              ["超出幅度", formatter(value - limit)],
              ["相关行情日期", quoteDate],
              ["数据源", riskDataSource],
            ],
          });
        };
      pushLimitAlert(
        `单票集中度超过 ${(Number(limits?.max_single_weight) * 100).toFixed(0)}%`,
        "单票集中度",
        maxConcentration,
        limits?.max_single_weight,
        (value) => `${(value * 100).toFixed(1)}%`,
      );
      pushLimitAlert(
        `行业集中度超过 ${(Number(limits?.max_industry_weight) * 100).toFixed(0)}%`,
        "行业集中度",
        maxIndustry,
        limits?.max_industry_weight,
        (value) => `${(value * 100).toFixed(1)}%`,
      );
      pushLimitAlert(
        `总杠杆超过 ${Number(limits?.max_gross_leverage).toFixed(2)}×`,
        "总杠杆",
        leverage,
        limits?.max_gross_leverage,
        (value) => `${value.toFixed(2)}×`,
      );
      pushLimitAlert(
        `组合回撤超过 ${(Number(limits?.max_drawdown) * 100).toFixed(0)}%`,
        "组合回撤",
        drawdown,
        limits?.max_drawdown,
        (value) => `${(value * 100).toFixed(1)}%`,
      );
      pushLimitAlert(
        `滚动年化波动率超过 ${(Number(limits?.max_annualized_volatility) * 100).toFixed(0)}%`,
        "滚动年化波动率",
        covarianceVolatility,
        limits?.max_annualized_volatility,
        (value) => `${(value * 100).toFixed(1)}%`,
      );
      pushLimitAlert(
        `单日参数 VaR 95% 超过 ${(Number(limits?.max_var_95) * 100).toFixed(2)}%`,
        "单日参数 VaR 95%",
        parametricVar95,
        limits?.max_var_95,
        (value) => `${(value * 100).toFixed(2)}%`,
      );
      pushLimitAlert(
        `单日参数 ES 95% 超过 ${(Number(limits?.max_es_95) * 100).toFixed(2)}%`,
        "单日参数 ES 95%",
        expectedShortfall95,
        limits?.max_es_95,
        (value) => `${(value * 100).toFixed(2)}%`,
      );
      pushLimitAlert(
        "组合日方差超过协方差限额",
        "组合日方差",
        dailyVariance,
        limits?.max_daily_variance,
        (value) => value.toExponential(3),
      );
      pushLimitAlert(
        `持仓最大相关性超过 ${(Number(limits?.max_pairwise_correlation) * 100).toFixed(0)}%`,
        "持仓最大相关性",
        maxPairwiseCorrelation,
        limits?.max_pairwise_correlation,
        (value) => value.toFixed(2),
      );

      /** @type {[string, (import('../types/domain').DataQuality & import('../types/domain').Freshness) | undefined][]} */
      const warningGroups = [
        ["纸面账户数据质量", paper?.data_quality],
        ["纸面行情新鲜度", paper?.data_freshness],
        ["组合数据质量", portfolio?.data_quality],
        ["组合行情新鲜度", portfolio?.data_freshness],
      ];
      const warningEntries = warningGroups.flatMap(([source, value]) =>
        (value?.warnings || [])
          .filter(Boolean)
          .map((warning) => ({ source, warning, context: value })),
      );
      const qualityWarnings = [
        ...new Set(warningEntries.map((item) => item.warning)),
      ];
      const dataSource = String(
        paper?.data_freshness?.source ||
          portfolio?.dataset_metadata?.data_source ||
          "",
      ).toLowerCase();
      const healthProbe = deps.getHealth()?.probe || {};
      qualityWarnings.forEach((warning) => {
        const sources = [
          ...new Set(
            warningEntries
              .filter((item) => item.warning === warning)
              .map((item) => item.source),
          ),
        ];
        const freshnessContexts = warningEntries.filter(
          (item) => item.warning === warning && item.context?.latest_trade_date,
        );
        const freshnessContext =
          freshnessContexts[0]?.context ||
          paper?.data_freshness ||
          portfolio?.data_freshness ||
          {};
        alertDetails.push({
          kind: "data-quality",
          title: warning,
          description: "本条提示来自服务返回的数据质量/新鲜度检查。",
          rows: [
            ["检查来源", sources.join("、") || "服务数据质量检查"],
            [
              "最新确认日线",
              freshnessContext.latest_trade_date ||
                healthProbe.latest_trade_date ||
                "—",
            ],
            [
              "期望交易日",
              freshnessContext.expected_trade_date ||
                healthProbe.expected_trade_date ||
                "—",
            ],
            [
              "数据质量状态",
              freshnessContext.fresh === true
                ? "通过"
                : freshnessContext.fresh === false ||
                    healthProbe.data_fresh === false
                  ? "未通过"
                  : "未返回",
            ],
            ["检查状态", healthProbe.status || "未返回"],
            ["数据源", freshnessContext.source || dataSource || "—"],
            ["诊断信息", healthProbe.error || "暂无额外诊断信息"],
          ],
        });
      });
      const dismissed = dismissedIds();
      const identifiedAlerts = alertDetails.map((item) => ({
        ...item,
        id: fingerprint(item),
      }));
      activeAlerts = identifiedAlerts.filter((item) => !dismissed.has(item.id));
      alertContext = {
        hasLimits: Boolean(limits),
        hasPositions: positions.length > 0,
        source: dataSource || "tushare",
        dismissedCount: identifiedAlerts.length - activeAlerts.length,
      };
      renderAlerts();
    }

    if (typeof window !== "undefined") {
      window.addEventListener("storage", (event) => {
        if (event.key !== dismissedStorageKey()) return;
        const dismissed = dismissedIds();
        const previousCount = activeAlerts.length;
        activeAlerts = activeAlerts.filter(
          (item) => !dismissed.has(item.id || ""),
        );
        alertContext.dismissedCount += previousCount - activeAlerts.length;
        renderAlerts();
      });
    }

    return { apply, dismissAlert };
  }

  return { create, normalizeLimits };
})();
