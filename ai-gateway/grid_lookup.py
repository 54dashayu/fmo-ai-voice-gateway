#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request


API_BASE = "https://grid.lzyike.cn/api/grid2addr/"
CACHE_SECONDS = 24 * 60 * 60
GRID_RE = re.compile(r"^[A-R]{2}[0-9]{2}(?:[A-X]{2}(?:[0-9]{2})?)?$", re.IGNORECASE)

_cache_lock = threading.Lock()
_rate_lock = threading.Lock()
_request_slots = threading.BoundedSemaphore(8)
_cache: dict[str, tuple[float, dict | None]] = {}
_last_request_at = 0.0


def normalize_grid(value: str) -> str:
    grid = value.strip().upper()
    return grid if GRID_RE.fullmatch(grid) else ""


def lookup(value: str) -> dict | None:
    global _last_request_at
    grid = normalize_grid(value)
    if not grid:
        return None
    now = time.time()
    with _cache_lock:
        cached = _cache.get(grid)
        if cached and now - cached[0] < CACHE_SECONDS:
            return dict(cached[1]) if cached[1] else None

    # 只对请求起始间隔限速，不在网络等待期间占用全局锁。
    with _rate_lock:
        now = time.time()
        # 上游限制约为每秒 2 次；按 0.65 秒间隔启动请求，网络等待可并行。
        wait = max(0.0, 0.65 - (now - _last_request_at))
        if wait:
            time.sleep(wait)
        _last_request_at = time.time()

    with _request_slots:
        try:
            request = urllib.request.Request(
                f"{API_BASE}{urllib.parse.quote(grid)}",
                headers={
                    "Accept": "application/json",
                    "User-Agent": os.getenv("FMO_HTTP_USER_AGENT", "FMO-AI-Voice-Gateway/1.0"),
                },
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.load(response)
            result = payload.get("data") if payload.get("retcode") == 0 else None
            if not isinstance(result, dict):
                result = None
        except (OSError, ValueError, TypeError):
            result = None

    if result:
        with _cache_lock:
            _cache[grid] = (time.time(), result)
    return dict(result) if result else None
