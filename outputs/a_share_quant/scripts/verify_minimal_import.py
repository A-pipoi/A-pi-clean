"""Import the service in a fresh stdlib-only venv without timezone data."""

from __future__ import annotations

import os
import subprocess
import tempfile
import venv
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    scratch = root / "deep_factor" / ".tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="minimal-import-", dir=scratch
    ) as directory:
        target = Path(directory)
        venv.EnvBuilder(with_pip=False).create(target / "venv")
        python = (
            target
            / "venv"
            / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        )
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.pop("TUSHARE_TOKEN", None)
        environment.update(
            PYTHONTZPATH="",
            PYTHONNOUSERSITE="1",
            A_PI_DATA_ROOT=str(target / "isolated-data"),
            A_PI_AUTH_SECRET="isolated-minimal-import-verification-not-user-secret",
        )
        code = (
            "import importlib.util\n"
            "if importlib.util.find_spec('tzdata') is not None:\n"
            "    raise RuntimeError('tzdata unexpectedly installed')\n"
            "import quant.api_server\n"
            "from quant.time_utils import SHANGHAI_TZ\n"
            "if SHANGHAI_TZ.tzname(None) != 'CST':\n"
            "    raise RuntimeError('Shanghai timezone fallback failed')\n"
            "print('CLEAN_VENV_NO_TZDATA_IMPORT_OK')"
        )
        subprocess.run([str(python), "-c", code], cwd=root, env=environment, check=True)


if __name__ == "__main__":
    main()
