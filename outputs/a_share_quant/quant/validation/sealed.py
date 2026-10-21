from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

from ..backtest.costs import CostModel
from ..backtest.engine import EventDrivenBacktester, Strategy
from ..data.schema import Bar
from ..data.universe import HistoricalUniverse
from ..numeric import as_int as _as_int
from .statistics import summarize_returns
from .strategy import _HistoryGuard


@dataclass(frozen=True)
class SealedTestReport:
    passed: bool
    data_days: int
    sealed_start: date
    sealed_end: date
    observations: int
    annualized_return: float | None
    sharpe: float | None
    max_drawdown: float | None
    turnover: float
    total_cost: float
    checks: tuple[dict[str, str], ...]
    note: str
    pit_universe: dict[str, object] | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "data_days": self.data_days,
            "sealed_start": self.sealed_start.isoformat(),
            "sealed_end": self.sealed_end.isoformat(),
            "observations": self.observations,
            "annualized_return": self.annualized_return,
            "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown,
            "turnover": self.turnover,
            "total_cost": self.total_cost,
            "checks": list(self.checks),
            "note": self.note,
            "pit_universe": self.pit_universe,
        }


def run_sealed_test(
    bars: Sequence[Bar],
    strategy_factory: Callable[[], Strategy],
    *,
    sealed_start: date,
    sealed_end: date,
    initial_cash: float = 1_000_000.0,
    cost_model: CostModel | None = None,
    minimum_observations: int = 20,
    pit_universe: HistoricalUniverse | None = None,
    pit_index_code: str | None = None,
) -> SealedTestReport:
    """Evaluate a reserved holdout window without using it for warm-up tuning."""

    ordered = sorted(bars, key=lambda bar: (bar.date, bar.symbol))
    dates = sorted({bar.date for bar in ordered})
    sealed_dates = [item for item in dates if sealed_start <= item <= sealed_end]
    checks: list[dict[str, str]] = []
    if not dates or not sealed_dates:
        return _failed_report(sealed_start, sealed_end, len(dates), "密封区间没有可用交易日")
    if min(dates) >= sealed_start:
        return _failed_report(
            sealed_start,
            sealed_end,
            len(dates),
            "密封测试缺少区间前的预热历史，禁止把测试集当训练集",
        )

    checks.append({
        "name": "密封区间",
        "status": "通过",
        "evidence": f"仅使用 {sealed_start.isoformat()} 至 {sealed_end.isoformat()} 的保留交易日",
    })
    pit_audit: dict[str, object] | None = None
    if pit_universe is not None:
        mismatches: list[tuple[str, date]] = []
        try:
            for bar in ordered:
                if not pit_universe.contains(bar.symbol.upper(), bar.date):
                    mismatches.append((bar.symbol, bar.date))
        except ValueError as error:
            checks.append({"name": "历史成分 PIT 覆盖", "status": "不通过", "evidence": str(error)})
        else:
            consistency_ok = not mismatches
            checks.append(
                {
                    "name": "历史成分 PIT 覆盖",
                    "status": "通过" if consistency_ok else "不通过",
                    "evidence": (
                        f"全部 {len(ordered)} 根预热与验收日线均属于各自日期已生效的指数成分"
                        if consistency_ok
                        else f"发现 {len(mismatches)} 根非成分股日线，首例 {mismatches[0][0]} {mismatches[0][1]}"
                    ),
                }
            )
        try:
            historical_changes = pit_universe.membership_changes(min(dates), max(dates))
            changes = pit_universe.membership_changes(sealed_start, sealed_end)
        except ValueError as error:
            checks.append({"name": "PIT 历史成分确有变更", "status": "不通过", "evidence": str(error)})
            historical_changes = ()
            changes = ()
        change_summary = [
            {
                "trade_date": str(item["trade_date"]),
                "added_count": _as_int(item["added_count"]),
                "removed_count": _as_int(item["removed_count"]),
            }
            for item in changes
        ]
        changes_ok = bool(historical_changes)
        if not any(item["name"] == "PIT 历史成分确有变更" for item in checks):
            checks.append(
                {
                    "name": "PIT 历史成分确有变更",
                    "status": "通过" if changes_ok else "不通过",
                    "evidence": (
                        f"预热与密封全样本捕捉到 {len(historical_changes)} 次真实成分变化；"
                        + (
                            f"其中验收窗口内 {len(changes)} 次："
                            + ", ".join(str(item["trade_date"]) for item in change_summary)
                            if changes
                            else "验收窗口内没有换样，窗口仍按当时已生效成分筛选"
                        )
                        if changes_ok
                        else "预热与密封全样本内没有指数成分变化，无法证明本次使用了动态 PIT 股票池"
                    ),
                }
            )
        pit_audit = {
            "mode": "pit_historical_index",
            "index_code": str(pit_index_code or "").upper() or None,
            "snapshot_count": len(pit_universe.snapshot_dates),
            "snapshot_start": pit_universe.snapshot_dates[0].isoformat(),
            "snapshot_end": pit_universe.snapshot_dates[-1].isoformat(),
            "union_symbol_count": len(pit_universe.all_symbols),
            "historical_membership_changes": [
                {
                    "trade_date": str(item["trade_date"]),
                    "added_count": _as_int(item["added_count"]),
                    "removed_count": _as_int(item["removed_count"]),
                }
                for item in historical_changes
            ],
            "sealed_membership_changes": change_summary,
            "membership_rule": "每根日线只按 trade_date 当天或之前最近一份 index_weight 成分快照判断资格",
            "availability_limit": "Tushare index_weight 未提供独立披露时间戳；本验收只验证有效日期 PIT，不声称可还原成分公告的实际发布时间。",
        }
    try:
        result = EventDrivenBacktester(
            initial_cash=initial_cash,
            cost_model=cost_model or CostModel(),
        ).run(
            ordered,
            _HistoryGuard(strategy_factory()),
            evaluation_start=sealed_start,
            evaluation_end=sealed_end,
        )
    except ValueError as error:
        checks.append({"name": "点时历史检查", "status": "不通过", "evidence": str(error)})
        return SealedTestReport(
            False,
            len(dates),
            sealed_start,
            sealed_end,
            0,
            None,
            None,
            None,
            0.0,
            0.0,
            tuple(checks),
            "密封测试未通过，不能作为研究结论",
        )

    checks.append({
        "name": "点时历史检查",
        "status": "通过",
        "evidence": "密封窗口内策略只收到当前交易日之前的历史 K 线",
    })
    equities = [result.initial_cash] + [point.equity for point in result.equity_curve]
    returns = [
        equities[index] / equities[index - 1] - 1.0
        for index in range(1, len(equities))
        if equities[index - 1] > 0
    ]
    stats = summarize_returns(returns)
    sample_status = "通过" if len(returns) >= minimum_observations else "不通过"
    checks.append({
        "name": "密封样本数量",
        "status": sample_status,
        "evidence": f"密封窗口产生 {len(returns)} 个收益观测；最低要求 {minimum_observations}",
    })
    passed = all(item["status"] == "通过" for item in checks)
    return SealedTestReport(
        passed,
        len(dates),
        sealed_start,
        sealed_end,
        len(returns),
        stats.annualized_return,
        stats.sharpe,
        stats.max_drawdown,
        result.turnover,
        result.total_cost,
        tuple(checks),
        "密封测试通过；该结果已封存，后续调参前必须先归档研究周期"
        if passed
        else "密封测试未通过，不能作为研究结论",
        pit_audit,
    )


def _failed_report(
    sealed_start: date,
    sealed_end: date,
    data_days: int,
    evidence: str,
) -> SealedTestReport:
    return SealedTestReport(
        False,
        data_days,
        sealed_start,
        sealed_end,
        0,
        None,
        None,
        None,
        0.0,
        0.0,
        ({"name": "密封区间", "status": "不通过", "evidence": evidence},),
        "密封测试未通过，不能作为研究结论",
    )
