from __future__ import annotations

import argparse
import os
from datetime import date

from quant.backtest import CostModel, EventDrivenBacktester
from quant.data import DEFAULT_SYMBOLS, TushareDailyAdapter
from quant.reports.audit import build_audit_checklist
from quant.reports.performance import build_performance_report
from quant.strategies import build_strategy
from quant.time_utils import china_today
from quant.validation import validate_strategy


def main() -> int:
    parser = argparse.ArgumentParser(description="Run A-pi against real Tushare daily data.")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS[:4]))
    parser.add_argument("--start", default="2024-01-02")
    parser.add_argument("--end", default=china_today().isoformat())
    args = parser.parse_args()

    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token:
        raise SystemExit("TUSHARE_TOKEN is required; this example never creates synthetic bars.")
    symbols = [item.strip() for item in args.symbols.replace("，", ",").split(",") if item.strip()]
    adapter = TushareDailyAdapter(
        symbols,
        token=token,
        adjust="",
        fetch_limits=True,
        cache_dir="data_cache",
    )
    bars = adapter.daily_bars(date.fromisoformat(args.start), date.fromisoformat(args.end))
    if not bars:
        raise SystemExit("Tushare returned no daily bars; no fallback data was generated.")

    strategy = build_strategy("baseline", rebalance_every=5)
    result = EventDrivenBacktester(cost_model=CostModel()).run(bars, strategy)
    validation = validate_strategy(bars, lambda: build_strategy("baseline", rebalance_every=5))
    audit = build_audit_checklist(
        result,
        pit_checked=True,
        walk_forward_checked=validation.passed,
        limits_checked=bool(adapter.snapshot().metadata.get("limits_available")),
    )
    report = build_performance_report(result)
    print(f"source={adapter.snapshot().source}")
    print(f"dataset={adapter.snapshot().version}")
    print(f"bars={len(bars)} latest={max(item.date for item in bars).isoformat()}")
    print(f"final_equity={result.final_equity:.2f} annualized_return={report.annualized_return}")
    print(f"validation_passed={validation.passed} folds={validation.folds} oos={validation.oos_observations}")
    print("audit=" + "; ".join(f"{item.name}:{item.status}" for item in audit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
