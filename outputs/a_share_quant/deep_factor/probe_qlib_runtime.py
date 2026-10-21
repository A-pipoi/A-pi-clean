"""Data-only Qlib validation, deliberately no experiment manager/model training."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / ".vendor"))


def main(root):
    import qlib
    from qlib.config import C
    from qlib.contrib.data.handler import Alpha360
    from qlib.data import D
    from qlib.data.data import register_all_wrappers
    from qlib.data.ops import register_all_ops

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    C.set(
        provider_uri=str((root / "qlib_data").resolve()),
        region="cn",
        kernels=1,
        expression_cache=None,
        dataset_cache=None,
        logging_level=30,
    )
    register_all_ops(C)
    register_all_wrappers(C)
    # Data-only registration; skip workflow setup in the worker callback.
    C._registered = True
    # This explicitly registers data providers only; no MLflow or torch needed.
    instruments = ["SH600519", "SZ000001", "SH600036"]
    sample = D.features(
        instruments,
        ["$close", "$open", "$volume", "$factor"],
        start_time="2026-05-25",
        end_time="2026-06-02",
    )
    if sample.empty or not sample.notna().all().all():
        raise ValueError("Qlib price sample is empty or incomplete")
    labels = [manifest["label_expressions"]["5"]]
    handler = Alpha360(
        instruments=instruments,
        start_time="2026-05-25",
        end_time="2026-06-02",
        fit_start_time=manifest["segments"]["train"][0],
        fit_end_time=manifest["segments"]["train"][1],
        infer_processors=[],
        learn_processors=[],
        label=labels,
    )
    features = handler.fetch(col_set="feature")
    target = handler.fetch(col_set="label")
    if features.empty or features.shape[1] != 360 or not features.notna().all().all():
        raise ValueError("Qlib Alpha360 features are empty or incomplete")
    if target.empty or not target.notna().all().all():
        raise ValueError("Qlib labels are empty or incomplete")
    all_labels = D.features(
        instruments,
        [manifest["label_expressions"][str(h)] for h in (5, 10, 20)],
        start_time="2026-05-25",
        end_time="2026-06-02",
    )
    if all_labels.empty or all_labels.shape[1] != 3 or not all_labels.notna().all().all():
        raise ValueError("Qlib multi-horizon labels are empty or incomplete")
    result = {
        "passed": True,
        "qlib_version": qlib.__version__,
        "validation_mode": "real_qlib_data_providers_and_Alpha360_no_training",
        "instruments": instruments,
        "price_rows": len(sample),
        "alpha360_shape": list(features.shape),
        "label_rows": len(target),
        "label_horizons_verified": [5, 10, 20],
        "model_training_performed": False,
    }
    (root / "qlib-runtime-verification.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    manifest["qlib_runtime_verified"] = True
    manifest["qlib_runtime_validation_scope"] = (
        "Three instruments, Alpha360, 5/10/20 labels"
    )
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    main(parser.parse_args().root)
