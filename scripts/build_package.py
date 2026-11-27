#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import subprocess
import tarfile
from pathlib import Path


INCLUDED_ROOTS = {
    "README.md", "SECURITY.md", "VERSION", "install.sh", "install-from-github.sh",
    "ai-gateway", "nas-knowledge-service", "deploy", "docs", "scripts",
}
EXCLUDED_PARTS = {".git", "dist", "__pycache__", "data", "audio", "runtime", "capture-output"}
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".key", ".pem", ".wav", ".pcap", ".pcapng")
SECRET_NAMES = {".env", "gateway-config.json", "cluster-credentials.json"}


def allowed(path: Path) -> bool:
    if not path.parts or path.parts[0] not in INCLUDED_ROOTS:
        return False
    if any(part in EXCLUDED_PARTS for part in path.parts):
        return False
    if path.name in SECRET_NAMES or (path.name.startswith(".env.") and path.name != ".env.example"):
        return False
    return not path.name.endswith(EXCLUDED_SUFFIXES)


def tracked_files(root: Path) -> list[Path]:
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
    files = [Path(item.decode()) for item in raw.split(b"\0") if item]
    selected = [path for path in files if allowed(path)]
    for relative in selected:
        source = root / relative
        if source.is_symlink() or not source.is_file():
            raise RuntimeError(f"release manifest rejects non-regular file: {relative}")
    return sorted(selected)


def build(root: Path, version: str) -> tuple[Path, Path]:
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    name = f"fmo-ai-voice-gateway-{version}"
    archive = dist / f"{name}.tar.gz"
    with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as bundle:
        for relative in tracked_files(root):
            bundle.add(root / relative, arcname=Path(name) / relative, recursive=False)
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
