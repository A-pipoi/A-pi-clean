"""Content-addressed sealed market/PIT truth, excluding personal research results."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def publish_sealed_data(root: Path, sealed: dict[str, object]) -> Path:
    content = json.dumps({"bars": sealed.get("bars", []), "pit_data": sealed.get("pit_data", {})}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(content).hexdigest()
    expected = sealed.get("dataset_hash")
    if expected and expected != digest:
        # Pre-PIT seals hashed the bars array alone. Accept that exact legacy
        # format only when there is no PIT evidence to leave unprotected.
        # Keep the ledger's original digest/result immutable; the shared file
        # remains addressed by the full canonical bars+PIT digest above.
        legacy_content = json.dumps(sealed.get('bars', []), ensure_ascii=False,
                                    sort_keys=True, separators=(',', ':')).encode()
        if sealed.get('pit_data') or hashlib.sha256(legacy_content).hexdigest() != expected:
            raise ValueError("sealed dataset hash does not match content")
    directory = root / "sealed"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (digest + ".json")
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        try:
            os.link(temporary, target)
        except FileExistsError:
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError("shared sealed artifact was modified")
    finally:
        temporary.unlink()
    return target
