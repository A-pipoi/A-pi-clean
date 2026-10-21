"""Fixed, resumable real-supplier history plan; no production orders or seal writes."""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant.data.extension_analytics import latest_rows
from quant.data.extension_coverage import reconcile_daily
from quant.data.extension_scheduler import FINANCIAL_INDICATOR_FIELDS
from quant.data.extension_store import ExtensionStore, default_store_path
from quant.data.extension_sync import load_token, sync
from quant.data.extension_transport import query
from quant.data.historical_experiments import (
    disclosure_ic,
    etf_allocation_diagnostic,
    fund_layers,
)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--stage",
    choices=(
        "experiments",
        "coverage",
        "coverage-tail",
        "etf",
        "options-probe",
        "options-coverage",
    ),
    required=True,
)
parser.add_argument(
    "--max-days",
    type=int,
    default=50,
    help="bounded option-history continuation per invocation",
)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1] / "data_cache/data_extension"
root.mkdir(parents=True, exist_ok=True)
store = ExtensionStore(default_store_path())
store.initialize()
token = load_token()
if not token:
    raise ValueError("token unavailable")
started = time.monotonic()


def save(name, value):
    path = root / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def collect(endpoint, params, max_pages=20):
    lease_deadline = time.monotonic() + 210
    batch = (
        5000
        if endpoint == "fund_portfolio"
        or (
            args.stage in ("coverage", "coverage-tail")
            and endpoint
            in ("daily", "daily_basic", "adj_factor", "stk_limit", "suspend_d")
        )
        else None
    )
    while True:
        result = sync(
            store,
            endpoint,
            params,
            token,
            max_pages=max_pages,
            page_size=batch,
            fetch=lambda token, name, params: query(
                token,
                name,
                params,
                fields=FINANCIAL_INDICATOR_FIELDS if name == "fina_indicator" else "",
            ),
        )
        if result["status"] != "busy" or time.monotonic() >= lease_deadline:
            break
        time.sleep(5)
    print(
        json.dumps(
            {
                "endpoint": endpoint,
                "params": params,
                "status": result["status"],
                "rows": result.get("rows"),
                "elapsed_seconds": round(time.monotonic() - started, 1),
            }
        ),
        flush=True,
    )
    if result["status"] not in ("complete", "partial"):
        raise RuntimeError(
            "supplier job not completed: " + str(result.get("error", result["status"]))
        )
    if result["status"] == "partial":
        raise RuntimeError(
            "page budget reached; resume same fixed plan, never claim completed"
        )
    return result


if args.stage == "options-coverage":
    if not 1 <= args.max_days <= 100:
        raise ValueError("option batch must be 1..100 sessions")
    start, end = "20150209", "20261008"
    collect("trade_cal", {"exchange": "SSE", "start_date": start, "end_date": end}, 20)
    sessions = sorted(
        {
            str(r["cal_date"])
            for r in latest_rows(store, "trade_cal")
            if r.get("exchange") == "SSE"
            and r.get("is_open") == 1
            and start <= str(r["cal_date"]) <= end
        }
    )
    if not sessions:
        raise ValueError("exchange calendar unavailable")
    plan = {
        "kind": "option_history_daily_fixed_plan",
        "start": start,
        "end": end,
        "sessions": len(sessions),
        "endpoint": "opt_daily",
        "research_budget_used": False,
        "orders_created": False,
    }
    path = root / "option_history_plan.json"
    if path.exists() and json.loads(path.read_text(encoding="utf-8")) != plan:
        raise ValueError("fixed option plan mismatch")
    if not path.exists():
        save(path.name, plan)
    with closing(store.connect()) as db:
        finished = {
            json.loads(row["params_json"])["trade_date"]
            for row in db.execute(
                "SELECT params_json FROM ts_sync WHERE endpoint='opt_daily' AND status='complete'"
            )
            if set(json.loads(row["params_json"])) == {"trade_date"}
        }
    pending = [day for day in sessions if day not in finished]
    for day in pending[: args.max_days]:
        collect("opt_daily", {"trade_date": day}, 100)
    with closing(store.connect()) as db:
        complete = [
            {"date": params["trade_date"], "rows": int(row["offset"])}
            for row in db.execute(
                "SELECT params_json,offset FROM ts_sync WHERE endpoint='opt_daily' AND status='complete'"
            )
            if (params := json.loads(row["params_json"])).keys() == {"trade_date"}
            and params["trade_date"] in sessions
        ]
    report = {
        "kind": "option_history_daily_progress",
        "plan": plan,
        "completed_sessions": len(complete),
        "planned_sessions": len(sessions),
        "empty_supplier_days": [r["date"] for r in complete if r["rows"] == 0],
        "all_daily_jobs_complete": len(complete) == len(sessions),
        "full_pcr_iv_coverage_verified": False,
        "contract_metadata_coverage_verified": False,
        "historical_pit_created": False,
        "sealed_approval_usable": False,
    }
    report["artifact_digest"] = store.archive_quality(3, report)
    save("option_history_progress.json", report)
    print(
        json.dumps(
            {
                "status": "supplier_jobs_complete"
                if report["all_daily_jobs_complete"]
                else "partial_resumable",
                "completed_sessions": len(complete),
                "planned_sessions": len(sessions),
                "empty_supplier_days": len(report["empty_supplier_days"]),
            }
        ),
        flush=True,
    )
elif args.stage == "options-probe":
    # A bounded permission/coverage probe, not a claim of 2015–present data.
    start, end = "20150209", "20261008"
    collect("trade_cal", {"exchange": "SSE", "start_date": start, "end_date": end}, 20)
    sessions = sorted(
        {
            str(r["cal_date"])
            for r in latest_rows(store, "trade_cal")
            if r.get("exchange") == "SSE"
            and r.get("is_open") == 1
            and start <= str(r["cal_date"]) <= end
        }
    )
    if not sessions:
        raise ValueError("exchange calendar unavailable")
    checked = []
    for day in sessions[:5]:
        result = collect("opt_daily", {"trade_date": day}, 20)
        checked.append(
            {
                "date": day,
                "rows": result["rows"],
                "status": result["status"],
                "empty_day_is_not_verified_completeness": result["rows"] == 0,
            }
        )
    result = {
        "kind": "option_history_start_probe",
        "first_session": sessions[0],
        "last_planned_session": sessions[-1],
        "planned_sessions": len(sessions),
        "checked_sessions": checked,
        "all_history_covered": False,
        "contract_metadata_coverage_verified": False,
        "pcr_iv_full_history_approved": False,
        "historical_pit_created": False,
        "research_budget_used": False,
    }
    result["artifact_digest"] = store.archive_quality(3, result)
    save("option_history_start_probe.json", result)
    print(
        json.dumps(
            {
                "status": "bounded_probe_only",
                "planned_sessions": len(sessions),
                "checked_sessions": len(checked),
                "rows": sum(x["rows"] for x in checked),
            }
        ),
        flush=True,
    )
elif args.stage == "etf":
    # Fixed before inspecting returns: broad equity, Treasury bond and gold ETFs.
    # This is a separate research diagnostic; it never touches paper positions.
    symbols = ("510300.SH", "511010.SH", "518880.SH")
    start, end = "20190101", "20211231"
    plan = {
        "kind": "fixed_etf_allocation_plan",
        "symbols": symbols,
        "start": start,
        "end": end,
        "allocation": "monthly_equal_weight",
        "modeled_cost_bps": 5,
        "sealed_samples_modified": False,
        "production_strategy_changed": False,
    }
    path = root / "etf_allocation_plan.json"
    if path.exists() and json.loads(path.read_text(encoding="utf-8")) != json.loads(
        json.dumps(plan)
    ):
        raise ValueError("fixed ETF plan mismatch")
    if not path.exists():
        save(path.name, plan)
    collect("trade_cal", {"exchange": "SSE", "start_date": start, "end_date": end}, 20)
    sessions = sorted(
        {
            str(r["cal_date"])
            for r in latest_rows(store, "trade_cal")
            if r.get("exchange") == "SSE"
            and r.get("is_open") == 1
            and start <= str(r["cal_date"]) <= end
        }
    )
    if not sessions:
        raise ValueError("exchange calendar unavailable")
    for symbol in symbols:
        for endpoint in ("fund_daily", "fund_adj"):
            collect(
                endpoint, {"ts_code": symbol, "start_date": start, "end_date": end}, 20
            )
    result = etf_allocation_diagnostic(store, symbols, sessions)
    result["plan"] = plan
    result["artifact_digest"] = store.archive_quality(3, result)
    save("etf_allocation_acceptance.json", result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "sessions": len(sessions),
                "missing": len(result["missing_observations"]),
                "elapsed_seconds": round(time.monotonic() - started, 1),
            }
        ),
        flush=True,
    )
elif args.stage == "coverage-tail":
    # Work backward through the unfinished fixed plan. Shared page leases and
    # provider-rate reservations prevent duplicate writes and quota bypass.
    plan = json.loads((root / "full_history_plan.json").read_text(encoding="utf-8"))
    progress = json.loads(
        (root / "full_history_progress.json").read_text(encoding="utf-8")
    )
    remaining = sorted(
        {
            r["cal_date"]
            for r in latest_rows(store, "trade_cal")
            if r.get("exchange") == "SSE"
            and r.get("is_open") == 1
            and progress["last_date"] < r["cal_date"] <= plan["end"]
        },
        reverse=True,
    )
    for index, day in enumerate(remaining, 1):
        with ThreadPoolExecutor(max_workers=4) as workers:
            for future in [
                workers.submit(collect, endpoint, {"trade_date": day}, 20)
                for endpoint in plan["endpoints"]
            ]:
                future.result()
        save(
            "full_history_tail_progress.json",
            {
                "completed_tail_sessions": index,
                "planned_tail_sessions": len(remaining),
                "last_date": day,
                "all_requirements_complete": False,
                "fixed_parent_plan": "full_history_plan.json",
                "provider_rate_limits_shared": True,
                "checkpoints_preserved": True,
            },
        )
    print(
        json.dumps(
            {"status": "tail_supplier_jobs_complete", "sessions": len(remaining)}
        ),
        flush=True,
    )
elif args.stage == "experiments":
    periods = [
        f"{year}{suffix}"
        for year in range(2019, 2022)
        for suffix in ("0331", "0630", "0930", "1231")
    ]
    plan = {
        "kind": "historical_research_fixed_plan",
        "fund_periods": periods,
        "financial_periods": ["20240331", "20240630", "20240930", "20241231"],
        "calendar_range": ["20190101", "20261008"],
        "fund_signal_delay_calendar_days": 60,
        "fund_label_sessions": 63,
        "ic_signal_dates": [
            "20240430",
            "20240531",
            "20240628",
            "20240731",
            "20240830",
            "20240930",
            "20241031",
            "20241129",
            "20241231",
        ],
        "maximum_fund_pages_per_period": 500,
        "maximum_financial_pages_per_period": 200,
        "maximum_price_pages_per_job": 20,
        "research_budget_used": False,
        "sealed_samples_modified": False,
        "production_strategy_changed": False,
        "historical_pit_created": False,
    }
    path = root / "historical_research_plan.json"
    if path.exists() and json.loads(path.read_text(encoding="utf-8")) != plan:
        raise ValueError("fixed plan mismatch")
    if not path.exists():
        save(path.name, plan)
    probe = root / "fund_batch_probe.json"
    if (
        not probe.exists()
        or json.loads(probe.read_text(encoding="utf-8")).get("proven_batch_size")
        != 5000
    ):
        raise ValueError(
            "real fund batch probe required before larger-page continuation"
        )
    save(
        "historical_research_batch_continuation.json",
        {
            "fund_page_size": 5000,
            "source_probe": probe.name,
            "maximum_fund_pages_per_period": 500,
            "existing_checkpoints_preserved": True,
        },
    )
    collect(
        "trade_cal",
        {"exchange": "SSE", "start_date": "20190101", "end_date": "20261008"},
        20,
    )
    calendar = sorted(
        r["cal_date"]
        for r in latest_rows(store, "trade_cal")
        if r.get("exchange") == "SSE" and r.get("is_open") == 1
    )
    windows = []
    for period in periods:
        threshold = (
            date.fromisoformat(period) + timedelta(days=60)
        ).strftime("%Y%m%d")
        signal = next(d for d in calendar if d >= threshold)
        label = calendar[calendar.index(signal) + 63]
        windows.append((period, signal, label))
    pairs = [
        (day, calendar[calendar.index(day) + 1]) for day in plan["ic_signal_dates"]
    ]
    for period in plan["financial_periods"]:
        collect("fina_indicator", {"period": period}, 200)
    for period in periods:
        collect("fund_portfolio", {"period": period}, 500)
    days = sorted(
        {d for _, a, b in windows for d in (a, b)} | {d for pair in pairs for d in pair}
    )
    for day in days:
        for endpoint in ("daily", "adj_factor"):
            collect(endpoint, {"trade_date": day}, 20)
    ic = disclosure_ic(store, pairs)
    fund = fund_layers(store, windows)
    ic["artifact_digest"] = store.archive_quality(1, ic)
    fund["artifact_digest"] = store.archive_quality(3, fund)
    report = {
        "plan": plan,
        "disclosure_ic": ic,
        "fund_layers": fund,
        "elapsed_seconds": time.monotonic() - started,
        "all_requirements_complete": False,
        "historical_pit_created": False,
    }
    save("historical_experiments_acceptance.json", report)
    print(
        json.dumps(
            {
                "status": "experiments_computed",
                "ic_dates": len(ic["series"]),
                "fund_periods": len(fund["periods"]),
                "elapsed_seconds": report["elapsed_seconds"],
            }
        ),
        flush=True,
    )
else:
    start, end = "20241008", "20261008"
    endpoints = ("daily", "daily_basic", "adj_factor", "stk_limit", "suspend_d")
    plan = {
        "kind": "two_year_full_market_history",
        "start": start,
        "end": end,
        "endpoints": endpoints,
        "maximum_pages_per_job": 20,
        "listing_statuses": ["L", "D", "P"],
        "historical_pit_created": False,
        "research_budget_used": False,
        "orders_created": False,
        "resume": "supplier page checkpoints in shared SQLite",
    }
    path = root / "full_history_plan.json"
    normalized = json.loads(json.dumps(plan))
    if path.exists() and json.loads(path.read_text(encoding="utf-8")) != normalized:
        raise ValueError("fixed coverage plan mismatch")
    if not path.exists():
        save(path.name, plan)
    for status in ("L", "D", "P"):
        collect("stock_basic", {"exchange": "", "list_status": status}, 20)
    collect("trade_cal", {"exchange": "SSE", "start_date": start, "end_date": end}, 20)
    sessions = sorted(
        {
            r["cal_date"]
            for r in latest_rows(store, "trade_cal")
            if r.get("exchange") == "SSE"
            and r.get("is_open") == 1
            and start <= r["cal_date"] <= end
        }
    )
    save(
        "full_history_estimate.json",
        {
            **plan,
            "sessions": len(sessions),
            "jobs": len(sessions) * len(endpoints),
            "maximum_requests": len(sessions) * len(endpoints) * 20,
        },
    )
    coverage = []
    for day in sessions:
        with ThreadPoolExecutor(max_workers=4) as workers:
            jobs = [
                workers.submit(collect, endpoint, {"trade_date": day}, 20)
                for endpoint in endpoints
            ]
            for job in jobs:
                job.result()
        result = reconcile_daily(store, day)
        coverage.append(result)
        save(
            "full_history_progress.json",
            {
                "plan": plan,
                "completed_sessions": len(coverage),
                "total_sessions": len(sessions),
                "last_date": day,
                "coverage": coverage,
                "complete": False,
                "elapsed_seconds": time.monotonic() - started,
            },
        )
    report = {
        "kind": "two_year_full_market_history_acceptance",
        "plan": plan,
        "sessions": len(sessions),
        "coverage": coverage,
        "complete": bool(sessions) and all(r["complete"] for r in coverage),
        "supplier_jobs_completed": True,
        "elapsed_seconds": time.monotonic() - started,
        "historical_pit_created": False,
        "research_budget_used": False,
    }
    report["artifact_digest"] = store.archive_quality(1, report)
    save("full_history_acceptance.json", report)
    print(
        json.dumps(
            {
                "status": "covered" if report["complete"] else "coverage_gaps",
                "sessions": len(sessions),
                "elapsed_seconds": report["elapsed_seconds"],
            }
        ),
        flush=True,
    )
