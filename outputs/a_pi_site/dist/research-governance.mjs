import { setSafeHtml } from "./safe-html.mjs";
import { apiFetch as fetch } from "./api-client.mjs";
import { requiredElement, toError } from "./runtime-utils.mjs";
import { APiExperimentComparison } from "./experiment-comparison.mjs";
import { APiGovernanceActions } from "./research-governance-actions.mjs";
import { APiNotifications } from "./notification-center.mjs";
/** research governance with explicit services and live state accessors. */
/** @param {import('../types/services').ResearchGovernanceServices} deps */
export function createResearchGovernance(deps) {
  /** @type {import('../types/services').SharedServices['loadGovernance']} */
  async function loadGovernance(showFailure = false) {
    const strategy = requiredElement("strategySelect")?.value || "baseline";
    try {
      const [ledgerResponse, sopResponse] = await Promise.all([
        fetch(`/api/research/ledger?strategy=${encodeURIComponent(strategy)}`, {
          cache: "no-store",
        }),
        fetch("/api/paper/sop", { cache: "no-store" }),
      ]);
      const ledger = await ledgerResponse.json();
      const sop = await sopResponse.json();
      if (!ledgerResponse.ok || !ledger.ok)
        throw new Error(
          ledger.error || `研究台账 HTTP ${ledgerResponse.status}`,
        );
      const used = Number(ledger.run_count ?? ledger.runs);
      const budget = Number(ledger.budget);
      const remaining = Number(ledger.remaining ?? Math.max(0, budget - used));
      deps.researchBudgetState = { used, budget, remaining, strategy };
      if (remaining === 0)
        APiNotifications.record(
          `研究预算已耗尽 · ${strategy} · ${used}/${budget}；请查看并归档当前研究周期。`,
          "backtest",
          `budget:${strategy}:${ledger.archived_count || 0}`,
        );
      deps.setText(
        "backtestRunCount",
        `${Number.isFinite(used) ? used : "—"} / ${Number.isFinite(budget) ? budget : "—"} 次`,
      );
      deps.setText(
        "researchBudgetStatus",
        `${Number.isFinite(used) ? used : "—"}/${Number.isFinite(budget) ? budget : "—"} · 剩余 ${ledger.remaining ?? "—"}`,
      );
      const budgetStatus = requiredElement("researchBudgetStatus");
      if (budgetStatus)
        budgetStatus.dataset.status =
          remaining <= 0 ? "error" : remaining <= 3 ? "warning" : "ok";
      deps.syncResearchBudgetControls();
      if (!APiGovernanceActions?.isSubmitting())
        APiGovernanceActions?.setStatus(
          ledger.archived_count
            ? `当前策略已归档 ${ledger.archived_count} 条研究记录`
            : "当前策略尚无归档记录",
          ledger.archived_count ? "success" : "",
        );
      const kindCounts = ledger.kind_counts || {};
      const recentKinds = Object.entries(kindCounts)
        .map(([kind, count]) => `${kind} ${count}`)
        .join(" · ");
      deps.setText(
        "governanceLedgerMeta",
        `${recentKinds || "暂无活动记录"}${ledger.archived_count ? ` · 已归档 ${ledger.archived_count}` : ""}`,
      );
      const entries = requiredElement("researchLedgerEntries");
      const hasDetailedLedger = Array.isArray(ledger.experiments);
      if (entries) APiExperimentComparison?.ensureControls(entries);
      if (entries)
        setSafeHtml(
          entries,
          !hasDetailedLedger
            ? "当前后端未提供预算明细，请升级并重启 Python 服务"
            : ledger.experiments.length
              ? ledger.experiments
                  .map(
                    (
                      /** @type {import('../types/domain').LedgerEntry} */ item,
                    ) => {
                      const status =
                        item.status === "started"
                          ? "执行中或中断"
                          : item.status;
                      const resultUrl = /^\/api\/backtest\/BT-[\w-]+$/.test(
                        String(item.result_url || ""),
                      )
                        ? String(item.result_url)
                        : "";
                      const link = resultUrl
                        ? `<button type="button" data-backtest-result="${deps.escapeHtml(resultUrl)}">查看结果</button>`
                        : "<span>无已保存产物</span>";
                      const compare = resultUrl
                        ? `<label class="ledger-compare-select"><input type="checkbox" data-compare-result="${deps.escapeHtml(resultUrl)}" aria-label="选择实验 #${deps.escapeHtml(item.id)} 参与对比">对比</label>`
                        : "<span></span>";
                      return `<div class="ledger-entry"><span>#${deps.escapeHtml(item.id)} · ${deps.escapeHtml(item.kind || "research")}</span><span>${deps.escapeHtml(status || "未知")}</span><span title="${deps.escapeHtml(item.reason || "")}">${deps.escapeHtml(item.reason || "未记录扣减原因")}</span>${compare}${link}</div>`;
                    },
                  )
                  .join("")
              : "当前研究周期尚无扣减记录",
        );
      if (entries && Array.isArray(ledger.shared_data_quality_artifacts)) {
        const qualityCopy = {
          "zh-CN": [
            "共享数据质量归档 · 不扣研究预算",
            "阶段",
            "查看报告",
            "尚无阶段报告",
          ],
          "en-US": [
            "Shared data quality archive · no research budget debit",
            "Phase",
            "View report",
            "No phase reports yet",
          ],
          "fr-FR": [
            "Archives qualité partagées · hors budget de recherche",
            "Phase",
            "Voir le rapport",
            "Aucun rapport de phase",
          ],
          "ja-JP": [
            "共有データ品質記録 · 研究予算の消費なし",
            "段階",
            "報告を見る",
            "段階報告はまだありません",
          ],
        }[document.documentElement.lang] || [
          "Shared data quality archive",
          "Phase",
          "View report",
          "No phase reports yet",
        ];
        const title = document.createElement("p");
        title.textContent = qualityCopy[0];
        entries.append(title);
        const latestByPhase = new Map();
        for (const item of ledger.shared_data_quality_artifacts) {
          if ([1, 2, 3].includes(item.phase) && !latestByPhase.has(item.phase))
            latestByPhase.set(item.phase, item);
        }
        if (!latestByPhase.size) {
          const empty = document.createElement("p");
          empty.textContent = qualityCopy[3];
          entries.append(empty);
        }
        for (const item of [...latestByPhase.values()].sort(
          (a, b) => a.phase - b.phase,
        )) {
          const row = document.createElement("div");
          row.className = "ledger-entry";
          for (const value of [
            `${qualityCopy[1]} ${item.phase}`,
            String(item.kind || "—"),
            String(item.status || "—"),
            String(item.created_at || "—"),
          ]) {
            const cell = document.createElement("span");
            cell.textContent = value;
            row.append(cell);
          }
          const link = document.createElement("a");
          if (/^[0-9a-f]{64}$/.test(String(item.digest))) {
            link.href = `/api/research/data-quality?digest=${item.digest}`;
            link.textContent = qualityCopy[2];
            link.target = "_blank";
            link.rel = "noopener";
          } else {
            link.textContent = "—";
          }
          row.append(link);
          entries.append(row);
        }
      }
      if (entries) APiExperimentComparison?.sync(entries);
      if (!deps.latestBacktestResult && !deps.restoringBacktestUrl) {
        const latestSaved = (ledger.experiments || []).find(
          (/** @type {import('../types/domain').LedgerEntry} */ item) =>
            /^\/api\/backtest\/BT-[\w-]+$/.test(String(item.result_url || "")),
        );
        if (latestSaved) deps.restoreBacktestResult(latestSaved.result_url);
        else if (used > 0) {
          deps.setText(
            "backtestAudit",
            hasDetailedLedger
              ? "历史研究记录已存在，当前没有可恢复的完整回测产物"
              : "旧版后端未提供历史产物索引，请重启升级",
          );
          deps.setText(
            "backtestMonthly",
            hasDetailedLedger
              ? "历史产物未保存；预算扣减明细可追溯"
              : "后端升级后可查看预算明细",
          );
        }
      }
      if (!sopResponse.ok || !sop.ok) {
        deps.setText("sopStage", "纸面 SOP 不可用");
        throw new Error(sop.error || `纸面 SOP HTTP ${sopResponse.status}`);
      }
      const factorRegistry = sop.factor_registry || {};
      deps.setText("sopStage", `阶段 ${sop.stage || "—"}`);
      deps.setText(
        "factorLockStatus",
        factorRegistry.locked ? "已冻结 · 阶段 C" : "可变更 · 阶段 A/B",
      );
      const sealed = sop.sealed_test || ledger.sealed_test || {};
      const paperGate = sop.paper_sealed_gate || {};
      const gatePassed =
        typeof paperGate.passed === "boolean"
          ? paperGate.passed
          : sealed.status === "passed";
      const gateReason = paperGate.reason ? ` · ${paperGate.reason}` : "";
      const sealedText = gatePassed
        ? `密封测试通过 · ${sealed.sealed_start || "—"} → ${sealed.sealed_end || "—"}`
        : sealed.status === "needs_review"
          ? `密封测试未通过${gateReason}`
          : `当前纸面股票池未验收${gateReason}`;
      deps.setText("sealedTestStatus", sealedText);
      const checklist = Array.isArray(sop.checklist) ? sop.checklist : [];
      const passedChecks = checklist.filter(
        (/** @type {import('../types/domain').AuditCheck} */ item) =>
          item && item.status === "通过",
      ).length;
      const coveredDays = sop.summary?.covered_calendar_days ?? 0;
      deps.setText(
        "sopChecklistStatus",
        `${passedChecks}/${checklist.length || "—"} 项通过 · 覆盖 ${coveredDays} 日`,
      );
      deps.setText(
        "sopReadinessStatus",
        sop.ready_for_stage_d === true ? "可进入阶段 D" : "暂不可进入阶段 D",
      );
      return true;
    } catch (errorCaught) {
      const error = toError(errorCaught);
      if (showFailure)
        deps.showToast(`研究治理数据加载失败 · ${error.message}`);
      return false;
    }
  }

  /** @param {string} id @param {unknown} value @param {string} status */
  function setStatusText(id, value, status = "") {
    const element = document.getElementById(id);
    if (!element) return;
    element.textContent = deps.localeText(value);
    if (status) element.dataset.status = status;
    else delete element.dataset.status;
  }

  /** @type {import('../types/services').SharedServices['loadOperationsStatus']} */
  async function loadOperationsStatus() {
    try {
      const response = await fetch("/api/ops/status", { cache: "no-store" });
      const data = await response.json();
      if (!response.ok || !data.ok)
        throw new Error(data.error || `HTTP ${response.status}`);
      const backup = data.last_backup || {};
      const healthy = Boolean(data.log_path && backup.available !== false);
      setStatusText(
        "opsStatus",
        healthy ? "运维正常" : "运维提示",
        healthy ? "ok" : "warning",
      );
      const element = requiredElement("opsStatus");
      if (element)
        element.title = backup.latest_mtime
          ? `备份：${backup.latest_mtime}`
          : "尚未产生纸面交易周期备份";
      return data;
    } catch (errorCaught) {
      const error = toError(errorCaught);
      setStatusText("opsStatus", "运维不可用", "error");
      return;
    }
  }

  /** @type {import('../types/services').SharedServices['loadAdjustmentHealth']} */
  async function loadAdjustmentHealth(symbols) {
    const normalized = Array.isArray(symbols)
      ? symbols.filter(Boolean).slice(0, 24)
      : [];
    if (!normalized.length) return;
    setStatusText("adjustmentHealthStatus", "正在检查复权因子");
    try {
      const query = encodeURIComponent(normalized.join(","));
      const response = await fetch(
        `/api/data/adjustment-health?symbols=${query}`,
        { cache: "no-store" },
      );
      const data = await response.json();
      if (!response.ok || !data.ok)
        throw new Error(data.error || `HTTP ${response.status}`);
      const adjustment = data.adjustment || {};
      const ratio = Number(adjustment.coverage_ratio);
      const percent = Number.isFinite(ratio)
        ? ` ${deps.formatPercent(ratio)}`
        : "";
      const status =
        adjustment.status === "available"
          ? "ok"
          : adjustment.status === "partial"
            ? "warning"
            : "error";
      const text =
        adjustment.status === "available"
          ? "复权完整"
          : adjustment.status === "partial"
            ? "复权部分缺失"
            : "复权不可用";
      setStatusText("adjustmentHealthStatus", `${text}${percent}`, status);
      return adjustment;
    } catch (errorCaught) {
      const error = toError(errorCaught);
      setStatusText("adjustmentHealthStatus", "复权不可用", "error");
      return;
    }
  }
  return {
    loadGovernance,
    setStatusText,
    loadOperationsStatus,
    loadAdjustmentHealth,
  };
}
