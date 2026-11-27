#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


BASE_URL = os.getenv("FMO_CHECKIN_API_URL", "").rstrip("/")
TOKEN = os.getenv("FMO_CHECKIN_API_TOKEN", "")


def configured() -> bool:
    return bool(BASE_URL and TOKEN)


def request(path: str, method: str = "GET", body: dict | None = None) -> dict:
    if not configured():
        raise RuntimeError("check-in automation API is not configured")
    data = None if body is None else json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            value = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"check-in API rejected request ({exc.code})") from None
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError("check-in API is unavailable") from exc
    if not isinstance(value, dict) or not value.get("ok"):
        raise RuntimeError("check-in API returned invalid data")
    return value


def create_session(name: str = "", planned_start_time: str = "", planned_end_time: str = "") -> dict:
    return request("/sessions", "POST", {
        "name": name,
        "plannedStartTime": planned_start_time,
        "plannedEndTime": planned_end_time,
    })["session"]


def lookup_profile(callsign: str) -> dict:
    value = request(f"/profiles/{callsign.strip().upper()}")
    profile = value.get("profile")
    return profile if isinstance(profile, dict) else {}


def add_candidate(session_id: str, callsign: str) -> dict:
    return request(f"/sessions/{session_id}/candidates", "POST", {"callsign": callsign})["candidate"]


def update_candidate(session_id: str, candidate_id: str, values: dict) -> dict:
    return request(f"/sessions/{session_id}/candidates/{candidate_id}", "PATCH", values)["candidate"]


def confirm_candidate(session_id: str, candidate_id: str) -> dict:
    return request(f"/sessions/{session_id}/candidates/{candidate_id}/confirm", "POST")["record"]


def save_session(session_id: str) -> dict:
    return request(f"/sessions/{session_id}/save", "POST")
