import { apiFetch } from "./api-client.mjs";

/** @typedef {{name:string,version:string,horizon:number,latest_signal:string,latest_factor_coverage:number,signal_status:string,ic_series:{date:string,value:number}[],decay:{status:string,observations?:number,required?:number}}} Monitor */
/** @typedef {{ok:boolean,status:string,attempts?:{date:string,status:string,error:string}[],monitors?:Monitor[]}} LiveReport */
/** @typedef {{ok:boolean,coverage:number,rows:Record<string,{available_at:string}>,last_capture?:{status:string,error:string,updated_at:string},benchmark?:{available_at:string}|null}} IndustryReport */

/** A read-only, account-scoped surface. It never triggers provider requests. */
export function createLiveICPanel() {
  const host = document.getElementById("liveICPanel");
  if (!host) return { refresh: async () => {} };
  const heading = document.createElement("div");
  heading.className = "panel-heading";
  const title = document.createElement("h2");
  const refresh = document.createElement("button");
  refresh.type = "button";
  refresh.className = "secondary-button";
  const actions = document.createElement("div");
  actions.className = "live-ic-actions";
  const horizon = document.createElement("select");
  horizon.setAttribute("aria-label", "IC label horizon / IC 标签交易日数");
  for (const days of [1, 5, 10, 20]) {
    const option = document.createElement("option");
    option.value = String(days);
    option.textContent = String(days);
    horizon.append(option);
  }
  horizon.value = "5";
  actions.append(horizon, refresh);
  heading.append(title, actions);
  const note = document.createElement("p");
  const state = document.createElement("p");
  state.setAttribute("role", "status");
  const industry = document.createElement("p");
  const scroll = document.createElement("div");
  scroll.className = "table-scroll";
  const table = document.createElement("table");
  table.className = "factor-table";
  scroll.append(table);
  host.append(heading, note, state, industry, scroll);
  /** @type {LiveReport|null} */
  let latest = null;
  /** @type {IndustryReport|null} */
  let pit = null;
  let failed = false;
  const english = () => document.documentElement.lang !== "zh-CN";
  /** @param {string} zh @param {string} en */
  const text = (zh, en) => (english() ? en : zh);
  function render() {
    title.textContent = text(
      "上线后样本外 IC 日更",
      "Prospective daily out-of-sample IC",
    );
    refresh.textContent = text("刷新监控状态", "Refresh monitor status");
    note.textContent = text(
      "每日 17:00 后自动更新 · 不消耗预算",
      "Automatic updates at 17:00–19:45 Shanghai time, at most four attempts. Signals are frozen prospectively; 1/5/10/20-session labels must mature, with at least 90% coverage and 30 symbols. No research budget, sealed data, or trading is used.",
    );
    const attempt = latest?.attempts?.[0];
    state.textContent = failed
      ? text(
          "监控接口不可用，请检查服务版本或登录状态。",
          "Monitor API unavailable; check the service version or session.",
        )
      : attempt
        ? `${attempt.date} · ${attempt.status}${attempt.error ? ` · ${attempt.error}` : ""}`
        : text(
            "等待首个可用交易日；尚未产生上线后样本外 IC。",
            "Waiting for the first available market session; no prospective IC yet.",
          );
    const dates = Object.values(pit?.rows || {})
      .map((row) => row.available_at)
      .sort();
    industry.textContent = pit
      ? text(
          `真实行业 PIT 观测覆盖 ${(pit.coverage * 100).toFixed(3)}% · 所选快照最早可得 ${dates[0] || "尚无快照"}。仅在可得时间之后使用，旧历史缺口仍禁用；月度基准权重快照不等于逐日漂移权重。`,
          `Observed industry PIT coverage ${(pit.coverage * 100).toFixed(3)}% · Earliest availability in selected snapshots ${dates[0] || "not yet observed"}. Usable only after availability; earlier history remains disabled. Monthly index snapshots are not daily drifted benchmark weights.`,
        )
      : text(
          "等待真实行业 PIT 快照。",
          "Waiting for actual industry PIT snapshots.",
        );
    table.replaceChildren();
    const head = document.createElement("thead");
    const row = document.createElement("tr");
    for (const label of [
      text(
        `因子 / ${horizon.value}日标签`,
        `Factor / ${horizon.value}-session label`,
      ),
      text("信号日期", "Signal date"),
      text("冻结覆盖", "Frozen coverage"),
      text("成熟样本", "Mature observations"),
      text("最新 IC", "Latest IC"),
      text("监控状态", "Monitor status"),
    ]) {
      const th = document.createElement("th");
      th.scope = "col";
      th.textContent = label;
      row.append(th);
    }
    head.append(row);
    table.append(head);
    const body = document.createElement("tbody");
    for (const item of (latest?.monitors || []).filter(
      (item) => item.horizon === Number(horizon.value),
    )) {
      const row = document.createElement("tr");
      const status =
        item.signal_status === "insufficient_factor_coverage"
          ? text(
              "因子覆盖不足（不生成 IC）",
              "Insufficient factor coverage (no IC)",
            )
          : !item.ic_series.length
            ? text("等待标签成熟", "Waiting for mature labels")
            : item.decay.status === "alert"
              ? text(
                  "持续衰减报警：请人工复核",
                  "Persistent decay alert: review required",
                )
              : item.decay.status === "insufficient_data"
                ? text("衰减窗口样本不足", "Insufficient decay history")
                : item.decay.status;
      for (const value of [
        item.name,
        item.latest_signal,
        `${(item.latest_factor_coverage * 100).toFixed(1)}%`,
        String(item.ic_series.length),
        item.ic_series.length
          ? `${item.ic_series.at(-1)?.date} · ${Number(item.ic_series.at(-1)?.value).toFixed(4)}`
          : "—",
        status,
      ]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      }
      row.title = `${text("不可变定义版本", "Immutable definition version")}: ${item.version}`;
      body.append(row);
    }
    table.append(body);
    table.hidden = !body.childElementCount;
  }
  async function load() {
    refresh.disabled = true;
    try {
      const responses = await Promise.all([
        apiFetch("/api/factors/live-ic", { cache: "no-store" }),
        apiFetch("/api/factors/industry-pit", { cache: "no-store" }),
      ]);
      if (responses.some((response) => !response.ok))
        throw new Error("monitor_unavailable");
      const [report, industry] = await Promise.all(
        responses.map((response) => response.json()),
      );
      if (!report.ok || !Array.isArray(report.monitors) || !industry.ok)
        throw new Error("invalid_monitor_contract");
      latest = report;
      pit = industry;
      failed = false;
    } catch {
      failed = true;
      latest = null;
      pit = null;
    } finally {
      refresh.disabled = false;
      render();
    }
  }
  refresh.addEventListener("click", load);
  horizon.addEventListener("change", render);
  new MutationObserver(render).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["lang"],
  });
  render();
  return { refresh: load };
}
