import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from scripts.audit_research_acceptance_version import FROZEN_FIELDS, audit


@pytest.mark.parametrize("tampered", [False, True])
def test_audit_keeps_old_signals_separate_and_never_approves(tmp_path, tampered):
    directory = tmp_path / "data/users/pipoi/research-paper"
    directory.mkdir(parents=True)
    config = dict(
        zip(
            FROZEN_FIELDS,
            (
                "baseline_cost_aware",
                "weekly",
                "000300.SH",
                [],
                1000000,
                "research",
                "new",
            ),
            strict=True,
        )
    )
    config_hash = hashlib.sha256(
        json.dumps(config, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    config.update(config_hash=config_hash, created_at="2026-10-09T09:00:00+00:00")
    with closing(sqlite3.connect(directory / "configuration.sqlite3")) as db, db:
        db.execute("CREATE TABLE configuration(id INTEGER,payload TEXT)")
        db.execute(
            "CREATE TABLE configuration_revisions(id INTEGER,changed_at TEXT,old_payload TEXT,new_hash TEXT)"
        )
        db.execute(
            "INSERT INTO configuration VALUES(1,?)",
            (json.dumps({**config, "code_hash": "tampered"} if tampered else config),),
        )
        db.execute(
            "INSERT INTO configuration_revisions VALUES(1,?,?,?)",
            (
                "2026-10-12T14:00:00+00:00",
                json.dumps({"code_hash": "old"}),
                config_hash,
            ),
        )
    with closing(sqlite3.connect(directory.parent / "live_ic.sqlite3")) as db, db:
        db.execute("CREATE TABLE signals(first_seen TEXT)")
        db.execute("CREATE TABLE observations(horizon INTEGER,status TEXT)")
        db.executemany(
            "INSERT INTO signals VALUES(?)",
            [("2026-10-12T17:00:00+08:00",), ("2026-10-13T17:00:00+08:00",)],
        )
        db.execute("INSERT INTO observations VALUES(1,'available')")
    report = audit(tmp_path, "pipoi", "new")
    assert report["code_hash_matches"] is not tampered
    assert report["configuration_integrity_valid"] is not tampered
    assert report["previous_code_hash"] == "old"
    assert report["revision_matches_current_configuration"] is True
    assert report["generic_forward_ic"]["signals"] == 2
    assert report["generic_forward_ic"]["before_current_version"] == 1
    assert (
        report["generic_forward_ic"]["automatically_credited_to_current_acceptance"]
        is False
    )
    assert report["formal_acceptance_granted_by_this_audit"] is False


def test_audit_rejects_account_path_traversal(tmp_path):
    with pytest.raises(ValueError, match="Invalid account"):
        audit(tmp_path, "../other", "new")
