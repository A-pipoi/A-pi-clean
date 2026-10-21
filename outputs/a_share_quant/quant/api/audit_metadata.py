"""Audit metadata services with explicit runtime dependencies."""

from __future__ import annotations

import hashlib
import math
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from quant.api.contracts import (
    CurrentGitRevisionCallback,
    FrontendSourceRevisionCallback,
)
from quant.research import ExperimentRecord, ResearchLedger


@dataclass(frozen=True)
class AuditMetadataDependencies:
    RESEARCH_LEDGER: ResearchLedger
    __file__: str
    _current_git_revision: CurrentGitRevisionCallback
    _frontend_source_revision: FrontendSourceRevisionCallback


def _record_research_experiment(
    strategy_version: str,
    data_version: str,
    parameters: dict[str, object],
    status: str,
    notes: str,
    *,
    deps: AuditMetadataDependencies,
) -> int:
    return deps.RESEARCH_LEDGER.record(
        ExperimentRecord(
            strategy_version=str(strategy_version),
            data_version=str(data_version),
            parameters=parameters,
            status=status,
            notes=notes,
            git_commit=os.getenv("A_PI_GIT_COMMIT", "").strip()
            or deps._current_git_revision(),
        )
    )


def _current_git_revision(*, deps: AuditMetadataDependencies) -> str:
    """Stamp experiments with backend Git and the exact served UI asset revision."""
    repository_root = Path(deps.__file__).resolve().parents[3]
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
        if not revision:
            return "unversioned"
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
        ui_revision = deps._frontend_source_revision()
        combined_revision = f"{revision}+ui:{ui_revision}" if ui_revision else revision
        return f"{combined_revision}-dirty" if dirty else combined_revision
    except (OSError, subprocess.SubprocessError):
        return "unversioned"


def _frontend_source_revision(
    site_dist: Path | None = None, *, deps: AuditMetadataDependencies
) -> str:
    """Hash the static UI bundle, including deployments without a frontend Git checkout."""
    site_dist = (
        site_dist or Path(deps.__file__).resolve().parents[2] / "a_pi_site" / "build"
    )
    digest = hashlib.sha256()
    found = False
    for name in ("index.html", "release.json", "app.js", "styles.css", "a-pi-mark.svg"):
        path = site_dist / name
        try:
            content = path.read_bytes()
        except OSError:
            continue
        found = True
        digest.update(name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(content)
        digest.update(b"\x00")
    return digest.hexdigest()[:12] if found else ""


def _cache_writable(path: Path, *, deps: AuditMetadataDependencies) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _sample_std(values: list[float], *, deps: AuditMetadataDependencies) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _average(values: list[float], *, deps: AuditMetadataDependencies) -> float | None:
    return sum(values) / len(values) if values else None
