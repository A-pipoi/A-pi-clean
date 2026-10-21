import json

from quant.data import industry_history_probe


class Frame:
    empty = False

    def to_dict(self, orient):
        return [
            {
                "ts_code": "000001.SZ",
                "l1_name": "银行",
                "in_date": "19910403",
                "out_date": None,
            }
        ]


def test_effective_history_never_promoted_to_pit(tmp_path, monkeypatch):
    calls = []

    def query(token, endpoint, params, fields):
        calls.append((endpoint, params))
        return Frame()

    monkeypatch.setattr(industry_history_probe, "_tushare_http_query", query)
    report = industry_history_probe.probe(tmp_path, "never-print-this-token")
    assert len(calls) == 6
    assert report["strict_historical_pit_usable"] is False
    assert report["historical_backfill"] is False
    assert all(
        row["has_historical_availability_evidence"] is False
        for row in report["results"]
    )
    evidence_file = next(
        row["evidence_file"]
        for row in report["results"]
        if row["symbol"] == "000001.SZ" and row["is_new"] == "Y"
    )
    evidence_path = tmp_path / evidence_file
    original = evidence_path.read_text(encoding="utf-8")
    raw = json.loads(original)
    assert raw["rows"][0]["in_date"] == "19910403"
    assert "available_at" not in raw["rows"][0]
    assert "never-print-this-token" not in (tmp_path / "report.json").read_text(
        encoding="utf-8"
    )
    second = industry_history_probe.probe(tmp_path, "never-print-this-token")
    assert second["run_id"] != report["run_id"]
    assert evidence_path.read_text(encoding="utf-8") == original
    assert len(list(tmp_path.glob("000001.SZ-Y-*.json"))) == 2
    assert (tmp_path / f"report-{report['run_id']}.json").is_file()


def test_permission_failures_are_redacted(tmp_path, monkeypatch):
    def query(*args, **kwargs):
        raise RuntimeError("权限不足 https://provider/token=secret")

    monkeypatch.setattr(industry_history_probe, "_tushare_http_query", query)
    report = industry_history_probe.probe(tmp_path, "secret")
    assert all(row["error"] == "permission_denied" for row in report["results"])
    assert "secret" not in json.dumps(report)
