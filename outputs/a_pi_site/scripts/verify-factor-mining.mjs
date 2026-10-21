// Offline production-source layout QA. No login, API call, or financial readiness claim.
import { readFile, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { chromium } = await import(
  pathToFileURL(path.join(process.env.A_PI_PLAYWRIGHT_ROOT, "index.mjs")).href
);
const out = path.join(root, "docs", "visual-sharpe-arena-20261012");
await mkdir(out, { recursive: true });
const css = (
  await Promise.all(
    [
      "design-tokens",
      "styles",
      "account-security",
      "typography",
      "theme",
      "workbench-layers",
      "reference-theme",
    ].map((name) => readFile(path.join(root, "dist", name + ".css"), "utf8")),
  )
).join("\n");
const logo =
  "data:image/svg+xml;base64," +
  (await readFile(path.join(root, "dist/a-pi-mark.svg"))).toString(
    "base64",
  );
const html = (await readFile(path.join(root, "dist/index.html"), "utf8"))
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "")
  .replace(/<link\b[^>]*>/gi, "")
  .replaceAll('src="a-pi-mark.svg"', `src="${logo}"`)
  .replace("</head>", `<style>${css}</style></head>`);
const bundle = await build({
  entryPoints: [path.join(root, "dist/factor-mining-panel.mjs")],
  bundle: true,
  write: false,
  format: "iife",
  globalName: "MiningLayoutFixture",
});
const browser = await chromium.launch({ channel: "msedge", headless: true });
const results = [];
try {
  const page = await browser.newPage();
  await page.route("**/*", (route) => route.abort());
  for (const theme of ["light", "dark"])
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.setContent(html);
      await page.addScriptTag({ content: bundle.outputFiles[0].text });
      await page.evaluate((theme) => {
        document.documentElement.dataset.theme = theme;
        document.getElementById("authView").classList.add("hidden");
        document.getElementById("appShell").classList.add("authenticated");
        document.getElementById("portfolioArena").open = true;
        document
          .querySelectorAll(".page-section")
          .forEach((el) =>
            el.classList.toggle("active", el.id === "factorMining"),
          );
        document
          .querySelectorAll(".nav-item")
          .forEach((el) =>
            el.classList.toggle(
              "active",
              el.dataset.section === "factorMining",
            ),
          );
        const report = window.MiningLayoutFixture.factorEvaluationReportMarkup({
          expression: "布局检查：数据不可用（非业务验收）",
          evaluation: {
            coverage: 0,
            horizons: { 5: { mean_ic: null } },
            ic_decay_curve: { status: "unavailable", curve: [] },
            portfolio: {10: {status: "insufficient_data"}},
            registry_diagnostics: { correlations: [] },
            layered_diagnostics: {
              5: {
                status: "insufficient_data",
                observations: 0,
                skipped_blocks: 0,
              },
            },
          },
        });
        const holder = document.createElement("section");
        holder.innerHTML = report;
        holder.querySelector("details").open = true;
        document.getElementById("factorMining").append(holder);
      }, theme);
      await page.locator("#factorMiningFitnessMode").focus();
      const result = await page.evaluate(() => {
        const select = document.getElementById("factorMiningFitnessMode");
        const style = getComputedStyle(select);
        return {
          width: innerWidth,
          pageWidth: document.documentElement.scrollWidth,
          selectHeight: select.getBoundingClientRect().height,
          focusStyle: style.outlineStyle,
          focusWidth: style.outlineWidth,
          font: style.fontFamily,
          reportBackground: getComputedStyle(
            document.querySelector("#factorMining .factor-evaluation-report"),
          ).backgroundColor,
          cancelDisplay: getComputedStyle(
            document.getElementById("factorMiningCancelButton"),
          ).display,
          reportTables: document.querySelectorAll(
            "#factorMining .factor-report-table",
          ).length,
        };
      });
      await page.screenshot({
        path: path.join(out, `${theme}-${width}.png`),
        fullPage: true,
      });
      results.push({ theme, ...result });
    }
} finally {
  await browser.close();
}
await writeFile(
  path.join(out, "checks.json"),
  JSON.stringify(
    {
      scope:
        "offline layout only; no authenticated API or real market evaluation",
      results,
    },
    null,
    2,
  ),
);
console.log(JSON.stringify(results, null, 2));
if (
  results.some(
    (result) =>
      result.pageWidth > result.width + 1 ||
      result.selectHeight < 43 ||
      result.focusStyle === "none" ||
      result.cancelDisplay !== "none" ||
      (result.theme === "dark" &&
        result.reportBackground === "rgb(255, 255, 255)"),
  )
)
  process.exitCode = 1;
