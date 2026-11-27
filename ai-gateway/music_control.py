#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path


STATE_PATH = Path(os.getenv("FMO_MUSIC_STATE_PATH", "/var/lib/fmo-ai-gateway/music-state.json"))
ADMIN_CALLSIGN = os.getenv("FMO_VOICE_CONTROL_CALLSIGN", "").strip().upper()
PLAY_PHRASES = ("播放歌曲", "播放音乐", "播放一段音乐", "播放一首歌", "放一首歌", "放首歌", "来一首歌", "来首歌")
STOP_PHRASES = ("停止播放", "停止音乐", "停播音乐", "音乐停止", "别放了", "不要播放")
GENERIC_PHRASES = PLAY_PHRASES + ("听歌", "唱歌", "放歌", "来点音乐", "听音乐")


def _empty() -> dict:
    return {
        "status": "idle", "request_id": "", "requested_by": "",
        "requested_query": "",
        "requested_at": 0, "stop_requested": False, "last_result": "never",
        "last_result_at": 0,
    }


def load() -> dict:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        value = {}
    state = _empty()
    if isinstance(value, dict):
        state.update({key: value.get(key, default) for key, default in state.items()})
    if state["status"] not in {"idle", "requested", "preparing", "playing", "stop_requested"}:
        state["status"] = "idle"
    return state


def save(state: dict) -> dict:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".music-state-", dir=STATE_PATH.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, STATE_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return state


def _normalize(text: str) -> str:
    return re.sub(r"[\s，。！？、,.!?：:；;]+", "", str(text or "").strip())


def _requested_query(normalized: str) -> str:
    def clean(value: str) -> str:
        value = re.sub(r"[《》<>\"'“”‘’]", "", value).strip()
        value = re.sub(r"的", " ", value)
        compact = re.sub(r"^(?:歌|歌曲|音乐)", "", value).strip()
        compact = re.sub(r"\s*(?:歌曲|音乐|歌)$", "", compact).strip()
        compact = re.sub(r"(?:听听|一下|好吗|可以吗|吗|吧)$", "", compact).strip()
        if not compact or compact in {"听", "唱", "播放", "来点"}:
            return ""
        return re.sub(r"\s+", " ", compact).strip()

    explicit = re.search(r"歌手(.{1,40}?)歌名(.{1,60})$", normalized)
    if explicit:
        return clean(f"{explicit.group(1)} {explicit.group(2)}")
    patterns = (
        r"^(?:我想|我要|我想要|请|麻烦|给我|可以|能不能)?(?:听|播放|放|唱)(?:一下|一段|一首|首)?(?:歌曲|音乐|歌)?(.{1,80})$",
        r"^(?:来|放)(?:一下|一段|一首|首)?(.{1,80})$",
    )
    for pattern in patterns:
        match = re.match(pattern, normalized)
        if match:
            query = clean(match.group(1))
            if query:
                return query
    return ""


def parse_voice_command(callsign: str, transcript: str) -> dict | None:
    # Authentication and blacklist checks are performed by the MQTT gateway
    # before this parser is called. An empty source identity is never accepted.
    if not str(callsign or "").strip():
        return None
    normalized = _normalize(transcript)
    has_generic = any(phrase in normalized for phrase in GENERIC_PHRASES)
    query = "" if has_generic else _requested_query(normalized)
    has_play = bool(query) or has_generic
    has_stop = any(phrase in normalized for phrase in STOP_PHRASES)
    if has_play == has_stop:
        return None
    if has_stop:
        return {"action": "stop", "query": ""}
    return {"action": "play", "query": query, "mode": "search" if query else "random"}


def request_play(*, enabled: bool, requested_by: str = ADMIN_CALLSIGN,
                 query: str = "", blocked_by: list[str] | None = None,
                 now: float | None = None) -> dict:
    state = load()
    if not enabled:
        return {"action": "disabled", "reply": "NAS音乐播放功能尚未在后台开启", "state": state}
    blockers = list(blocked_by or [])
    if blockers:
        return {"action": "blocked", "reply": "当前有更高优先级业务，暂不播放音乐", "blockers": blockers, "state": state}
    if state["status"] in {"requested", "preparing", "playing"}:
        return {"action": "already_active", "reply": "音乐任务已经在处理中", "state": state}
    timestamp = int(now if now is not None else time.time())
    state.update({
        "status": "requested", "request_id": f"music-{timestamp}",
        "requested_by": str(requested_by or ADMIN_CALLSIGN).upper(),
        "requested_query": str(query or "").strip()[:120],
        "requested_at": timestamp, "stop_requested": False,
    })
    save(state)
    return {"action": "play_requested", "reply": "已提交一段NAS音乐播放请求", "state": state}


def request_stop(*, requested_by: str = ADMIN_CALLSIGN, now: float | None = None) -> dict:
    state = load()
    timestamp = int(now if now is not None else time.time())
    if state["status"] == "idle":
        return {"action": "already_stopped", "reply": "当前没有正在播放的音乐", "state": state}
    state.update({"status": "stop_requested", "stop_requested": True,
                  "requested_by": str(requested_by or ADMIN_CALLSIGN).upper(),
                  "last_result": "stop_requested", "last_result_at": timestamp})
    save(state)
    return {"action": "stop_requested", "reply": "已停止音乐并释放频道", "state": state}


def recover_after_restart(now: float | None = None) -> dict:
    """Clear work that cannot survive the in-memory playback process restarting."""
    state = load()
    if state["status"] in {"requested", "preparing", "playing", "stop_requested"}:
        return mark("idle", result="service_restarted", now=now)
    return state


def mark(status: str, *, result: str = "", now: float | None = None) -> dict:
    if status not in {"preparing", "playing", "idle"}:
        raise ValueError("invalid music status")
    state = load()
    timestamp = int(now if now is not None else time.time())
    state["status"] = status
    if status == "idle":
        state.update({
            "stop_requested": False,
            "last_result": str(result or "completed"),
            "last_result_at": timestamp,
        })
    return save(state)
