#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import tempfile
from difflib import SequenceMatcher
from pathlib import Path


STATE_PATH = Path(os.getenv("FMO_PERSONA_STATE_PATH", "/var/lib/fmo-ai-gateway/persona-state.json"))
ADMIN_CALLSIGN = os.getenv("FMO_VOICE_CONTROL_CALLSIGN", "").strip().upper()
DEFAULTS = {"name": "机婶婶", "gender": "女", "age": "未设定", "accent": "普通话", "description": "友好、简洁的无线电语音助手"}
LIMITS = {"name": 16, "gender": 12, "age": 12, "accent": 24, "description": 120}


def load() -> dict:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        value = {}
    return {key: str(value.get(key, default)) for key, default in DEFAULTS.items()}


def save(updates: dict) -> dict:
    if not isinstance(updates, dict) or not updates or set(updates) - set(DEFAULTS):
        raise ValueError("invalid persona settings")
    cleaned = {}
    for key, value in updates.items():
        value = re.sub(r"[\x00-\x1f\x7f]", "", str(value)).strip()
        if not value or len(value) > LIMITS[key]:
            raise ValueError(f"invalid persona {key}")
        cleaned[key] = value
    value = {**load(), **cleaned}
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".persona-", dir=STATE_PATH.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, STATE_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return value


def prompt() -> str:
    value = load()
    return (
        f"你的名字是{value['name']}，身份设定为{value['gender']}性，年龄{value['age']}，"
        f"使用{value['accent']}表达；补充设定：{value['description']}。"
        "这些只是公开频道中的虚拟角色设定，不得声称是真实个人。"
    )


def _split_public_command(normalized: str):
    """Return the instruction after a 60%-matched identity and command marker."""
    marker = "我命令"
    marker_at = normalized.find(marker)
    marker_end = marker_at + len(marker)
    if marker_at < 3:
        best = None
        for start in range(3, min(11, len(normalized))):
            identity_score = SequenceMatcher(None, normalized[:start], "我是机婶婶").ratio()
            if identity_score < 0.60:
                continue
            for width in range(2, 5):
                candidate = normalized[start:start + width]
                marker_score = SequenceMatcher(None, candidate, marker).ratio()
                if marker_score >= 0.60 and (best is None or identity_score + marker_score > best[0]):
                    best = (identity_score + marker_score, start, start + width)
        if best is None:
            return None
        marker_at, marker_end = best[1], best[2]
    if SequenceMatcher(None, normalized[:marker_at], "我是机婶婶").ratio() < 0.60:
        return None
    return normalized[marker_end:]


def parse_voice_command(callsign: str, transcript: str):
    # The MQTT listener rejects blacklisted callsigns before this parser. Public
    # callers may change only the four non-administrative expression fields.
    callsign = callsign.strip().upper()
    if not callsign:
        return None
    normalized = re.sub(r"[\s，。！？、,.!?：:；;]+", "", transcript.strip())
    instruction = _split_public_command(normalized)
    if not instruction:
        return None
    instruction = re.sub(r"^(?:你|请你|给我|麻烦你)+", "", instruction)
    setter = r"(?:改一下为?|换为|换成|改为|改成|设置为|设置成|设为|设成|调整为|调整成|使用|变为|变成|是)"
    patterns = (
        ("gender", rf"(?:把|将)?(?:你的|AI的)?(?:性别|性別|人物性别){setter}(.{{1,12}})"),
        ("age", rf"(?:把|将)?(?:你的|AI的)?(?:年龄|年纪|年齡){setter}(.{{1,12}})"),
        ("accent", rf"(?:把|将)?(?:你的|AI的)?(?:说话口音|说话方式|口音|口吻|腔调){setter}(.{{1,24}})"),
        ("name", rf"(?:把|将)?(?:你的|AI的)?(?:名字|名称|称呼|昵称){setter}(.{{1,16}})"),
    )
    labels = {"gender": "性别", "age": "年龄", "accent": "口音或口吻", "name": "名字"}
    for key, pattern in patterns:
        match = re.search(pattern, instruction)
        if match:
            value = re.sub(r"(?:可以吗|好不好|行不行|好吗|谢谢|吧|了)$", "", match.group(1)).strip()
            if value:
                return {"updates": {key: value}, "reply": f"{labels[key]}已设置为{value}"}
    return None
