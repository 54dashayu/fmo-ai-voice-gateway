#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


STATE_PATH = Path(os.getenv("FMO_NET_CHECKIN_STATE_PATH", "/var/lib/fmo-ai-gateway/net-checkin-state.json"))
DEFAULTS = {
    "start_time": "",
    "end_time": "",
    "repeat_enabled": True,
    "repeat_type": "daily",
    "schedule_date": "",
    "weekly_day": 0,
    "opening_text": "这里是个人技术研究 AI 测试台，今天的台网点名活动现在开始。请参加活动的台友依次报出呼号，等待点名。有请第一位友台。",
    "question_text": "{callsign}，{callsign}，这里是 AI-FMO，收到你的报到。请依次报告你的QTH、使用设备和发射功率。",
    "closing_text": "本次台网点名活动到此结束，感谢各位台友参与，这里是个人技术研究 AI 测试台，73。",
}
MAX_TEXT_LENGTH = 500


def load() -> dict:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    result = dict(DEFAULTS)
    for key in ("start_time", "end_time", "schedule_date"):
        result[key] = str(value.get(key, DEFAULTS[key])).strip()
    result["repeat_enabled"] = bool(value.get("repeat_enabled", True))
    result["repeat_type"] = str(value.get("repeat_type", "daily")).strip() or "daily"
    try:
        result["weekly_day"] = int(value.get("weekly_day", 0))
    except (TypeError, ValueError):
        result["weekly_day"] = 0
    for key in ("opening_text", "question_text", "closing_text"):
        result[key] = str(value.get(key, DEFAULTS[key])).strip() or DEFAULTS[key]
    return result


def save(updates: dict, now: datetime | None = None) -> dict:
    legacy_fields = {"start_time", "end_time", "opening_text", "question_text", "closing_text"}
    if isinstance(updates, dict) and set(updates) == legacy_fields:
        updates = {**DEFAULTS, **updates}
    if not isinstance(updates, dict) or set(updates) != set(DEFAULTS):
        raise ValueError("expected opening_text, question_text and closing_text")
    values: dict = {}
    for key in ("start_time", "end_time", "schedule_date", "opening_text", "question_text", "closing_text"):
        value = updates[key]
        if not isinstance(value, str):
            raise ValueError(f"{key} must be text")
        value = value.strip()
        if key in {"start_time", "end_time"}:
            if value and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
                raise ValueError(f"{key} must use HH:MM")
            values[key] = value
            continue
        if key == "schedule_date":
            if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("schedule_date must use YYYY-MM-DD")
            values[key] = value
            continue
        if not value or len(value) > MAX_TEXT_LENGTH:
            raise ValueError(f"{key} length must be 1-{MAX_TEXT_LENGTH}")
        values[key] = value
    values["repeat_enabled"] = updates["repeat_enabled"]
    if not isinstance(values["repeat_enabled"], bool):
        raise ValueError("repeat_enabled must be boolean")
    values["repeat_type"] = str(updates["repeat_type"])
    if values["repeat_type"] not in {"daily", "weekly"}:
        raise ValueError("repeat_type must be daily or weekly")
    try:
        values["weekly_day"] = int(updates["weekly_day"])
    except (TypeError, ValueError):
        raise ValueError("weekly_day must be 0-6") from None
    if not 0 <= values["weekly_day"] <= 6:
        raise ValueError("weekly_day must be 0-6")
    if values["start_time"] and values["end_time"] and values["end_time"] <= values["start_time"]:
        raise ValueError("结束时间必须晚于开始时间")
    local_now = now
    if local_now is not None and local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    if local_now is not None and values["start_time"]:
        hour, minute = map(int, values["start_time"].split(":"))
        if not values["repeat_enabled"]:
            if not values["schedule_date"]:
                raise ValueError("不循环计划必须选择日期")
            try:
                day = datetime.strptime(values["schedule_date"], "%Y-%m-%d").date()
            except ValueError:
                raise ValueError("计划日期无效") from None
            candidate = datetime.combine(day, datetime.min.time(), tzinfo=local_now.tzinfo).replace(hour=hour, minute=minute)
        elif values["repeat_type"] == "weekly":
            days = (values["weekly_day"] - local_now.weekday()) % 7
            candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=days)
            if candidate <= local_now:
                candidate += timedelta(days=7)
        else:
            candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= local_now:
                candidate += timedelta(days=1)
        if candidate < local_now + timedelta(minutes=2):
            raise ValueError("计划开始时间必须至少晚于当前时间2分钟")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".net-checkin-", dir=STATE_PATH.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(values, stream, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, STATE_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return values
