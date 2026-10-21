"""Read-only source ZIP integrity check; never extract, import or run its code.

The accompanying manifest is not a digital signature. Obtain both files from
a trusted channel. This does not certify licensing, runtime or installation.
"""
import argparse
import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

LIMIT = 200_000_000
BLOCKED = {".git", "node_modules", ".vendor", ".venv", ".validation", "data_cache", "local_tls"}
SECRET = re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?:ghp_|github_pat_|sk-proj-)[A-Za-z0-9_]{20,}")


def verify(archive: Path, manifest: Path) -> dict:
    evidence = json.loads(manifest.read_text(encoding="utf-8"))
    expected = evidence["sha256"]
    if evidence.get("original_changes") or evidence.get("secret_pattern_findings"):
        raise ValueError("source baseline or secret-pattern gate failed")
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        names = [item.filename for item in entries]
        if len(names) != len({name.casefold() for name in names}) or set(names) != set(expected):
            raise ValueError("archive entries differ from manifest")
        if sum(item.file_size for item in entries) > LIMIT:
            raise ValueError("uncompressed archive exceeds limit")
        for item in entries:
            path = PurePosixPath(item.filename)
            lower = PurePosixPath(item.filename.casefold())
            if (path.is_absolute() or ".." in path.parts or "\\" in item.filename
                    or ":" in item.filename or any(part in BLOCKED for part in lower.parts)
                    or ("data" in lower.parts and not item.filename.startswith("outputs/a_share_quant/quant/data/"))
                    or stat.S_ISLNK(item.external_attr >> 16)
                    or path.suffix.lower() in {".sqlite3", ".db", ".pem", ".key", ".png", ".ico"}
                    or lower.name == ".env"):
                raise ValueError("forbidden archive path")
            raw = bundle.read(item)
            if hashlib.sha256(raw).hexdigest() != expected[item.filename]:
                raise ValueError("source hash mismatch")
            if SECRET.search(raw):
                raise ValueError("secret-pattern gate failed")
    return {"integrity_verified": True, "files": len(entries),
            "installer_tested": False, "public_release_approved": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.archive, args.manifest)))


if __name__ == "__main__":
    main()
