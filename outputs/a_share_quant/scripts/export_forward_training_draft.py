"""Export mature retained observations for review, never production approval."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon", type=int, choices=(5, 10, 20), required=True)
    parser.add_argument("--training-end", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))
    import re

    from quant.research.deep_evidence import canonical
    from quant.research.forward_draft import build_draft

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.name):
        raise ValueError("Bounded draft identity required")
    draft = build_draft(
        project / "data/.deep-formal/forward-training/pipoi/samples.sqlite3",
        args.horizon,
        args.training_end,
        datetime.now(UTC),
    )
    encoded = canonical(draft["dataset"])
    root = (project / "reports/forward-drafts").resolve()
    destination = (root / args.name).resolve()
    if destination.parent != root:
        raise PermissionError("Draft destination escaped report directory")
    destination.mkdir(parents=True, exist_ok=False)
    sources = destination / "sources"
    sources.mkdir()
    for key, blob in draft["sources"].items():
        with (sources / (key + ".data")).open("xb") as stream:
            stream.write(blob)
    with (destination / "candidate-dataset.json").open("xb") as stream:
        stream.write(encoded)
    print(
        json.dumps(
            {
                "draft_directory": str(destination),
                "rows": len(draft["dataset"]["rows"]),
                "approved_for_training": False,
                "training_started": False,
                "formal_acceptance": False,
            }
        )
    )


if __name__ == "__main__":
    main()
