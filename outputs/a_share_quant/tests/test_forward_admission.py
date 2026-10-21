import json
import sqlite3
from contextlib import closing
from datetime import datetime

import pytest

from quant.api.research_paper import execution_code_hash
from quant.research.deep_evidence import canonical, digest
from quant.research.forward_samples import ForwardSamples
from tests import test_deep_worker
from tests.test_deep_worker import training_data


@pytest.fixture
def fixture(tmp_path):
    return test_deep_worker.fixture.__wrapped__(tmp_path)


def prepare(fixture, *, change=None):
    ledger, worker, config, clock = fixture
    # Isolated synthetic cohort, never used as production source evidence.
    with closing(sqlite3.connect(ledger.path)) as db, db:
        db.execute(
            "INSERT INTO sealed_tests(strategy_version,factor_pipeline_version,"
            "data_version,sealed_start,sealed_end,dataset_hash,bars_json,metadata_json,"
            "parameters_json,created_at,archived_at) SELECT strategy_version,"
            "factor_pipeline_version,data_version,'2023-10-01','2023-11-30',"
            "dataset_hash,bars_json,metadata_json,parameters_json,created_at,created_at "
            "FROM sealed_tests WHERE id=1"
        )
        prior = [
            list(row)
            for row in db.execute(
                "SELECT id,sealed_start,sealed_end FROM sealed_tests WHERE id<>1 ORDER BY id"
            )
        ]
    data, sources = training_data()
    data["isolated_test_cohort"] = True  # Separate immutable dataset identity.
    scope = {
        "protocol": "forward-isolated-v1",
        "study_created_at": "2023-12-01T00:00:00+08:00",
        "code_hash": execution_code_hash(),
        "training_start": "2024-01-01",
        "training_end": "2025-12-31",
        "sealed_test_id": 1,
        "sealed_start": "2026-10-13",
        "sealed_end": "2026-10-30",
        "prior_seals_sha256": digest(canonical(prior)),
    }
    if change:
        scope.update(change)
    ForwardSamples(
        worker.authority.root.parent / "forward-training/alice/samples.sqlite3",
        version=execution_code_hash(),
        now=datetime.fromisoformat("2023-12-01T00:00:00+08:00"),
    )
    key = worker.authority.approve_dataset(
        canonical(data),
        sources,
        owner="alice",
        data_version="fixture-v1",
        audit_reference="ISOLATED TEST ONLY",
        now=clock[0],
        forward_scope=scope,
    )
    config = {**config, "dataset_hash": key}
    attempt = worker.trials.reserve(1, "forward-cohort-01", config)["attempt_id"]
    return ledger, worker, attempt


def test_signed_new_cohort_preserves_old_periods_and_count(fixture):
    ledger, worker, attempt = prepare(fixture)
    assert worker.enqueue(attempt)["worker_status"] == "queued"
    assert ledger.factor_mining_trial_total(1) == 1
    with closing(sqlite3.connect(ledger.path)) as db:
        assert db.execute("SELECT COUNT(*) FROM sealed_tests").fetchone()[0] == 2


@pytest.mark.parametrize(
    "change",
    [
        {"code_hash": "0" * 64},
        {"prior_seals_sha256": "0" * 64},
        {"sealed_test_id": 2},
        {"sealed_end": "2026-10-31"},
    ],
)
def test_signed_but_wrong_context_cannot_bypass_old_boundary(fixture, change):
    ledger, worker, attempt = prepare(fixture, change=change)
    with pytest.raises(PermissionError):
        worker.enqueue(attempt)
    assert worker.job(attempt)["worker_status"] == "not_queued"
    assert ledger.factor_mining_trial_total(1) == 1


def test_new_seal_after_approval_invalidates_scope(fixture):
    ledger, worker, attempt = prepare(fixture)
    with closing(sqlite3.connect(ledger.path)) as db, db:
        db.execute("UPDATE sealed_tests SET sealed_end='2023-12-02' WHERE id=2")
    with pytest.raises(PermissionError, match="protected intervals"):
        worker.enqueue(attempt)


def test_historical_features_cannot_be_relabelled_new_cohort(fixture):
    with pytest.raises(PermissionError, match="Historical observations"):
        prepare(
            fixture,
            change={
                "study_created_at": "2024-06-01T00:00:00+08:00",
                "training_start": "2024-06-01",
            },
        )


def test_tampered_approval_stays_rejected(fixture):
    ledger, worker, attempt = prepare(fixture)
    # Verify the signed envelope itself is still checked before cohort admission.
    with closing(sqlite3.connect(ledger.path)) as db:
        config = json.loads(
            db.execute(
                "SELECT config_json FROM deep_formal_trials WHERE attempt_id=?",
                (attempt,),
            ).fetchone()[0]
        )
    path = worker.authority.root / (config["dataset_hash"] + ".approval")
    envelope = json.loads(path.read_bytes())
    envelope["payload"]["forward_scope"]["prior_seals_sha256"] = "0" * 64
    path.write_bytes(canonical(envelope))
    with pytest.raises(PermissionError, match="altered approval"):
        worker.enqueue(attempt)
