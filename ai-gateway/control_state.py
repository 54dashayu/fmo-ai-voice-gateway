#!/usr/bin/env python3
from __future__ import annotations

import callsign_policy
import json
import os
import re
import tempfile
from difflib import SequenceMatcher
from pathlib import Path


STATE_PATH = Path(os.getenv("FMO_CONTROL_STATE_PATH", "/var/lib/fmo-ai-gateway/control-state.json"))
DEFAULTS = {
    "auto_reply": False,
    "hourly_announcement": False,
    "persona_voice_commands": True,
    # Administrator-only web gate. Enabling it permits music workflows to be
    # scheduled or started, but never bypasses channel/PTT safety checks.
    "music_playback": False,
    # Public voice/web gate for one-shot raw voice replay. It is deliberately
    # disabled by default and never grants any other administration right.
    "parrot_replay": False,
    "parrot_restore_auto_reply": False,
    # This is only the master permission gate. The net-control state machine
    # and its voice start command must also pass their own checks before use.
    "net_checkin": False,
}
ADMIN_CALLSIGN = os.getenv("FMO_VOICE_CONTROL_CALLSIGN", "").strip().upper()
COMMANDS = {
    "我是机婶婶开启自动回复": ("auto_reply", True, "自动回复已开启"),
    "我是机婶婶关闭自动回复": ("auto_reply", False, "自动回复已关闭"),
    "我是机婶婶我命令开启整点报时": ("hourly_announcement", True, "整点报时已开启"),
    "我是机婶婶我命令关闭整点报时": ("hourly_announcement", False, "整点报时已关闭"),
}
VOICE_MATCH_THRESHOLD = min(1.0, max(0.75, float(os.getenv("FMO_VOICE_CONTROL_MATCH_THRESHOLD", "0.75"))))
AUTO_REPLY_MATCH_THRESHOLD = min(1.0, max(0.5, float(os.getenv("FMO_AUTO_REPLY_MATCH_THRESHOLD", "0.60"))))
PARROT_MATCH_THRESHOLD = min(1.0, max(0.5, float(os.getenv("FMO_PARROT_MATCH_THRESHOLD", "0.60"))))

PARROT_COMMANDS = {
    "我是机婶婶我命令开启鹦鹉海螺功能": (True, "鹦鹉海螺功能已开启"),
    "我是机婶婶我命令关闭鹦鹉海螺功能": (False, "鹦鹉海螺功能已关闭"),
}
PARROT_OPEN_WORDS = ("开启", "打开", "启动")
PARROT_CLOSE_WORDS = ("关闭", "关掉", "停止")
PARROT_NEGATIONS = ("不要", "别", "不准", "不许", "不能", "无需", "并非", "不是")
PARROT_QUOTE_MARKERS = ("如果", "比如", "例如", "他说", "她说", "你说", "引用", "重复", "这句话", "口令是")
PARROT_NAME_ALIASES = ("鹦鹉海螺", "鹦鹉海罗", "鹦鹉海裸", "鹦鹉海落", "鹦鹉海路")


def load() -> dict:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        value = {}
    result = {key: bool(value.get(key, default)) for key, default in DEFAULTS.items()}
    # A legacy/racing client must never leave two reply engines enabled. An
    # ambiguous persisted conflict migrates to the safest state: both off.
    if result["auto_reply"] and result["parrot_replay"]:
        result["auto_reply"] = False
        result["parrot_replay"] = False
    if not result["parrot_replay"]:
        result["parrot_restore_auto_reply"] = False
    return result


def save(updates: dict) -> dict:
    if not isinstance(updates, dict) or not updates or set(updates) - set(DEFAULTS) or "parrot_restore_auto_reply" in updates:
        raise ValueError("invalid control state")
    if any(type(value) is not bool for value in updates.values()):
        raise ValueError("control values must be boolean")
    if updates.get("auto_reply") is True and updates.get("parrot_replay") is True:
        raise ValueError("reply modes are mutually exclusive")
    old = load()
    value = {**old, **updates}
    if updates.get("auto_reply") is True:
        value["parrot_replay"] = False
    elif updates.get("parrot_replay") is True:
        value["auto_reply"] = False
        value["parrot_restore_auto_reply"] = old["auto_reply"] or old["parrot_restore_auto_reply"]
    elif updates.get("auto_reply") is False:
        value["parrot_replay"] = False
    elif updates.get("parrot_replay") is False and old["parrot_replay"]:
        value["auto_reply"] = old["parrot_restore_auto_reply"]
    if not value["parrot_replay"]:
        value["parrot_restore_auto_reply"] = False
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


def reply_mode() -> str:
    value = load()
    if value["auto_reply"]:
        return "auto_reply"
    if value["parrot_replay"]:
        return "parrot_replay"
    return "off"


def set_reply_mode(mode: str) -> dict:
    if mode not in {"auto_reply", "parrot_replay", "off"}:
        raise ValueError("invalid reply mode")
    return save({
        "auto_reply": mode == "auto_reply",
        "parrot_replay": mode == "parrot_replay",
    })


def parse_voice_command(callsign: str, transcript: str):
    callsign = callsign.strip().upper()
    normalized = re.sub(r"[\s，。！？、,.!?：:；;]+", "", transcript.strip())
    if not callsign:
        return None
    parrot_intent = looks_like_parrot_command(normalized)
    parrot = parse_parrot_voice_command(callsign, normalized)
    if parrot:
        return _resolve_reply_mode_command(parrot)
    # A rejected/ambiguous parrot phrase must not fall through into the fuzzy
    # matcher for auto reply or any other control.
    if parrot_intent:
        return None
    public_commands = {
        "语音控制开启自动回复": (True, "自动回复已开启"),
        "语音控制关闭自动回复": (False, "自动回复已关闭"),
    }
    if normalized in public_commands:
        value, reply = public_commands[normalized]
        return _resolve_reply_mode_command({"name": "auto_reply", "value": value, "reply": reply})
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
    if command[0] == "hourly_announcement" and not callsign_policy.is_admin_callsign(callsign, ADMIN_CALLSIGN):
        return None
    if not command:
        return None
    name, value, reply = command
    command_result = {"name": name, "value": value, "reply": reply}
    return _resolve_reply_mode_command(command_result) if name == "auto_reply" else command_result


def parse_parrot_voice_command(callsign: str, transcript: str):
    """Parse only an explicit public parrot switch command.

    Similarity is textual similarity after normalization, not ASR confidence.
    The action is resolved before fuzzy matching so open and close can never be
    confused with each other.
    """
    if not callsign.strip():
        return None
    normalized = re.sub(r"[\s，。！？、,.!?：:；;（）()\[\]【】]+", "", transcript.strip())
    if not normalized.startswith("我是") or not looks_like_parrot_command(normalized):
        return None
    if any(word in normalized for word in PARROT_NEGATIONS + PARROT_QUOTE_MARKERS):
        return None
    for alias in PARROT_NAME_ALIASES:
        normalized = normalized.replace(alias, "鹦鹉海螺")
    normalized = normalized.replace("工能", "功能")
    has_open = any(word in normalized for word in PARROT_OPEN_WORDS)
    has_close = any(word in normalized for word in PARROT_CLOSE_WORDS)
    if has_open == has_close:
        return None
    desired = has_open
    candidates = [(text, value) for text, value in PARROT_COMMANDS.items() if value[0] is desired]
    best_text, command = max(candidates, key=lambda item: SequenceMatcher(None, normalized, item[0]).ratio())
    similarity = SequenceMatcher(None, normalized, best_text).ratio()
    if similarity < PARROT_MATCH_THRESHOLD:
        return None
    value, reply = command
    return {"name": "parrot_replay", "value": value, "reply": reply, "similarity": similarity}


def looks_like_parrot_command(normalized: str) -> bool:
    compact = re.sub(r"[\s，。！？、,.!?：:；;（）()\[\]【】]+", "", str(normalized))
    return any(alias in compact for alias in PARROT_NAME_ALIASES)


def _resolve_reply_mode_command(command: dict) -> dict:
    name = command["name"]
    value = command["value"]
    current = reply_mode()
    if value:
        return {**command, "mode": name, "noop": current == name}
    if name == "auto_reply":
        return {**command, "mode": "off", "noop": current == "off", "reply": "自动回复和鹦鹉海螺均已关闭"}
    if current != name:
        current_label = {"auto_reply": "自动回复", "parrot_replay": "鹦鹉海螺", "off": "关闭"}[current]
        target_label = "自动回复" if name == "auto_reply" else "鹦鹉海螺"
        return {
            **command,
            "noop": True,
            "reply": f"{target_label}当前未开启，{current_label}模式保持不变",
        }
    restore = load()["parrot_restore_auto_reply"]
    return {**command, "mode": "auto_reply" if restore else "off", "noop": False,
            "reply": "鹦鹉海螺已关闭，自动回复已恢复" if restore else command["reply"]}
