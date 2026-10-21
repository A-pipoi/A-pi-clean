from copy import deepcopy
from datetime import UTC, datetime

import pytest

from quant.factors.external_deep import stage_candidate, validate_scores


def fixture():
    now = datetime.now(UTC).isoformat()
    metadata = {
        "schema_version": 1,
        "source": "external_deep",
        "owner": "alice",
        "model_id": "model1",
        "label_horizon": 5,
        "training_start": "2025-01-03",
        "training_end": "2025-12-19",
        "max_training_label_date": "2025-12-26",
        "validation_start": "2026-01-21",
        "validation_end": "2026-06-02",
        "created_at": now,
        "trained_at": now,
        "n_tested": 1,
        "attempts": [{"id": "model1", "status": "completed"}],
        "purpose": "historical_replay",
        "pit_verified": False,
        "production_import_allowed": False,
    }
    rows = [
        {
            "date": "2026-01-21",
            "instrument": "SH600519",
            "score": 0.1,
            "model_id": "model1",
            "label_horizon": 5,
        }
    ]
    return metadata, rows


def validate(metadata, rows, **extra):
    return validate_scores(
        metadata, rows, owner="alice", sealed_start="2026-07-02", **extra
    )


def test_replay_is_disabled_and_cannot_be_production():
    metadata, rows = fixture()
    result = validate(metadata, rows)
    assert result["enabled"] is False and result["status"] == "research_only"
    with pytest.raises(ValueError, match="cannot be enabled"):
        validate(metadata, rows, production=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner", "bob"),
        ("training_end", "2026-07-02"),
        ("max_training_label_date", "2026-01-21"),
        ("validation_end", "2026-07-02"),
        ("pit_verified", True),
        ("production_import_allowed", True),
        ("created_at", "2026-01-01T00:00:00"),
    ],
)
def test_reject_bad_metadata(field, value):
    metadata, rows = fixture()
    metadata[field] = value
    with pytest.raises(ValueError):
        validate(metadata, rows)


@pytest.mark.parametrize(
    "field,value",
    [
        ("score", float("nan")),
        ("score", float("inf")),
        ("score", True),
        ("date", "2026-07-02"),
        ("instrument", "../secret"),
        ("label_horizon", True),
        ("model_id", "other"),
    ],
)
def test_reject_bad_score(field, value):
    metadata, rows = fixture()
    rows[0][field] = value
    with pytest.raises(ValueError):
        validate(metadata, rows)


def test_duplicate_scores_rejected():
    metadata, rows = fixture()
    with pytest.raises(ValueError, match="duplicate"):
        validate(metadata, rows + deepcopy(rows))


@pytest.mark.parametrize(
    "attempts,count",
    [
        ([], 0),
        ([], 1),
        ([{"id": "model1", "status": "failed"}], 1),
        ([{"id": "model1", "status": "completed"}] * 2, 2),
    ],
)
def test_trial_history_cannot_hide_failures_or_duplicate_attempts(attempts, count):
    metadata, rows = fixture()
    metadata.update(attempts=attempts, n_tested=count)
    with pytest.raises(ValueError):
        validate(metadata, rows)


def test_immutable_idempotent_owner_scoped_stage(tmp_path):
    metadata, rows = fixture()
    path = tmp_path / "isolated.sqlite3"
    first = stage_candidate(
        path, metadata, rows, owner="alice", sealed_start="2026-07-02"
    )
    assert first == stage_candidate(
        path, metadata, rows, owner="alice", sealed_start="2026-07-02"
    )
    rows[0]["score"] = 0.2
    with pytest.raises(ValueError, match="Immutable"):
        stage_candidate(path, metadata, rows, owner="alice", sealed_start="2026-07-02")
