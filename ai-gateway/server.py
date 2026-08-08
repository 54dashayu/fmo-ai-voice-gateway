#!/usr/bin/env python3
from __future__ import annotations

import hmac
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import gateway
import callsign_policy
import aprs_positions
import reverse_geocode
import grid_lookup
import control_state
import persona_state
import speech
import gateway_config


MAX_BODY = 16384
MAX_UPLOAD_BODY = 2 * 1024 * 1024
MQTT_STATE_PATH = os.getenv("FMO_MQTT_STATE_PATH", "/run/fmo-ai-mqtt/status.json")
PTT_ACTIVE_PATH = os.getenv("FMO_PTT_ACTIVE_PATH", "/run/fmo-ai-mqtt/ptt-active")
WATCHDOG_STATE_PATH = os.getenv("FMO_WATCHDOG_STATE_PATH", "/run/fmo-service-watchdog/status.json")
APRS_POSITION_MAX_AGE = int(os.getenv("FMO_APRS_POSITION_MAX_AGE", "7200"))
STATUS_PAGE_DIR = os.getenv("FMO_STATUS_PAGE_DIR", "/opt/fmo-ai-gateway/status-page")
STATUS_ASSETS = {
    "/ai/": "index.html",
    "/ai/index.html": "index.html",
    "/ai/app.js": "app.js",
    "/ai/style.css": "style.css",
}


def flag(name: str) -> bool:
    return os.getenv(name, "false").lower() == "true"


def mqtt_state() -> dict:
    try:
        with open(MQTT_STATE_PATH, encoding="utf-8") as stream:
            value = json.load(stream)
        burst = value.get("last_burst")
        if isinstance(burst, dict):
            burst = {key: burst.get(key) for key in ("ended_at", "frames", "bytes", "duration_ms")}
        else:
            burst = None
        controls = control_state.load()
        mode = value.get("mode", "offline")
        if mode in {"automatic", "control_only"}:
            mode = "automatic" if controls["auto_reply"] else "control_only"
        hourly = value.get("hourly_announcement") if isinstance(value.get("hourly_announcement"), dict) else {}
        hourly = {**hourly, "enabled": controls["hourly_announcement"]}
        return {
            "connected": bool(value.get("connected")),
            "mode": mode,
            "bursts_total": int(value.get("bursts_total", 0)),
            "receiving": bool(value.get("receiving")),
            "end_gap_ms": int(value.get("end_gap_ms", 500)),
            "last_burst": burst,
            "ptt_enabled": bool(value.get("ptt_enabled")),
            "ptt_active": os.path.exists(PTT_ACTIVE_PATH),
            "processing": bool(value.get("processing")),
            "voice_total": int(value.get("voice_total", 0)),
            "voice_success": int(value.get("voice_success", 0)),
            "chat_total": int(value.get("chat_total", 0)),
            "knowledge_total": int(value.get("knowledge_total", 0)),
            "last_voice": value.get("last_voice") if isinstance(value.get("last_voice"), dict) else None,
            "hourly_announcement": hourly,
            "controls": controls,
            "callsign_policy": value.get("callsign_policy", "allowlist"),
            "blacklist_count": int(value.get("blacklist_count", 0)),
            "callsign_stats": value.get("callsign_stats") if isinstance(value.get("callsign_stats"), list) else [],
        }
    except (OSError, ValueError, TypeError):
        return {"connected": False, "mode": "offline", "ptt_enabled": False}


def watchdog_state() -> dict:
    try:
        with open(WATCHDOG_STATE_PATH, encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else {"ok": False}
    except (OSError, ValueError, TypeError):
        return {"ok": False, "last_action": "unavailable"}


def knowledge_admin_request(path: str, method: str = "GET", body: dict | None = None) -> dict:
    if not flag("FMO_KB_ENABLED"):
        raise RuntimeError("NAS knowledge service is disabled")
    token = os.getenv("FMO_KB_ADMIN_TOKEN", "")
    if not token:
        raise RuntimeError("NAS knowledge token is not configured")
    base = os.getenv("FMO_KB_BASE_URL", "http://127.0.0.1:18787").rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{base}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            value = json.load(response)
        if not isinstance(value, dict):
            raise RuntimeError("NAS knowledge service returned invalid data")
        return value
    except urllib.error.HTTPError as exc:
        raise ValueError(f"NAS knowledge request rejected ({exc.code})") from None
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError("NAS knowledge service is unavailable") from None


class Handler(BaseHTTPRequestHandler):
    server_version = "FmoAiGateway/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin", "")
        allowed_origins = {
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        }
        if origin in allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        expected = os.getenv("FMO_GATEWAY_TOKEN", "")
        supplied = self.headers.get("Authorization", "")
        return bool(expected) and hmac.compare_digest(supplied, f"Bearer {expected}")

    def send_status_asset(self, path: str) -> bool:
        filename = STATUS_ASSETS.get(path)
        if not filename:
            return False
        try:
            with open(os.path.join(STATUS_PAGE_DIR, filename), "rb") as stream:
                body = stream.read()
        except OSError:
            self.send_json(404, {"error": "status page asset not found"})
            return True
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(body)
        return True

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if self.send_status_asset(path):
            return
        if path == "/health":
            mqtt = mqtt_state()
            self.send_json(200, {
                "ok": True,
                "ai_enabled": gateway.enabled(),
                "mqtt": mqtt["connected"],
                "asr": flag("FMO_ASR_ENABLED"),
                "tts": flag("FMO_TTS_ENABLED"),
                "ptt": mqtt["ptt_enabled"],
            })
            return
        if path == "/status":
            mqtt = mqtt_state()
            watchdog = watchdog_state()
            gateway_status = gateway.status_snapshot()
            gateway_status["chat_total"] += mqtt.get("chat_total", 0)
            gateway_status["knowledge_total"] += mqtt.get("knowledge_total", 0)
            gateway_status["requests_total"] += mqtt.get("chat_total", 0) + mqtt.get("knowledge_total", 0)
            if mqtt.get("last_voice"):
                gateway_status["last_result"] = mqtt["last_voice"].get("result", gateway_status["last_result"])
            kb_enabled = flag("FMO_KB_ENABLED")
            nas = {"enabled": kb_enabled, "ok": False, "sections": None}
            if kb_enabled:
                try:
                    kb_base = os.getenv("FMO_KB_BASE_URL", "http://127.0.0.1:18787").rstrip("/")
                    with urllib.request.urlopen(f"{kb_base}/health", timeout=3) as response:
                        value = json.load(response)
                    nas = {"enabled": True, "ok": bool(value.get("ok")), "sections": value.get("sections")}
                except (OSError, ValueError, urllib.error.URLError):
                    pass
            self.send_json(200, {
                "ok": True,
                "gateway": gateway_status,
                "nas": nas,
                "models": {
                    "chat": os.getenv("DASHSCOPE_MODEL", str(gateway_config.provider("chat").get("model", "qwen-plus")),
                    ),
                    "embedding": os.getenv("DASHSCOPE_EMBEDDING_MODEL", str(gateway_config.provider("embedding").get("model", "text-embedding-v4"))),
                    "provider": {
                        "chat": gateway_config.provider("chat").get("provider", "dashi"),
                        "asr": gateway_config.provider("asr").get("provider", "dashi"),
                        "tts": gateway_config.provider("tts").get("provider", "dashi"),
                    },
                },
                "policy": {
                    "ai_enabled": gateway.enabled(),
                    "allowed_callsigns_count": "全部" if gateway.allow_all_callsigns() else len(gateway.allowed_callsigns()),
                    "callsign_policy": mqtt.get("callsign_policy", "allowlist"),
                    "blacklist_count": mqtt.get("blacklist_count", 0),
                    "mqtt": mqtt["connected"],
                    "asr": flag("FMO_ASR_ENABLED"),
                    "tts": flag("FMO_TTS_ENABLED"),
                    "ptt": mqtt["ptt_enabled"],
                    "knowledge": kb_enabled,
                },
                "mqtt": mqtt,
                "watchdog": watchdog,
            })
            return
        if path == "/aprs/position":
            from urllib.parse import parse_qs

            callsign = parse_qs(urlparse(self.path).query).get("callsign", [""])[0]
            normalized = aprs_positions.normalize_callsign(callsign)
            if not normalized:
                self.send_json(400, {"error": "invalid callsign"})
                return
            position = aprs_positions.lookup(normalized)
            if not position:
                self.send_json(404, {"error": "position not found", "callsign": normalized})
                return
            age = max(0, int(time.time()) - int(position["receivedAt"]))
            address = reverse_geocode.lookup(position["latitude"], position["longitude"])
            self.send_json(
                200,
                {
                    **position,
                    "address": address,
                    "ageSeconds": age,
                    "fresh": age <= APRS_POSITION_MAX_AGE,
                },
            )
            return
        if path == "/grid/address":
            from urllib.parse import parse_qs

            grid = parse_qs(urlparse(self.path).query).get("grid", [""])[0]
            normalized = grid_lookup.normalize_grid(grid)
            if not normalized:
                self.send_json(400, {"error": "invalid grid"})
                return
            address = grid_lookup.lookup(normalized)
            if not address:
                self.send_json(404, {"error": "address not found", "grid": normalized})
                return
            self.send_json(200, {"grid": normalized, "address": address})
            return
        if path == "/admin/blacklist" and self.headers.get("X-FMO-Admin") == "1":
            self.send_json(200, {"callsigns": sorted(callsign_policy.load_blacklist())})
            return
        if path == "/admin/control" and self.headers.get("X-FMO-Admin") == "1":
            self.send_json(200, {"controls": control_state.load()})
            return
        if path == "/admin/persona" and self.headers.get("X-FMO-Admin") == "1":
            self.send_json(200, {"persona": persona_state.load(), "tts": speech.tts_profile()})
            return
        if path in {"/admin/knowledge/overview", "/admin/knowledge/files"} and self.headers.get("X-FMO-Admin") == "1":
            try:
                target = "/v1/admin/overview" if path.endswith("overview") else "/v1/files"
                query = urlparse(self.path).query
                if query and target == "/v1/files":
                    target += "?" + query
                self.send_json(200, knowledge_admin_request(target))
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
            except RuntimeError as exc:
                self.send_json(503, {"error": str(exc)})
            return
        if path == "/admin/gateway-config" and self.headers.get("X-FMO-Admin") == "1":
            self.send_json(200, gateway_config.safe_snapshot())
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/admin/blacklist" and self.headers.get("X-FMO-Admin") == "1":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 2 or length > MAX_BODY:
                    raise ValueError("invalid body length")
                body = json.loads(self.rfile.read(length))
                values = callsign_policy.save_blacklist(body["callsigns"])
                self.send_json(200, {"ok": True, "callsigns": values})
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self.send_json(400, {"error": str(exc)})
            return
        if path == "/admin/control" and self.headers.get("X-FMO-Admin") == "1":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 2 or length > MAX_BODY:
                    raise ValueError("invalid body length")
                body = json.loads(self.rfile.read(length))
                if set(body) != {"name", "enabled"}:
                    raise ValueError("expected name and enabled")
                values = control_state.save({str(body["name"]): body["enabled"]})
                self.send_json(200, {"ok": True, "controls": values})
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                self.send_json(400, {"error": str(exc)})
            return
        if path == "/admin/persona" and self.headers.get("X-FMO-Admin") == "1":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 2 or length > MAX_BODY:
                    raise ValueError("invalid body length")
                body = json.loads(self.rfile.read(length))
                values = persona_state.save(body)
                self.send_json(200, {"ok": True, "persona": values})
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                self.send_json(400, {"error": str(exc)})
            return
        if path == "/admin/knowledge/upload" and self.headers.get("X-FMO-Admin") == "1":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 2 or length > MAX_UPLOAD_BODY:
                    raise ValueError("invalid upload body length")
                body = json.loads(self.rfile.read(length))
                self.send_json(200, knowledge_admin_request("/v1/files/upload", "POST", body))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                self.send_json(400, {"error": str(exc)})
            except RuntimeError as exc:
                self.send_json(503, {"error": str(exc)})
            return
        if path == "/admin/gateway-config" and self.headers.get("X-FMO-Admin") == "1":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 2 or length > MAX_BODY:
                    raise ValueError("invalid body length")
                body = json.loads(self.rfile.read(length))
                gateway_config.merge_and_save(body, target_path=os.getenv("FMO_GATEWAY_CONFIG_PATH"))
                self.send_json(200, gateway_config.safe_snapshot())
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                self.send_json(400, {"error": str(exc)})
            except OSError as exc:
                self.send_json(503, {"error": f"unable to write config: {exc}"})
            return
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_BODY:
                raise ValueError("invalid body length")
            body = json.loads(self.rfile.read(length))
            if urlparse(self.path).path != "/v1/chat":
                self.send_json(404, {"error": "not found"})
                return
            self.send_json(200, gateway.answer(str(body["callsign"]), str(body["text"])))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})
        except (RuntimeError, PermissionError) as exc:
            self.send_json(503 if isinstance(exc, RuntimeError) else 403, {"error": str(exc)})


def main() -> None:
    host = os.getenv("FMO_GATEWAY_HOST", "127.0.0.1")
    port = int(os.getenv("FMO_GATEWAY_PORT", "18788"))
    aprs_positions.start_collector()
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
