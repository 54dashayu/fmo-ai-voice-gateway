from __future__ import annotations

import os
import threading
import time


_LOCK = threading.Lock()
_LAST_RECONNECT_AT = 0.0
_MIN_INTERVAL_SECONDS = 30.0
_REQUEST_PATH = os.getenv(
    "FMO_MQTT_RECONNECT_REQUEST_PATH",
    "/var/lib/fmo-ai-gateway/reconnect-mqtt.request",
)


def reconnect_voice_link(now: float | None = None, request_path: str | None = None) -> dict:
    """Signal the root-owned systemd path unit without granting web process privileges."""
    global _LAST_RECONNECT_AT
    current = time.time() if now is None else float(now)
    with _LOCK:
        remaining = _MIN_INTERVAL_SECONDS - (current - _LAST_RECONNECT_AT)
        if _LAST_RECONNECT_AT and remaining > 0:
            raise RuntimeError(f"请等待 {int(remaining) + 1} 秒后再重连")
        target = request_path or _REQUEST_PATH
        try:
            with open(target, "w", encoding="ascii") as stream:
                stream.write(f"{int(current)}\n")
        except OSError as exc:
            raise RuntimeError("无法提交语音链路重连请求") from exc
        _LAST_RECONNECT_AT = current
    return {"ok": True, "action": "mqtt_voice_link_reconnect_requested", "requested_at": int(current)}
