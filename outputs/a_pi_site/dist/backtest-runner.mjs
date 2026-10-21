import { setSafeHtml } from "./safe-html.mjs";
import { apiFetch as fetch } from "./api-client.mjs";
import { requiredElement, toError } from "./runtime-utils.mjs";
import { selectedRulebookVersion } from "./rulebook-editor.mjs";
import { setExperimentProgress } from "./experiment-progress.mjs";
/** Async Tushare backtest transport and research-only sensitivity execution. */
/** @param {import('../types/services').BacktestRunnerServices} deps */
export function createBacktestRunner(deps) {
  let activeJobId = "";
  const restoreButton = document.createElement("button");
  restoreButton.type = "button";
  restoreButton.className = "secondary-button";
  restoreButton.textContent = deps.localeText("恢复 / 查看最近回测任务");
  const cancelButton = document.createElement("button");
  cancelButton.type = "button";
  cancelButton.className = "secondary-button";
  cancelButton.textContent = deps.localeText("请求取消回测");
  cancelButton.hidden = true;
  restoreButton.className =
    "secondary-button ghost-button backtest-restore-button";
  const primaryRow = document.querySelector(
    "#backtestRunSection .config-actions__primary",
  );
  if (primaryRow) {
    const runBtn = document.getElementById("configRunButton");
    if (runBtn) runBtn.after(restoreButton);
    else primaryRow.append(restoreButton);
    document.getElementById("backtestProgress")?.after(cancelButton);
  } else {
    document
      .getElementById("backtestProgress")
      ?.after(restoreButton, cancelButton);
  }
  /** @param {string} jobId */
  async function monitor(jobId) {
    while (true) {
      const response = await fetch(
        `/api/backtest/${encodeURIComponent(jobId)}`,
        { cache: "no-store", signal: AbortSignal.timeout(15000) },
      );
      const job = await response.json();
      if (!response.ok || !job.ok)
        throw new Error(job.error || `HTTP ${response.status}`);
      progress(
        job.status,
        deps.localeText(job.message || "回测运行中"),
        job.progress,
        `任务 ${jobId} · 结果持久保存，关闭页面不会取消任务`,
      );
      if (job.status === "completed") {
        activeJobId = "";
        cancelButton.hidden = true;
        return {
          ...job.result,
          job_id: job.job_id,
          completed_at: job.completed_at || job.created_at,
        };
      }
      if (job.status === "cancelled") {
        activeJobId = "";
        cancelButton.hidden = true;
        await deps.loadGovernance(false);
        return null;
      }
      if (job.status === "failed") {
        activeJobId = "";
        cancelButton.hidden = true;
        throw new Error(
          `${job.error || "回测任务失败"}；已保留原默认展示和上一成功结果`,
        );
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1200));
    }
  }
  async function recentJob() {
    const response = await fetch("/api/backtest/jobs", {
      cache: "no-store",
      signal: AbortSignal.timeout(15000),
    });
    const data = await response.json();
    if (!response.ok || !data.ok)
      throw new Error(data.error || `HTTP ${response.status}`);
    /** @type {import('../types/domain').BacktestJob[]} */
    const jobs = data.jobs || [];
    return (
      jobs.find((job) =>
        ["queued", "running", "cancelling"].includes(job.status || ""),
      ) || jobs[0]
    );
  }
  let monitoring = false;
  restoreButton.addEventListener("click", async () => {
    if (monitoring) return;
    monitoring = true;
    restoreButton.disabled = true;
    try {
      const job = await recentJob();
      if (!job?.job_id) {
        deps.toast("暂无回测任务");
        return;
      }
      activeJobId = job.job_id;
      cancelButton.hidden = !["queued", "running", "cancelling"].includes(
        job.status || "",
      );
      const result = await monitor(job.job_id);
      if (result) deps.applyResult(result);
      await deps.loadGovernance(false);
    } catch (caught) {
      progress(
        "paused",
        "回测连接中断，可恢复查看任务",
        0,
        toError(caught).message,
      );
    } finally {
      monitoring = false;
      restoreButton.disabled = false;
    }
  });
  cancelButton.addEventListener("click", async () => {
    if (!activeJobId) return;
    cancelButton.disabled = true;
    try {
      const response = await fetch(
        `/api/backtest/${encodeURIComponent(activeJobId)}/cancel`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
          signal: AbortSignal.timeout(15000),
        },
      );
      const data = await response.json();
      if (!response.ok || !data.ok)
        throw new Error(
          data.message || data.error || `HTTP ${response.status}`,
        );
      progress(
        "cancelling",
        "已请求取消，等待安全检查点",
        0,
        "取消不会退还已记账的研究预算，也不会删除已完成结果",
      );
    } catch (caught) {
      deps.toast(toError(caught).message);
    } finally {
      cancelButton.disabled = false;
    }
  });
  /** @param {string} state @param {string} message @param {unknown} value */
  function progress(state, message, value, meta = "") {
    setExperimentProgress({
      kind: "backtest",
      message,
      value,
      meta: meta || "Tushare 实时行情 · Python 异步任务",
      error: state === "error",
    });
  }

  /** @param {string} jobId @param {(job: import('../types/domain').BacktestJob) => void} onProgress @param {number} timeoutMs */
  function subscribe(jobId, onProgress, timeoutMs) {
    if (typeof WebSocket !== "function") return Promise.resolve(false);
    return new Promise((resolve) => {
      let settled = false;
      let terminal = false;
      /** @type {WebSocket | undefined} */
      let socket;
      const finish =
        /** @param {unknown} value */
        (value) => {
          if (settled) return;
          settled = true;
          window.clearTimeout(timer);
          try {
            socket?.close();
          } catch (_Caught) {
            const _ = toError(_Caught); /* already closed */
          }
          resolve(value);
        };
      const timer = window.setTimeout(() => finish(null), timeoutMs);
      try {
        const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
        socket = new WebSocket(
          `${scheme}//${window.location.host}/api/ws/backtest/${encodeURIComponent(jobId)}`,
        );
        socket.onmessage = (event) => {
          try {
            const state = JSON.parse(event.data);
            onProgress(state);
            if (
              ["completed", "failed", "cancelled", "not_found"].includes(
                state.status,
              )
            ) {
              terminal = true;
              finish(state.status);
            }
          } catch (_Caught) {
            const _ =
              toError(_Caught); /* malformed event: retain polling fallback */
          }
        };
        socket.onerror = () => finish(false);
        socket.onclose = () => {
          if (!terminal) finish(false);
        };
      } catch (_Caught) {
        const _ = toError(_Caught);
        finish(false);
      }
    });
  }

  /** @param {HTMLButtonElement | null} button */
  async function run(button) {
    if (!button || button.dataset.running === "true" || monitoring) return null;
    const originalLabel = button.innerHTML;
    button.dataset.running = "true";
    button.disabled = true;
    setSafeHtml(button, deps.localeText("Python 回测中…"));
    try {
      const preset = deps.backtestPreset();
      if (preset.strategy === "rulebook")
        preset.recipe_version = selectedRulebookVersion(preset.strategy);
      const existing = await recentJob();
      if (
        existing?.job_id &&
        ["queued", "running", "cancelling"].includes(existing.status || "")
      ) {
        activeJobId = existing.job_id;
        cancelButton.hidden = false;
        monitoring = true;
        const result = await monitor(existing.job_id);
        if (result) deps.applyResult(result);
        return result;
      }
      monitoring = true;
      progress(
        "running",
        deps.localeText("回测任务已提交，正在请求 Tushare 实时行情"),
        5,
      );
      const response = await fetch("/api/backtest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(preset),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        const error = new Error(data.error || `HTTP ${response.status}`);
        error.code = data.code || "";
        throw error;
      }
      if (data.status !== "queued" || !data.job_id) {
        if (data.ok && data.metrics) {
          deps.applyResult(data);
          await deps.loadGovernance(false);
          progress(
            "completed",
            deps.localeText("真实 Tushare 回测已完成"),
            100,
            "兼容同步回测响应",
          );
          deps.toast(
            `Tushare Python 回测完成 · ${data.fills ?? 0} 笔成交 · 成本 ¥${deps.formatNumber(data.total_cost)}`,
          );
          return data;
        }
        throw new Error("回测服务未返回异步任务编号");
      }
      activeJobId = data.job_id;
      cancelButton.hidden = false;
      const result = await monitor(data.job_id);
      if (!result) return null;
      deps.applyResult(result);
      await deps.loadGovernance(false);
      progress(
        "completed",
        deps.localeText("真实 Tushare 回测已完成"),
        100,
        `任务 ${data.job_id} · 数据源 ${deps.dataSourceLabel(result.dataset_metadata?.data_source || "tushare", false)}`,
      );
      deps.toast(
        `Tushare Python 回测完成 · ${result.fills ?? 0} 笔成交 · 成本 ¥${deps.formatNumber(result.total_cost)}`,
      );
      return result;
    } catch (errorCaught) {
      const error = toError(errorCaught);
      progress(
        "error",
        deps.localeText(
          activeJobId
            ? "任务连接中断，请恢复查看；后台任务保留"
            : "真实 Tushare 回测失败",
        ),
        0,
        error.message || "未知错误",
      );
      const serviceUnavailable =
        error instanceof TypeError || error.name === "TypeError";
      const message =
        window.location.protocol === "file:"
          ? "请用 python -m quant.api_server --port 8000 启动网页与回测服务"
          : error.code === "data_provider_unavailable"
            ? "Tushare 数据源暂不可用，请检查 Token、权限或网络"
            : serviceUnavailable
              ? "Python 回测服务未连接，请检查启动命令"
              : "回测执行失败，请检查参数或查看服务日志";
      deps.toast(`${message} · ${error.message}`);
      if (error.code === "research_budget_exceeded")
        await deps.loadGovernance(false);
      return null;
    } finally {
      monitoring = false;
      button.dataset.running = "false";
      deps.syncBudgetControls();
      setSafeHtml(button, originalLabel);
    }
  }

  /** @param {HTMLButtonElement | null} button */
  async function sensitivity(button) {
    if (!button || button.dataset.running === "true") return null;
    const result = requiredElement("sensitivityResult");
    const originalLabel = button.innerHTML;
    button.dataset.running = "true";
    button.disabled = true;
    setSafeHtml(button, deps.localeText("扫描中…"));
    if (result) {
      result.className = "sensitivity-result";
      result.textContent = deps.localeText(
        "正在调用 Tushare 真实行情，运行参数敏感性验证…",
      );
    }
    try {
      const response = await fetch("/api/validation/sensitivity", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...deps.backtestPreset(),
          delta: 0.3,
        }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        const error = new Error(data.error || `HTTP ${response.status}`);
        error.code = data.code || "";
        throw error;
      }
      const scan = data.sensitivity || {};
      const quality = scan.passed ? "通过" : "需要复核";
      const gate = data.validation_gate || data.research?.validation_gate || {};
      deps.setText(
        "authBaselineSharpe",
        gate.conclusion_ready === true
          ? deps.localeText("验证通过")
          : deps.localeText(gate.status || "待敏感性检验"),
      );
      deps.setText(
        "backtestStatus",
        deps.localeText(gate.status || (scan.passed ? "验证通过" : "需要复核")),
      );
      deps.setText(
        "backtestAudit",
        gate.conclusion_ready === true
          ? "验证通过 · 参数敏感性已通过"
          : deps.localeText("验证完成，待敏感性检验；指标不作结论"),
      );
      if (data.research)
        deps.setText(
          "backtestRunCount",
          `${data.research.run_count ?? data.research.runs ?? "—"} / ${data.research.budget ?? "—"} 次`,
        );
      if (result) {
        result.className = `sensitivity-result ${scan.passed ? "success" : "warning"}`;
        result.textContent = deps.localeText(
          `${quality} · ${scan.trial_count || 0} 组参数 · 多重检验总数 ${scan.multiple_testing_trials || scan.trial_count || 0} · ${scan.note || "请结合密封测试集判断"} 研究台账 #${data.research?.experiment_id ?? "—"}`,
        );
      }
      deps.toast(`参数敏感性扫描${quality}`);
      await deps.loadGovernance(false);
      return data;
    } catch (errorCaught) {
      const error = toError(errorCaught);
      if (result) {
        result.className = "sensitivity-result warning";
        result.textContent = deps.localeText(`扫描失败 · ${error.message}`);
      }
      deps.toast(`参数敏感性扫描失败 · ${error.message}`);
      if (error.code === "research_budget_exceeded")
        await deps.loadGovernance(false);
      return null;
    } finally {
      button.dataset.running = "false";
      deps.syncBudgetControls();
      setSafeHtml(button, originalLabel);
    }
  }

  return { run, sensitivity, progress, subscribe };
}

/** @type {ReturnType<typeof createBacktestRunner> | undefined} */
let runner;
export const APiBacktestRunner = {
  /** @param {import('../types/services').BacktestRunnerServices} deps */
  init(deps) {
    runner = createBacktestRunner(deps);
  },
  /** @param {HTMLButtonElement | null} button */
  run(button) {
    return runner?.run(button) ?? Promise.resolve(null);
  },
  /** @param {HTMLButtonElement | null} button */
  sensitivity(button) {
    return runner?.sensitivity(button) ?? Promise.resolve(null);
  },
};
