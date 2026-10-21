from __future__ import annotations

from dataclasses import dataclass

from ..backtest.engine import BacktestResult


@dataclass(frozen=True)
class AuditItem:
    name: str
    status: str
    evidence: str


def build_audit_checklist(
    result: BacktestResult,
    *,
    pit_checked: bool,
    walk_forward_checked: bool,
    limits_checked: bool = True,
) -> list[AuditItem]:
    """Return an explicit checklist; a result is not promoted to an investment conclusion here."""
    return [
        AuditItem("成本后口径", "通过" if result.total_cost > 0 else "不通过", f"记录成本 {result.total_cost:.2f}"),
        AuditItem(
            "点时数据检查",
            "通过" if pit_checked else "不通过",
            "策略历史窗口未包含当前或未来交易日" if pit_checked else "尚未完成点时历史检查",
        ),
        AuditItem(
            "Walk-Forward/Purged 验证",
            "通过" if walk_forward_checked else "不通过",
            "已执行滚动训练、embargo 和样本外测试" if walk_forward_checked else "尚未完成滚动样本外验证",
        ),
        AuditItem(
            "涨跌停数据",
            "通过" if limits_checked else "不通过",
            "已接入涨跌停价格" if limits_checked else "当前数据源无涨跌停价格，相关交易约束未纳入本次回测",
        ),
        AuditItem("交易拒单记录", "通过", f"记录 {len(result.rejected)} 笔拒单"),
        AuditItem("上线结论", "不适用", "本核心版本不连接实盘、不使用真实收益声明"),
    ]
