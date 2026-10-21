/** Coverage and predeclared training comparisons belonging to one saved run. */
export function createBacktestFactorAudit() {
  const host = document.getElementById("backtestFactorAuditBody");
  if (!host) return { render() {} };
  /** @param {string} tag @param {unknown} text */
  const element = (tag, text) => {
    const node = document.createElement(tag);
    node.textContent = String(text ?? "—");
    return node;
  };
  /** @param {unknown} n @param {boolean} [percent] */
  const number = (n, percent = false) =>
    typeof n === "number" && Number.isFinite(n)
      ? `${(n * (percent ? 100 : 1)).toFixed(2)}${percent ? "%" : ""}`
      : "—";
  /** @param {string} title @param {string[]} headings @param {unknown[][]} rows */
  const table = (title, headings, rows) => {
    const wrap = document.createElement("div");
    wrap.className = "table-scroll";
    wrap.tabIndex = 0;
    wrap.setAttribute("role", "region");
    wrap.setAttribute("aria-label", title);
    const grid = document.createElement("table");
    grid.append(element("caption", title));
    const head = document.createElement("thead");
    const header = document.createElement("tr");
    for (const heading of headings) {
      const cell = element("th", heading);
      cell.setAttribute("scope", "col");
      header.append(cell);
    }
    head.append(header);
    grid.append(head);
    const body = document.createElement("tbody");
    for (const values of rows) {
      const row = document.createElement("tr");
      for (const value of values) row.append(element("td", value));
      body.append(row);
    }
    grid.append(body);
    wrap.append(grid);
    host.append(wrap);
  };
  /** @type {Record<string, string>} */
  const reasons = {
    missing_point_in_time_financial: "缺少当时可用的财务证据",
    insufficient_history_or_invalid_value: "历史长度不足或数值无效",
  };
  /** @type {Record<string, string>} */
  const profiles = {
    equal_factor: "因子等权",
    group_balanced: "价格／财务各半",
    price_priority: "价格占75%",
    momentum_priority: "动量／低波动 3:1",
    low_volatility_priority: "动量／低波动 1:3",
  };
  return {
    /** @param {import('../types/domain').BacktestResult} data */
    render(data) {
      host.replaceChildren();
      host.append(
        element(
          "p",
          `${data.strategy_label || data.strategy || "策略未记录"} · ${data.job_id || "当前回测结果"}`,
        ),
      );
      const pipeline = data.factor_pipeline;
      const audit = pipeline?.participation;
      if (!audit || audit.status !== "available") {
        host.append(
          element(
            "p",
            "此回测未保存因子参与诊断；已启用因子列表不代表实际参与评分。",
          ),
        );
      } else {
        const latest = audit.latest;
        host.append(
          element(
            "p",
            `覆盖统计来自 ${audit.evaluations} 次信号计算；最近信号历史截止 ${latest.day || "—"}，${latest.eligible_symbols}/${latest.requested_symbols} 只股票满足评分条件。`,
          ),
        );
        host.append(
          element(
            "p",
            pipeline.coverage_policy === "complete"
              ? "严格口径：每只入选股票必须有全部要求的因子；完整覆盖低于90%时停止本次调仓。"
              : "原基准口径：按各股票可用因子评分，不同股票的实际参与因子可能不同。",
          ),
        );
        table(
          "实际因子参与情况",
          ["因子", "权重", "最近覆盖", "全程覆盖", "最近参与评分", "缺失原因"],
          audit.factors.map((row) => [
            row.name,
            number(row.weight),
            `${row.covered}/${row.requested}`,
            number(row.overall_coverage, true),
            row.used,
            row.missing_reason
              ? reasons[row.missing_reason] || row.missing_reason
              : "完整",
          ]),
        );
        if (audit.correlations.length) {
          const names = Object.fromEntries(
            audit.factors.map((row) => [row.key, row.name]),
          );
          table(
            "因子重复信息检查",
            [
              "因子组合",
              "平均有向排名相关",
              "平均绝对相关",
              "有效截面数",
              "提示",
            ],
            audit.correlations.map((row) => [
              `${names[row.left] || row.left} / ${names[row.right] || row.right}`,
              number(row.mean_rank_correlation),
              number(row.mean_absolute_correlation),
              row.evaluations,
              row.redundancy_warning
                ? "高度相关，注意重复计权"
                : "未达到0.8提示线",
            ]),
          );
        } else
          host.append(
            element(
              "p",
              "相关性样本不足：每对因子至少需要20只共同股票且数值有差异。",
            ),
          );
        host.append(element("p", audit.correlation_basis));
        if (pipeline.retention_rank)
          host.append(
            element(
              "p",
              `低换手规则：原持仓排名前${pipeline.retention_rank}名可保留；持仓权重偏差小于${number(pipeline.rebalance_deadband, true)}时不调整，超上限减仓除外；预留${number(pipeline.cash_reserve, true)}现金。`,
            ),
          );
      }
      const comparison = data.training_weight_comparison;
      if (comparison) {
        host.append(
          element(
            "p",
            `预设权重训练比较：${comparison.training_start} 至 ${comparison.training_end}（首个40个交易日）。3组试验各扣1次预算，加主回测共4次。`,
          ),
        );
        table(
          "训练区间成本后比较",
          [
            "预设方案",
            "年化收益",
            "夏普率",
            "最大回撤",
            "换手",
            "费用（元）",
            "状态",
          ],
          comparison.rows.map((row) => [
            profiles[row.profile] || row.profile,
            number(row.annualized_return, true),
            number(row.sharpe),
            number(row.max_drawdown, true),
            number(row.turnover),
            number(row.total_cost),
            row.status === "available" ? "训练诊断" : "因子覆盖不足",
          ]),
        );
        host.append(element("p", comparison.note));
      }
    },
  };
}
