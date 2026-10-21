/** One lazy engine for every financial chart; no network data or research runs. */
/** @type {Promise<typeof import('./chart-engine.mjs')>|undefined} */
let engine;
/** @type {WeakMap<HTMLElement,number>} */
const revisions = new WeakMap();
/** @param {HTMLElement} anchor @param {import('./chart-engine.mjs').ChartModel} model */
export async function renderFinancialChart(anchor, model) {
  const revision = (revisions.get(anchor) || 0) + 1;
  revisions.set(anchor, revision);
  try {
    const renderer = await (engine ||= import("./chart-engine.mjs"));
    if (anchor.isConnected && revisions.get(anchor) === revision)
      renderer.renderFinancialChart(anchor, model);
  } catch {
    if (revisions.get(anchor) !== revision) return;
    if (engine) (await engine.catch(() => null))?.clearFinancialChart(anchor);
    engine = undefined;
    const target =
      anchor instanceof HTMLCanvasElement ? anchor.parentElement : anchor;
    if (target) {
      target
        .querySelectorAll("[data-chart-error]")
        .forEach((node) => node.remove());
      const note = document.createElement("p");
      note.dataset.chartError = "true";
      note.textContent = "图表加载失败，请刷新重试；研究产物未受影响。";
      target.append(note);
    }
  }
}
