"""Local, private approval authority. Never exposed as an HTTP upload API.

Approval is an operator decision after a source/PIT audit, not a client flag.
Signed approvals bind the actual numeric data and all underlying evidence bytes.
The existing replay exporter cannot create these approvals automatically.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
from datetime import datetime
from pathlib import Path

from quant.api.private_files import protect_private_path
from quant.factors.live_ic import SHANGHAI

FEATURE_VERSION = "Alpha360-v1"
HASH = re.compile(r"[0-9a-f]{64}")
SYMBOL = re.compile(r"\d{6}\.(?:SH|SZ|BJ)")


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def aware(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("Timezone-aware evidence required")
    return result


def regular_path(path: Path) -> None:
    # Reject junctions/reparse points as well as ordinary symlinks, including
    # every parent. Approval roots must not be redirected to another account.
    for entry in (path, *path.parents):
        info = entry.lstat()
        if entry.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise PermissionError("Redirected evidence path rejected")
    if not path.is_file() or path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("Invalid or oversized evidence file")


def regular(path: Path) -> bytes:
    regular_path(path)
    return path.read_bytes()


def validate_features(rows: list[dict]) -> None:
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100000:
        raise ValueError("Bounded feature rows required")
    seen = set()
    for row in rows:
        instrument = row.get("instrument")
        values = row.get("features")
        identity = (row.get("date"), instrument)
        if (
            not isinstance(instrument, str)
            or not SYMBOL.fullmatch(instrument)
            or identity in seen
            or not isinstance(values, list)
            or len(values) != 360
            or any(
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(item)
                or abs(item) > 1e12
                for item in values
            )
        ):
            raise ValueError("Invalid, duplicate or incomplete Alpha360 row")
        seen.add(identity)


class EvidenceAuthority:
    """Service/operator-only signing root, separate from research import inbox."""

    def __init__(self, root: Path):
        self.root = root.absolute()

    def _key(self, *, create: bool = True) -> bytes:
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        protect_private_path(self.root)
        path = self.root / "authority.key"
        if create:
            try:
                with path.open("xb") as stream:
                    # Lock down the empty file BEFORE writing the secret.
                    protect_private_path(path)
                    stream.write(os.urandom(32))
            except FileExistsError:
                protect_private_path(path)
        else:
            protect_private_path(path)
        result = regular(path)
        if len(result) != 32:
            raise PermissionError("Approval authority unavailable")
        return result

    def put(self, name: str, payload: dict) -> None:
        """Exclusive, immutable operator/worker record; not a remote request."""
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name):
            raise ValueError("Invalid evidence identity")
        key = self._key()
        encoded = canonical(payload)
        envelope = canonical(
            {
                "payload": payload,
                "signature": hmac.new(key, encoded, "sha256").hexdigest(),
            }
        )
        path = self.root / name
        try:
            with path.open("xb") as stream:
                protect_private_path(path)
                stream.write(envelope)
        except FileExistsError:
            if regular(path) != envelope:
                raise PermissionError("Immutable approval mismatch")

    def load(self, name: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name):
            raise ValueError("Invalid evidence identity")
        key = self._key(create=False)
        value = json.loads(regular(self.root / name))
        if (
            set(value) != {"payload", "signature"}
            or not isinstance(value["payload"], dict)
            or not isinstance(value["signature"], str)
            or not hmac.compare_digest(
                value["signature"],
                hmac.new(key, canonical(value["payload"]), "sha256").hexdigest(),
            )
        ):
            raise PermissionError("Unsigned or altered approval rejected")
        return value["payload"]

    def approve_dataset(
        self,
        dataset: bytes,
        sources: dict[str, bytes],
        *,
        owner: str,
        data_version: str,
        audit_reference: str,
        now: datetime,
        forward_scope: dict | None = None,
    ) -> str:
        """Local operator admission AFTER checking source provenance/vintages.

        Numeric/timing validation does not independently prove a supplier's
        historical timestamps. The operator must have audited that evidence;
        missing historical evidence is a reason NOT to call this method.
        """
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", owner):
            raise ValueError("Invalid owner")
        if not data_version or not audit_reference or now.utcoffset() is None:
            raise ValueError("Documented source audit and aware service time required")
        value = json.loads(dataset)
        if (
            value.get("schema_version") != 1
            or value.get("purpose") != "prospective"
            or value.get("feature_version") != FEATURE_VERSION
            or value.get("label_horizon") not in (5, 10, 20)
            or not sources
            or any(
                not HASH.fullmatch(key) or digest(blob) != key
                for key, blob in sources.items()
            )
        ):
            raise PermissionError("Audited prospective data and source bytes required")
        rows = value["rows"]
        validate_features(rows)
        calendar = value["calendar"]
        if calendar != sorted(set(calendar)):
            raise ValueError("Unique ascending real-session calendar required")
        positions = {day: i for i, day in enumerate(calendar)}
        for row in rows:
            cutoff = datetime.fromisoformat(row["date"] + "T23:59:59").replace(
                tzinfo=SHANGHAI
            )
            close_available = cutoff.replace(hour=17, minute=0, second=0)
            label_available = datetime.fromisoformat(
                row["label_end"] + "T17:00:00"
            ).replace(tzinfo=SHANGHAI)
            if (
                row["date"] not in positions
                or row["label_end"] not in positions
                or positions[row["label_end"]] - positions[row["date"]]
                != value["label_horizon"]
                or aware(row["feature_available_at"]) > cutoff
                or aware(row["feature_available_at"]) < close_available
                or aware(row["membership_available_at"])
                > cutoff.replace(hour=15, minute=0, second=0)
                or aware(row["label_available_at"]) > now
                or aware(row["label_available_at"]) < label_available
                or aware(row["label_available_at"])
                .astimezone(SHANGHAI)
                .date()
                .isoformat()
                < row["label_end"]
                or isinstance(row["label"], bool)
                or not isinstance(row["label"], (int, float))
                or not math.isfinite(row["label"])
                or not row.get("source_hashes")
                or any(key not in sources for key in row["source_hashes"])
            ):
                raise PermissionError(
                    "PIT, membership or mature training label mismatch"
                )
        if forward_scope is not None:
            from quant.research.forward_admission import validate_scope

            validate_scope(forward_scope, rows, now)
        dataset_hash = digest(dataset)
        # Immutable source bytes are retained, not only self-reported hashes.
        self._key()
        for key, blob in {**sources, dataset_hash: dataset}.items():
            path = self.root / (key + ".data")
            try:
                with path.open("xb") as stream:
                    protect_private_path(path)
                    stream.write(blob)
            except FileExistsError:
                if regular(path) != blob:
                    raise PermissionError("Immutable source mismatch")
        self.put(
            dataset_hash + ".approval",
            {
                "owner": owner,
                "dataset_hash": dataset_hash,
                "data_version": data_version,
                "feature_version": FEATURE_VERSION,
                "label_horizon": value["label_horizon"],
                "approved_at": now.isoformat(),
                "audit_reference": audit_reference,
                "source_hashes": sorted(sources),
                "purpose": "prospective",
                **(
                    {"forward_scope": forward_scope}
                    if forward_scope is not None
                    else {}
                ),
            },
        )
        return dataset_hash

    def dataset(self, config: dict, owner: str, data_version: str) -> tuple[dict, dict]:
        key = config["dataset_hash"]
        if not isinstance(key, str) or not HASH.fullmatch(key):
            raise ValueError("Invalid dataset identity")
        approval = self.load(key + ".approval")
        blob = regular(self.root / (key + ".data"))
        if (
            approval.get("owner") != owner
            or approval.get("data_version") != data_version
            or approval.get("purpose") != "prospective"
            or approval.get("dataset_hash") != key
            or digest(blob) != key
            or approval.get("feature_version") != config["feature_version"]
            or approval.get("label_horizon") != config["label_horizon"]
        ):
            raise PermissionError("Approved dataset binding mismatch")
        for source in approval["source_hashes"]:
            if digest(regular(self.root / (source + ".data"))) != source:
                raise PermissionError("Source evidence altered")
        return json.loads(blob), approval

    def publish_features(
        self,
        owner: str,
        day: str,
        rows: list[dict],
        sources: dict[str, bytes],
        *,
        now: datetime,
    ) -> None:
        """Trusted collector hook: first observation is always actual receipt.

        Caller has already computed the audited Alpha360-v1 features from its
        source. No HTTP import, historical timestamp override, or labels here.
        """
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", owner)
            or now.utcoffset() is None
            or now.astimezone(SHANGHAI).date().isoformat() != day
            or now.astimezone(SHANGHAI).hour < 17
            or not sources
            or any(
                not HASH.fullmatch(key) or digest(blob) != key
                for key, blob in sources.items()
            )
            or any(set(row) != {"instrument", "features"} for row in rows)
        ):
            raise PermissionError("Actual current-day collector receipt required")
        validate_features(rows)
        self._key()
        for key, blob in sources.items():
            path = self.root / (key + ".data")
            try:
                with path.open("xb") as stream:
                    protect_private_path(path)
                    stream.write(blob)
            except FileExistsError:
                if regular(path) != blob:
                    raise PermissionError("Immutable collector source mismatch")
        self.put(
            owner + "-" + day + ".features",
            {
                "owner": owner,
                "date": day,
                "feature_version": FEATURE_VERSION,
                "feature_available_at": now.isoformat(),
                "rows": rows,
                "source_hashes": sorted(sources),
            },
        )

    def features(self, owner: str, day: str) -> dict:
        value = self.load(owner + "-" + day + ".features")
        if (
            value.get("owner") != owner
            or value.get("date") != day
            or value.get("feature_version") != FEATURE_VERSION
        ):
            raise PermissionError("Feature receipt binding mismatch")
        for source in value["source_hashes"]:
            if digest(regular(self.root / (source + ".data"))) != source:
                raise PermissionError("Collector source evidence altered")
        validate_features(value["rows"])
        return value
