import { apiFetch as fetch } from "./api-client.mjs";
import { requiredElement, toError } from "./runtime-utils.mjs";
import { selectedRulebookVersion } from "./rulebook-editor.mjs";
/** Explicit, audited research-cycle archive and sealed-test actions. */
/** @param {import('../types/services').GovernanceActionsServices} deps */
export function createGovernanceActions(deps) {
  let requestId = 0;
  let strategy = "";
  let submitting = false;
  let ready = false;

  /** @type {import('../types/services').SharedServices['setStatus']} */
  function setStatus(message, state = "") {
    const host = requiredElement("archiveResearchStatus");
    if (!host) return;
    host.textContent = deps.localeText(message);
    if (state) host.dataset.state = state;
    else delete host.dataset.state;
  }

  /** @param {string} message */
  function setError(message = "") {
    const host = requiredElement("archiveResearchError");
    if (!host) return;
    host.textContent = deps.localeText(message);
    host.hidden = !message;
  }

  function close() {
    if (submitting) return;
    requestId += 1;
    strategy = "";
    ready = false;
    const dialog = requiredElement("archiveResearchDialog");
    if (dialog?.open) dialog.close();
  }

  async function openArchive() {
    if (submitting) return;
    const select = requiredElement("strategySelect");
    const selectedStrategy = select?.value;
    if (!selectedStrategy) {
      setStatus("请先选择需要归档的策略", "error");
      return;
    }
    const dialog = requiredElement("archiveResearchDialog");
    if (!dialog || dialog.open) return;
    strategy = selectedStrategy;
    ready = false;
    const activeRequest = ++requestId;
    const label = select.selectedOptions?.[0]?.textContent?.trim() || strategy;
    const summary = requiredElement("archiveResearchSummary");
    const confirm = requiredElement("archiveResearchConfirm");
    const reason = requiredElement("archiveResearchReason");
    if (summary)
      summary.textContent = deps.localeText(
        `${label} · 正在读取当前策略的研究记录…`,
      );
    if (confirm) confirm.disabled = true;
    if (reason) reason.value = "";
    setError();
    dialog.showModal();
    try {
      const response = await fetch(
        `/api/research/ledger?strategy=${encodeURIComponent(strategy)}`,
        { cache: "no-store" },
      );
      const ledger = await response.json();
      if (activeRequest !== requestId || !dialog.open) return;
      if (!response.ok || !ledger.ok)
        throw new Error(ledger.error || `HTTP ${response.status}`);
      const runs = Number(ledger.run_count ?? ledger.runs) || 0;
      const sealed = ledger.sealed_test?.status === "passed";
      if (summary)
        summary.textContent = deps.localeText(
          `${label} · ${runs} 条研究记录${sealed ? " · 含已通过的密封测试" : ""}；仅归档此策略`,
        );
      if (runs || sealed) {
        ready = true;
        if (confirm) confirm.disabled = false;
        reason?.focus();
      } else setError("当前策略没有可归档的研究记录");
    } catch (errorCaught) {
      const error = toError(errorCaught);
      if (activeRequest !== requestId || !dialog.open) return;
      setError(`无法读取研究台账 · ${error.message}，请稍后重试`);
    }
  }

  /** @param {Event} event */
  async function submitArchive(event) {
    event.preventDefault();
    if (submitting || !strategy || !ready) return;
    const reason = requiredElement("archiveResearchReason")?.value.trim() || "";
    if (!reason) {
      setError("请输入归档原因");
      requiredElement("archiveResearchReason")?.focus();
      return;
    }
    submitting = true;
    const button = requiredElement("archiveResearchConfirm");
    if (button) {
      button.disabled = true;
      button.textContent = deps.localeText("归档中…");
    }
    setError();
    setStatus("正在归档当前策略的研究周期…");
    try {
      const response = await fetch("/api/research/ledger/archive", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ strategy, reason }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok)
        throw new Error(data.error || `HTTP ${response.status}`);
      const archived = Number(data.archive?.archived_count) || 0;
      const sealed = Number(data.archive?.sealed_archived_count) || 0;
      requestId += 1;
      strategy = "";
      ready = false;
      requiredElement("archiveResearchDialog")?.close();
      await deps.loadGovernance(true);
      setStatus(
        `归档成功 · ${archived} 条研究记录、${sealed} 个密封测试已保留审计记录`,
        "success",
      );
    } catch (errorCaught) {
      const error = toError(errorCaught);
      setError(`归档失败 · ${error.message}，请检查后重试`);
      setStatus(`归档失败 · ${error.message}`, "error");
    } finally {
      submitting = false;
      if (button) {
        button.disabled = false;
        button.textContent = deps.localeText("确认归档");
      }
    }
  }

  async function runSealedTest() {
    const selectedStrategy =
      requiredElement("strategySelect")?.value || "baseline";
    if (
      !window.confirm(
        deps.localeText(
          "执行密封测试？每个研究周期仅可执行一次，失败或中断也会消耗机会并留痕。",
        ),
      )
    )
      return;
    const button = requiredElement("runSealedTestButton");
    if (button) button.disabled = true;
    try {
      const response = await fetch("/api/validation/sealed", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          strategy: selectedStrategy,
          recipe_version: selectedRulebookVersion(selectedStrategy),
          universe_mode: "pit_index",
        }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok)
        throw new Error(data.error || `HTTP ${response.status}`);
      const result = data.sealed_test?.result || {};
      deps.toast(
        data.sealed_test?.status === "passed"
          ? `密封测试通过 · Sharpe ${deps.formatNumber(result.sharpe, 2)}`
          : "密封测试未通过，暂不允许生成纸面委托",
      );
      await deps.loadGovernance(true);
    } catch (errorCaught) {
      const error = toError(errorCaught);
      deps.toast(`密封测试执行失败 · ${error.message}`);
    } finally {
      if (button) button.disabled = false;
    }
  }

  function init() {
    requiredElement("archiveResearchButton")?.addEventListener(
      "click",
      openArchive,
    );
    requiredElement("archiveResearchForm")?.addEventListener(
      "submit",
      submitArchive,
    );
    requiredElement("archiveResearchCancel")?.addEventListener("click", close);
    requiredElement("archiveResearchClose")?.addEventListener("click", close);
    requiredElement("archiveResearchDialog")?.addEventListener(
      "cancel",
      (event) => {
        if (submitting) event.preventDefault();
        else {
          requestId += 1;
          strategy = "";
          ready = false;
        }
      },
    );
    requiredElement("runSealedTestButton")?.addEventListener(
      "click",
      runSealedTest,
    );
  }

  return { init, openArchive, setStatus, isSubmitting: () => submitting };
}

/** @type {ReturnType<typeof createGovernanceActions> | undefined} */
let governanceActions;
export const APiGovernanceActions = {
  /** @param {import('../types/services').GovernanceActionsServices} deps */
  init(deps) {
    governanceActions = createGovernanceActions(deps);
    governanceActions.init();
  },
  openArchive() {
    return governanceActions?.openArchive();
  },
  /** @param {string} message @param {string} state */
  setStatus(message, state) {
    governanceActions?.setStatus(message, state);
  },
  isSubmitting() {
    return governanceActions?.isSubmitting() ?? false;
  },
};
