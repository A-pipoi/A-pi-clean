import hashlib
import json
from datetime import datetime

import pytest

from quant.data.capture_industry import capture
from quant.data.live_monitor import LiveMonitorProvider
from quant.data.observed_industry import ObservedIndustryStore
from quant.factors.live_ic import SHANGHAI


def test_capture_preserves_raw_response_and_first_seen(tmp_path, monkeypatch):
    rows = [{"ts_code": f"{i:06d}.SZ", "industry": "银行"} for i in range(3000)]
    monkeypatch.setattr(LiveMonitorProvider, "query", lambda *args: rows)
    first = datetime.now(SHANGHAI).replace(hour=0, minute=0, second=0)
    store = ObservedIndustryStore(tmp_path / "industry.sqlite3")
    store.record({"000000.SZ": "旧分类"}, first, "prior-response")
    before = store.records(["000000.SZ"], first.date())
    result = capture(tmp_path, "test-token-never-save")
    evidence = next((tmp_path / "industry_evidence").glob("*.json"))
    raw = evidence.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert "test-token-never-save" not in raw
    assert json.loads(payload["raw_response_json"]) == rows
    assert (
        hashlib.sha256(payload["raw_response_json"].encode()).hexdigest()
        == result["source_hash"]
    )
    assert payload["observed_at"] == result["observed_at"]
    assert payload["historical_backfill"] is False
    assert store.records(["000000.SZ"], first.date()) == before
    capture(tmp_path, "test-token-never-save")
    assert len(list((tmp_path / "industry_evidence").glob("*.json"))) == 2
    assert evidence.read_text(encoding="utf-8") == raw


def test_incomplete_capture_does_not_create_evidence_or_industries(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(LiveMonitorProvider, "query", lambda *args: [])
    with pytest.raises(ValueError, match="industry_coverage_failed"):
        capture(tmp_path, "test-token")
    assert not (tmp_path / "industry.sqlite3").exists()
    assert not (tmp_path / "industry_evidence").exists()
