import { setSafeHtml } from "./safe-html.mjs";
import { requiredElement, toError } from "./runtime-utils.mjs";
/** @type {import('../types/services').NotificationsServices} */
let services;
let readNotificationIds = new Set();
let deletedNotificationKeys = new Set();
/** Deletion is local to this account and exact event content, not the audit log.
 * @param {import('../types/domain').Notification} item */
const deletionKey = (item) => JSON.stringify([item.id, item.title, item.body]);
/** @type {import('../types/domain').Notification[]} */
let notificationItems = [];
let initialized = false;
let notificationStorageKey = "";
let activeAccount = "";
/** @type {import('../types/domain').Notification[]} */
let eventHistory = [];
function syncAccountHistory() {
  const username = document.documentElement.dataset.username || "";
  if (username === activeAccount) return;
  activeAccount = username;
  notificationStorageKey = `a_pi_notification_read:${username}`;
  readNotificationIds = new Set();
  deletedNotificationKeys = new Set();
  eventHistory = [];
  if (!username) return;
  try {
    const history = JSON.parse(
      localStorage.getItem(`a_pi_notification_events:${username}`) || "[]",
    );
    if (Array.isArray(history))
      eventHistory = history
        .filter(
          (item) =>
            item &&
            typeof item.title === "string" &&
            typeof item.id === "string",
        )
        .slice(-200);
    const ids = JSON.parse(
      localStorage.getItem(notificationStorageKey) || "[]",
    );
    if (Array.isArray(ids))
      readNotificationIds = new Set(ids.filter((id) => typeof id === "string"));
    const deleted = JSON.parse(
      localStorage.getItem(`a_pi_notification_deleted:${username}`) || "[]",
    );
    if (Array.isArray(deleted))
      deletedNotificationKeys = new Set(
        deleted.filter((id) => typeof id === "string"),
      );
  } catch {
    /* Storage is optional. */
  }
}
/** @param {string} message @param {string} [section] @param {string} [key] */
export function recordEvent(message, section = "overview", key = "") {
  syncAccountHistory();
  if (!activeAccount) return;
  if (key && eventHistory.some((item) => item.id === `event:${key}`)) return;
  const title = String(message)
    .slice(0, 500)
    .replace(/(token|password|secret)=\S+/gi, "$1=[redacted]");
  const previous = eventHistory.at(-1);
  if (
    previous?.title === title &&
    Date.now() - new Date(String(previous.timestamp)).getTime() < 60000
  )
    return;
  const item = {
    id: key ? `event:${key}` : `event:${Date.now()}:${eventHistory.length}`,
    title,
    body: "操作通知；刷新后仍可在本机通知中心查看。",
    section,
    type: /** @type {'warning'|'info'} */ (
      /失败|耗尽|错误|拒绝|中断/.test(title) ? "warning" : "info"
    ),
    timestamp: new Date().toISOString(),
  };
  eventHistory.push(item);
  eventHistory = eventHistory.slice(-200);
  try {
    localStorage.setItem(
      `a_pi_notification_events:${activeAccount}`,
      JSON.stringify(eventHistory),
    );
  } catch {}
  renderNotifications();
}

/** @param {string} prefix @param {unknown} value */
export function notificationId(prefix, value) {
  return `${prefix}:${String(value || "unknown")
    .replace(/[^\w\u4e00-\u9fa5]+/g, "-")
    .slice(0, 96)}`;
}

/** @param {unknown} value */
export function notificationTime(value, locale = "zh-CN") {
  if (!value) return "刚刚";
  const date =
    value instanceof Date
      ? value
      : new Date(typeof value === "number" ? value : String(value));
  return Number.isNaN(date.getTime())
    ? String(value).replace("T", " ").slice(0, 16)
    : date.toLocaleString(locale, {
        timeZone: "Asia/Shanghai",
        month: "numeric",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
}

/** @param {import('../types/domain').NotificationState} notificationState @param {(status: unknown) => string} paperStatusLabel */
export function buildNotifications(
  notificationState,
  paperStatusLabel,
  now = new Date(),
) {
  /** @type {import('../types/domain').Notification[]} */
  const items = [];
  /** @param {import('../types/domain').Notification} item */
  const add = (item) =>
    items.push({
      timestamp: "刚刚",
      section: "overview",
      type: "info",
      ...item,
    });
  const health = notificationState.health;
  if (health?.online === false) {
    add({
      id: "service-offline",
      type: "error",
      title: "Python 回测服务未连接",
      body: "请启动 A-pi Python 服务，回测和纸面策略循环暂不可用。",
      section: "backtest",
    });
  } else if (
    health?.online &&
    !health.providerReady &&
    health.probe?.status !== "pending"
  ) {
    add({
      id: "tushare-unavailable",
      type: "warning",
      title: "Tushare 数据源需要检查",
      body: "服务在线，但 Token、权限或网络尚未通过健康检查。",
      section: "overview",
    });
  } else if (
    health?.online &&
    health.providerReady &&
    health.probe?.data_fresh !== true &&
    health.probe?.status !== "pending"
  ) {
    add({
      id: "tushare-stale",
      type: "warning",
      title: "Tushare 数据新鲜度未通过",
      body:
        health.probe?.error ||
        `最近日线 ${health.probe?.latest_trade_date || "未知"}；应有交易日 ${health.probe?.expected_trade_date || "未确认"}。`,
      section: "overview",
    });
  }

  const workspaceWarnings = [
    ...(notificationState.workspace?.warnings || []),
    ...(notificationState.workspace?.portfolio?.data_quality?.warnings || []),
  ];
  [...new Set(workspaceWarnings)].slice(0, 5).forEach((warning, index) =>
    add({
      id: notificationId(`workspace-warning-${index}`, warning),
      type: "warning",
      title: "数据质量提示",
      body: warning,
      section: "overview",
      timestamp: notificationState.workspace?.latest_trade_date,
    }),
  );

  const paper = notificationState.paper || {};
  const paperWarnings = [
    ...new Set([
      ...(paper.warnings || []),
      ...(paper.data_quality?.warnings || []),
    ]),
  ];
  paperWarnings.slice(0, 4).forEach((warning, index) =>
    add({
      id: notificationId(`paper-warning-${index}`, warning),
      type: "warning",
      title: "纸面交易数据提示",
      body: warning,
      section: "paper",
      timestamp: paper.latest_trade_date,
    }),
  );
  (paper.orders || []).slice(0, 4).forEach((order) => {
    const side = order.side === "BUY" ? "买入" : "卖出";
    const rejected = order.status === "REJECTED";
    const filled = order.status === "FILLED";
    add({
      id: notificationId("paper-order", order.order_id),
      type: rejected ? "warning" : filled ? "success" : "info",
      title: rejected
        ? `${order.symbol || "标的"} 委托被拒绝`
        : `${order.symbol || "标的"} ${side} · ${paperStatusLabel(order.status)}`,
      body: rejected
        ? order.reject_reason || "请查看纸面交易详情。"
        : `委托 ${order.quantity || 0} 股，已成交 ${order.filled_quantity || 0} 股，状态：${paperStatusLabel(order.status)}`,
      section: "paper",
      timestamp: order.created_at,
    });
  });

  const backtest = notificationState.backtest;
  if (backtest?.strategy_validation) {
    const passed = backtest.strategy_validation.passed;
    add({
      id: notificationId(
        "backtest",
        backtest.dataset || backtest.experiment_id || "latest",
      ),
      type: passed ? "success" : "warning",
      title: passed ? "策略验证已通过" : "策略验证需要关注",
      body: passed
        ? `样本外验证通过，共 ${backtest.strategy_validation.folds ?? "—"} 个验证折。`
        : "请查看回测实验中的验证项和数据质量说明。",
      section: "backtest",
      timestamp: now.toISOString(),
    });
  }
  return items;
}

function persistReadNotifications() {
  if (!activeAccount) return;
  try {
    localStorage.setItem(
      notificationStorageKey,
      JSON.stringify([...readNotificationIds].slice(-200)),
    );
  } catch {
    /* local storage can be unavailable */
  }
}

export function renderNotifications() {
  if (!services) return;
  syncAccountHistory();
  notificationItems = [
    ...eventHistory.slice().reverse(),
    ...buildNotifications(
      services.notificationState,
      services.paperStatusLabel,
    ),
  ].filter((item) => !deletedNotificationKeys.has(deletionKey(item)));
  const unreadCount = notificationItems.filter(
    (item) => !readNotificationIds.has(item.id),
  ).length;
  const count = requiredElement("notificationCount");
  const dot = requiredElement("notificationDot");
  if (count) {
    count.textContent = unreadCount > 9 ? "9+" : String(unreadCount);
    count.hidden = unreadCount === 0;
  }
  if (dot) dot.hidden = unreadCount === 0;
  const list = requiredElement("notificationList");
  if (!list) return;
  if (!notificationItems.length) {
    setSafeHtml(
      list,
      '<div class="notification-empty"><span>✓</span><strong>暂无新通知</strong><p>Python 服务、Tushare 数据和纸面账户没有新的提醒。</p></div>',
    );
    services.applyLocale();
    return;
  }
  /** @type {Record<string,string>} */
  const iconByType = { error: "!", warning: "!", success: "✓", info: "i" };
  setSafeHtml(
    list,
    notificationItems
      .map((item) => {
        const unread = !readNotificationIds.has(item.id);
        return `<div class="notification-row"><button type="button" class="notification-item${unread ? " unread" : ""}" data-notification-id="${services.escapeHtml(item.id)}" data-notification-section="${services.escapeHtml(item.section || "overview")}" aria-label="查看通知详情：${services.escapeHtml(item.title)}">
      <span class="notification-marker ${services.escapeHtml(item.type)}">${iconByType[item.type || "info"] || "i"}</span>
      <span class="notification-copy"><strong>${services.escapeHtml(item.title)}</strong><span>${services.escapeHtml(item.body)}</span><small>${services.escapeHtml(notificationTime(item.timestamp, services.getLocale()))}</small></span>
      <span class="notification-arrow" aria-hidden="true">→</span>
    </button><button type="button" class="notification-delete" data-notification-delete="${services.escapeHtml(item.id)}" aria-label="删除通知：${services.escapeHtml(item.title)}">删除</button></div>`;
      })
      .join(""),
  );
  services.applyLocale();
}

/** @param {boolean} open */
export function setNotificationCenterOpen(open) {
  const center = requiredElement("notificationCenter");
  const button = requiredElement("notificationButton");
  if (!center || !button) return;
  center.hidden = !open;
  button.setAttribute("aria-expanded", String(open));
}

function bindEvents() {
  requiredElement("notificationButton")?.addEventListener("click", (event) => {
    event.stopPropagation();
    const center = requiredElement("notificationCenter");
    setNotificationCenterOpen(Boolean(center?.hidden));
  });
  requiredElement("markNotificationsRead")?.addEventListener(
    "click",
    (event) => {
      event.stopPropagation();
      notificationItems.forEach((item) => readNotificationIds.add(item.id));
      persistReadNotifications();
      renderNotifications();
    },
  );
  requiredElement("refreshNotifications")?.addEventListener(
    "click",
    async (event) => {
      event.stopPropagation();
      const button = /** @type {HTMLButtonElement} */ (event.currentTarget);
      button.disabled = true;
      button.textContent = services.localeText("刷新中");
      try {
        const results = await Promise.all([
          services.checkServiceHealth(false),
          services.loadWorkspace(false),
          services.fetchPaperState(false),
        ]);
        if (results.some((result) => result === false))
          throw new Error("部分后端数据请求失败");
        renderNotifications();
        services.showToast(services.localeText("通知中心已从服务刷新"));
      } catch (errorCaught) {
        const error = toError(errorCaught);
        services.showToast(
          `${services.localeText("通知刷新失败")} · ${error.message}`,
        );
      } finally {
        button.disabled = false;
        button.textContent = services.localeText("刷新");
      }
    },
  );
  requiredElement("notificationList")?.addEventListener("click", (event) => {
    // Rendering replaces the clicked node; don't let the document's outside-click
    // handler mistake the now-detached target for a click outside the center.
    event.stopPropagation();
    const back = services.closestHTMLElement(event, "[data-notification-back]");
    if (back) {
      renderNotifications();
      requiredElement("notificationList")?.querySelector("button")?.focus();
      return;
    }
    const route = services.closestHTMLElement(
      event,
      "[data-notification-route]",
    );
    if (route) {
      setNotificationCenterOpen(false);
      services.showSection(route.dataset.notificationRoute || "overview");
      return;
    }
    const remove = services.closestHTMLElement(
      event,
      "[data-notification-delete]",
    );
    if (remove) {
      const entry = notificationItems.find(
        (row) => row.id === remove.dataset.notificationDelete,
      );
      if (!entry) return;
      deletedNotificationKeys.add(deletionKey(entry));
      try {
        localStorage.setItem(
          `a_pi_notification_deleted:${activeAccount}`,
          JSON.stringify([...deletedNotificationKeys].slice(-500)),
        );
      } catch {}
      renderNotifications();
      requiredElement("notificationList")?.querySelector("button")?.focus();
      return;
    }
    const item = services.closestHTMLElement(event, "[data-notification-id]");
    if (!item) return;
    const entry = notificationItems.find(
      (row) => row.id === item.dataset.notificationId,
    );
    if (!entry) return;
    readNotificationIds.add(item.dataset.notificationId);
    persistReadNotifications();
    renderNotifications();
    const list = requiredElement("notificationList");
    if (!list) return;
    setSafeHtml(
      list,
      `<div class="notification-detail"><button type="button" class="text-button" data-notification-back>返回通知列表</button><h3>${services.escapeHtml(entry.title)}</h3><p>${services.escapeHtml(entry.body)}</p><small>${services.escapeHtml(notificationTime(entry.timestamp, services.getLocale()))}</small><button type="button" class="secondary-button" data-notification-route="${services.escapeHtml(entry.section || "overview")}">打开相关事件页面</button></div>`,
    );
    list.querySelector("button")?.focus();
    services.applyLocale();
  });
  document.addEventListener("click", (event) => {
    const wrap = document.querySelector(".notification-wrap");
    if (wrap && !(event.target instanceof Node && wrap.contains(event.target)))
      setNotificationCenterOpen(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setNotificationCenterOpen(false);
  });
}

/** @param {import('../types/services').NotificationsServices} dependencies */
export function initNotificationCenter(dependencies) {
  services = dependencies;
  if (initialized) return;
  try {
    const storedIds = JSON.parse(
      localStorage.getItem(notificationStorageKey) || "[]",
    );
    if (Array.isArray(storedIds)) readNotificationIds = new Set(storedIds);
  } catch {
    readNotificationIds = new Set();
  }
  bindEvents();
  initialized = true;
}

export const APiNotifications = {
  record: recordEvent,
  init: initNotificationCenter,
  render: renderNotifications,
  setOpen: setNotificationCenterOpen,
  formatTime: notificationTime,
};
