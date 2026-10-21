import { setSafeHtml } from "./safe-html.mjs";
import { apiFetch as fetch } from "./api-client.mjs";
import { requiredElement, toError } from "./runtime-utils.mjs";
/** workspace controller with explicit services and live state accessors. */
/** @param {import('../types/services').WorkspaceControllerServices} deps */
export function createWorkspaceController(deps) {
  /** @type {import('../types/services').SharedServices['loadWorkspace']} */
  async function loadWorkspace(showFailure = false) {
    if (deps.workspaceLoading) return false;
    deps.workspaceLoading = true;
    if (deps.workspaceRetryTimer) {
      clearTimeout(deps.workspaceRetryTimer);
      deps.workspaceRetryTimer = null;
    }
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 25000);
      let response;
      try {
        response = await fetch("/api/workspace", {
          cache: "no-store",
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timeout);
      }
      const data = await response.json();
      if (response.status === 202 && data.code === "workspace_building") {
        deps.workspaceBuilding = true;
        deps.workspaceLoadError = null;
        deps.workspaceRetryAttempt = 0;
        deps.workspaceRetryDelaySeconds = 15;
        deps.renderDataProbeStatus();
        scheduleWorkspaceRetry(15);
        return false;
      }
      if (!response.ok || !data.ok)
        throw new Error(data.error || `HTTP ${response.status}`);
      deps.workspaceBuilding = false;
      if (deps.workspaceRetryTimer) {
        clearTimeout(deps.workspaceRetryTimer);
        deps.workspaceRetryTimer = null;
      }
      deps.workspaceLoadError = null;
      deps.workspaceRetryAttempt = 0;
      deps.workspaceRetryDelaySeconds = 0;
      deps.applyWorkspaceSnapshot(data);
      await deps.loadGovernance(false);
      await deps.loadOperationsStatus();
      return true;
    } catch (errorCaught) {
      const error = toError(errorCaught);
      deps.workspaceBuilding = false;
      const message =
        error.name === "AbortError"
          ? "工作区请求超过 25 秒，请检查 Tushare 网络或后端版本"
          : error.message;
      deps.workspaceLoadError = `工作区加载失败：${message}`;
      deps.workspaceRetryDelaySeconds = Math.min(
        300,
        15 * 2 ** Math.min(deps.workspaceRetryAttempt, 5),
      );
      deps.workspaceRetryAttempt += 1;
      deps.loadFactorDefinitions();
      deps.setText("todayMarketStatus", "数据不可用");
      deps.setText("todayBacktestStatus", "数据不可用");
      deps.renderDataProbeStatus();
      deps.renderDataHealth(
        deps.latestWorkspaceSnapshot,
        deps.notificationState.health,
      );
      const guardrail = requiredElement("guardrailList");
      if (guardrail)
        setSafeHtml(
          guardrail,
          `<div class="audit-row"><span class="audit-warn">!</span><span>${deps.escapeHtml(message)}</span><b>加载失败</b></div>`,
        );
      if (showFailure) deps.showToast(`真实数据工作台加载失败 · ${message}`);
      scheduleWorkspaceRetry(deps.workspaceRetryDelaySeconds);
      return false;
    } finally {
      deps.workspaceLoading = false;
    }
  }

  /** @param {number} delaySeconds */
  function scheduleWorkspaceRetry(delaySeconds) {
    if (deps.workspaceRetryTimer) clearTimeout(deps.workspaceRetryTimer);
    deps.workspaceRetryDelaySeconds = Math.max(1, Number(delaySeconds) || 15);
    deps.workspaceRetryTimer = setTimeout(() => {
      deps.workspaceRetryTimer = null;
      loadWorkspace(false);
    }, deps.workspaceRetryDelaySeconds * 1000);
    deps.renderDataProbeStatus();
  }

  /** @param {boolean} open */
  function setWorkspaceMenuOpen(open) {
    const button = requiredElement("workspaceSwitcher");
    const menu = requiredElement("workspaceMenu");
    if (!button || !menu) return;
    menu.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
  }

  /** @param {import('../types/domain').WorkspaceCatalog} data */
  function renderWorkspaceCatalog(data) {
    const button = requiredElement("workspaceSwitcher");
    if (button) {
      button.disabled =
        data.can_switch !== true || (data.workspaces || []).length < 2;
      button.title = button.disabled
        ? "当前为单工作区，无切换功能"
        : "切换工作区";
      button.setAttribute("aria-expanded", "false");
      if (button.disabled) button.removeAttribute("aria-haspopup");
      else button.setAttribute("aria-haspopup", "listbox");
    }
    const list = requiredElement("workspaceMenuList");
    const name = requiredElement("workspaceName");
    const items = Array.isArray(data?.workspaces) ? data.workspaces : [];
    const currentId = String(data?.current_id || "");
    if (!list) return;
    if (!items.length) {
      setSafeHtml(
        list,
        '<div class="workspace-menu-empty">服务没有返回可用工作区。</div>',
      );
      return;
    }
    const current =
      items.find((item) => String(item.id) === currentId) || items[0];
    if (name && current?.name)
      name.textContent = `${deps.localeText(current.name)} · ${document.documentElement.dataset.username || ""}`;
    const mainDot = requiredElement("workspaceStatusDot");
    if (mainDot)
      mainDot.classList.toggle("is-degraded", current?.status !== "active");
    setSafeHtml(
      list,
      items
        .map((item) => {
          const active = currentId
            ? String(item.id) === currentId
            : Boolean(item.active);
          const description = item.description || "由服务接口返回的工作区";
          const stateClass =
            item.status === "active" ? "active" : "is-degraded";
          return `<button class="workspace-menu-item${active ? " active" : ""}" type="button" role="option" aria-selected="${active}" data-workspace-id="${deps.escapeHtml(item.id)}">
        <span class="status-dot ${stateClass}" aria-hidden="true"></span>
        <span class="workspace-menu-copy"><strong>${deps.escapeHtml(item.name || item.id)}</strong><small>${deps.escapeHtml(description)}</small></span>
        <span class="workspace-menu-state" title="${active ? "当前" : "切换"}">${active ? "当前" : "切换"}</span>
      </button>`;
        })
        .join(""),
    );
    deps.applyLocale(deps.activeLocale);
  }

  async function loadWorkspaceCatalog() {
    if (deps.workspaceCatalogLoading) return;
    const list = requiredElement("workspaceMenuList");
    deps.workspaceCatalogLoading = true;
    if (list)
      setSafeHtml(
        list,
        '<div class="workspace-menu-loading">正在读取工作区接口…</div>',
      );
    try {
      const response = await fetch("/api/workspaces", { cache: "no-store" });
      const data = await response.json();
      if (!response.ok || !data.ok)
        throw new Error(data.error || "HTTP " + response.status);
      deps.workspaceCatalog = data;
      renderWorkspaceCatalog(data);
    } catch (errorCaught) {
      const error = toError(errorCaught);
      if (list)
        setSafeHtml(
          list,
          `<div class="workspace-menu-error">工作区接口暂不可用：${deps.escapeHtml(error.message)}</div>`,
        );
      const button = requiredElement("workspaceSwitcher");
      if (button) {
        button.disabled = true;
        button.title = "工作区接口不可用，未确认切换能力";
        button.removeAttribute("aria-haspopup");
      }
    } finally {
      deps.workspaceCatalogLoading = false;
    }
  }

  /** @param {string} workspaceId */
  async function selectWorkspace(workspaceId) {
    const selected = (deps.workspaceCatalog?.workspaces || []).find(
      (item) => String(item.id) === String(workspaceId),
    );
    if (!selected) return;
    try {
      const response = await fetch("/api/workspaces/select", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace_id: workspaceId }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok)
        throw new Error(data.error || "HTTP " + response.status);
      const current = data.workspace || selected;
      const name = requiredElement("workspaceName");
      if (name)
        name.textContent = `${deps.localeText(current.name || current.id)} · ${document.documentElement.dataset.username || ""}`;
      deps.workspaceCatalog = {
        ...(deps.workspaceCatalog || {}),
        current_id: current.id,
      };
      renderWorkspaceCatalog(deps.workspaceCatalog);
      setWorkspaceMenuOpen(false);
      deps.showToast(
        data.persisted === false
          ? "已确认当前工作区 · 暂无其他工作区"
          : `已切换至 ${current.name || current.id}`,
      );
    } catch (errorCaught) {
      const error = toError(errorCaught);
      deps.showToast(`工作区切换失败 · ${error.message}`);
    }
  }

  function initWorkspaceSelector() {
    const button = requiredElement("workspaceSwitcher");
    const menu = requiredElement("workspaceMenu");
    const list = requiredElement("workspaceMenuList");
    if (!button || !menu || !list) return;
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const open = menu.hidden;
      setWorkspaceMenuOpen(open);
      if (open) loadWorkspaceCatalog();
    });
    list.addEventListener("click", (event) => {
      const item = deps.closestHTMLElement(event, "[data-workspace-id]");
      if (item?.dataset.workspaceId) selectWorkspace(item.dataset.workspaceId);
    });
    document.addEventListener("click", (event) => {
      if (!deps.closestHTMLElement(event, ".workspace-selector"))
        setWorkspaceMenuOpen(false);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") setWorkspaceMenuOpen(false);
    });
  }
  return {
    loadWorkspace,
    scheduleWorkspaceRetry,
    setWorkspaceMenuOpen,
    renderWorkspaceCatalog,
    loadWorkspaceCatalog,
    selectWorkspace,
    initWorkspaceSelector,
  };
}
