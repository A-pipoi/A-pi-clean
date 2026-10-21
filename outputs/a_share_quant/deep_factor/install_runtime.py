"""Install into this directory only, with an explicitly supplied Python 3.12."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def install(base: Path, temporary: Path) -> None:
    environment = {**os.environ, "TEMP": str(temporary), "TMP": str(temporary)}
    target = str(base / ".vendor")
    if os.name == "nt":
        # Pip's wheel license trees can exceed MAX_PATH in deep project folders.
        target = "\\\\?\\" + target
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-deps",
        "--target",
        target,
    ]
    subprocess.run(
        command + ["-r", str(base / "requirements-data.txt")],
        env=environment,
        check=True,
    )
    subprocess.run(
        command
        + ["--index-url", "https://download.pytorch.org/whl/cpu", "torch==2.14.1+cpu"],
        env=environment,
        check=True,
    )
    # Torch wheels may require setuptools at import time; do not retain the old
    # transitive build helper bundled by the CPU wheel index.
    subprocess.run(
        command + ["--upgrade", "--index-url", "https://pypi.org/simple", "setuptools==83.0.0"],
        env=environment,
        check=True,
    )
    subprocess.run(
        [sys.executable, "-S", str(base / "test_training.py")],
        env=environment,
        check=True,
    )


def main():
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("Use a separate Python 3.12; not the backend environment")
    base = Path(__file__).resolve().parent
    # Use the system's short temporary root rather than another deeply nested
    # project folder. This is local installation only; no registry/ACL changes.
    with tempfile.TemporaryDirectory(prefix="a-pi-runtime-") as directory:
        install(base, Path(directory))


if __name__ == "__main__":
    main()
