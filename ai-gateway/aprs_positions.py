#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import socket
import tempfile
import threading
import time
from pathlib import Path


STATE_PATH = Path(
    os.getenv("FMO_APRS_POSITION_STATE_PATH", "/var/lib/fmo-ai-gateway/aprs-positions.json")
)
APRS_LOGIN = os.getenv("FMO_APRS_LOGIN", "N0CALL")
APRS_SERVERS = os.getenv(
    "FMO_APRS_SERVERS", "china.aprs2.net:14580,rotate.aprs2.net:14580"
)
APRS_FILTER = os.getenv("FMO_APRS_FILTER", "u/APFMO*")
SAVE_INTERVAL_SECONDS = 10
RECONNECT_DELAY_SECONDS = 5

POSITION_RE = re.compile(
    r"^[!=/@](?:\d{6}[hz/])?"
    r"(?P<lat_deg>\d{2})(?P<lat_min>\d{2}\.\d{2})(?P<lat_hemi>[NS])"
    r"(?P<table>.)(?P<lon_deg>\d{3})(?P<lon_min>\d{2}\.\d{2})(?P<lon_hemi>[EW])"
)
CALLSIGN_RE = re.compile(r"^[A-Z0-9]{3,6}(?:-[A-Z0-9]{1,2})?$")

_lock = threading.RLock()
_positions: dict[str, dict] = {}
_collector_thread: threading.Thread | None = None
_stop_event = threading.Event()
_last_saved_at = 0.0


def normalize_callsign(value: str) -> str:
    callsign = value.strip().upper()
    return callsign if CALLSIGN_RE.fullmatch(callsign) else ""


def parse_position_packet(line: str, received_at: int | None = None) -> dict | None:
    if ":" not in line or ">" not in line:
        return None
    header, payload = line.split(":", 1)
    source, route = header.split(">", 1)
    callsign = normalize_callsign(source)
    if not callsign:
        return None

    destination = route.split(",", 1)[0].upper()
    if not destination.startswith("APFMO"):
        return None

    match = POSITION_RE.match(payload)
    if not match:
        return None

    latitude = int(match.group("lat_deg")) + float(match.group("lat_min")) / 60
    longitude = int(match.group("lon_deg")) + float(match.group("lon_min")) / 60
    if match.group("lat_hemi") == "S":
        latitude = -latitude
    if match.group("lon_hemi") == "W":
        longitude = -longitude
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None

    return {
        "callsign": callsign,
        "latitude": round(latitude, 6),
        "longitude": round(longitude, 6),
        "receivedAt": int(received_at if received_at is not None else time.time()),
        "destination": destination,
        "source": "APRS-IS",
    }


def _load_state() -> None:
    global _positions
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            with _lock:
                _positions = {
                    key: item
                    for key, item in value.items()
                    if normalize_callsign(key) and isinstance(item, dict)
                }
    except (OSError, ValueError, TypeError):
        pass


def _save_state(force: bool = False) -> None:
    global _last_saved_at
    now = time.time()
    if not force and now - _last_saved_at < SAVE_INTERVAL_SECONDS:
        return
    with _lock:
        snapshot = dict(_positions)
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".aprs-positions-", dir=STATE_PATH.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(snapshot, stream, ensure_ascii=False, separators=(",", ":"))
            os.replace(temp_name, STATE_PATH)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        _last_saved_at = now
    except OSError as exc:
        print(f"[APRS] save state failed: {exc}", flush=True)


def lookup(callsign: str) -> dict | None:
    normalized = normalize_callsign(callsign)
    if not normalized:
        return None
    with _lock:
        item = _positions.get(normalized)
        return dict(item) if item else None


def _server_candidates() -> list[tuple[str, int]]:
    result = []
    for raw in APRS_SERVERS.split(","):
        host, _, port = raw.strip().partition(":")
        if host:
            result.append((host, int(port or "14580")))
    return result


def _read_server(host: str, port: int) -> None:
    print(f"[APRS] connecting to {host}:{port} filter {APRS_FILTER}", flush=True)
    with socket.create_connection((host, port), timeout=15) as sock:
        sock.settimeout(30)
        login = f"user {APRS_LOGIN} pass -1 vers FmoDashboard 2.0.3 filter {APRS_FILTER}\r\n"
        sock.sendall(login.encode("ascii"))
        buffer = b""
        while not _stop_event.is_set():
            try:
                chunk = sock.recv(8192)
            except socket.timeout:
                sock.sendall(b"# keepalive\r\n")
                continue
            if not chunk:
                raise ConnectionError("APRS-IS connection closed")
            buffer += chunk
            while b"\n" in buffer:
                raw_line, buffer = buffer.split(b"\n", 1)
                line = raw_line.decode("utf-8", "replace").strip()
                if not line or line.startswith("#"):
                    continue
                position = parse_position_packet(line)
                if position:
                    with _lock:
                        _positions[position["callsign"]] = position
                    _save_state()


def _collector_loop() -> None:
    _load_state()
    candidates = _server_candidates()
    index = 0
    while not _stop_event.is_set():
        if not candidates:
            print("[APRS] no server configured", flush=True)
            return
        host, port = candidates[index % len(candidates)]
        index += 1
        try:
            _read_server(host, port)
        except (OSError, ValueError, ConnectionError) as exc:
            print(f"[APRS] {host}:{port} disconnected: {exc}", flush=True)
            _stop_event.wait(RECONNECT_DELAY_SECONDS)
    _save_state(force=True)


def start_collector() -> None:
    global _collector_thread
    if _collector_thread and _collector_thread.is_alive():
        return
    _stop_event.clear()
    _collector_thread = threading.Thread(
        target=_collector_loop, name="fmo-aprs-position-collector", daemon=True
    )
    _collector_thread.start()
