#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlparse


def ask(label: str, default: str = "", secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    value = getpass.getpass(f"{label}{suffix}: ") if secret else input(f"{label}{suffix}: ")
    return value.strip() or default


def yes_no(label: str, default: bool = False) -> bool:
    marker = "Y/n" if default else "y/N"
    value = input(f"{label} [{marker}]: ").strip().lower()
    return default if not value else value in {"y", "yes", "1", "true", "是"}


def read_env(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    values: dict[str, str] = {}
    for line in lines:
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return lines, values


def write_env(path: Path, updates: dict[str, str]) -> None:
    previous = path.stat() if path.exists() else None
    lines, _ = read_env(path)
    remaining = dict(updates)
    output = []
    for line in lines:
        if line and not line.startswith("#") and "=" in line:
            key = line.split("=", 1)[0]
            if key in remaining:
                output.append(f"{key}={remaining.pop(key)}")
                continue
        output.append(line)
    output.extend(f"{key}={value}" for key, value in remaining.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".fmo-ai-env-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("\n".join(output).rstrip() + "\n")
        os.replace(temporary, path)
        if previous:
            os.chown(path, previous.st_uid, previous.st_gid)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path: Path, value: dict) -> None:
    previous = path.stat() if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".gateway-config-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
        if previous:
            os.chown(path, previous.st_uid, previous.st_gid)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure FMO AI Voice Gateway without printing secrets")
    parser.add_argument("--env-file", default="/etc/fmo-ai-gateway.env")
    parser.add_argument("--config-file", default="/etc/fmo-ai-gateway/gateway-config.json")
    args = parser.parse_args()
    env_path, config_path = Path(args.env_file), Path(args.config_file)
    _, existing = read_env(env_path)
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    current_mqtt = next((item for item in config.get("mqtt_servers", []) if isinstance(item, dict)), {})
    current_kb = config.get("knowledge", {}) if isinstance(config.get("knowledge"), dict) else {}

    station = ask("FMO station/admin callsign", existing.get("FMO_STATION_CALLSIGN", "")).upper()
    if not re.fullmatch(r"[A-Z0-9/-]{3,16}", station):
        raise SystemExit("invalid station callsign")
    uid = ask("FMO server/test UID", existing.get("FMO_TEST_UID", "0"))
    if not uid.isdigit():
        raise SystemExit("UID must be numeric")
    ai_callsign = ask("AI transmit marker", existing.get("FMO_AI_CALLSIGN", f"AI-{station}" if station else "AI-FMO")).upper()
    if not re.fullmatch(r"[A-Z0-9/>-]{2,16}", ai_callsign):
        raise SystemExit("invalid AI transmit marker")
    bailian_key = ask("Alibaba Cloud Model Studio API Key (blank keeps current)", secret=True)
    gateway_token = existing.get("FMO_GATEWAY_TOKEN") or secrets.token_urlsafe(32)

    mqtt_host = ask("MQTT host", str(current_mqtt.get("host", "127.0.0.1")))
    mqtt_port = int(ask("MQTT port", str(current_mqtt.get("port", 1884))))
    if not 1 <= mqtt_port <= 65535:
        raise SystemExit("MQTT port must be 1..65535")
    mqtt_topic = ask("FMO voice topic", str(current_mqtt.get("topic", "FMO/RAW")))
    mqtt_client = ask("MQTT Client ID", str(current_mqtt.get("client_id", f"FMO-AI-MONITOR-{station}")))
    kb_enabled = yes_no("Use NAS knowledge base", bool(current_kb.get("enabled", False)))
    kb_url = ask("NAS knowledge URL", str(current_kb.get("base_url", "http://127.0.0.1:18787"))) if kb_enabled else "http://127.0.0.1:18787"
    parsed_kb = urlparse(kb_url)
    if kb_enabled and (parsed_kb.scheme not in {"http", "https"} or not parsed_kb.hostname):
        raise SystemExit("invalid NAS knowledge URL")
    kb_token = ask("NAS independent token (blank keeps current)", secret=True) if kb_enabled else ""

    updates = {
        "FMO_STATION_CALLSIGN": station,
        "FMO_TEST_CALLSIGN": station,
        "FMO_TEST_UID": uid,
        "FMO_AI_CALLSIGN": ai_callsign,
        "FMO_VOICE_CONTROL_CALLSIGN": station,
        "FMO_ALLOWED_CALLSIGNS": station,
        "FMO_GATEWAY_TOKEN": gateway_token,
        "FMO_KB_ENABLED": "true" if kb_enabled else "false",
        "FMO_KB_BASE_URL": kb_url,
        "FMO_AI_ENABLED": "false",
        "FMO_ASR_ENABLED": "false",
        "FMO_TTS_ENABLED": "false",
        "FMO_VOICE_AUTO_ENABLED": "false",
        "FMO_HOURLY_ANNOUNCEMENT_ENABLED": "false",
    }
    if bailian_key:
        updates["DASHSCOPE_API_KEY"] = bailian_key
    if kb_token:
        updates["FMO_KB_ADMIN_TOKEN"] = kb_token
    write_env(env_path, updates)

    config["mqtt_servers"] = [{
        "name": "primary", "host": mqtt_host, "port": mqtt_port,
        "topic": mqtt_topic, "client_id": mqtt_client, "enabled": True,
        "tls": {"enabled": False},
    }]
    config["knowledge"] = {
        "enabled": kb_enabled, "base_url": kb_url,
        "token_env": "FMO_KB_ADMIN_TOKEN", "max_results": 3, "timeout_seconds": 8,
    }
    write_json(config_path, config)
    print("Configuration saved. All AI and transmit switches remain disabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
