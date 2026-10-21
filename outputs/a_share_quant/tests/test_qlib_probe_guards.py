"""Probe gate regression with fake providers; not real Qlib/data acceptance."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROBE = Path(__file__).resolve().parents[1] / "deep_factor/probe_qlib_runtime.py"
STUB = r'''
import runpy, sys, types
from pathlib import Path
mode = sys.argv[3]
class Frame:
    def __init__(self, name, columns):
        self.name = name
        self.empty = mode == name + "_empty"
        self.shape = (0 if self.empty else 3, 1 if mode == name + "_width" else columns)
    def notna(self): return self
    def all(self): return self if not getattr(self, "reduced", False) else mode != self.name + "_nan"
    def __len__(self): return self.shape[0]
class Reduced:
    def __init__(self, frame): self.frame = frame
    def all(self): return mode != self.frame.name + "_nan"
Frame.all = lambda self: Reduced(self)
def module(name, **values):
    item = types.ModuleType(name)
    item.__dict__.update(values)
    sys.modules[name] = item
    return item
module("qlib", __version__="isolated-stub-not-real-qlib")
module("qlib.config", C=types.SimpleNamespace(set=lambda **kw: None))
module("qlib.contrib")
module("qlib.contrib.data")
class Handler:
    def __init__(self, **kw): pass
    def fetch(self, col_set): return Frame("features" if col_set == "feature" else "target", 360 if col_set == "feature" else 1)
module("qlib.contrib.data.handler", Alpha360=Handler)
def features(instruments, expressions, **kw):
    return Frame("prices" if len(expressions) == 4 else "horizons", len(expressions))
module("qlib.data", D=types.SimpleNamespace(features=features))
module("qlib.data.data", register_all_wrappers=lambda config: None)
module("qlib.data.ops", register_all_ops=lambda config: None)
probe = runpy.run_path(sys.argv[1])
probe["main"](Path(sys.argv[2]))
'''


@pytest.mark.parametrize("optimized", [False, True])
@pytest.mark.parametrize("failure", ["prices_empty", "prices_nan", "features_empty", "features_nan", "features_width", "target_empty", "target_nan", "horizons_empty", "horizons_nan", "horizons_width", "valid"])
def test_runtime_probe_cannot_mark_invalid_data_verified(tmp_path, optimized, failure):
    manifest = {"segments": {"train": ["2024-01-01", "2024-12-01"]},
                "label_expressions": {str(h): "fixture" for h in (5, 10, 20)}}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = subprocess.run([sys.executable, *(["-O"] if optimized else []), "-c", STUB, str(PROBE), str(tmp_path), failure],
                            text=True, capture_output=True, timeout=20, check=False)
    if failure == "valid":
        assert result.returncode == 0, result.stderr
        assert json.loads(path.read_text("utf-8"))["qlib_runtime_verified"] is True
    else:
        assert result.returncode != 0
        assert "ValueError: Qlib" in result.stderr
        assert json.loads(path.read_text("utf-8")) == manifest
        assert not (tmp_path / "qlib-runtime-verification.json").exists()
