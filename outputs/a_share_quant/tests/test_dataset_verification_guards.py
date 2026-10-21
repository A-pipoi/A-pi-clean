"""Dataset validation must remain fail-closed when Python removes assertions."""
import hashlib
import json
import struct
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "deep_factor"


@pytest.fixture
def dataset(tmp_path):
    days = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(130)]
    calendar = tmp_path / "qlib_data/calendars/day.txt"
    calendar.parent.mkdir(parents=True)
    calendar.write_text("\n".join(days), encoding="utf-8")
    (tmp_path / "csv").mkdir()
    manifest = {"protected_sealed_start": "2030-01-01", "source_rows": 0,
                "segments": {"train": [days[60], days[61]], "valid": [days[82], days[100]]}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "checksums.json").write_text(json.dumps({"qlib_data/calendars/day.txt": hashlib.sha256(calendar.read_bytes()).hexdigest()}), encoding="utf-8")
    return tmp_path


def execute(script, root, optimized):
    return subprocess.run([sys.executable, *(["-O"] if optimized else []), str(SCRIPTS / script), str(root)],
                          capture_output=True, text=True, check=False, timeout=30)


@pytest.mark.parametrize("optimized", [False, True])
def test_verifier_checksums_and_sealed_boundary_survive_optimization(dataset, optimized):
    manifest_path = dataset / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["protected_sealed_start"] = "2024-01-02"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = execute("verify_data.py", dataset, optimized)
    assert result.returncode != 0
    assert "overlaps protected sealed interval" in result.stderr
    assert not (dataset / "verification.json").exists()


@pytest.mark.parametrize("optimized", [False, True])
@pytest.mark.parametrize("script", ["verify_data.py", "add_vwap.py"])
def test_tampered_checksums_fail_before_output(dataset, optimized, script):
    calendar = dataset / "qlib_data/calendars/day.txt"
    calendar.write_text("tampered", encoding="utf-8")
    result = execute(script, dataset, optimized)
    assert result.returncode != 0
    assert "checksum mismatch" in result.stderr
    assert calendar.read_text("utf-8") == "tampered"
    assert not (dataset / "verification.json").exists()


@pytest.mark.parametrize("volume,money", [("0", "100"), ("inf", "100"), ("100", "inf")])
def test_optimized_vwap_rejects_invalid_numbers_without_rewriting(dataset, volume, money):
    path = dataset / "csv/a.csv"
    content = f"date,volume,money\n2024-01-01,{volume},{money}\n"
    path.write_text(content, encoding="utf-8")
    result = execute("add_vwap.py", dataset, True)
    assert result.returncode != 0
    assert "finite positive volume" in result.stderr
    assert path.read_text("utf-8") == content


def test_valid_empty_dataset_verification_survives_optimization(dataset):
    result = execute("verify_data.py", dataset, True)
    assert result.returncode == 0, result.stderr
    assert json.loads((dataset / "verification.json").read_text("utf-8"))["passed"] is True


@pytest.mark.parametrize("script", ["verify_data.py", "add_vwap.py"])
def test_optimized_checksums_cannot_escape_dataset(dataset, script):
    (dataset / "checksums.json").write_text(json.dumps({"../outside.txt": "unused"}), encoding="utf-8")
    result = execute(script, dataset, True)
    assert result.returncode != 0
    assert "checksum path escapes dataset" in result.stderr


def test_optimized_verifier_rejects_binary_csv_mismatch(dataset):
    manifest = json.loads((dataset / "manifest.json").read_text("utf-8"))
    day = manifest["segments"]["train"][0]
    manifest["source_rows"] = 1
    (dataset / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    fields = ["open", "high", "low", "close", "volume", "factor", "money", "vwap"]
    (dataset / "csv/a.csv").write_text("date," + ",".join(fields) + "\n" + day + "," + ",".join("10" for _ in fields) + "\n", encoding="utf-8")
    binaries = dataset / "qlib_data/features/a"
    binaries.mkdir(parents=True)
    for field in fields:
        (binaries / (field + ".day.bin")).write_bytes(struct.pack("<ff", 60, 9 if field == "open" else 10))
    result = execute("verify_data.py", dataset, True)
    assert result.returncode != 0
    assert "binary CSV mismatch" in result.stderr
    assert not (dataset / "verification.json").exists()


def test_optimized_vwap_preserves_actual_turnover(dataset):
    path = dataset / "csv/a.csv"
    path.write_text("date,volume,money\n2024-01-01,100,1234\n", encoding="utf-8")
    binaries = dataset / "qlib_data/features/a"
    binaries.mkdir(parents=True)
    result = execute("add_vwap.py", dataset, True)
    assert result.returncode == 0, result.stderr
    assert struct.unpack("<ff", (binaries / "vwap.day.bin").read_bytes()) == pytest.approx((0, 12.34))
    checksums = json.loads((dataset / "checksums.json").read_text("utf-8"))
    assert checksums[str(path.relative_to(dataset))] == hashlib.sha256(path.read_bytes()).hexdigest()
