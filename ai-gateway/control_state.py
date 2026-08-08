#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import tempfile
from difflib import SequenceMatcher
from pathlib import Path


STATE_PATH = Path(os.getenv("FMO_CONTROL_STATE_PATH", "/var/lib/fmo-ai-gateway/control-state.json"))
DEFAULTS = {"auto_reply": True, "hourly_announcement": True, "persona_voice_commands": True}
ADMIN_CALLSIGN = os.getenv("FMO_VOICE_CONTROL_CALLSIGN", "").strip().upper()
COMMANDS = {
    "我是机婶婶开启自动回复": ("auto_reply", True, "自动回复已开启"),
    "我是机婶婶关闭自动回复": ("auto_reply", False, "自动回复已关闭"),
    "我是机婶婶我命令开启整点报时": ("hourly_announcement", True, "整点报时已开启"),
    "我是机婶婶我命令关闭整点报时": ("hourly_announcement", False, "整点报时已关闭"),
}
VOICE_MATCH_THRESHOLD = min(1.0, max(0.75, float(os.getenv("FMO_VOICE_CONTROL_MATCH_THRESHOLD", "0.75"))))
AUTO_REPLY_MATCH_THRESHOLD = min(1.0, max(0.5, float(os.getenv("FMO_AUTO_REPLY_MATCH_THRESHOLD", "0.60"))))


def load() -> dict:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        value = {}
    return {key: bool(value.get(key, default)) for key, default in DEFAULTS.items()}


def save(updates: dict) -> dict:
    if not isinstance(updates, dict) or not updates or set(updates) - set(DEFAULTS):
        raise ValueError("invalid control state")
    if any(type(value) is not bool for value in updates.values()):
        raise ValueError("control values must be boolean")
    value = {**load(), **updates}
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".control-", dir=STATE_PATH.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, STATE_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return value


def enabled(name: str) -> bool:
    if name not in DEFAULTS:
        raise ValueError("unknown control")
    return load()[name]


def parse_voice_command(callsign: str, transcript: str):
    callsign = callsign.strip().upper()
    normalized = re.sub(r"[\s，。！？、,.!?：:；;]+", "", transcript.strip())
    actions = [action for action in ("开启", "关闭") if action in normalized]
    if not normalized.startswith("我是") or len(actions) != 1:
        return None
    action = actions[0]
    candidates = [(text, value) for text, value in COMMANDS.items() if action in text]
    best_text, command = max(candidates, key=lambda item: SequenceMatcher(None, normalized, item[0]).ratio())
    similarity = SequenceMatcher(None, normalized, best_text).ratio()
    threshold = AUTO_REPLY_MATCH_THRESHOLD if command[0] == "auto_reply" else VOICE_MATCH_THRESHOLD
    if similarity < threshold:
        return None
    if command[0] == "hourly_announcement" and callsign != ADMIN_CALLSIGN:
        return None
    if not command:
        return None
    name, value, reply = command
    return {"name": name, "value": value, "reply": reply}
