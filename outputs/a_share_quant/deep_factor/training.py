"""Independent, audited CPU baselines; never access a sealed data segment.

Historical replay artifacts are explicitly NOT live historical PIT evidence.
The main service does not import this module or its heavy dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / ".vendor"))


def initialize_qlib(provider):
    from qlib.config import C
    from qlib.data.data import register_all_wrappers
    from qlib.data.ops import register_all_ops

    C.set(
        provider_uri=str(provider.resolve()),
        region="cn",
        kernels=1,
        expression_cache=None,
        dataset_cache=None,
        logging_level=30,
    )
    register_all_ops(C)
    register_all_wrappers(C)
    C._registered = True  # Data-only runtime, no experiment/MLflow registration.


def temporal_split(index, manifest, horizon):
    """Validate actual session indices, not calendar-day approximations."""
    import numpy as np

    calendar = manifest["_calendar"]
    positions = {day: i for i, day in enumerate(calendar)}
    train_start, train_end = manifest["segments"]["train"]
    valid_start, valid_end = manifest["segments"]["valid"]
    if not (
        positions[train_start] >= 59
        and positions[train_end] + horizon < positions[valid_start]
        and positions[valid_end] + horizon < len(calendar)
        and calendar[-1] < manifest["protected_sealed_start"]
    ):
        raise ValueError("Warm-up, label purge or sealed boundary violation")
    days = index.get_level_values("datetime").strftime("%Y-%m-%d")
    return np.asarray((days >= train_start) & (days <= train_end)), np.asarray(
        (days >= valid_start) & (days <= valid_end)
    )


def sequence_layout(values):
    # Official Alpha360 ordering is six feature groups, 59 -> 0 lags per group.
    return values.reshape(-1, 6, 60).transpose(1, 2).contiguous()


def align_labels(labels, index):
    if set(labels.index.names) != set(index.names):
        raise ValueError("Label index dimensions mismatch")
    return labels.reorder_levels(index.names).reindex(index).iloc[:, 0]


def create_model(kind, hidden):
    import torch

    if kind == "linear":
        return torch.nn.Linear(360, 1)
    if kind == "mlp":
        return torch.nn.Sequential(
            torch.nn.Linear(360, hidden), torch.nn.ReLU(), torch.nn.Linear(hidden, 1)
        )
    if kind != "gru":
        raise ValueError("Unknown model type")

    class GRU(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.gru = torch.nn.GRU(6, hidden, batch_first=True)
            self.output = torch.nn.Linear(hidden, 1)

        def forward(self, values):
            encoded, _ = self.gru(sequence_layout(values))
            return self.output(encoded[:, -1, :])

    return GRU()


def validation_ic(index, prediction, labels):
    import numpy as np
    import pandas as pd

    frame = pd.DataFrame({"score": prediction, "label": labels}, index=index)
    records = []
    for day, values in frame.groupby(level="datetime"):
        if len(values) < 3 or values.score.nunique() < 2 or values.label.nunique() < 2:
            continue
        correlation = values.score.rank().corr(values.label.rank())
        if np.isfinite(correlation):
            records.append(
                {
                    "date": str(day.date()),
                    "rank_ic": float(correlation),
                    "sample_count": len(values),
                }
            )
    numbers = np.array([r["rank_ic"] for r in records])
    deviation = float(numbers.std(ddof=1)) if len(numbers) > 1 else 0
    return {
        "mean_rank_ic": float(numbers.mean()) if len(numbers) else None,
        "icir": float(numbers.mean()) / deviation if deviation > 0 else None,
        "icir_definition": "unannualized mean daily RankIC / sample std",
        "days": len(records),
        "daily": records,
    }


def reserve_trial(directory, owner, dataset_hash, configuration):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "training_attempts.sqlite3"
    trial = uuid.uuid4().hex
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS attempts("
            "id TEXT PRIMARY KEY,owner TEXT,dataset_hash TEXT,configuration TEXT,"
            "status TEXT,created_at TEXT,finished_at TEXT,result TEXT)"
        )
        db.execute(
            "INSERT INTO attempts VALUES (?,?,?,?,?,?,NULL,NULL)",
            (
                trial,
                owner,
                dataset_hash,
                json.dumps(configuration, sort_keys=True),
                "running",
                datetime.now(UTC).isoformat(),
            ),
        )
        db.commit()
    return path, trial


def finish_trial(path, trial, status, result):
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            "UPDATE attempts SET status=?,finished_at=?,result=? WHERE id=?",
            (
                status,
                datetime.now(UTC).isoformat(),
                json.dumps(result, sort_keys=True),
                trial,
            ),
        )
        db.commit()


def run(args):
    import numpy as np
    import pandas as pd
    import torch
    from qlib.contrib.data.handler import Alpha360

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", args.owner):
        raise ValueError("A bounded owner identifier is required")
    root = args.dataset.resolve()
    from verify_data import verify

    verify(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    dataset_hash = hashlib.sha256((root / "checksums.json").read_bytes()).hexdigest()
    manifest["_calendar"] = (
        (root / "qlib_data/calendars/day.txt").read_text().splitlines()
    )
    # Confirm the live metadata boundary did not move earlier since data export.
    from prepare_qlib_data import sealed_cutoff

    if manifest["data_end"] >= sealed_cutoff(
        Path(manifest["source_sealed_metadata_db"])
    ):
        raise ValueError("Dataset now intersects a registered sealed boundary")
    configuration = {
        "model": args.model,
        "horizon": args.horizon,
        "epochs": args.epochs,
        "hidden": args.hidden,
        "batch_size": args.batch_size,
        "seed": args.seed,
        "learning_rate": args.learning_rate,
        "purpose": "historical_replay",
    }
    audit, trial = reserve_trial(
        args.output / args.owner, args.owner, dataset_hash, configuration
    )
    artifact = args.output / args.owner / trial
    artifact.mkdir(exist_ok=False)
    try:
        torch.set_num_threads(4)
        torch.manual_seed(args.seed)
        np.random.seed(args.seed)
        torch.use_deterministic_algorithms(True)
        initialize_qlib(root / "qlib_data")
        print(
            json.dumps({"stage": "Alpha360", "trial_id": trial, "model": args.model}),
            flush=True,
        )
        cache_key = hashlib.sha256(
            json.dumps(
                {
                    "dataset": dataset_hash,
                    "segments": manifest["segments"],
                    "label": manifest["label_expressions"]["5"],
                    "feature": "Alpha360-v1",
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()[:20]
        cache = root / f"alpha360-{cache_key}.parquet"
        cache_digest = cache.with_suffix(".sha256")
        if cache.exists():
            if (
                not cache_digest.exists()
                or hashlib.sha256(cache.read_bytes()).hexdigest()
                != cache_digest.read_text().strip()
            ):
                raise ValueError("Feature cache integrity check failed")
            frame = pd.read_parquet(cache)
        else:
            handler = Alpha360(
                instruments="csi300",
                start_time=manifest["segments"]["train"][0],
                end_time=manifest["segments"]["valid"][1],
                infer_processors=[],
                learn_processors=[],
                label=[manifest["label_expressions"]["5"]],
            )
            frame = handler.fetch(col_set=["feature", "label"])
            temporary = cache.with_suffix(".parquet.tmp")
            frame.to_parquet(temporary)
            temporary.replace(cache)
            cache_digest.write_text(
                hashlib.sha256(cache.read_bytes()).hexdigest(), encoding="ascii"
            )
        if args.horizon != 5:
            from qlib.data import D

            labels = D.features(
                D.instruments("csi300"),
                [manifest["label_expressions"][str(args.horizon)]],
                start_time=manifest["segments"]["train"][0],
                end_time=manifest["segments"]["valid"][1],
            )
            frame[("label", frame["label"].columns[0])] = align_labels(
                labels, frame.index
            )
        features = frame["feature"].to_numpy(dtype=np.float32)
        targets = frame["label"].iloc[:, 0].to_numpy(dtype=np.float32)
        train, valid = temporal_split(frame.index, manifest, args.horizon)
        finite = np.isfinite(features).all(axis=1) & np.isfinite(targets)
        train &= finite
        valid &= finite
        if train.sum() < 1000 or valid.sum() < 100:
            raise ValueError(
                "Insufficient complete samples; missing values are not filled"
            )
        mean = features[train].mean(axis=0, dtype=np.float64).astype(np.float32)
        scale = features[train].std(axis=0, dtype=np.float64).astype(np.float32)
        scale[scale < 1e-6] = 1
        xtrain = torch.from_numpy(np.clip((features[train] - mean) / scale, -10, 10))
        xvalid = torch.from_numpy(np.clip((features[valid] - mean) / scale, -10, 10))
        ytrain = torch.from_numpy(targets[train])
        model = create_model(args.model, args.hidden)
        optimizer = torch.optim.Adam(model.parameters(), lr=args.learning_rate)
        # Fixed epochs; no validation-selected weights/early stopping on this set.
        losses = []
        for epoch in range(args.epochs):
            model.train()
            permutation = torch.randperm(len(xtrain))
            total = 0.0
            for start in range(0, len(xtrain), args.batch_size):
                batch = permutation[start : start + args.batch_size]
                optimizer.zero_grad(set_to_none=True)
                output = model(xtrain[batch]).squeeze(-1)
                loss = torch.nn.functional.mse_loss(output, ytrain[batch])
                if not torch.isfinite(loss):
                    raise ValueError("Non-finite training loss")
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 5)
                optimizer.step()
                total += float(loss.detach()) * len(batch)
            losses.append(total / len(xtrain))
            print(
                json.dumps(
                    {
                        "stage": "training",
                        "epoch": epoch + 1,
                        "loss": losses[-1],
                        "trial_id": trial,
                    }
                ),
                flush=True,
            )
        model.eval()
        with torch.no_grad():
            scores = torch.cat(
                [model(batch).squeeze(-1) for batch in xvalid.split(args.batch_size)]
            ).numpy()
        if not np.isfinite(scores).all():
            raise ValueError("Non-finite predictions")
        selected = frame.index[valid]
        metrics = validation_ic(selected, scores, targets[valid])
        # 'date' is feature/signal date; actual creation time is never backdated.
        dates = selected.get_level_values("datetime").strftime("%Y-%m-%d")
        instruments = selected.get_level_values("instrument")
        exported = pd.DataFrame(
            {
                "date": dates,
                "instrument": instruments,
                "score": scores,
                "model_id": trial,
                "label_horizon": args.horizon,
            }
        )
        exported.to_parquet(artifact / "factor_scores.parquet", index=False)
        torch.save(
            {
                "state_dict": model.state_dict(),
                "mean": torch.from_numpy(mean),
                "scale": torch.from_numpy(scale),
                "configuration": configuration,
            },
            artifact / "model.pt",
        )
        with closing(sqlite3.connect(audit)) as db:
            attempts = [
                dict(zip(("id", "status"), row))
                for row in db.execute(
                    "SELECT id,status FROM attempts WHERE owner=? AND dataset_hash=? "
                    "ORDER BY created_at",
                    (args.owner, dataset_hash),
                )
            ]
        for attempt in attempts:
            if attempt["id"] == trial:
                attempt["status"] = "completed"
        completed_at = datetime.now(UTC).isoformat()
        metadata = {
            "schema_version": 1,
            "source": "external_deep",
            "owner": args.owner,
            "model_id": trial,
            "architecture": args.model,
            "purpose": "historical_replay",
            "created_at": completed_at,
            "trained_at": completed_at,
            "feature_version": "Qlib-Alpha360-v1",
            "dataset_hash": dataset_hash,
            "training_start": manifest["segments"]["train"][0],
            "training_end": manifest["segments"]["train"][1],
            "validation_start": manifest["segments"]["valid"][0],
            "validation_end": manifest["segments"]["valid"][1],
            "max_training_label_date": manifest["_calendar"][
                manifest["_calendar"].index(manifest["segments"]["train"][1])
                + args.horizon
            ],
            "protected_sealed_start": manifest["protected_sealed_start"],
            "label_horizon": args.horizon,
            "hyperparameters": configuration,
            "train_samples": int(train.sum()),
            "valid_samples": int(valid.sum()),
            "dropped_incomplete_samples": int((~finite).sum()),
            "train_losses": losses,
            "validation": metrics,
            "n_tested": len(attempts),
            "attempts": attempts,
            "parquet_sha256": hashlib.sha256(
                (artifact / "factor_scores.parquet").read_bytes()
            ).hexdigest(),
            "pit_verified": False,
            "production_import_allowed": False,
            "limitations": [
                item
                for item in manifest["limitations"]
                if "no training or score import performed" not in item
            ]
            + [
                "No industry/fundamental features or test/sealed segment.",
                (
                    "Historical replay scores were produced now, "
                    "not available on historical signal dates."
                ),
            ],
        }
        (artifact / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        finish_trial(
            audit, trial, "completed", {"artifact": str(artifact), "n_tested": 1}
        )
        print(
            json.dumps(
                {
                    "stage": "completed",
                    "artifact": str(artifact),
                    "train_samples": int(train.sum()),
                    "valid_samples": int(valid.sum()),
                    "validation_icir": metrics["icir"],
                    "production_ready": False,
                }
            ),
            flush=True,
        )
        return artifact
    except Exception as error:
        finish_trial(
            audit, trial, "failed", {"error_type": type(error).__name__, "n_tested": 1}
        )
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).resolve().parent / "runs"
    )
    parser.add_argument("--owner", required=True)
    parser.add_argument("--model", choices=("gru", "linear", "mlp"), default="gru")
    parser.add_argument("--horizon", type=int, choices=(5, 10, 20), default=5)
    parser.add_argument("--epochs", type=int, choices=range(1, 101), default=3)
    parser.add_argument("--hidden", type=int, choices=range(4, 257), default=32)
    parser.add_argument("--batch-size", type=int, choices=range(32, 4097), default=512)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if not 0 <= args.seed < 2**31:
        parser.error("seed must be in [0, 2**31)")
    if not 0 < args.learning_rate <= 0.1:
        parser.error("learning rate must be in (0,.1]")
    run(args)


if __name__ == "__main__":
    main()
