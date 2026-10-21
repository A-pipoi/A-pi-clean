"""One forward-only public industry capture. No backtest, paper cycle or orders."""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from ..factors.live_ic import SHANGHAI
from .industry_evidence import preserve_response
from .live_monitor import LiveMonitorProvider
from .observed_industry import ObservedIndustryStore


def capture(root: Path, token: str) -> dict[str, object]:
    if not token:
        raise ValueError("token_unavailable")
    provider = LiveMonitorProvider(root, token)
    rows = provider.query(
        "stock_basic", {"exchange": "", "list_status": "L"}, "ts_code,industry"
    )
    observed = datetime.now(SHANGHAI)
    values = {
        str(row["ts_code"]): str(row["industry"]).strip()
        for row in rows
        if isinstance(row.get("industry"), str) and str(row["industry"]).strip()
    }
    if len(rows) < 3000 or len(values) < len(rows) * 0.9:
        raise ValueError("industry_coverage_failed")
    digest, evidence_path = preserve_response(root, rows, observed)
    store = ObservedIndustryStore(root / "industry.sqlite3")
    store.record_universe([str(row["ts_code"]) for row in rows], observed)
    store.record(values, observed, digest)
    store.capture_status(
        observed, "complete" if len(values) == len(rows) else "incomplete"
    )
    return {
        "ok": True,
        "observed_at": observed.isoformat(),
        "provider_rows": len(rows),
        "classified_symbols": len(values),
        "coverage": len(values) / len(rows),
        "source_hash": digest,
        "historical_backfill": False,
        "paper_cycle_run": False,
        "evidence_path": str(evidence_path),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(
            os.getenv(
                "A_PI_DATA_CACHE", Path(__file__).resolve().parents[2] / "data_cache"
            )
        )
        / "live_ic",
    )
    args = parser.parse_args()
    try:
        report = capture(args.root, os.getenv("TUSHARE_TOKEN", ""))
        print(json.dumps(report, ensure_ascii=False))
    except Exception as error:
        logging.getLogger(__name__).exception("quant.data.capture_industry failed: %s", type(error).__name__, exc_info=None)
        # Provider exceptions may contain credentials; never echo their message.
        print(json.dumps({"ok": False, "error": type(error).__name__}))
        raise SystemExit(1)
