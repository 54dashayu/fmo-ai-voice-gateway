#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_GATEWAY_CONFIG_PATH = os.getenv(
    "FMO_GATEWAY_CONFIG_PATH", "/etc/fmo-ai-gateway/gateway-config.json",
)

ALLOWED_PROVIDER_NAMES = {"dashi", "dashscope", "bailian", "aliyun-bailian"}
ALLOWED_DASHSCOPE_HOSTS = {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"}
PROVIDER_CAPABILITIES = {"chat", "embedding", "asr", "tts"}
SECRET_PLACEHOLDER = "********"


def _load_json(path: str) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def load() -> dict[str, Any]:
    return _load_json(DEFAULT_GATEWAY_CONFIG_PATH)


def _merge_dict(base: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(overrides, dict):
        return base
    value = dict(base)
    for key, item in overrides.items():
        if isinstance(item, dict) and isinstance(value.get(key), dict):
            value[key] = _merge_dict(value[key], item)
        else:
            value[key] = item
    return value


def mqtt_servers() -> list[dict[str, Any]]:
    config = load()
    value = config.get("mqtt_servers")
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def provider(name: str) -> dict[str, Any]:
    config = load().get("providers", {})
    if not isinstance(config, dict):
        config = {}
    base = {
        "provider": "dashscope",
        "base_url": os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/"),
        "model": os.getenv(f"DASHSCOPE_{name.upper()}_MODEL", "qwen-plus"),
        "api_key_env": "DASHSCOPE_API_KEY",
        "api_key": os.getenv("DASHSCOPE_API_KEY", ""),
    }
    specific = config.get(name)
    if isinstance(specific, dict):
        return _merge_dict(base, specific)
    override = os.getenv(f"FMO_{name.upper()}_PROVIDER")
    if override:
        return _merge_dict(base, {"provider": override})
    return base


def knowledge() -> dict[str, Any]:
    config = load().get("knowledge", {})
    if not isinstance(config, dict):
        config = {}
    token_env = str(config.get("token_env", "FMO_KB_ADMIN_TOKEN"))
    enabled_value = config.get("enabled")
    enabled = enabled_value if type(enabled_value) is bool else os.getenv("FMO_KB_ENABLED", "false").lower() == "true"
    return {
        "enabled": enabled,
        "base_url": str(config.get("base_url", os.getenv("FMO_KB_BASE_URL", "http://127.0.0.1:18787"))).rstrip("/"),
        "token_env": token_env,
        "token": str(config.get("token", os.getenv(token_env, ""))),
        "max_results": int(config.get("max_results", os.getenv("FMO_KB_MAX_RESULTS", "3"))),
        "timeout_seconds": float(config.get("timeout_seconds", os.getenv("FMO_KB_TIMEOUT_SECONDS", "8"))),
    }


def selected_mqtt_server() -> dict[str, Any]:
    targets = mqtt_servers()
    selected = os.getenv("FMO_MQTT_PROFILE", "").strip()
    chosen: dict[str, Any] = {}
    for item in targets:
        if item.get("enabled") is False:
            continue
        if selected and item.get("name") == selected:
            chosen = item
            break
        if not chosen:
            chosen = item
    if chosen:
        return {
            "name": chosen.get("name", "primary"),
            "host": str(chosen.get("host", "127.0.0.1")),
            "port": int(chosen.get("port", 1884)),
            "topic": str(chosen.get("topic", "FMO/RAW")),
            "client_id": str(chosen.get("client_id", "FMO-AI-MONITOR-CHANGE-ME")),
            "username": chosen.get("username"),
            "password": chosen.get("password"),
            "tls": chosen.get("tls", {}),
        }
    return {
        "name": "default",
        "host": os.getenv("FMO_MQTT_HOST", "127.0.0.1"),
        "port": int(os.getenv("FMO_MQTT_PORT", "1884")),
        "topic": os.getenv("FMO_MQTT_TOPIC", "FMO/RAW"),
        "client_id": os.getenv("FMO_MQTT_CLIENT_ID", "FMO-AI-MONITOR-CHANGE-ME"),
        "username": os.getenv("FMO_MQTT_USERNAME"),
        "password": os.getenv("FMO_MQTT_PASSWORD"),
        "tls": {},
    }


def safe_snapshot() -> dict[str, Any]:
    """Return a redacted config snapshot suitable for web UI."""
    loaded = load()
    def _redact_server(server: dict[str, Any]) -> dict[str, Any]:
        value = dict(server)
        if value.get("password"):
            value["password"] = "********"
        return {
            k: value.get(k) for k in ("name", "host", "port", "topic", "client_id", "username", "password", "enabled", "tls")
            if k in value
        }

    providers = loaded.get("providers", {})
    if not isinstance(providers, dict):
        providers = {}
    sanitized_providers = {}
    for name, conf in providers.items():
        if not isinstance(conf, dict):
            continue
        sanitized_providers[name] = {
            "provider": conf.get("provider", "dashscope"),
            "base_url": conf.get("base_url", ""),
            "model": conf.get("model", ""),
            "api_key_env": conf.get("api_key_env", ""),
            "has_api_key": bool(conf.get("api_key") or os.getenv(conf.get("api_key_env", "DASHSCOPE_API_KEY"), "")),
        }
    knowledge_config = knowledge()
    return {
        "mqtt_servers": [_redact_server(item) for item in mqtt_servers()],
        "providers": sanitized_providers,
        "knowledge": {
            "enabled": knowledge_config["enabled"],
            "base_url": knowledge_config["base_url"],
            "token_env": knowledge_config["token_env"],
            "has_token": bool(knowledge_config["token"]),
            "max_results": knowledge_config["max_results"],
            "timeout_seconds": knowledge_config["timeout_seconds"],
        },
        "selected_profile": os.getenv("FMO_MQTT_PROFILE", ""),
    }


def _validate_config(config: dict[str, Any]) -> None:
    servers = config.get("mqtt_servers", [])
    if not isinstance(servers, list) or len(servers) > 20:
        raise ValueError("mqtt_servers must be a list with at most 20 entries")
    for server in servers:
        if not isinstance(server, dict):
            raise ValueError("invalid MQTT server entry")
        host = str(server.get("host", "")).strip()
        topic = str(server.get("topic", "")).strip()
        try:
            port = int(server.get("port", 0))
        except (TypeError, ValueError):
            port = 0
        if not host or not topic or not 1 <= port <= 65535:
            raise ValueError("MQTT host, port or topic is invalid")

    providers = config.get("providers", {})
    if not isinstance(providers, dict) or not set(providers).issubset(PROVIDER_CAPABILITIES):
        raise ValueError("unsupported provider capability")
    for capability, provider_config in providers.items():
        if not isinstance(provider_config, dict):
            raise ValueError(f"invalid {capability} provider")
        provider_name = str(provider_config.get("provider", "dashscope")).strip().lower()
        if provider_name not in ALLOWED_PROVIDER_NAMES:
            raise ValueError("only Alibaba Cloud Model Studio is allowed")
        base_url = str(provider_config.get("base_url", "")).strip()
        parsed = urlparse(base_url)
        provider_host = (parsed.hostname or "").lower()
        is_workspace_endpoint = provider_host.endswith(".maas.aliyuncs.com")
        if parsed.scheme != "https" or (provider_host not in ALLOWED_DASHSCOPE_HOSTS and not is_workspace_endpoint):
            raise ValueError("provider endpoint must be an Alibaba Cloud Model Studio HTTPS endpoint")
        if not str(provider_config.get("model", "")).strip():
            raise ValueError(f"{capability} model is required")
        api_key_env = str(provider_config.get("api_key_env", "DASHSCOPE_API_KEY")).strip()
        if not api_key_env.startswith("DASHSCOPE_") or not api_key_env.endswith("API_KEY"):
            raise ValueError("API key environment name must use DASHSCOPE_*API_KEY")

    knowledge_config = config.get("knowledge", {})
    if not isinstance(knowledge_config, dict):
        raise ValueError("invalid knowledge configuration")
    if knowledge_config:
        if type(knowledge_config.get("enabled", False)) is not bool:
            raise ValueError("knowledge enabled must be boolean")
        parsed = urlparse(str(knowledge_config.get("base_url", "")))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("knowledge base URL must be HTTP or HTTPS")
        token_env = str(knowledge_config.get("token_env", "FMO_KB_ADMIN_TOKEN"))
        if not token_env.startswith("FMO_") or not token_env.endswith("TOKEN"):
            raise ValueError("knowledge token environment name must use FMO_*TOKEN")
        if not 1 <= int(knowledge_config.get("max_results", 3)) <= 10:
            raise ValueError("knowledge max_results must be 1..10")
        if not 1 <= float(knowledge_config.get("timeout_seconds", 8)) <= 60:
            raise ValueError("knowledge timeout_seconds must be 1..60")


def _preserve_secrets(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Treat blank or redacted UI secret values as 'keep the existing value'."""
    value = json.loads(json.dumps(overrides))
    old_servers = {
        str(item.get("name", "primary")): item
        for item in base.get("mqtt_servers", [])
        if isinstance(item, dict)
    }
    for item in value.get("mqtt_servers", []):
        if not isinstance(item, dict):
            continue
        old = old_servers.get(str(item.get("name", "primary")), {})
        if item.get("password") in {None, "", SECRET_PLACEHOLDER}:
            if old.get("password"):
                item["password"] = old["password"]
            else:
                item.pop("password", None)

    old_providers = base.get("providers", {}) if isinstance(base.get("providers"), dict) else {}
    for name, item in value.get("providers", {}).items():
        if not isinstance(item, dict):
            continue
        old = old_providers.get(name, {}) if isinstance(old_providers.get(name), dict) else {}
        if item.get("api_key") in {None, "", SECRET_PLACEHOLDER}:
            if old.get("api_key"):
                item["api_key"] = old["api_key"]
            else:
                item.pop("api_key", None)
    knowledge_override = value.get("knowledge")
    if isinstance(knowledge_override, dict) and knowledge_override.get("token") in {None, "", SECRET_PLACEHOLDER}:
        old_knowledge = base.get("knowledge", {}) if isinstance(base.get("knowledge"), dict) else {}
        if old_knowledge.get("token"):
            knowledge_override["token"] = old_knowledge["token"]
        else:
            knowledge_override.pop("token", None)
    return value


def merge_and_save(overrides: dict[str, Any], target_path: str | None = None) -> dict[str, Any]:
    """Merge overrides into the existing config and persist atomically."""
    if not isinstance(overrides, dict):
        raise TypeError("invalid config payload")

    config_path = Path(target_path or DEFAULT_GATEWAY_CONFIG_PATH)
    base = load()
    merged = _merge_dict(base, _preserve_secrets(base, overrides))
    _validate_config(merged)
    text = json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp_path = config_path.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as stream:
        stream.write(text)
    os.chmod(temp_path, 0o600)
    temp_path.replace(config_path)
    return merged
