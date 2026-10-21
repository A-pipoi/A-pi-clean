"""Pre-registered, pre-sealed price-strategy execution comparison in a QA ledger."""

import hashlib
import json
import sys
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quant.backtest.costs import CostModel
from quant.backtest.engine import EventDrivenBacktester
from quant.data.extension_analytics import latest_rows
from quant.data.extension_store import ExtensionStore, canonical, default_store_path
from quant.data.extension_sync import load_token, sync
from quant.data.schema import Bar
from quant.research.ledger import ExperimentRecord, ResearchLedger
from quant.strategies.baseline import CrossSectionalMomentum

base = Path(__file__).resolve().parents[1]
output = (
    base / "data_cache/data_extension/constraints_strategy_acceptance_20261008.json"
)
if output.exists():
    saved = json.loads(output.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "status": "existing_immutable_result",
                "artifact_digest": saved["artifact_digest"],
            }
        )
    )
    raise SystemExit(0)
symbols = ["000001.SZ", "600036.SH"]
plan = {
    "symbols": symbols,
    "start": "20240101",
    "end": "20241231",
    "initial_cash": 1000000,
    "lookback": 20,
    "top_n": 5,
    "rebalance_every": 5,
    "maximum_requests": 12,
    "universe_note": "fixed survivorship-prone fixture; NOT historical index universe",
    "off_scope": "limit prices, pauses, share/amount participation; board-lot and T+1 stay enabled",
    "not_strategy_selection": True,
    "not_sealed_evaluation": True,
}
output.parent.mkdir(parents=True, exist_ok=True)
output.with_name("constraints_strategy_plan_20261008.json").write_text(
    json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
)
store = ExtensionStore(default_store_path())
store.initialize()
jobs = []
for symbol in symbols:
    for endpoint in ("daily", "stk_limit"):
        jobs.append(
            sync(
                store,
                endpoint,
                {
                    "ts_code": symbol,
                    "start_date": plan["start"],
                    "end_date": plan["end"],
                },
                load_token(),
                max_pages=3,
            )
        )
if any(j["status"] != "complete" for j in jobs):
    raise RuntimeError("provider coverage incomplete; no comparison accepted")
limits = {
    (r.get("ts_code"), r.get("trade_date")): r for r in latest_rows(store, "stk_limit")
}
bars = []
for row in latest_rows(store, "daily"):
    if (
        row.get("ts_code") not in symbols
        or not plan["start"] <= str(row.get("trade_date", "")) <= plan["end"]
    ):
        continue
    limit = limits.get((row["ts_code"], row["trade_date"]))
    if not limit or limit.get("up_limit") is None or limit.get("down_limit") is None:
        raise RuntimeError("actual limit price missing; refuse comparison")
    day = str(row["trade_date"])
    bars.append(
        Bar(
            str(row["ts_code"]),
            date(int(day[:4]), int(day[4:6]), int(day[6:8])),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["vol"]) * 100,
            amount=float(row["amount"]) * 1000,
            limit_up=float(limit["up_limit"]),
            limit_down=float(limit["down_limit"]),
        )
    )
bars.sort(key=lambda b: (b.date, b.symbol))
if not bars or {b.symbol for b in bars} != set(symbols):
    raise RuntimeError("missing planned symbol data")
dataset = hashlib.sha256(
    canonical([{**asdict(b), "date": b.date.isoformat()} for b in bars]).encode()
).hexdigest()
files = [
    "quant/backtest/engine.py",
    "quant/backtest/costs.py",
    "quant/strategies/baseline.py",
    "quant/factors/composite.py",
]
code = hashlib.sha256(b"".join((base / p).read_bytes() for p in files)).hexdigest()
ledger = ResearchLedger(
    base / "data_cache/data_extension/isolated_acceptance/research.sqlite3", budget=2
)
results = {}
for mode in ("on", "off"):
    record = ledger.record(
        ExperimentRecord(
            strategy_version="baseline",
            data_version=dataset,
            parameters={
                "kind": "backtest",
                "execution_mode": mode,
                "registered_plan": plan,
                "code_sha256": code,
            },
            status="started",
            kind="backtest",
            git_commit="source-sha256:" + code,
            notes="QA-only pre-registered comparison; not eligible for production promotion",
        )
    )
    try:
        inputs = (
            bars
            if mode == "on"
            else [
                replace(b, limit_up=None, limit_down=None, amount=None, paused=False)
                for b in bars
            ]
        )
        result = EventDrivenBacktester(
            initial_cash=1000000,
            cost_model=CostModel(max_participation=0.1 if mode == "on" else 1),
        ).run(inputs, CrossSectionalMomentum(lookback=20, top_n=5, rebalance_every=5))
        report = {
            "experiment_id": record,
            "fills": len(result.fills),
            "rejections": len(result.rejected),
            "reject_reasons": {
                reason: sum(r.reason.value == reason for r in result.rejected)
                for reason in sorted({r.reason.value for r in result.rejected})
            },
            "final_equity": result.final_equity,
            "total_cost": result.total_cost,
            "equity_curve": [
                {"date": e.date.isoformat(), "equity": e.equity}
                for e in result.equity_curve
            ],
        }
        job_id = "QA-constraints-" + mode
        ledger.create_backtest_job(job_id, {"strategy": "baseline", "mode": mode})
        ledger.update_backtest_job(
            job_id,
            status="completed",
            result={"research": {"experiment_id": record}, **report},
        )
        ledger.update_result(
            record,
            status="passed",
            notes="Saved isolated acceptance curve; not an investment or sealed result",
        )
        results[mode] = report
    except Exception:
        ledger.update_result(
            record,
            status="failed",
            notes="execution comparison failed; budget still accounted",
        )
        raise
report = {
    "kind": "fixed_strategy_execution_constraint_comparison",
    "plan": plan,
    "dataset_hash": dataset,
    "code_sha256": code,
    "source_jobs": jobs,
    "input_bars": len(bars),
    "results": results,
    "acceptance_ledger": str(ledger.path),
    "acceptance_budget_used": ledger.status("baseline")["used"],
    "production_research_budget_changed": False,
    "sealed_samples_modified": False,
    "historical_industry_pit_accepted": False,
    "status": "comparison_completed_not_strategy_approved",
}
report["artifact_digest"] = store.archive_quality(1, report)
output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(
    json.dumps(
        {
            "status": report["status"],
            "input_bars": len(bars),
            "on_fills": results["on"]["fills"],
            "off_fills": results["off"]["fills"],
            "acceptance_budget_used": report["acceptance_budget_used"],
            "artifact_digest": report["artifact_digest"],
        }
    )
)
