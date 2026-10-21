"""Release resources and installer logic without network or production state."""
import importlib.util
import os
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

from quant.api.deep_scheduler import independent_runtime


def test_arena_guide_is_shipped_as_package_data():
    guide = files("quant").joinpath("resources/llm-factor-mining-guide.md").read_text(encoding="utf-8")
    checkout = Path(__file__).resolve().parents[1]
    assert guide == (checkout / "docs/llm-factor-mining-guide.md").read_text(encoding="utf-8")
    assert "不自动打开密封集" in guide
    assert "禁止" in guide


def test_independent_runtime_defaults_to_project_environment(tmp_path):
    with patch.dict(os.environ, {}, clear=True):
        runtime = independent_runtime(tmp_path)
    executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    assert runtime == tmp_path / "deep_factor" / ".venv" / executable
    assert "codex-runtimes" not in str(runtime)


def test_independent_runtime_honors_explicit_override(tmp_path):
    configured = tmp_path / "separate-python" / "python.exe"
    with patch.dict(os.environ, {"A_PI_DEEP_PYTHON": str(configured)}):
        assert independent_runtime(tmp_path) == configured


def test_runtime_installer_preserves_independent_cpu_contract(tmp_path):
    source = Path(__file__).resolve().parents[1] / "deep_factor/install_runtime.py"
    spec = importlib.util.spec_from_file_location("release_runtime_installer", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with patch.object(module.subprocess, "run") as run:
        module.install(tmp_path, tmp_path / "short-temp")
    assert run.call_count == 4
    commands = [call.args[0] for call in run.call_args_list]
    target = commands[0][commands[0].index("--target") + 1]
    assert target.endswith(".vendor")
    if os.name == "nt":
        assert target.startswith("\\\\?\\")
    assert "https://download.pytorch.org/whl/cpu" in commands[1]
    assert "torch==2.14.1+cpu" in commands[1]
    assert "setuptools==83.0.0" in commands[2]
    assert "-S" in commands[3]
    assert all(call.kwargs["check"] for call in run.call_args_list)
    assert run.call_args_list[0].kwargs["env"]["TEMP"] == str(tmp_path / "short-temp")
