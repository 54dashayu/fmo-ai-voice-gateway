#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.parse
import urllib.request


API_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = os.getenv("FMO_HTTP_USER_AGENT", "FMO-AI-Voice-Gateway/0.2")
CACHE_SECONDS = 24 * 60 * 60

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict | None]] = {}
_last_request_at = 0.0


def normalize_address(payload: dict) -> dict | None:
    address = payload.get("address")
    if not isinstance(address, dict):
        return None
    result = {
        "country": address.get("country", ""),
        "province": address.get("state") or address.get("province") or "",
        "city": address.get("city") or address.get("municipality") or address.get("county") or "",
        "district": address.get("city_district") or address.get("district") or "",
        "township": address.get("suburb") or address.get("town") or address.get("township") or "",
        "displayName": payload.get("display_name", ""),
        "source": "Nominatim",
    }
    return result if any(result[key] for key in ("province", "city", "district", "township")) else None


def _lookup_sync(latitude: float, longitude: float) -> dict | None:
    global _last_request_at
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    key = f"{latitude:.4f},{longitude:.4f}"
    now = time.time()
    if not _lock.acquire(blocking=False):
        return None
    try:
        cached = _cache.get(key)
        if cached and now - cached[0] < CACHE_SECONDS:
            return dict(cached[1]) if cached[1] else None
        wait = max(0.0, 1.1 - (now - _last_request_at))
        if wait:
            time.sleep(wait)
        query = urllib.parse.urlencode(
            {
                "lat": f"{latitude:.6f}",
                "lon": f"{longitude:.6f}",
                "format": "jsonv2",
                "addressdetails": "1",
                "accept-language": "zh-CN",
            }
        )
        try:
            request = urllib.request.Request(
                f"{API_URL}?{query}",
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                result = normalize_address(json.load(response))
        except (OSError, ValueError, TypeError):
            result = None
        _last_request_at = time.time()
        _cache[key] = (_last_request_at, result)
        return dict(result) if result else None
    finally:
        _lock.release()


_jobs = queue.Queue(maxsize=16)
_pending = set()
_queue_lock = threading.Lock()
_worker_started = False


def _worker():
    while True:
        key, lat, lon = _jobs.get()
        try:
            _lookup_sync(lat, lon)
        finally:
            with _queue_lock:
                _pending.discard(key)
            _jobs.task_done()


def lookup(latitude: float, longitude: float) -> dict | None:
    """Never make an HTTP handler wait for a slow external geocoder."""
    global _worker_started
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    key = f'{latitude:.4f},{longitude:.4f}'
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return dict(cached[1]) if cached[1] else None
    with _queue_lock:
        if not _worker_started:
            threading.Thread(target=_worker, daemon=True, name='reverse-geocode').start()
            _worker_started = True
        if key not in _pending:
            try:
                _jobs.put_nowait((key, latitude, longitude))
                _pending.add(key)
            except queue.Full:
                pass
    return None
