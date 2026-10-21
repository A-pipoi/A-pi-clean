import { apiFetch } from "./api-client.mjs";

/** @param {Date} now */
export function beijingDay(now) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

/** @param {import('../types/domain').Scheduler|null} scheduler @param {Date} now */
export function manualCatchupEligibility(scheduler, now = new Date()) {
  const day = beijingDay(now);
  const audit = scheduler?.maintenance?.latest;
  const manual = scheduler?.maintenance?.manual_catchups?.find(
    (item) => item.run_date === day,
  );
  const time = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(now);
  const weekday = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    weekday: "short",
  }).format(now);
  let reason = "仅补跑当前正式纸面账户；不操作独立研究账户。";
  let allowed = true;
  if (!scheduler) reason = "正在读取维护记录，请稍候。";
  else if (scheduler.execution_in_progress)
    reason = "当前账户正在执行，不能重复启动。";
  else if (scheduler.enabled === false) reason = "当前账户维护入口未启用。";
  else if (manual) reason = "今日已有人工补跑记录，请查看下方结果。";
  else if (weekday === "Sat" || weekday === "Sun" || time < "16:11")
    reason = "人工补跑仅在工作日北京时间16:11后开放；开市状态仍由后端核验。";
  else if (
    audit?.run_date !== day ||
    audit.status !== "missed_window" ||
    audit.attempts !== 0
  )
    reason = "仅允许当日零次尝试的漏跑账户补跑一次。";
  else return { day, allowed, reason };
  allowed = false;
  return { day, allowed, reason };
}

/** @param {import('../types/domain').PaperState} result */
export function manualCatchupMessage(result) {
  const reason =
    result.skip_reason || result.no_trade_reason || "请查看运行与维护详情。";
  if (result.outcome === "failed") return `人工补跑失败 · ${reason}`;
  if (
    result.outcome === "manual_catchup_not_due" ||
    result.outcome === "already_running"
  )
    return `人工补跑未启动 · ${reason}`;
  if (
    ["blocked", "outside_window", "waiting_data", "market_closed"].includes(
      result.outcome || "",
    ) ||
    (result.skipped && result.outcome !== "completed")
  )
    return `人工补跑被阻止 · ${reason}`;
  if (result.skipped !== false && result.outcome !== "completed")
    return "人工补跑结果未确认，请刷新维护记录。";
  return `人工补跑已完成 · ${result.generated_orders ?? 0} 个委托信号；未补造成交，原漏跑记录保留。`;
}

/** @param {{translate:(text:string)=>string, apply:(data:import('../types/domain').PaperState & {state?:import('../types/domain').PaperState})=>void, refresh:()=>Promise<unknown>, clock?:()=>Date, onBusy?:(busy:boolean)=>void}} options */
export function createManualPaperCatchup(options) {
  /** @type {HTMLButtonElement|null} */
  const foundButton = /** @type {HTMLButtonElement|null} */ (
    document.getElementById("paperManualCatchupButton")
  );
  const foundHint = document.getElementById("paperManualCatchupHint");
  const foundFeedback = document.getElementById("paperManualCatchupResult");
  if (!foundButton || !foundHint || !foundFeedback) return null;
  const button = foundButton;
  const hint = foundHint;
  const feedback = foundFeedback;
  /** @type {import('../types/domain').Scheduler|null} */
  let latest = null;
  let pending = false;
  let submittedDay = "";
  let message = "";
  const clock = options.clock || (() => new Date());
  const text = options.translate;
  /** @param {import('../types/domain').Scheduler|null} scheduler */
  function update(scheduler) {
    latest = scheduler;
    const eligibility = manualCatchupEligibility(latest, clock());
    button.disabled =
      pending || !eligibility.allowed || submittedDay === eligibility.day;
    button.setAttribute("aria-busy", String(pending));
    button.textContent = text(pending ? "人工补跑中…" : "人工补跑");
    hint.textContent = text(eligibility.reason);
    const saved = latest?.maintenance?.manual_catchups?.find(
      (item) => item.run_date === eligibility.day,
    );
    if (saved && !pending) {
      message =
        saved.status === "running"
          ? "人工补跑正在执行，请刷新维护记录。"
          : saved.status === "failed"
            ? "人工补跑失败，请查看运行日志；未标记完成。"
            : manualCatchupMessage(
                saved.result || { skipped: saved.status !== "completed" },
              );
    }
    feedback.textContent = text(
      pending ? "正在核验行情与门禁，请勿重复启动。" : message,
    );
    feedback.hidden = !feedback.textContent;
  }
  button.addEventListener("click", () => {
    const eligibility = manualCatchupEligibility(latest, clock());
    if (
      button.disabled ||
      !eligibility.allowed ||
      document.querySelector(".paper-catchup-dialog")
    )
      return;
    const opener = document.activeElement;
    const dialog = document.createElement("dialog");
    dialog.className =
      "account-security account-security-action paper-catchup-dialog";
    dialog.dataset.slot = "manual-catchup-dialog";
    dialog.setAttribute("aria-labelledby", "paper-catchup-title");
    const title = document.createElement("h2");
    title.id = "paper-catchup-title";
    title.textContent = text("确认人工补跑");
    const description = document.createElement("p");
    description.id = "paper-catchup-description";
    description.textContent = text(
      `仅补跑 ${eligibility.day} 当前登录账号的正式纸面账户。保留原漏跑记录；不撮合旧委托，不回填成交；行情、密封与风控门禁仍然有效，不自动修改验收版本。`,
    );
    dialog.setAttribute("aria-describedby", description.id);
    const status = document.createElement("p");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    const actions = document.createElement("div");
    actions.className = "security-inline-actions";
    actions.dataset.slot = "manual-catchup-actions";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "secondary-button";
    cancel.textContent = text("取消");
    const confirm = document.createElement("button");
    confirm.type = "button";
    confirm.className = "primary-button";
    confirm.textContent = text("确认人工补跑");
    cancel.addEventListener("click", () => {
      if (!pending) dialog.close();
    });
    confirm.addEventListener("click", async () => {
      if (pending || submittedDay === eligibility.day) return;
      const current = manualCatchupEligibility(latest, clock());
      if (!current.allowed || current.day !== eligibility.day) {
        status.textContent = text(
          "日期或维护状态已变化，请关闭弹窗后刷新账户。",
        );
        confirm.disabled = true;
        return;
      }
      pending = true;
      options.onBusy?.(true);
      submittedDay = eligibility.day;
      confirm.disabled = cancel.disabled = true;
      confirm.textContent = text("人工补跑中…");
      dialog.setAttribute("aria-busy", "true");
      status.textContent = text(
        "请求已发出；关闭页面不会取消后端维护。请等待结果。",
      );
      update(latest);
      try {
        const response = await apiFetch("/api/paper/cycle", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            manual_catchup: true,
            expected_day: eligibility.day,
            reason: "用户在已登录工作台确认当日人工补跑",
          }),
        });
        const data = await response.json();
        if (!response.ok || data.ok !== true)
          throw new Error(data.error || `HTTP ${response.status}`);
        options.apply(data);
        message = manualCatchupMessage(data);
      } catch (error) {
        message = `人工补跑结果未确认 · ${error instanceof Error ? error.message : String(error)}。请刷新维护记录，不要重复提交。`;
      } finally {
        pending = false;
        options.onBusy?.(false);
        dialog.setAttribute("aria-busy", "false");
        status.textContent = text(message);
        cancel.disabled = false;
        cancel.textContent = text("关闭");
        confirm.textContent = text("请求已提交");
        update(latest);
        await options.refresh();
        cancel.focus();
      }
    });
    dialog.addEventListener("cancel", (event) => {
      if (pending) event.preventDefault();
    });
    dialog.addEventListener("close", () => {
      dialog.remove();
      if (
        opener instanceof HTMLElement &&
        opener.isConnected &&
        !button.disabled
      )
        opener.focus();
    });
    actions.append(cancel, confirm);
    dialog.append(title, description, status, actions);
    document.body.append(dialog);
    dialog.showModal();
    cancel.focus();
  });
  update(null);
  return { update };
}
