import { apiFetch as fetch } from "./api-client.mjs";
import { requiredElement, toError } from "./runtime-utils.mjs";
/** Factor registration owns its dialog, governance gate and submission lifecycle. */
/** @param {import('../types/services').FactorRegistrationServices} deps */
export function createFactorRegistration(deps) {
  let canSubmit = false;
  let submitting = false;
  let gateRequest = 0;

  /** @param {string} state @param {string} message */
  function setGate(state, message) {
    const gate = requiredElement("factorRegistrationGate");
    const submit = requiredElement("factorRegistrationSubmit");
    if (gate) {
      gate.dataset.state = state;
      gate.textContent = message;
    }
    canSubmit = state === "ready";
    if (submit) submit.disabled = !canSubmit || submitting;
  }

  async function refreshGate() {
    const requestId = ++gateRequest;
    setGate("checking", "正在读取真实治理状态与预算…");
    const error = requiredElement("factorRegistrationError");
    if (error) error.hidden = true;
    const governance = requiredElement("factorRegistrationGovernance");
    if (governance) governance.hidden = true;
    try {
      const responses = await Promise.all([
        fetch("/api/paper/sop", { cache: "no-store" }),
        fetch("/api/research/ledger?strategy=factor-lab", {
          cache: "no-store",
        }),
      ]);
      const [sop, ledger] = await Promise.all(
        responses.map((response) => response.json()),
      );
      if (!responses[0].ok || !sop.ok)
        throw new Error(sop.error || "SOP HTTP " + responses[0].status);
      if (!responses[1].ok || !ledger.ok)
        throw new Error(ledger.error || "预算 HTTP " + responses[1].status);
      if (requestId !== gateRequest || !requiredElement("utilityDialog")?.open)
        return;
      const registry = sop.factor_registry || {};
      const sealed =
        Boolean(sop.sealed_test?.sealed) ||
        sop.sealed_test?.status === "passed";
      const remaining = Math.max(0, Number(ledger.remaining) || 0);
      const budget =
        "factor-lab 预算：已用 " +
        (ledger.runs ?? 0) +
        "/" +
        (ledger.budget ?? "—") +
        "，剩余 " +
        remaining +
        " 次。";
      if (registry.locked || sealed) {
        const reason = sealed
          ? "已有通过并封存的密封测试，因子集合已冻结。"
          : registry.reason || "当前研究阶段已冻结因子集合。";
        setGate(
          "blocked",
          "当前不可新增。" +
            reason +
            " " +
            budget +
            "如需调整，请先到研究治理归档当前周期；此处不会自动归档。",
        );
        if (governance) governance.hidden = false;
      } else if (ledger.can_record !== true || remaining < 1) {
        setGate(
          "blocked",
          "研究预算已用尽，当前不能新增。" +
            budget +
            "请到研究治理查看归档选项。",
        );
        if (governance) governance.hidden = false;
      } else {
        setGate("ready", "可以注册，本次登记会占用 1 次研究预算。" + budget);
      }
    } catch (errCaught) {
      const err = toError(errCaught);
      if (requestId === gateRequest && requiredElement("utilityDialog")?.open) {
        setGate(
          "error",
          "无法确认注册条件：" +
            err.message +
            "。为避免绕过治理检查，暂不允许提交。",
        );
      }
    }
  }

  /** @param {Event} event */
  async function submit(event) {
    event.preventDefault();
    const form = /** @type {HTMLFormElement} */ (event.currentTarget);
    if (!form.reportValidity() || !canSubmit || submitting) return;
    const button = requiredElement("factorRegistrationSubmit");
    const error = requiredElement("factorRegistrationError");
    const type = requiredElement("factorRegistrationType").value;
    submitting = true;
    button.disabled = true;
    button.textContent = "正在保存…";
    button.setAttribute("aria-busy", "true");
    error.hidden = true;
    try {
      const response = await fetch("/api/factors", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: requiredElement("factorRegistrationName").value.trim(),
          factor_type: type,
          window:
            type === "fundamental"
              ? 0
              : Number(requiredElement("factorRegistrationWindow").value),
          metric:
            type === "fundamental"
              ? requiredElement("factorRegistrationMetric").value
              : "",
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        if (response.status === 409 || response.status === 429) {
          canSubmit = false;
          setGate(
            "blocked",
            data.error || "服务端治理条件已变化，请重新检查。",
          );
          requiredElement("factorRegistrationGovernance").hidden = false;
        }
        throw new Error(data.error || "HTTP " + response.status);
      }
      deps.registered(data.factor);
      deps.closeDialog();
      deps.toast(
        "因子“" +
          (data.factor?.name || "新因子") +
          "”已写入注册表；实时值待 Tushare 计算",
      );
      void deps.reloadWorkspace();
    } catch (errCaught) {
      const err = toError(errCaught);
      error.textContent = "注册失败：" + err.message;
      error.hidden = false;
    } finally {
      submitting = false;
      if (button.isConnected) {
        button.removeAttribute("aria-busy");
        button.textContent = "保存并注册";
        button.disabled = !canSubmit;
      }
    }
  }

  function open() {
    canSubmit = false;
    submitting = false;
    const body = [
      '<div class="factor-registration"><p class="factor-registration-description">新增定义会写入本地因子注册表，并计入 factor-lab 研究预算。通过密封测试后，因子集合按治理规则冻结。</p>',
      '<div class="factor-registration-gate" id="factorRegistrationGate" data-state="checking" role="status" aria-live="polite">正在读取真实治理状态与预算…</div>',
      '<form class="factor-registration-form" id="factorRegistrationForm">',
      '<label for="factorRegistrationName">因子名称</label><input id="factorRegistrationName" name="name" type="text" minlength="2" maxlength="60" placeholder="例如：20日反转" autocomplete="off" required>',
      '<label for="factorRegistrationType">因子类型</label><select id="factorRegistrationType" name="factor_type" required><option value="momentum">动量</option><option value="reversal" selected>反转</option><option value="volatility">波动率</option><option value="fundamental">基本面</option></select>',
      '<label id="factorRegistrationWindowLabel" for="factorRegistrationWindow">计算窗口（交易日，3–120）</label><input id="factorRegistrationWindow" name="window" type="number" min="3" max="120" step="1" value="20" required>',
      '<label id="factorRegistrationMetricLabel" for="factorRegistrationMetric" hidden>基本面指标</label><select id="factorRegistrationMetric" name="metric" hidden><option value="roe">净资产收益率（ROE）</option><option value="netprofit_yoy">净利润同比</option><option value="tr_yoy">营业收入同比</option><option value="debt_to_assets">资产负债率</option></select>',
      '<p class="factor-registration-error" id="factorRegistrationError" role="alert" hidden></p>',
      '<div class="utility-dialog-actions factor-registration-actions"><button class="text-button" id="factorRegistrationRetry" type="button">重新检查</button><button class="text-button" id="factorRegistrationGovernance" type="button" hidden>查看研究治理</button><button class="primary-button" id="factorRegistrationSubmit" type="submit" disabled>保存并注册</button></div></form></div>',
    ].join("");
    deps.openDialog("新增因子", body);
    const form = requiredElement("factorRegistrationForm");
    const type = requiredElement("factorRegistrationType");
    const windowInput = requiredElement("factorRegistrationWindow");
    const metric = requiredElement("factorRegistrationMetric");
    const syncFields = () => {
      const fundamental = type.value === "fundamental";
      requiredElement("factorRegistrationWindowLabel").hidden = fundamental;
      windowInput.hidden = fundamental;
      windowInput.required = !fundamental;
      requiredElement("factorRegistrationMetricLabel").hidden = !fundamental;
      metric.hidden = !fundamental;
      metric.required = fundamental;
    };
    type.addEventListener("change", syncFields);
    syncFields();
    requiredElement("factorRegistrationRetry").addEventListener(
      "click",
      refreshGate,
    );
    requiredElement("factorRegistrationGovernance").addEventListener(
      "click",
      () => {
        deps.closeDialog();
        deps.showGovernance();
      },
    );
    form.addEventListener("submit", submit);
    requiredElement("factorRegistrationName").focus();
    void refreshGate();
  }

  return { open, refreshGate };
}

/** @type {ReturnType<typeof createFactorRegistration> | undefined} */
let registrationInstance;
export const APiFactorRegistration = {
  /** @param {import('../types/services').FactorRegistrationServices} dependencies */
  init(dependencies) {
    registrationInstance = createFactorRegistration(dependencies);
  },
  open() {
    registrationInstance?.open();
  },
};
