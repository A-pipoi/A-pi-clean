// Isolated visual fixture. No login, network data fetch or real account mutation.
import { installWorkbenchPolish } from "../../dist/workbench-polish.mjs";
import { installSidebarDock } from "../../dist/sidebar-dock.mjs";
import { installWorkbenchDisclosure } from "../../dist/workbench-disclosure.mjs";
import { renderFinancialChart } from "../../dist/chart-engine.mjs";
import { initNotificationCenter, renderNotifications, recordEvent } from "../../dist/notification-center.mjs";
document.documentElement.dataset.username = "visual-fixture";
document.getElementById("authView").classList.add("hidden");
document.getElementById("appShell").classList.add("authenticated");
installWorkbenchPolish();
const sidebarDock = installSidebarDock();
installWorkbenchDisclosure();
const names = { overview: "研究概览", research: "因子研究", factorMining: "因子挖掘", backtest: "回测实验", risk: "风险控制", paper: "纸面交易" };
function showSection(id) {
  const previous = document.querySelector(".page-section.active")?.id;
  if (location.hash !== `#/${id}`) history.pushState({ aPiPreviousSection: previous }, "", `#/${id}`);
  document.querySelectorAll(".page-section").forEach((node) => node.classList.toggle("active", node.id === id));
  document.querySelectorAll(".nav-item").forEach((node) => node.classList.toggle("active", node.dataset.section === id));
}
window.addEventListener("popstate", () => showSection(location.hash.slice(2) || "overview"));
document.querySelectorAll("[data-section]").forEach((button) => button.addEventListener("click", () => showSection(button.dataset.section)));
initNotificationCenter({ notificationState: { workspace: { warnings: ["视觉测试用警告 · " + "很长的说明需要完整显示，不遮挡按钮。".repeat(20)] } }, escapeHtml: (s) => String(s).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;"), getLocale: () => "zh-CN", applyLocale() {}, paperStatusLabel: (s) => s, localeText: (s) => s, showToast() {}, showSection, checkServiceHealth: async () => true, loadWorkspace: async () => true, fetchPaperState: async () => true, closestHTMLElement: (e, selector) => e.target.closest(selector) });
recordEvent("视觉测试：研究状态已读取", "factorMining", "fixture");
renderNotifications();
const ring = renderFinancialChart(document.getElementById("allocationDonut"), { kind: "pie", title: "当前暴露（视觉测试数据）", segments: [{ name: "现金", value: 600000 }, { name: "测试股票", value: 400000 }] });
window.__usabilityFixture = { showSection, ring, renderNotifications, names, sidebarDock };
