#!/usr/bin/env python3
from __future__ import annotations

import callsign_policy
import json
import os
import re
import tempfile
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import net_checkin_state


STATE_PATH = Path(os.getenv("FMO_NET_CHECKIN_RUNTIME_STATE_PATH", "/var/lib/fmo-ai-gateway/net-checkin-runtime.json"))
ADMIN_CALLSIGN = os.getenv("FMO_VOICE_CONTROL_CALLSIGN", "").strip().upper()
COMMAND_THRESHOLD = min(1.0, max(0.5, float(os.getenv("FMO_NET_CHECKIN_COMMAND_THRESHOLD", "0.60"))))
RESPONSE_TIMEOUT_SECONDS = max(3, min(60, int(os.getenv("FMO_NET_CHECKIN_RESPONSE_TIMEOUT_SECONDS", "8"))))
HARD_END_GRACE_SECONDS = max(30, min(600, int(os.getenv("FMO_NET_CHECKIN_HARD_END_GRACE_SECONDS", "120"))))
START_COMMAND = "我是机婶婶我宣布台网点名活动开始"
END_COMMAND = "我是机婶婶我宣布台网点名活动结束"

SPELLING_ALPHABET = {
    "A": "Alfa", "B": "Bravo", "C": "Charlie", "D": "Delta", "E": "Echo",
    "F": "Foxtrot", "G": "Golf", "H": "Hotel", "I": "India", "J": "Juliett",
    "K": "Kilo", "L": "Lima", "M": "Mike", "N": "November", "O": "Oscar",
    "P": "Papa", "Q": "Quebec", "R": "Romeo", "S": "Sierra", "T": "Tango",
    "U": "Uniform", "V": "Victor", "W": "Whiskey", "X": "X-ray", "Y": "Yankee",
    "Z": "Zulu", "0": "Zero", "1": "One", "2": "Two", "3": "Three", "4": "Four",
    "5": "Five", "6": "Six", "7": "Seven", "8": "Eight", "9": "Nine",
}


def _empty_state() -> dict:
    return {
        "status": "idle",
        "session_id": "",
        "remote_session_id": "",
        "current": None,
        "waiting": [],
        "completed": [],
        "skipped": [],
        "started_at": 0,
        "ended_at": 0,
        "closing_pending": False,
        "closing_started_at": 0,
        "last_schedule_slot": "",
    }


def _normalize_text(value: str) -> str:
    return re.sub(r"[\s，。！？、,.!?：:；;]+", "", str(value or "").strip())


def _normalize_callsign(value: str) -> str:
    callsign = re.sub(r"[^A-Z0-9/-]", "", str(value or "").upper())
    if not callsign or len(callsign) > 16:
        raise ValueError("invalid callsign")
    return callsign


def spoken_callsign(value: str) -> str:
    callsign = _normalize_callsign(value)
    return " ".join(SPELLING_ALPHABET.get(char, char) for char in callsign)


def load() -> dict:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        value = {}
    state = _empty_state()
    if isinstance(value, dict):
        state.update({key: value.get(key, default) for key, default in state.items()})
    if state["status"] not in {"idle", "active", "finished"}:
        state["status"] = "idle"
    # Compatibility field only. The product deliberately has no waiting queue.
    state["waiting"] = []
    if not isinstance(state["completed"], list):
        state["completed"] = []
    if not isinstance(state["skipped"], list):
        state["skipped"] = []
    if state["current"] is not None and not isinstance(state["current"], dict):
        state["current"] = None
    return state


def save(state: dict) -> dict:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".net-checkin-runtime-", dir=STATE_PATH.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, STATE_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return state


def parse_admin_command(callsign: str, transcript: str) -> str | None:
    if not callsign_policy.is_admin_callsign(callsign, ADMIN_CALLSIGN):
        return None
    normalized = _normalize_text(transcript)
    has_start = "开始" in normalized
    has_end = "结束" in normalized
    if has_start == has_end:
        return None
    target = START_COMMAND if has_start else END_COMMAND
    if SequenceMatcher(None, normalized, target).ratio() < COMMAND_THRESHOLD:
        return None
    return "start" if has_start else "end"


def start(now: float | None = None) -> dict:
    timestamp = int(now if now is not None else time.time())
    state = _empty_state()
    state.update({
        "status": "active",
        "session_id": f"net-checkin-{timestamp}",
        "started_at": timestamp,
    })
    save(state)
    return {"action": "started", "reply": net_checkin_state.load()["opening_text"], "state": state}


def end(now: float | None = None) -> dict:
    state = load()
    state["status"] = "finished"
    state["ended_at"] = int(now if now is not None else time.time())
    state["closing_pending"] = False
    state["closing_started_at"] = 0
    state["current"] = None
    state["waiting"] = []
    save(state)
    return {"action": "ended", "reply": net_checkin_state.load()["closing_text"], "state": state}


def mark_closing_pending(now: float | None = None) -> dict:
    state = load()
    if state["status"] == "active":
        state["closing_pending"] = True
        if not int(state.get("closing_started_at") or 0):
            state["closing_started_at"] = int(now if now is not None else time.time())
        save(state)
    return state


def force_end(now: float | None = None) -> dict:
    """End an expired session without speech/PTT so it cannot block normal ASR."""
    timestamp = int(now if now is not None else time.time())
    state = load()
    current = state.get("current")
    if current:
        current["phase"] = "hard_deadline"
        current["skipped_at"] = timestamp
        current["skip_reason"] = "hard_end_deadline"
        state["skipped"].append(current)
    state["status"] = "finished"
    state["ended_at"] = timestamp
    state["closing_pending"] = False
    state["closing_started_at"] = 0
    state["current"] = None
    state["waiting"] = []
    save(state)
    return {"action": "force_ended", "reply": "", "state": state}


def set_remote_session_id(session_id: str) -> dict:
    state = load()
    state["remote_session_id"] = str(session_id or "").strip()
    return save(state)


def set_remote_candidate_id(callsign: str, candidate_id: str) -> dict:
    state = load()
    callsign = _normalize_callsign(callsign)
    targets = [state["current"]] if state.get("current") else []
    for candidate in targets:
        if candidate.get("callsign") == callsign:
            candidate["remote_candidate_id"] = str(candidate_id or "").strip()
            return save(state)
    raise ValueError("candidate not found")


def handle_admin_command(callsign: str, transcript: str, enabled: bool) -> dict | None:
    command = parse_admin_command(callsign, transcript)
    if command is None:
        return None
    if not enabled:
        return {"action": "disabled", "reply": "自动台网点名功能尚未在后台开启", "state": load()}
    if command == "start":
        if load()["status"] == "active":
            return {"action": "already_active", "reply": "台网点名活动已经开始", "state": load()}
        return start()
    if load()["status"] != "active":
        return {"action": "not_active", "reply": "当前没有正在进行的台网点名活动", "state": load()}
    return end()


def _question(callsign: str) -> str:
    return net_checkin_state.load()["question_text"].replace("{callsign}", spoken_callsign(callsign))


def _merge_details(current: dict, details: dict | None) -> None:
    values = details if isinstance(details, dict) else {}
    for key in ("qth", "device", "power"):
        value = str(values.get(key, "")).strip()[:120]
        if value:
            current[key] = value


def _missing_details(current: dict) -> list[tuple[str, str]]:
    return [(key, label) for key, label in (("qth", "QTH"), ("device", "设备"), ("power", "发射功率")) if not current.get(key)]


def _details_summary(current: dict) -> str:
    power = current.get("power") or "未抄收"
    power = {"H": "高功率", "M": "中功率", "L": "低功率"}.get(str(power).upper(), power)
    return "，".join((
        f"QTH {current.get('qth') or '未抄收'}",
        f"设备 {current.get('device') or '未抄收'}",
        f"功率 {power}",
    ))


def register_mqtt_candidate(callsign: str, now: float | None = None) -> dict:
    """Reject contenders while one callsign is current; never create a queue."""
    state = load()
    callsign = _normalize_callsign(callsign)
    if state.get("status") != "active" or state.get("closing_pending") or not state.get("current"):
        return {"action": "not_queued", "state": state}
    known = {state["current"].get("callsign")}
    known.update(item.get("callsign") for item in state.get("completed", []))
    known.update(item.get("callsign") for item in state.get("skipped", []))
    if callsign in known:
        return {"action": "duplicate", "state": state}
    return {"action": "busy_current", "state": state}


def mark_prompt_sent(callsign: str, now: float | None = None) -> dict:
    state = load()
    current = state.get("current") or {}
    if (
        current.get("callsign") == _normalize_callsign(callsign)
        and current.get("phase") in {"awaiting_details", "awaiting_confirmation"}
    ):
        current["response_deadline"] = int(now if now is not None else time.time()) + RESPONSE_TIMEOUT_SECONDS
        save(state)
    return state


def mark_response_started(callsign: str) -> dict:
    """Pause no-response retries as soon as the current participant keys PTT."""
    state = load()
    current = state.get("current") or {}
    if current.get("callsign") == _normalize_callsign(callsign):
        current["response_deadline"] = 0
        save(state)
    return state


def resume_response_timeout(callsign: str, now: float | None = None) -> dict:
    """Re-arm retries only when a received turn produced no next prompt."""
    state = load()
    current = state.get("current") or {}
    if (
        current.get("callsign") == _normalize_callsign(callsign)
        and current.get("phase") in {"awaiting_details", "awaiting_confirmation"}
        and not int(current.get("response_deadline") or 0)
    ):
        current["response_deadline"] = int(now if now is not None else time.time()) + RESPONSE_TIMEOUT_SECONDS
        save(state)
    return state


def retry_plan(now: float | None = None) -> dict | None:
    timestamp = int(now if now is not None else time.time())
    state = load()
    current = state.get("current") or {}
    phase = current.get("phase")
    if state.get("status") != "active" or phase not in {"awaiting_details", "awaiting_confirmation"}:
        return None
    deadline = int(current.get("response_deadline") or 0)
    if not deadline or timestamp < deadline:
        return None
    callsign = current.get("callsign", "")
    attempts = int(current.get("call_attempts") or 1)
    missing = "、".join(label for _key, label in _missing_details(current)) or "确认信息"
    if attempts < 3:
        next_attempt = attempts + 1
        prefix = "再次呼叫" if next_attempt == 2 else "最后一次呼叫"
        request = (
            "请回答主控抄收是否正确；如有错误，请重新报告QTH、设备和功率。"
            if phase == "awaiting_confirmation"
            else f"请回应主控并报告{missing}。"
        )
        return {
            "action": "retry_call", "callsign": callsign, "expected_attempts": attempts,
            "next_attempts": next_attempt,
            "reply": f"{prefix} {spoken_callsign(callsign)}，{request}",
        }
    reply = f"{spoken_callsign(callsign)}，连续三次呼叫未收到回应，本轮暂缓登记。"
    reply += "有请下一位友台。"
    return {
        "action": "skip_no_response", "callsign": callsign, "expected_attempts": attempts,
        "next_callsign": "", "reply": reply,
    }


def commit_retry_plan(plan: dict, now: float | None = None) -> dict:
    timestamp = int(now if now is not None else time.time())
    state = load()
    current = state.get("current") or {}
    if current.get("callsign") != plan.get("callsign") or int(current.get("call_attempts") or 1) != int(plan.get("expected_attempts") or 0):
        return state
    if plan.get("action") == "retry_call":
        current["call_attempts"] = int(plan["next_attempts"])
        current["response_deadline"] = timestamp + RESPONSE_TIMEOUT_SECONDS
        current["last_called_at"] = timestamp
    elif plan.get("action") == "skip_no_response":
        current["phase"] = "no_response"
        current["skipped_at"] = timestamp
        state["skipped"].append(current)
        state["current"] = None
    return save(state)


def handle_participant(
    callsign: str, transcript: str, now: float | None = None, details: dict | None = None,
    mqtt_callsign: str = "",
) -> dict:
    state = load()
    if state["status"] != "active":
        return {"action": "ignored_inactive", "reply": "", "state": state}
    callsign = _normalize_callsign(callsign)
    timestamp = int(now if now is not None else time.time())
    current = state["current"]

    if current and current.get("callsign") == callsign:
        phase = current.get("phase", "awaiting_details")
        normalized = _normalize_text(transcript)
        if phase == "awaiting_details":
            current["details_text"] = str(transcript).strip()[:500]
            _merge_details(current, details)
            current["updated_at"] = timestamp
            current["response_deadline"] = 0
            missing = _missing_details(current)
            summary = _details_summary(current)
            if missing:
                current["phase"] = "awaiting_details"
                reply = f"收到，{spoken_callsign(callsign)}。已抄收：{summary}。请只补充{'、'.join(label for _key, label in missing)}。"
            else:
                current["phase"] = "awaiting_confirmation"
                current["call_attempts"] = 1
                reply = f"收到，{spoken_callsign(callsign)}。主控抄收为：{summary}。请问主控抄收是否正确？"
            save(state)
            return {"action": "missing_details" if missing else "confirm_details", "reply": reply, "state": state}
        if any(word in normalized for word in ("正确", "确认", "没问题", "对的")):
            missing = [label for _key, label in _missing_details(current)]
            if missing:
                current["phase"] = "awaiting_details"
                current["updated_at"] = timestamp
                save(state)
                return {
                    "action": "missing_details",
                    "reply": f"{spoken_callsign(callsign)}，还有{'、'.join(missing)}未抄收，请只重新报告缺少的项目。",
                    "state": state,
                }
            current["phase"] = "confirmed"
            current["confirmed_at"] = timestamp
            state["completed"].append(current)
            position = len(state["completed"])
            state["current"] = None
            save(state)
            confirmed_time = time.strftime("%H点%M分", time.localtime(timestamp))
            reply = f"{spoken_callsign(callsign)}，您是今天第{position}位友台，确认时间是{confirmed_time}，感谢您的参与，73。"
            if state.get("closing_pending"):
                reply += "本次点名已到结束时间，主控将在本条记录完成后结束活动。"
            else:
                reply += "有请下一位友台。"
            return {"action": "confirmed", "reply": reply, "state": state}
        if any(word in normalized for word in ("修改", "不对", "错误", "重报")):
            _merge_details(current, details)
            current["phase"] = "awaiting_details"
            current["updated_at"] = timestamp
            save(state)
            return {"action": "retry_details", "reply": f"好的，{spoken_callsign(callsign)}。已抄收项目继续保留，请只重新报告需要修改或缺少的项目。", "state": state}
        # A non-affirmative answer containing a new report is treated as a correction.
        if len(normalized) >= 4:
            current["details_text"] = str(transcript).strip()[:500]
            _merge_details(current, details)
            current["updated_at"] = timestamp
            current["phase"] = "awaiting_confirmation"
            current["call_attempts"] = 1
            summary = _details_summary(current)
            save(state)
            return {"action": "corrected_details", "reply": f"已按你的重报修正为：{summary}。请问主控抄收是否正确？", "state": state}
        return {"action": "awaiting_confirmation", "reply": "请回答正确；如有错误，请重新报告QTH、设备和功率。", "state": state}

    if state.get("closing_pending"):
        return {"action": "ignored_closing", "reply": "", "state": state}

    known = {item.get("callsign") for item in state["completed"]}
    if callsign in known:
        return {
            "action": "duplicate",
            "reply": (
                f"{spoken_callsign(callsign)}，您已经参与本次台网点名并记录，"
                "请您保持守听，有请下一位友台。"
            ),
            "state": state,
        }

    candidate = {
        "callsign": callsign,
        "mqtt_callsign": _normalize_callsign(mqtt_callsign) if mqtt_callsign else callsign,
        "phase": "awaiting_details",
        "heard_text": str(transcript).strip()[:500],
        "heard_at": timestamp,
        "call_attempts": 1,
        "response_deadline": 0,
    }
    if current is None:
        state["current"] = candidate
        save(state)
        return {"action": "ask_details", "reply": _question(callsign), "state": state}
    return {"action": "busy_current", "reply": "", "state": state}


def recover_after_restart(now: float | None = None) -> dict:
    """Re-arm an active participant after the voice monitor was restarted."""
    state = load()
    current = state.get("current") or {}
    if (
        state.get("status") == "active"
        and current.get("phase") in {"awaiting_details", "awaiting_confirmation"}
        and not int(current.get("response_deadline") or 0)
    ):
        current["response_deadline"] = int(now if now is not None else time.time()) + RESPONSE_TIMEOUT_SECONDS
        save(state)
    return state


def scheduled_action(now: datetime) -> str | None:
    settings = net_checkin_state.load()
    current = now.strftime("%H:%M")
    state = load()
    slot = now.strftime("%Y-%m-%dT%H:%M")
    if state.get("last_schedule_slot") == slot and not (
        state.get("closing_pending") and state["status"] == "active" and not state.get("current")
    ):
        return None
    start_time = settings.get("start_time", "")
    end_time = settings.get("end_time", "")
    if end_time and current >= end_time and state["status"] == "active":
        if state.get("current") and state.get("closing_pending"):
            closing_started_at = int(state.get("closing_started_at") or 0)
            if closing_started_at and int(now.timestamp()) >= closing_started_at + HARD_END_GRACE_SECONDS:
                return "force_end"
        return "mark_closing" if state.get("current") else "end"
    scheduled_today = True
    if not settings.get("repeat_enabled", True):
        scheduled_today = settings.get("schedule_date") == now.strftime("%Y-%m-%d")
    elif settings.get("repeat_type") == "weekly":
        scheduled_today = int(settings.get("weekly_day", 0)) == now.weekday()
    if scheduled_today and start_time and current >= start_time and (not end_time or current < end_time) and state["status"] != "active":
        scheduled_start = now.replace(
            hour=int(start_time[:2]), minute=int(start_time[3:]), second=0, microsecond=0,
        ).timestamp()
        # If the configured minute was busy, start at the first safe idle point.
        # Do not start the same schedule again after it has already run today.
        if int(state.get("started_at") or 0) < int(scheduled_start):
            return "start"
    return None


def mark_schedule_slot(now: datetime) -> dict:
    state = load()
    state["last_schedule_slot"] = now.strftime("%Y-%m-%dT%H:%M")
    return save(state)
