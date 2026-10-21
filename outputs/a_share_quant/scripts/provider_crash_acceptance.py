"""Real supplier child crash and natural-lease recovery, without clock tampering."""

import json
import queue
import subprocess
import sys
import threading
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant.data.extension_scheduler import FINANCIAL_INDICATOR_FIELDS
from quant.data.extension_store import ExtensionStore, default_store_path
from quant.data.extension_sync import load_token, sync
from quant.data.extension_transport import query

root = Path(__file__).resolve().parents[1] / "data_cache/data_extension"
target = root / "provider_crash_acceptance_20261008.json"
if target.exists():
    print(json.dumps({"status": "already_recorded", "report": str(target)}))
    raise SystemExit(0)
plan = {
    "endpoint": "fina_indicator_vip",
    "period": "20240331",
    "maximum_requests": 3,
    "kill_target": "only child process created by this script",
    "lease_clock_modified": False,
    "research_budget_used": False,
    "orders_created": False,
}
(root / "provider_crash_estimate_20261008.json").write_text(
    json.dumps(plan, indent=2), encoding="utf-8"
)
store = ExtensionStore(default_store_path())
store.initialize()
params = {"period": "20240331"}
job, offset, status = store.begin("fina_indicator", params)
if offset:
    raise SystemExit("Fixed acceptance job already has a checkpoint; do not reset it.")
events = queue.Queue()
child = subprocess.Popen(
    [
        sys.executable,
        "-X",
        "utf8",
        str(Path(__file__).with_name("provider_crash_worker.py")),
    ],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    encoding="utf-8",
)


def observe():
    if not (child.stdout is not None):
        raise AssertionError(
            "Verification gate failed: scripts/provider_crash_acceptance.py:58"
        )
    events.put(child.stdout.readline().strip())


threading.Thread(target=observe, daemon=True).start()
try:
    if events.get(timeout=60) != "REAL_PAGE_COMMITTED":
        raise RuntimeError("Real first page not confirmed; acceptance failed.")
    with closing(store.connect()) as db:
        before_offset = db.execute(
            "SELECT offset FROM ts_sync WHERE job=?", (job,)
        ).fetchone()[0]
        before = {
            r[0]
            for r in db.execute(
                "SELECT source_hash FROM ts_fina_indicator WHERE period_date='20240331'"
            )
        }
        expiry = db.execute(
            "SELECT expires FROM ts_job_leases WHERE job=?", (job,)
        ).fetchone()[0]
    child.kill()
    child.wait(timeout=10)
    print(
        json.dumps(
            {
                "status": "owned_child_killed",
                "checkpoint_offset": before_offset,
                "natural_lease_wait_seconds": max(0, round(expiry - time.time(), 1)),
            }
        ),
        flush=True,
    )
    last_notice = time.monotonic()
    while time.time() <= expiry:
        time.sleep(1)
        if time.monotonic() - last_notice >= 45:
            print(
                json.dumps({"status": "waiting_for_natural_lease_expiry"}), flush=True
            )
            last_notice = time.monotonic()
    offsets = []

    def resume(token, name, values):
        offsets.append(values["offset"])
        return query(token, name, values, fields=FINANCIAL_INDICATOR_FIELDS)

    result = sync(
        store, "fina_indicator", params, load_token(), max_pages=2, fetch=resume
    )
    with closing(store.connect()) as db:
        after = {
            r[0]
            for r in db.execute(
                "SELECT source_hash FROM ts_fina_indicator WHERE period_date='20240331'"
            )
        }
        duplicates = db.execute(
            "SELECT COUNT(*) FROM (SELECT business_key,source_hash,COUNT(*) n FROM ts_fina_indicator GROUP BY business_key,source_hash HAVING n>1)"
        ).fetchone()[0]
    passed = bool(
        offsets
        and offsets[0] == before_offset
        and before <= after
        and duplicates == 0
        and result["status"] in ("partial", "complete")
    )
    report = {
        "kind": "real_supplier_process_crash_recovery",
        "passed": passed,
        "plan": plan,
        "before_offset": before_offset,
        "resume_offsets": offsets,
        "original_source_hashes_preserved": before <= after,
        "duplicate_source_rows": duplicates,
        "resume": result,
        "lease_clock_modified": False,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    report["artifact_digest"] = store.archive_quality(1, report)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": passed,
                "resume_offsets": offsets,
                "duplicate_source_rows": duplicates,
            }
        ),
        flush=True,
    )
    if not passed:
        raise SystemExit(1)
finally:
    if child.poll() is None:
        child.kill()
        child.wait(timeout=10)
