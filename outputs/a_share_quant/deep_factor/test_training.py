import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from training import (
    align_labels,
    create_model,
    finish_trial,
    reserve_trial,
    sequence_layout,
    temporal_split,
)


class TrainingTests(unittest.TestCase):
    def test_label_index_order_is_aligned(self):
        import pandas as pd

        day = pd.Timestamp("2026-01-21")
        feature_index = pd.MultiIndex.from_tuples(
            [(day, "SH600519")], names=["datetime", "instrument"]
        )
        label_index = pd.MultiIndex.from_tuples(
            [("SH600519", day)], names=["instrument", "datetime"]
        )
        labels = pd.DataFrame({"label": [0.12]}, index=label_index)
        self.assertEqual(float(align_labels(labels, feature_index).iloc[0]), 0.12)

    def test_parquet_integrity_and_schema(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        from import_scores import inspect_artifact

        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory)
            now = datetime.now(UTC).isoformat()
            metadata = {
                "schema_version": 1,
                "source": "external_deep",
                "owner": "alice",
                "model_id": "model1",
                "label_horizon": 5,
                "training_start": "2025-01-03",
                "training_end": "2025-12-19",
                "max_training_label_date": "2025-12-26",
                "validation_start": "2026-01-21",
                "validation_end": "2026-06-02",
                "created_at": now,
                "trained_at": now,
                "n_tested": 1,
                "attempts": [{"id": "model1", "status": "completed"}],
                "purpose": "historical_replay",
                "pit_verified": False,
                "production_import_allowed": False,
            }
            rows = [
                {
                    "date": "2026-01-21",
                    "instrument": "SH600519",
                    "score": 0.1,
                    "model_id": "model1",
                    "label_horizon": 5,
                }
            ]
            score = artifact / "factor_scores.parquet"
            pq.write_table(pa.Table.from_pylist(rows), score)
            metadata["parquet_sha256"] = hashlib.sha256(score.read_bytes()).hexdigest()
            (artifact / "metadata.json").write_text(json.dumps(metadata))
            report = inspect_artifact(
                artifact, owner="alice", sealed_start="2026-07-02"
            )[2]
            self.assertEqual(report["score_count"], 1)
            with self.assertRaisesRegex(ValueError, "different dataset"):
                inspect_artifact(
                    artifact,
                    owner="alice",
                    sealed_start="2026-07-02",
                    dataset_hash="0" * 64,
                )
            metadata["parquet_sha256"] = "0" * 64
            (artifact / "metadata.json").write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, "integrity"):
                inspect_artifact(artifact, owner="alice", sealed_start="2026-07-02")
            rows[0]["unexpected"] = "not allowed"
            pq.write_table(pa.Table.from_pylist(rows), score)
            metadata["parquet_sha256"] = hashlib.sha256(score.read_bytes()).hexdigest()
            (artifact / "metadata.json").write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, "shape"):
                inspect_artifact(artifact, owner="alice", sealed_start="2026-07-02")

    def test_gru_sequence_order_and_baselines(self):
        import torch

        values = torch.arange(360, dtype=torch.float32).reshape(1, 360)
        sequence = sequence_layout(values)
        self.assertEqual(tuple(sequence.shape), (1, 60, 6))
        self.assertEqual(sequence[0, 0].tolist(), [0, 60, 120, 180, 240, 300])
        for name in ("gru", "linear", "mlp"):
            model = create_model(name, 8)
            prediction = model(values)
            self.assertEqual(tuple(prediction.shape), (1, 1))
            prediction.sum().backward()
        with self.assertRaises(ValueError):
            create_model("invalid", 8)

    def test_failed_trials_remain_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            path, first = reserve_trial(Path(directory), "alice", "hash", {})
            finish_trial(path, first, "failed", {"n_tested": 1})
            reserve_trial(Path(directory), "alice", "hash", {})
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 2
                )
                self.assertEqual(
                    db.execute(
                        "SELECT status FROM attempts WHERE id=?", (first,)
                    ).fetchone()[0],
                    "failed",
                )

    def test_trading_session_purge(self):
        import pandas as pd

        calendar = (
            pd.bdate_range("2024-01-01", periods=180).strftime("%Y-%m-%d").tolist()
        )
        manifest = {
            "_calendar": calendar,
            "segments": {
                "train": [calendar[60], calendar[100]],
                "valid": [calendar[121], calendar[150]],
            },
            "protected_sealed_start": calendar[179],
        }
        index = pd.MultiIndex.from_product(
            [pd.to_datetime(calendar), ["SH600519"]], names=["datetime", "instrument"]
        )
        with self.assertRaises(ValueError):
            temporal_split(index, manifest, 20)
        manifest["protected_sealed_start"] = "2025-12-31"
        train, valid = temporal_split(index, manifest, 20)
        self.assertEqual(int(train.sum()), 41)
        self.assertEqual(int(valid.sum()), 30)
        manifest["segments"]["valid"][0] = calendar[120]
        with self.assertRaises(ValueError):
            temporal_split(index, manifest, 20)


if __name__ == "__main__":
    unittest.main()
