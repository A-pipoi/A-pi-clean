"""Fixed numeric CPU training/inference subprocess. No keys or sealed database.

Launched by the light service with -I; heavy imports live only in .vendor.
Never load client pickles or a serialized Python model object.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / ".vendor"))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def run(mode: str, input_path: Path, output: Path) -> None:
    import numpy as np
    import torch
    from training import create_model

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    config = payload["config"]
    options = config["hyperparameters"]
    torch.set_num_threads(1)
    torch.manual_seed(options["seed"])
    torch.use_deterministic_algorithms(True)
    model = create_model(config["architecture"].lower(), options["hidden"])
    features = np.asarray([r["features"] for r in payload["rows"]], dtype=np.float32)
    if mode == "train":
        labels = np.asarray([r["label"] for r in payload["rows"]], dtype=np.float32)
        mean = features.mean(axis=0, dtype=np.float64).astype(np.float32)
        scale = features.std(axis=0, dtype=np.float64).astype(np.float32)
        scale[scale < 1e-6] = 1
    else:
        checkpoint = torch.load(
            payload["weights"], map_location="cpu", weights_only=True
        )
        if checkpoint["config"] != config:
            raise ValueError("Checkpoint configuration mismatch")
        model.load_state_dict(checkpoint["state_dict"], strict=True)
        mean = checkpoint["mean"].numpy()
        scale = checkpoint["scale"].numpy()
        if mean.shape != (360,) or scale.shape != (360,) or (scale <= 0).any():
            raise ValueError("Invalid normalization")
    if not (
        np.isfinite(features).all()
        and np.isfinite(mean).all()
        and np.isfinite(scale).all()
    ):
        raise ValueError("Non-finite feature or normalization")
    values = torch.from_numpy(np.clip((features - mean) / scale, -10, 10))
    if mode == "train":
        if not np.isfinite(labels).all() or len(values) < 1000:
            raise ValueError("Insufficient complete training samples")
        targets = torch.from_numpy(labels)
        optimizer = torch.optim.Adam(model.parameters(), lr=options["learning_rate"])
        losses = []
        for _ in range(options["epochs"]):
            model.train()
            order = torch.randperm(len(values))
            total = 0.0
            for batch in order.split(options["batch_size"]):
                optimizer.zero_grad(set_to_none=True)
                loss = torch.nn.functional.mse_loss(
                    model(values[batch]).squeeze(-1), targets[batch]
                )
                if not torch.isfinite(loss):
                    raise ValueError("Non-finite training loss")
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 5)
                optimizer.step()
                total += float(loss.detach()) * len(batch)
            losses.append(total / len(values))
        with (output / "model.pt").open("xb") as stream:
            torch.save(
                {
                    "state_dict": model.state_dict(),
                    "mean": torch.from_numpy(mean),
                    "scale": torch.from_numpy(scale),
                    "config": config,
                },
                stream,
            )
        result = {"train_samples": len(values), "losses": losses, "config": config}
    else:
        model.eval()
        with torch.no_grad():
            scores = torch.cat(
                [
                    model(batch).squeeze(-1)
                    for batch in values.split(options["batch_size"])
                ]
            ).numpy()
        if not np.isfinite(scores).all():
            raise ValueError("Non-finite inference")
        result = {
            "rows": [
                {"instrument": row["instrument"], "score": float(score)}
                for row, score in zip(payload["rows"], scores, strict=True)
            ]
        }
    with (output / "result.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, allow_nan=False, sort_keys=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("train", "score"))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    run(arguments.mode, arguments.input, arguments.output)
