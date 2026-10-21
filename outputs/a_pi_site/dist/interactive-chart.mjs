import { renderFinancialChart } from "./chart-loader.mjs";
/** @param {{date:string,equity:number}[]} rows */
export function normalizedChartData(rows) {
  const seen = new Set();
  const points = rows
    .map((row) => {
      const compact = row.date.replaceAll("-", "");
      if (
        !/^\d{8}$/.test(compact) ||
        !Number.isFinite(row.equity) ||
        row.equity <= 0
      )
        throw new Error("图表包含无效日期或净值");
      const time = `${compact.slice(0, 4)}-${compact.slice(4, 6)}-${compact.slice(6, 8)}`;
      if (new Date(time).toISOString().slice(0, 10) !== time || seen.has(time))
        throw new Error("图表日期无效或重复");
      seen.add(time);
      return { time, value: row.equity };
    })
    .sort((left, right) => left.time.localeCompare(right.time));
  const base = points[0]?.value || 1;
  return points.map((point) => ({ ...point, value: point.value / base }));
}

/** Compatibility API, now backed by the same financial chart engine.
 * @param {HTMLElement} host @param {{date:string,equity:number}[]} curve @param {{date:string,equity:number}[]} benchmark */
export function renderInteractiveChart(host, curve, benchmark) {
  const points = normalizedChartData(curve);
  /** @type {import('./chart-engine.mjs').ChartSeries[]} */
  const series = [
    {
      name: "策略净值",
      points: points.map((row) => ({ date: row.time, value: row.value })),
      color: "#148359",
    },
  ];
  const byDate = new Map(
    benchmark.map((row) => [row.date.replaceAll("-", ""), row.equity]),
  );
  const aligned = points.map((row) => ({
    date: row.time,
    equity: byDate.get(row.time.replaceAll("-", "")),
  }));
  if (
    aligned.length &&
    aligned.every(
      (row) =>
        row.equity !== undefined &&
        Number.isFinite(row.equity) &&
        row.equity > 0,
    )
  )
    series.push({
      name: "沪深300",
      points: normalizedChartData(
        /** @type {{date:string,equity:number}[]} */ (aligned),
      ).map((row) => ({ date: row.time, value: row.value })),
      color: "#6856c9",
      dashed: true,
    });
  return renderFinancialChart(host, {
    kind: "line",
    title: "净值交互图",
    series,
  });
}
