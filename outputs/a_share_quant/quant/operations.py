from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

LOGGER = logging.getLogger("a_pi.operations")


def configure_service_logging(log_path: str | Path) -> Path:
    """Configure durable service logs without changing the API response contract."""

    target = Path(log_path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(target, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(
        isinstance(item, logging.FileHandler) and Path(item.baseFilename) == target.resolve()
        for item in root.handlers
    ):
        root.addHandler(handler)
    else:
        handler.close()
    return target


def backup_sqlite_databases(
    database_paths: Iterable[str | Path],
    destination: str | Path,
    *,
    keep: int = 14,
) -> dict[str, object]:
    """Create consistent SQLite backups using SQLite's online backup API."""

    if keep < 1:
        raise ValueError("keep must be at least 1")
    database_paths = tuple(database_paths)
    root = Path(destination).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backed_up: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []
    pruned: list[str] = []
    for raw_path in database_paths:
        source_path = Path(raw_path).expanduser()
        if not source_path.is_file():
            skipped.append(str(source_path))
            continue
        target_path = root / f"{source_path.stem}-{stamp}.sqlite3"
        try:
            source = sqlite3.connect(source_path)
            target = sqlite3.connect(target_path)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
            backed_up.append(str(target_path))
        except (OSError, sqlite3.Error) as error:
            errors.append({"source": str(source_path), "error": str(error)})
            LOGGER.exception("SQLite backup failed for %s", source_path)
    # Retain a bounded, recoverable history per database. Only files matching
    # the exact generated naming scheme are eligible for pruning.
    for raw_path in database_paths:
        stem = Path(raw_path).expanduser().stem
        candidates = sorted(root.glob(f"{stem}-????????T??????Z.sqlite3"), key=lambda item: item.name, reverse=True)
        for old_path in candidates[keep:]:
            try:
                old_path.unlink()
                pruned.append(str(old_path))
            except OSError as error:
                errors.append({"source": str(old_path), "error": str(error)})
                LOGGER.exception("SQLite backup retention cleanup failed for %s", old_path)
    return {
        "ok": not errors,
        "created_at": datetime.now(UTC).isoformat(),
        "destination": str(root),
        "backed_up": backed_up,
        "skipped": skipped,
        "pruned": pruned,
        "errors": errors,
    }


def latest_backup_summary(destination: str | Path) -> dict[str, object]:
    """Return restart-safe backup status derived from the backup directory."""

    root = Path(destination).expanduser()
    files = sorted(root.glob("*.sqlite3"), key=lambda item: item.stat().st_mtime, reverse=True) if root.is_dir() else []
    latest = files[0] if files else None
    return {
        "available": bool(files),
        "directory": str(root),
        "file_count": len(files),
        "latest_file": str(latest) if latest else None,
        "latest_mtime": datetime.fromtimestamp(latest.stat().st_mtime, UTC).isoformat() if latest else None,
    }
