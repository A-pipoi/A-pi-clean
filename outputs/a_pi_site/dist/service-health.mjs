import { setSafeHtml } from "./safe-html.mjs";
import { apiFetch as fetch } from "./api-client.mjs";
import { requiredElement, toError } from "./runtime-utils.mjs";
import { APiNotifications } from "./notification-center.mjs";
import { APiStockPicker } from "./stock-picker.mjs";
import { applyPresentationState } from "./presentation-state.mjs";
/** service health with explicit services and live state accessors. */
/** @param {import('../types/services').ServiceHealthServices} deps */
export function createServiceHealth(deps) {
  /** @type {import('../types/services').SharedServices['checkServiceHealth']} */
  async function checkServiceHealth(showFailure = false) {
    const pill = requiredElement("servicePill");
    try {
      const response = await fetch("/api/health");
      const health = await response.json();
      if (!response.ok || !health.ok) throw new Error("health check failed");
      if (
        Number(health.max_research_symbols) > 0 &&
        deps.MAX_RESEARCH_SYMBOLS !== Number(health.max_research_symbols)
      ) {
        deps.MAX_RESEARCH_SYMBOLS = Number(health.max_research_symbols);
        APiStockPicker?.renderSelected();
      }
      const probe = health.data_probe || {
        status: "unsupported",
        data_reachable: false,
        data_fresh: null,
        error: `当前后端 ${health.api_version || "未知"} 不支持数据探针；请重启至 governance-v5`,
      };
      const providerReady = probe.data_reachable === true;
      deps.notificationState.health = {
        online: true,
        providerReady,
        dataReachable: providerReady,
        dataFresh: probe.data_fresh === true,
        probe,
      };
      deps.connectionState.python = true;
      deps.connectionState.tushare = Boolean(providerReady);
      deps.updateConnectionStatus();
      APiNotifications?.render();
      deps.renderDataProbeStatus();
      return true;
    } catch (errorCaught) {
      const error = toError(errorCaught);
      deps.notificationState.health = {
        online: false,
        providerReady: false,
        dataReachable: false,
      };
      deps.connectionState.python = false;
      deps.connectionState.tushare = false;
      deps.updateConnectionStatus();
      APiNotifications?.render();
      if (pill) {
        applyPresentationState(pill, "is-unavailable");
        setSafeHtml(pill, '<span class="pulse-dot"></span>Python 服务未连接');
      }
      deps.setText("dataHealthLabel", "Python 服务未连接");
      deps.renderDataHealth(
        deps.latestWorkspaceSnapshot,
        deps.notificationState.health,
      );
      if (showFailure)
        deps.showToast("Python 回测服务未连接，请先启动 quant.api_server");
      return false;
    }
  }
  return { checkServiceHealth };
}
