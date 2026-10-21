/** Render only fills saved with the selected backtest; no extra requests. */
export function createBacktestTrades() {
  const host = document.getElementById("backtestTrades");
  if (!host) return { render() {} };
  const filter = host.querySelector("select");
  const body = host.querySelector("tbody");
  const status = host.querySelector('[role="status"]');
  const previous = host.querySelector('[data-page="previous"]');
  const next = host.querySelector('[data-page="next"]');
  const identity = host.querySelector(".backtest-trades-identity");
  if (
    !(filter instanceof HTMLSelectElement) ||
    !body ||
    !status ||
    !(previous instanceof HTMLButtonElement) ||
    !(next instanceof HTMLButtonElement) ||
    !identity
  )
    return { render() {} };
  /** @type {NonNullable<import('../types/domain').BacktestResult['fills_detail']>} */
  let rows = [];
  let available = false;
  let page = 0;
  const pageSize = 20;
  const draw = () => {
    const selected = rows.filter(
      (row) => filter.value === "ALL" || row.side === filter.value,
    );
    const pages = Math.ceil(selected.length / pageSize);
    page = Math.max(0, Math.min(page, pages - 1));
    body.replaceChildren();
    for (const fill of selected.slice(page * pageSize, (page + 1) * pageSize)) {
      const row = document.createElement("tr");
      for (const value of [
        fill.date,
        fill.symbol,
        fill.side === "BUY"
          ? "买入"
          : fill.side === "SELL"
            ? "卖出"
            : fill.side,
        fill.quantity,
      ]) {
        const cell = document.createElement("td");
        cell.textContent = value == null ? "—" : String(value);
        row.append(cell);
      }
      body.append(row);
    }
    if (!selected.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 4;
      cell.textContent = !available
        ? "此回测结果未保存成交明细，无法还原买卖股票。"
        : rows.length
          ? "当前筛选没有成交记录。"
          : "本次回测没有成交。";
      row.append(cell);
      body.append(row);
    }
    status.textContent = available
      ? `共 ${selected.length} 笔 · 第 ${pages ? page + 1 : 0}/${pages} 页 · 每页 ${pageSize} 笔`
      : "成交明细未保存";
    previous.disabled = page === 0;
    next.disabled = page + 1 >= pages;
    filter.disabled = !available;
  };
  filter.addEventListener("change", () => {
    page = 0;
    draw();
  });
  previous.addEventListener("click", () => {
    page--;
    draw();
  });
  next.addEventListener("click", () => {
    page++;
    draw();
  });
  return {
    /** @param {import('../types/domain').BacktestResult} data */
    render(data) {
      available = Array.isArray(data.fills_detail);
      rows = Array.isArray(data.fills_detail) ? data.fills_detail : [];
      filter.value = "ALL";
      page = 0;
      identity.textContent = `${data.strategy_label || data.strategy || "策略未记录"} · ${data.job_id || (data.experiment_id ? `实验 ${data.experiment_id}` : "当前回测结果")}`;
      draw();
    },
  };
}
