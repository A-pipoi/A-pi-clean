"""Read-only financial coverage and score wiring diagnostic, never a backtest."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from quant.data.strategy_fundamentals import observed_financial_inputs
from quant.factors.composite import FactorComposite


def main() -> None:
    now = datetime.now(timezone(timedelta(hours=8)))
    day = now.date()
    membership = json.loads(
        (
            ROOT / f"data_cache/tushare_index_weight_000300_SH_{day.isoformat()}.json"
        ).read_text(encoding="utf-8")
    )["result"]
    symbols = [row["ts_code"] for row in membership["members"]]
    _, today_report = observed_financial_inputs(day, symbols)
    preview = day + timedelta(days=1)
    while preview.weekday() >= 5:
        preview += timedelta(days=1)
    values, preview_report = observed_financial_inputs(preview, symbols)
    composite = FactorComposite()
    definitions = [
        item for item in composite.definitions if item.factor_type == "fundamental"
    ]
    finance = FactorComposite(definitions)
    ranked = sorted(
        finance.rank_scores(
            {symbol: [] for symbol in symbols}, fundamental_values=values
        ),
        key=lambda item: (-item.score, item.symbol),
    )
    impacts = {}
    for definition in definitions:
        reduced = FactorComposite([item for item in definitions if item != definition])
        reduced_scores = {
            item.symbol: item.score
            for item in reduced.rank_scores(
                {symbol: [] for symbol in symbols}, fundamental_values=values
            )
        }
        impacts[definition.metric] = sum(
            abs(item.score - reduced_scores[item.symbol]) > 1e-12 for item in ranked
        )
    report = {
        "checked_at": now.isoformat(),
        "kind": "read_only_financial_score_wiring_diagnostic",
        "membership_trade_date": membership["trade_date"],
        "today_signal_close": today_report,
        "next_weekday_preview": preview.isoformat(),
        "preview_is_not_exchange_calendar_verification": True,
        "prospective_coverage": preview_report,
        "financial_component_rows": len(ranked),
        "metric_ablation_changed_scores": impacts,
        "financial_component_top5": [
            {
                "symbol": item.symbol,
                "score": item.score,
                "financial_values": dict(item.factor_values),
            }
            for item in ranked[:5]
        ],
        "orders_generated": 0,
        "research_budget_used": False,
        "sealed_evaluated": False,
        "outperformance_proven": False,
        "warning": "财务组件排名不是完整策略信号；后续真实交易日仍需行情、PIT股票池、风险和执行窗口门禁。新数据不倒填到今天收盘。",
    }
    output = ROOT / "docs" / "FINANCIAL_BASELINE_DATA_20261009.json"
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "today_close_covered": today_report.get("covered_symbols", 0),
                "prospective_covered": preview_report.get("covered_symbols", 0),
                "requested": len(symbols),
                "metric_impacts": impacts,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
