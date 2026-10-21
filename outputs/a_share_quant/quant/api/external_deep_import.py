"""Authenticated research inbox bridge; never registers or enables replay factors.

Reads canonical, previously checked score JSON from the isolated inbox, not
model weights or Parquet. The main service needs neither torch nor pyarrow.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from quant.factors.external_deep import stage_candidate, validate_scores


def _readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)


def _inbox(root: Path, owner: str) -> Path:
    if not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", owner):
        raise PermissionError("Authenticated owner required")
    root = root.resolve()
    path = (root / owner / "research_candidates.sqlite3").resolve()
    if path.parent.parent != root or path.parent.name != owner:
        raise PermissionError("Research inbox escapes owner directory")
    return path


def candidate_summaries(path: Path, *, owner: str) -> list[dict]:
    """Bounded read-only listing, without scores, models or full metadata."""
    if not path.is_file():
        return []
    with closing(_readonly(path)) as db:
        rows = db.execute(
            "SELECT model_id,version,status,enabled,created_at "
            "FROM external_deep_candidates WHERE owner=? "
            "ORDER BY created_at DESC, model_id LIMIT 100",
            (owner,),
        ).fetchall()
    return [
        {
            "model_id": model,
            "version": version,
            "status": status,
            "enabled": bool(enabled),
            "created_at": created,
        }
        for model, version, status, enabled, created in rows
    ]


def external_deep_request(
    action: str,
    payload: dict,
    *,
    owner: str,
    inbox_root: Path,
    destination: Path,
    ledger_path: Path,
    dataset_checksums: Path,
) -> dict:
    """Owner is supplied by authenticated context, never by request payload.

    This import is not a formal research trial or a sealed acceptance. Retained
    offline attempt history must not be mistaken for the main research budget.
    """
    source = _inbox(inbox_root, owner)
    if action == "list":
        if payload:
            raise ValueError("Listing does not accept overrides")
        return {
            "available": candidate_summaries(source, owner=owner),
            "imported": candidate_summaries(destination, owner=owner),
            "production_import_allowed": False,
            "formal_budget_recorded": False,
        }
    if action != "import" or set(payload) != {"model_id", "version"}:
        raise ValueError("Only model_id and immutable version are accepted")
    model, version = payload["model_id"], payload["version"]
    if (
        not isinstance(model, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", model)
        or not isinstance(version, str)
        or not re.fullmatch(r"[0-9a-f]{64}", version)
    ):
        raise ValueError("Invalid model or version")
    if not source.is_file():
        raise LookupError("Research candidate not found for current user")
    with closing(_readonly(source)) as db:
        row = db.execute(
            "SELECT version,payload,status,enabled FROM external_deep_candidates "
            "WHERE owner=? AND model_id=? AND length(CAST(payload AS BLOB))<=33554432",
            (owner, model),
        ).fetchone()
    if row is None:
        raise LookupError("Research candidate not found for current user")
    if row[0] != version or row[2] != "research_only" or row[3] != 0:
        raise ValueError("Candidate version or disabled research status mismatch")
    document = json.loads(row[1])
    if (
        not isinstance(document, dict)
        or set(document) != {"metadata", "scores"}
        or not isinstance(document["metadata"], dict)
    ):
        raise ValueError("Invalid canonical candidate document")
    metadata = document["metadata"]
    if metadata.get("model_id") != model:
        raise ValueError("Candidate model identity mismatch")
    if (
        not dataset_checksums.is_file()
        or dataset_checksums.stat().st_size > 4 * 1024 * 1024
    ):
        raise PermissionError("Configured research dataset snapshot unavailable")
    if (
        metadata.get("dataset_hash")
        != hashlib.sha256(dataset_checksums.read_bytes()).hexdigest()
    ):
        raise ValueError("Candidate dataset snapshot mismatch")
    if not ledger_path.is_file():
        raise PermissionError("Registered sealed boundary required")
    # Metadata only; include archived and unopened windows. Never load payloads.
    with closing(_readonly(ledger_path)) as db:
        cutoff = db.execute("SELECT MIN(sealed_start) FROM sealed_tests").fetchone()[0]
    if not cutoff:
        raise PermissionError("Registered sealed boundary required")
    validated = validate_scores(
        metadata, document["scores"], owner=owner, sealed_start=cutoff
    )
    if validated["version"] != version:
        raise ValueError("Canonical candidate integrity mismatch")
    result = stage_candidate(
        destination, metadata, document["scores"], owner=owner, sealed_start=cutoff
    )
    return {
        **result,
        "model_id": model,
        "production_import_allowed": False,
        "formal_budget_recorded": False,
        "note": (
            "Authenticated research import only; offline trials are retained, "
            "not merged into the formal budget. No enabled factor or sealed "
            "acceptance created."
        ),
    }
