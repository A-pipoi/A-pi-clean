"""Offline, recoverable migration of legacy research data to one account."""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import sqlite3
import sys
import uuid
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant.api.auth_store import AuthStore, validate_username

LEGACY_ITEMS = (
    "factors.sqlite3",
    "paper.sqlite3",
    "research.sqlite3",
    "workspace.json",
    "mining",
)


def migrate(
    data_root: Path, username: str, *, confirmed_offline: bool, port: int = 8000
) -> Path:
    validate_username(username)
    root = data_root.resolve()
    if not confirmed_offline:
        raise ValueError("Stop the backend and pass --confirmed-offline first")
    if port:
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise ValueError(
                    "Backend port is still listening; stop the service first"
                )
    user = AuthStore(root / "auth" / "users.sqlite3").get_by_username(username)
    if not user or user.disabled:
        raise ValueError("Target user must exist and be enabled")
    users = root / "users"
    users.mkdir(exist_ok=True)
    target = (users / username).resolve()
    if target.parent != users.resolve() or target.is_symlink():
        raise ValueError("Unsafe target path")
    if target.exists():
        raise ValueError("Target exists; refusing to merge or overwrite user data")
    lock = root / ".user-migration.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    stage = users / (".migration-" + uuid.uuid4().hex)
    recovery = root / ("legacy-before-auth-" + uuid.uuid4().hex)
    moved: list[tuple[Path, Path]] = []
    published = False
    try:
        os.close(descriptor)
        stage.mkdir()
        sources = [root / name for name in LEGACY_ITEMS if (root / name).exists()]
        if not sources:
            raise ValueError("No legacy data found")
        for source in sources:
            if source.is_symlink():
                raise ValueError("Refusing symlink legacy data")
            destination = stage / source.name
            if source.suffix == ".sqlite3":
                with (
                    closing(sqlite3.connect(source)) as original,
                    closing(sqlite3.connect(destination)) as copy,
                ):
                    original.backup(copy)
                    if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("Database integrity check failed")
            elif source.is_dir():
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
        recovery.mkdir()
        stage.rename(target)
        published = True
        for source in sources:
            for item in (
                source,
                Path(str(source) + "-wal"),
                Path(str(source) + "-shm"),
            ):
                if item.exists():
                    archived = recovery / item.name
                    item.rename(archived)
                    moved.append((item, archived))
        return recovery
    except Exception:
        for source, archived in reversed(moved):
            archived.rename(source)
        if published:
            target.rename(stage)
        raise
    finally:
        lock.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", required=True)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(
            os.getenv(
                "A_PI_DATA_ROOT", str(Path(__file__).resolve().parents[1] / "data")
            )
        ),
    )
    parser.add_argument("--confirmed-offline", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    try:
        recovery = migrate(
            args.data_root,
            args.to,
            confirmed_offline=args.confirmed_offline,
            port=args.port,
        )
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f"Migration refused: {error}\n")
    print(f"Migrated to {args.to}; original files preserved in {recovery}")


if __name__ == "__main__":
    main()
