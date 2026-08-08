#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path


CALLSIGN_RE = re.compile(r"^[A-Z0-9][A-Z0-9/-]{2,11}$")
BLACKLIST_PATH = Path(os.getenv("FMO_CALLSIGN_BLACKLIST_PATH", "/var/lib/fmo-ai-gateway/callsign-blacklist.json"))
STATS_PATH = Path(os.getenv("FMO_CALLSIGN_STATS_PATH", "/var/lib/fmo-ai-gateway/callsign-stats.json"))


def normalize(value: str) -> str:
    callsign = value.strip().upper()
    if not CALLSIGN_RE.fullmatch(callsign):
        raise ValueError("invalid callsign")
    return callsign


def _read(path: Path, fallback):
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError, TypeError):
        return fallback


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_blacklist() -> set[str]:
    values = _read(BLACKLIST_PATH, [])
    if not isinstance(values, list):
        return set()
    return {item for value in values[:1000] if isinstance(value, str) for item in [value.strip().upper()] if CALLSIGN_RE.fullmatch(item)}


def save_blacklist(values: list[str]) -> list[str]:
    if not isinstance(values, list) or len(values) > 1000:
        raise ValueError("blacklist must be a list with at most 1000 callsigns")
    normalized = sorted({normalize(value) for value in values})
    _write(BLACKLIST_PATH, normalized)
    return normalized


def load_stats() -> dict[str, dict]:
    value = _read(STATS_PATH, {})
    return value if isinstance(value, dict) else {}


def save_stats(value: dict[str, dict]) -> None:
    _write(STATS_PATH, value)
