import { apiFetch } from "./api-client.mjs";
import { setSafeHtml } from "./safe-html.mjs";

const escape = (/** @type {unknown} */ value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (ch) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        ch
      ] || ch,
  );
const number = (/** @type {unknown} */ value, digits = 3) =>
  typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(digits)
    : "—";
const percent = (/** @type {unknown} */ value) =>
  typeof value === "number" && Number.isFinite(value)
    ? `${(value * 100).toFixed(1)}%`
    : "—";

/** @param {Record<string, import('../types/domain').PortfolioReport>} reports */
export function portfolioMarkup(reports = {}) {
  return Object.entries(reports)
    .map(([layers, report]) => {
      const gross = report.gross || {};
      const net = report.net || [];
      const metrics = (
        /** @type {import('../types/domain').PortfolioMetrics} */ item,
      ) =>
        `<td>${number(item.annual_sharpe)}</td><td>${percent(item.annual_return)}</td><td>${percent(item.annual_volatility)}</td><td>${percent(item.max_drawdown)}</td><td>${number(item.calmar)}</td>`;
      const rows = `<tr><th scope="row">毛收益</th>${metrics(gross)}</tr>${net.map((item) => `<tr><th scope="row">净收益 · 日单边换手 ${percent(item.daily_one_way_turnover)}</th>${metrics(item.metrics)}</tr>`).join("")}`;
      const points = (report.curve || []).filter(
        (x) => typeof x.gross_nav === "number" && Number.isFinite(x.gross_nav),
      );
      const neutral = String(report.policy?.neutral_turnover ?? 0.1);
      const limits = points
        .flatMap((x) => [x.gross_nav, x.net_nav?.[neutral]])
        .filter((x) => typeof x === "number" && Number.isFinite(x))
        .map(Number);
      const low = Math.min(1, ...limits),
        high = Math.max(1, ...limits);
      const coordinates = (/** @type {string} */ key) =>
        points
          .map((x, i) => {
            const value = key === "gross" ? x.gross_nav : x.net_nav?.[neutral];
            return typeof value === "number" && Number.isFinite(value)
              ? `${12 + (i * 336) / Math.max(1, points.length - 1)},${108 - ((value - low) * 96) / Math.max(0.00001, high - low)}`
              : "";
          })
          .filter(Boolean)
          .join(" ");
      const chart =
        points.length > 1
          ? `<svg class="portfolio-nav-chart" viewBox="0 0 360 120" role="img" aria-label="${escape(layers)} 层理论毛净多空净值"><polyline class="portfolio-gross-line" fill="none" stroke-width="2" points="${coordinates("gross")}"/><polyline class="portfolio-net-line" fill="none" stroke-width="2" stroke-dasharray="5 3" points="${coordinates("net")}"/></svg><p>实线：毛净值 · 虚线：${percent(report.policy?.neutral_turnover ?? 0.1)} 换手净净值</p>`
          : "";
      return `<section class="factor-report-cross portfolio-report" data-slot="portfolio-report"><h4>${escape(layers)} 层日频多空组合</h4><p>${report.status === "available" ? "理论样本可用" : "样本不足或不完整，不作为验收通过依据"} · ${Number(gross.observations || 0)} 个日收益 · 跳过 ${report.skipped_dates?.length || 0} 日</p>${chart}<div class="factor-report-table-wrap" tabindex="0" role="region" aria-label="毛净组合指标，可横向滚动"><table class="factor-report-table"><caption>次日开盘至再下一日开盘，不使用重叠 5 日收益</caption><thead><tr><th>口径</th><th>年化夏普</th><th>年化收益</th><th>年化波动</th><th>最大回撤</th><th>卡玛</th></tr></thead><tbody>${rows}</tbody></table></div><dl class="portfolio-probabilities"><div><dt>PSR · 真实夏普超过基准的近似概率</dt><dd>${percent(report.psr?.probability)}</dd></div><div><dt>组合 DSR · 多重试验折减概率</dt><dd>${percent(report.dsr?.probability)} · N_tested ${Number(report.dsr?.n_tested || 0)}</dd></div></dl><p>与旧 IC 报告的“DSR p”不是同一统计量。使用真实台账累计次数；历史试验夏普样本不完整时明确采用方差下限近似，不以幸存候选冒充全部试验。</p><p>佣金 ${number(report.policy?.commission_rate, 5)} · 滑点 ${number(report.policy?.slippage_bps, 1)} bps · 印花税 ${report.policy?.stamp_tax_rate == null ? "沿用按日期计税模型" : number(report.policy.stamp_tax_rate, 5)}。理论多空不等于可执行融券策略；未模拟最小佣金、融资融券费、涨跌停与市场冲击。</p><details><summary>查看日收益与净值明细</summary><div class="factor-report-table-wrap"><table class="factor-report-table"><thead><tr><th>信号日</th><th>入场日</th><th>结算日</th><th>毛日收益</th><th>毛净值</th><th>中性成本净值</th></tr></thead><tbody>${(report.curve || []).map((x) => `<tr><td>${escape(x.signal_date)}</td><td>${escape(x.entry_date)}</td><td>${escape(x.date)}</td><td>${percent(x.return)}</td><td>${number(x.gross_nav)}</td><td>${number(x.net_nav?.[neutral])}</td></tr>`).join("") || '<tr><td colspan="6">没有可用日收益，不补零。</td></tr>'}</tbody></table></div></details></section>`;
    })
    .join("");
}

/** @param {import('../types/domain').ArenaReport} report */
export function arenaReportMarkup(report) {
  /** @type {Record<string,string>} */
  const states = {
    running: "独立进化中",
    closed: "已封闭提交，待确认验收",
    accepting: "验收已请求",
    completed: "验收完成",
    acceptance_failed: "验收失败，不自动重试",
    archived: "已归档",
  };
  const rows = (report.participants || [])
    .map(
      (row) =>
        `<tr><th scope="row">${escape(row.proposer_tag)}</th><td>${Number(row.evaluations_used)}/${Number(row.budget)}${row.locked ? " · 锁定" : ""}</td><td>${Number(row.n_tested)}</td><td>${number(row.best_icir)}</td><td>${number(row.top10_mean_icir)}</td><td>${percent(row.effective_hypothesis_rate)}</td><td>${percent(row.sealed_pass_rate)}</td><td>${number(row.sealed_mean_ic)}</td></tr>`,
    )
    .join("");
  const pairs = report.manifest?.peer_correlations?.pairs || [];
  const correlationRows = pairs
    .map(
      (row) =>
        `<tr><td>${escape(row.left_proposer)} / ${escape(row.right_proposer)}</td><td>${escape(row.left_expression)} / ${escape(row.right_expression)}</td><td>${number(row.correlation)}</td></tr>`,
    )
    .join("");
  const registryRows =
    (report.participants || [])
      .map(
        (row) =>
          `<tr><th>${escape(row.proposer_tag)}</th><td>${(row.registry_correlations || []).map((x) => number(x)).join(" / ") || "—"}</td></tr>`,
      )
      .join("") +
    (report.manifest?.peer_correlations?.registry_pairs || [])
      .map(
        (row) =>
          `<tr><th>${escape(row.proposer_tag)} · ${escape(row.expression)} / ${escape(row.registry_key)}</th><td>${number(row.correlation)}</td></tr>`,
      )
      .join("");
  return `<section data-slot="arena-report"><h4>${escape(states[report.status] || report.status)}</h4><p>赛季 ${escape(report.arena_id)} · 全窗口 N_tested ${Number(report.global_n_tested)} · ${report.ranking_basis === "sealed_results" ? "最终排名按密封通过率、实测 IC；未通过者不宣称获胜" : "仅训练暂榜，不能据此判定最终胜负"}</p><p>训练 ${escape(report.brief?.training_start)} 至 ${escape(report.brief?.training_end)} · 每方预算 ${Number(report.brief?.evaluation_budget || 0)} · ICIR Top-${Number(report.brief?.top_n || 0)} 统一提交</p><div class="factor-report-table-wrap" tabindex="0" role="region" aria-label="竞技场排行榜，可横向滚动"><table class="factor-report-table"><thead><tr><th>参赛方</th><th>评估预算</th><th>真实 N</th><th>最佳 ICIR</th><th>Top-10 均值</th><th>有效假设率</th><th>密封通过率</th><th>密封 IC</th></tr></thead><tbody>${rows}</tbody></table></div><details><summary>查看增量贡献与撞车诊断</summary><div class="factor-report-table-wrap"><table class="factor-report-table"><thead><tr><th>参赛方</th><th>各候选与已启用因子库的最大相关性</th></tr></thead><tbody>${registryRows}</tbody></table><table class="factor-report-table"><thead><tr><th>参赛方对</th><th>候选对</th><th>训练相关性</th></tr></thead><tbody>${correlationRows || '<tr><td colspan="3">统一提交后生成跨方诊断，进化期间不向参赛方泄露。</td></tr>'}</tbody></table></div></details><p>相同命题、DSL、训练快照、期限与预算；参赛令牌只可读取本方会话，不可访问密封端点。不会自动调用外部模型或启用因子。</p></section>`;
}

let initialized = false;
let busy = false;
let currentSeason = "";

/** @param {string} path @param {Record<string,unknown>} [payload] */
async function request(path, payload) {
  const response = await apiFetch(
    path,
    payload === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
  );
  const data = await response.json();
  if (!response.ok || !data.ok)
    throw new Error(data.error || data.code || `HTTP ${response.status}`);
  return data;
}

/** @param {string} message */
function status(message) {
  const node = document.getElementById("portfolioArenaStatus");
  if (node) node.textContent = message;
}

/** @param {import('../types/domain').FactorMiningSnapshot} snapshot */
export function updatePortfolioFactorOptions(snapshot) {
  const select = document.getElementById("portfolioFactorSelect");
  if (!(select instanceof HTMLSelectElement)) return;
  const previous = select.value;
  const seen = new Set();
  select.replaceChildren();
  for (const factor of [
    ...(snapshot.composition_factors || []),
    ...(snapshot.factor_library || []),
  ]) {
    if (!factor.key || seen.has(factor.key)) continue;
    seen.add(factor.key);
    const option = select.ownerDocument.createElement("option");
    option.textContent = factor.name || factor.key;
    option.value = factor.key;
    select.add(option);
  }
  if (seen.has(previous)) select.value = previous;
}

export function initPortfolioArena() {
  const host = document.getElementById("portfolioArena");
  if (!host || initialized) return;
  initialized = true;
  host.addEventListener("click", async (event) => {
    const button =
      event.target instanceof Element
        ? event.target.closest("button[data-research-action]")
        : null;
    if (!(button instanceof HTMLButtonElement) || busy) return;
    const action = button.dataset.researchAction;
    const seasonSelect = document.getElementById("arenaSeasonSelect");
    if (seasonSelect instanceof HTMLSelectElement)
      currentSeason = seasonSelect.value;
    if (
      action === "arena-accept" &&
      !window.confirm(
        "一次性打开当前窗口密封样本，失败或中断也不能重试；将评估各方 Top-N 及非竞技场候选，N 保留全部试验。继续吗？",
      )
    )
      return;
    busy = true;
    button.disabled = true;
    status("正在执行；未确认时不会自动重试。");
    try {
      if (action === "refresh") {
        const [seasons, policy] = await Promise.all([
          request("/api/mining/arena"),
          request("/api/mining/portfolio/policy"),
        ]);
        if (seasonSelect instanceof HTMLSelectElement) {
          seasonSelect.replaceChildren();
          for (const season of seasons.seasons)
            seasonSelect.add(
              new Option(
                `${season.arena_id.slice(0, 10)} · ${season.status}`,
                season.arena_id,
              ),
            );
        }
        const editor = document.getElementById("portfolioPolicyJson");
        if (editor instanceof HTMLTextAreaElement)
          editor.value = JSON.stringify(policy.policy, null, 2);
        status(
          policy.locked
            ? "已读取。验收口径已被首个试验冻结；归档后才能修改。"
            : "已读取。默认仅 IC 验收；升级须在首个试验前保存配置。",
        );
      } else if (action === "portfolio") {
        const select = document.getElementById("portfolioFactorSelect");
        if (!(select instanceof HTMLSelectElement) || !select.value)
          throw new Error("请先读取因子库并选择因子");
        const data = await request("/api/mining/portfolio/report", {
          factor_key: select.value,
        });
        const target = document.getElementById("portfolioFactorReport");
        if (target)
          setSafeHtml(
            target,
            data.portfolio
              ? portfolioMarkup(data.portfolio)
              : `<p>${escape(data.reason || "数据不可用")}</p>`,
          );
        status("组合报告已读取；没有增加 N 或打开密封集。");
      } else if (action === "policy") {
        const editor = document.getElementById("portfolioPolicyJson");
        if (!(editor instanceof HTMLTextAreaElement)) return;
        await request("/api/mining/portfolio/policy", JSON.parse(editor.value));
        status("验收配置已保存；首个试验开始后不可修改。");
      } else {
        let data;
        if (action === "arena-create") {
          const input = document.getElementById("arenaProposers");
          const budget = document.getElementById("arenaBudget");
          const topN = document.getElementById("arenaTopN");
          if (
            !(input instanceof HTMLInputElement) ||
            !(budget instanceof HTMLInputElement) ||
            !(topN instanceof HTMLInputElement)
          )
            return;
          data = await request("/api/mining/arena", {
            proposers: input.value
              .split(/[,，]/)
              .map((x) => x.trim())
              .filter(Boolean),
            budget: Number(budget.value),
            top_n: Number(topN.value),
          });
          currentSeason = data.arena_id;
          if (seasonSelect instanceof HTMLSelectElement) {
            seasonSelect.add(
              new Option(currentSeason.slice(0, 10), currentSeason),
            );
            seasonSelect.value = currentSeason;
          }
          const credentials = document.getElementById("arenaCredentials");
          if (credentials instanceof HTMLTextAreaElement)
            credentials.value = JSON.stringify(
              data.participant_credentials,
              null,
              2,
            );
        } else {
          if (!/^[0-9a-f]{32}$/.test(currentSeason))
            throw new Error("请先选择赛季");
          const path = `/api/mining/arena/${currentSeason}`;
          data =
            action === "arena-report"
              ? await request(path)
              : await request(
                  `${path}/${action?.replace("arena-", "")}`,
                  action === "arena-accept"
                    ? { confirm_open_sealed: true }
                    : {},
                );
        }
        const target = document.getElementById("arenaReport");
        if (target) setSafeHtml(target, arenaReportMarkup(data));
        status("赛季状态已确认。只有点击统一验收并确认，才会打开密封集。");
      }
    } catch (error) {
      status(
        `未完成：${error instanceof Error ? error.message : String(error)}。请读取状态核对，不自动重试。`,
      );
    } finally {
      busy = false;
      button.disabled = false;
    }
  });
}
