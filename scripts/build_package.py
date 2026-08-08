#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import tarfile
from pathlib import Path


INCLUDED = ("README.md", "SECURITY.md", "VERSION", "install.sh", "install-from-github.sh", "ai-gateway", "nas-knowledge-service", "deploy", "docs", "scripts")
EXCLUDED_PARTS = {".git", "dist", "__pycache__", "data", "audio", "runtime", "capture-output"}
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".key", ".pem", ".wav", ".pcap", ".pcapng")


def allowed(path: Path) -> bool:
    relative = path.parts
    if any(part in EXCLUDED_PARTS for part in relative):
        return False
    if path.name in {".env", "gateway-config.json"} or (path.name.startswith(".env.") and path.name != ".env.example"):
        return False
    return not path.name.endswith(EXCLUDED_SUFFIXES)


def build(root: Path, version: str) -> tuple[Path, Path]:
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    name = f"fmo-ai-voice-gateway-{version}"
    archive = dist / f"{name}.tar.gz"
    with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as bundle:
        for item_name in INCLUDED:
            source = root / item_name
            for path in [source, *sorted(source.rglob("*"))] if source.is_dir() else [source]:
                relative = path.relative_to(root)
                if allowed(relative):
                    bundle.add(path, arcname=Path(name) / relative, recursive=False)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = dist / f"{archive.name}.sha256"
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive, checksum


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("version", nargs="?", default=(Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip())
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    archive, checksum = build(root, args.version)
    print(archive)
    print(checksum)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
