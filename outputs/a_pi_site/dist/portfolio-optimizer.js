import { apiFetch as fetch } from "./api-client.mjs";
import { requiredElement, toError } from "./runtime-utils.mjs";
export const APiPortfolioOptimizer = (() => {
  /** @param {{translate?:(value: unknown)=>string}} options */
  function init({ translate = (value) => String(value) } = {}) {
    const form = /** @type {HTMLFormElement | null} */ (
      requiredElement("portfolioOptimizerForm")
    );
    const symbolsInput = /** @type {HTMLTextAreaElement | null} */ (
      requiredElement("portfolioOptimizerSymbols")
    );
    const dateInput = /** @type {HTMLInputElement | null} */ (
      requiredElement("portfolioOptimizerDate")
    );
    const submit = /** @type {HTMLButtonElement | null} */ (
      requiredElement("portfolioOptimizerRun")
    );
    const result = requiredElement("portfolioOptimizerResult");
    if (
      !form ||
      !symbolsInput ||
      !submit ||
      !result ||
      form.dataset.initialized === "true"
    )
      return;
    form.dataset.initialized = "true";

    const setMessage =
      /** @param {string} message */
      (message, state = "") => {
        result.replaceChildren(document.createTextNode(translate(message)));
        result.dataset.state = state;
      };

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const symbols = [
        ...new Set(
          symbolsInput.value
            .split(/[\s,，;；]+/)
            .map((value) => value.trim().toUpperCase())
            .filter(Boolean),
        ),
      ];
      if (!symbols.length) {
        setMessage("请至少填写一个候选股票代码。", "error");
        symbolsInput.focus();
        return;
      }
      if (symbols.length > 120) {
        setMessage("组合优化最多接受 120 个候选标的。", "error");
        return;
      }

      submit.disabled = true;
      submit.setAttribute("aria-busy", "true");
      setMessage("正在读取 Tushare 日线与严格 PIT 面板…", "loading");
      try {
        const response = await fetch("/api/portfolio/optimize", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            symbols,
            ...(dateInput?.value ? { as_of: dateInput.value } : {}),
          }),
          signal: AbortSignal.timeout(180000),
        });
        const payload = await response.json();
        if (
          !response.ok ||
          payload.ok !== true ||
          payload.optimization?.status !== "available"
        ) {
          throw new Error(
            payload.error || "严格 PIT 数据或行情不足，组合优化未生成权重。",
          );
        }
        renderResult(payload.optimization);
      } catch (errorCaught) {
        const error = toError(errorCaught);
        setMessage(
          error?.name === "TimeoutError"
            ? "组合优化超时，请减少候选标的后重试。"
            : error?.message || "组合优化暂不可用。",
          "error",
        );
      } finally {
        submit.disabled = false;
        submit.removeAttribute("aria-busy");
      }
    });

    /** @param {import('../types/domain').OptimizationResult} optimization */
    function renderResult(optimization) {
      const formatter = new Intl.NumberFormat(
        document.documentElement.lang || "zh-CN",
        {
          style: "percent",
          minimumFractionDigits: 1,
          maximumFractionDigits: 2,
        },
      );
      result.replaceChildren();
      result.dataset.state = "available";
      const heading = document.createElement("strong");
      heading.textContent = `${translate("优化完成")} · ${optimization.as_of} · ${translate("候选")} ${optimization.candidate_count} · ${translate("PIT 行业覆盖")} ${optimization.industry_coverage_count} · ${translate("基准成分")} ${optimization.benchmark_constituent_count}`;
      result.append(heading);
      const list = document.createElement("ul");
      for (const [symbol, weight] of Object.entries(
        optimization.weights || {},
      ).sort((left, right) => right[1] - left[1])) {
        const item = document.createElement("li");
        item.textContent = `${symbol} · ${formatter.format(Number(weight))}`;
        list.append(item);
      }
      result.append(list);
      const risk = optimization.pre_trade_risk || {};
      const details = document.createElement("p");
      details.textContent =
        risk.status === "available"
          ? `${translate("年化波动率")} ${formatter.format(Number(risk.annualized_volatility))} · ${translate("单日 VaR 95%")} ${formatter.format(Number(risk.var_95))} · ${translate("单日 ES 95%")} ${formatter.format(Number(risk.es_95))} · ${translate("最大相关性")} ${Number.isFinite(Number(risk.max_pairwise_correlation)) ? Number(risk.max_pairwise_correlation).toFixed(2) : "—"} · ${translate("模型")} ${optimization.covariance_basis}`
          : `${translate("风险估计不可用")} (${risk.status || "unknown"}) · ${translate("请检查共同日线覆盖。")}`;
      result.append(details);
      const provenance = document.createElement("small");
      provenance.textContent = `${translate("PIT 面板 SHA-256")} ${optimization.pit_panel_sha256 || "—"} · ${translate("数据版本")} ${optimization.data_version || "—"} · ${translate("仅研究用途，不会更改纸面策略。")}`;
      result.append(provenance);
    }
  }

  return { init };
})();
