"""Source archive gate is independent of installation or formal acceptance."""
import hashlib
import json
import runpy
import stat
import zipfile
from pathlib import Path

import pytest

VERIFY = runpy.run_path(str(Path(__file__).resolve().parents[3] / "tools/verify_source_archive.py"))["verify"]


def bundle(tmp_path, name="README.md", raw=b"safe", *, symlink=False):
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        item = zipfile.ZipInfo(name)
        if symlink:
            item.external_attr = (stat.S_IFLNK | 0o777) << 16
        zipped.writestr(item, raw)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"sha256": {name: hashlib.sha256(raw).hexdigest()},
                                    "original_changes": [], "secret_pattern_findings": []}), encoding="utf-8")
    return archive, manifest


@pytest.mark.parametrize("name", ["../outside", "/absolute", "C:/escape", "data/private.sqlite3", "local_tls/key.pem", "node_modules/vendor.js", "old.png", ".env", "a\\b", "DATA/private.json", ".ENV", "NODE_MODULES/vendor.js"])
def test_archive_gate_rejects_unsafe_paths(tmp_path, name):
    with pytest.raises(ValueError, match="forbidden archive path|archive entries differ from manifest"):
        VERIFY(*bundle(tmp_path, name))


def test_archive_gate_rejects_symbolic_links(tmp_path):
    with pytest.raises(ValueError, match="forbidden archive path"):
        VERIFY(*bundle(tmp_path, symlink=True))


def test_archive_gate_rejects_tampering(tmp_path):
    archive, manifest = bundle(tmp_path)
    manifest.write_text(json.dumps({"sha256": {"README.md": "0" * 64}}), encoding="utf-8")
    with pytest.raises(ValueError, match="source hash mismatch"):
        VERIFY(archive, manifest)


def test_archive_integrity_does_not_claim_installation_acceptance(tmp_path):
    result = VERIFY(*bundle(tmp_path))
    assert result == {"integrity_verified": True, "files": 1, "installer_tested": False, "public_release_approved": False}


def test_data_source_namespace_is_not_a_private_market_database(tmp_path):
    result = VERIFY(*bundle(tmp_path, "outputs/a_share_quant/quant/data/extension_transport.py"))
    assert result["integrity_verified"] is True
