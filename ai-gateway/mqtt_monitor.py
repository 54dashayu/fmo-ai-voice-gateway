#!/usr/bin/env python3
"""FMO/RAW monitor with fail-closed publication controls."""

from __future__ import annotations

import json
import itertools
import os
import audioop
import wave
import struct
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import paho.mqtt.client as mqtt

import fmo_codec
import gateway
import speech
import callsign_policy
import control_state
import audio_cues
import persona_state
import net_checkin_runtime
import net_checkin_state
import checkin_client
import checkin_details
import music_control
import music_playback
import nas_gallery
import sstv_level
import audio_schedule
import audio_chime
import audio_library
import audio_channel_guard
import parrot_replay
from announcement_schedule import NIGHT_NOTICE_MINUTES, night_notice_slot


HEADER = fmo_codec.FMO_HEADER
import cluster_runtime
import gateway_config

_selected_mqtt = gateway_config.selected_mqtt_server()
HOST = str(_selected_mqtt["host"])
PORT = int(_selected_mqtt["port"])
TOPIC = str(_selected_mqtt["topic"])
CLIENT_ID = str(_selected_mqtt.get("client_id", "FMO-AI-MONITOR"))
MQTT_USERNAME = _selected_mqtt.get("username")
MQTT_PASSWORD = _selected_mqtt.get("password")
MQTT_TLS = _selected_mqtt.get("tls", {})
END_GAP_SECONDS = min(float(os.getenv("FMO_PTT_END_GAP_SECONDS", "0.9")), 0.999)
NET_CHECKIN_END_GAP_SECONDS = min(float(os.getenv("FMO_NET_CHECKIN_END_GAP_SECONDS", "0.2")), 0.999)
NET_CHECKIN_ASR_SETTLE_SECONDS = min(
    2.0, max(NET_CHECKIN_END_GAP_SECONDS, float(os.getenv("FMO_NET_CHECKIN_ASR_SETTLE_SECONDS", "0.9")))
)
STATE_PATH = Path(os.getenv("FMO_MQTT_STATE_PATH", "/run/fmo-ai-mqtt/status.json"))
AUDIO_PLAYBACK_ENABLED = os.getenv("FMO_AUDIO_PLAYBACK_ENABLED", "false").lower() == "true"
AUTO_ENABLED = os.getenv("FMO_VOICE_AUTO_ENABLED", "false").lower() == "true"
AUDIO_DIR = Path(os.getenv("FMO_AUDIO_DIR", "/var/lib/fmo-ai-gateway/audio"))
PTT_MARKER = Path(os.getenv("FMO_PTT_ACTIVE_PATH", "/run/fmo-ai-mqtt/ptt-active"))
MAX_INPUT_MS = min(int(os.getenv("FMO_VOICE_MAX_INPUT_MS", "60000")), 60000)
MAX_REPLY_MS = min(int(os.getenv("FMO_VOICE_MAX_REPLY_MS", "60000")), 60000)
LOW_INFORMATION_TRANSCRIPTS = frozenset({"嗯", "啊", "哦", "呃", "额", "唔", "哼"})
LEAD_IN_MS = min(int(os.getenv("FMO_PTT_LEAD_IN_MS", "480")), 900)
FADE_IN_MS = min(int(os.getenv("FMO_AUDIO_FADE_IN_MS", "80")), 200)
TAIL_MS = min(int(os.getenv("FMO_PTT_TAIL_MS", "120")), 500)
AI_CALLSIGN = os.getenv("FMO_AI_CALLSIGN", "AI-FMO").strip().upper()
HOURLY_ENABLED = os.getenv("FMO_HOURLY_ANNOUNCEMENT_ENABLED", "false").lower() == "true"
HOURLY_TIMEZONE_NAME = os.getenv("FMO_HOURLY_ANNOUNCEMENT_TIMEZONE", "Asia/Shanghai")
HOURLY_TIMEZONE = timezone(timedelta(hours=8), HOURLY_TIMEZONE_NAME)
HOURLY_FIRST_HOUR = max(0, min(23, int(os.getenv("FMO_HOURLY_ANNOUNCEMENT_FIRST_HOUR", "7"))))
HOURLY_LAST_HOUR = max(HOURLY_FIRST_HOUR, min(23, int(os.getenv("FMO_HOURLY_ANNOUNCEMENT_LAST_HOUR", "23"))))
HOURLY_IDLE_SECONDS = max(1, int(os.getenv("FMO_HOURLY_ANNOUNCEMENT_IDLE_SECONDS", "30")))
HOURLY_GRACE_SECONDS = max(5, min(180, int(os.getenv("FMO_HOURLY_ANNOUNCEMENT_GRACE_SECONDS", "90"))))
HOURLY_STATE_PATH = Path(os.getenv(
    "FMO_HOURLY_ANNOUNCEMENT_STATE_PATH", "/var/lib/fmo-ai-gateway/hourly-announcement.json"
))
NET_CHECKIN_RUNTIME_ENABLED = os.getenv("FMO_NET_CHECKIN_RUNTIME_ENABLED", "false").lower() == "true"
NET_CHECKIN_ALLOWED_CALLSIGNS = {
    item.strip().upper()
    for item in os.getenv("FMO_NET_CHECKIN_ALLOWED_CALLSIGNS", "").split(",")
    if item.strip()
}
NET_CHECKIN_MIN_FIRST_CALL_MS = max(1000, int(os.getenv("FMO_NET_CHECKIN_MIN_FIRST_CALL_MS", "3000")))
NET_CHECKIN_IDLE_REMINDER_SECONDS = max(
    60, int(os.getenv("FMO_NET_CHECKIN_IDLE_REMINDER_SECONDS", "60"))
)


def fade_in_pcm16(pcm: bytes, duration_ms: int) -> bytes:
    """Apply a short linear fade without changing the audio duration."""
    sample_count = min(len(pcm) // 2, round(fmo_codec.SAMPLE_RATE * duration_ms / 1000))
    if sample_count <= 1:
        return pcm
    output = bytearray(pcm)
    for index in range(sample_count):
        sample = struct.unpack_from("<h", pcm, index * 2)[0]
        struct.pack_into("<h", output, index * 2, round(sample * index / (sample_count - 1)))
    return bytes(output)


class Monitor:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.connected = False
        self.frames_total = 0
        self.bytes_total = 0
        self.bursts_total = 0
        self.last_frame_at: float | None = None
        self.active_frames = 0
        self.active_bytes = 0
        self.active_started_at: float | None = None
        self.active_protocol_base: int | None = None
        self.active_protocol_end: int | None = None
        self.active_uid: int | None = None
        self.active_callsign: str | None = None
        self.active_body_sizes: list[int] = []
        self.active_body_prefixes: list[str] = []
        self.last_burst: dict | None = None
        self.active_payloads: list[bytes] = []
        self.channel_last_activity = 0.0
        self.activity_generation = 0
        self.audio_exclusive = False
        self.voice_worker = None
        self.processing = False
        self.last_audio_poll = 0.0
        self.audio_runtime_ready = True
        self.audio_scheduler_error = ""
        self.client: mqtt.Client | None = None
        self.voice_total = 0
        self.voice_success = 0
        self.chat_total = 0
        self.knowledge_total = 0
        self.last_voice: dict | None = None
        self.callsign_stats = callsign_policy.load_stats()
        self.last_blocked_key: tuple[int, str, int] | None = None
        self.hourly_state = self._load_hourly_state()
        self.checkin_contenders: dict[tuple[int, str, int], dict] = {}
        self.checkin_fragment_key: tuple[int, str, int] | None = None
        self.checkin_fragment_payloads: list[bytes] = []
        self.last_checkin_idle_reminder_at = 0.0

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "connected": self.connected,
                "cluster_hook_version": 1,
                "mode": "automatic" if AUTO_ENABLED and control_state.enabled("auto_reply") else "control_only",
                "topic": TOPIC,
                "callsign_policy": "blacklist",
                "blacklist_count": len(callsign_policy.load_blacklist()),
                "callsign_stats": sorted(
                    ({"callsign": key, **value} for key, value in self.callsign_stats.items()),
                    key=lambda item: (item.get("last_at", 0), item.get("heard", 0)), reverse=True,
                )[:100],
                "end_gap_ms": round(END_GAP_SECONDS * 1000),
                "frames_total": self.frames_total,
                "bytes_total": self.bytes_total,
                "bursts_total": self.bursts_total,
                "receiving": self.active_frames > 0,
                "last_burst": self.last_burst,
                "ptt_enabled": AUTO_ENABLED,
                "processing": self.processing,
                "audio_playback_gate": AUDIO_PLAYBACK_ENABLED and self.audio_runtime_ready,
                "audio_scheduler_error": self.audio_scheduler_error,
                "audio_exclusive": self.audio_exclusive,
                "voice_total": self.voice_total,
                "voice_success": self.voice_success,
                "chat_total": self.chat_total,
                "knowledge_total": self.knowledge_total,
                "last_voice": self.last_voice,
                "net_checkin": {
                    **net_checkin_runtime.load(),
                    "runtime_enabled": NET_CHECKIN_RUNTIME_ENABLED,
                    "gate_enabled": control_state.enabled("net_checkin"),
                },
                "hourly_announcement": {
                    **self.hourly_state,
                    "enabled": HOURLY_ENABLED and control_state.enabled("hourly_announcement"),
                    "timezone": str(HOURLY_TIMEZONE),
                    "first_hour": HOURLY_FIRST_HOUR,
                    "last_hour": HOURLY_LAST_HOUR,
                    "idle_seconds": HOURLY_IDLE_SECONDS,
                    "night_notice_times": [f"23:{minute:02d}" for minute in NIGHT_NOTICE_MINUTES],
                },
            }

    @staticmethod
    def _load_hourly_state() -> dict:
        try:
            value = json.loads(HOURLY_STATE_PATH.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _save_hourly_state(self) -> None:
        HOURLY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".hourly-", dir=HOURLY_STATE_PATH.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self.hourly_state, stream, ensure_ascii=False, separators=(",", ":"))
            os.replace(temporary, HOURLY_STATE_PATH)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def write_state(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = json.dumps(self.snapshot(), ensure_ascii=False, separators=(",", ":"))
        fd, temporary = tempfile.mkstemp(prefix="status-", dir=STATE_PATH.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(data)
            os.replace(temporary, STATE_PATH)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def connected_changed(self, value: bool) -> None:
        with self.lock:
            self.connected = value
            if not value:
                # Cancel any pending/downlink work and discard the only
                # in-memory caller segment. Nothing is replayed after reconnect.
                self.activity_generation += 1
                self.active_frames = 0
                self.active_bytes = 0
                self.active_started_at = None
                self.active_protocol_base = None
                self.active_protocol_end = None
                self.active_uid = None
                self.active_callsign = None
                self.active_body_sizes = []
                self.active_body_prefixes = []
                self.active_payloads = []
                self.last_frame_at = None
                self.checkin_fragment_key = None
                self.checkin_fragment_payloads = []
        self.write_state()

    def receive(self, payload: bytes) -> None:
        try:
            identity, protocol_base, protocol_timestamp, _frames = fmo_codec.parse_packet(payload)
            _vendor, uid, callsign, _wire_call = identity
        except (ValueError, UnicodeError, struct.error):
            return
        with self.lock:
            if self.audio_exclusive:
                return  # No inbound ASR, AI reply or interruption during the activity.
        bridge_meta = cluster_runtime.incoming(payload)
        if bridge_meta and bridge_meta.get("origin") != "local":
            # Remote bot output must still occupy the channel, but never trigger AI.
            with self.lock:
                if self.audio_exclusive:
                    return
                self.channel_last_activity = time.time()
                self.activity_generation += 1
            if not bridge_meta.get("allow_ai", False):
                return
        # Ignore our own echo; retain channel-busy protection for other sources.
        if callsign == AI_CALLSIGN:
            return
        if uid != 65535:
            with self.lock:
                if self.audio_exclusive:
                    return
                self.channel_last_activity = time.time()
                self.activity_generation += 1
        if uid in {0, 65535}:
            return
        try:
            callsign = callsign_policy.normalize(callsign)
        except ValueError:
            return
        now = time.time()
        if callsign in callsign_policy.load_blacklist():
            key = (uid, callsign, protocol_base)
            with self.lock:
                if key != self.last_blocked_key:
                    self.last_blocked_key = key
                    item = self.callsign_stats.setdefault(callsign, {"heard": 0, "blocked": 0, "answered": 0})
                    item["heard"] = int(item.get("heard", 0)) + 1
                    item["blocked"] = int(item.get("blocked", 0)) + 1
                    item["last_at"] = round(now)
                    item["uid"] = uid
                    callsign_policy.save_stats(self.callsign_stats)
            self.write_state()
            return
        contender_qualified = False
        runtime_for_frame = net_checkin_runtime.load() if NET_CHECKIN_RUNTIME_ENABLED else {}
        current_for_frame = runtime_for_frame.get("current") or {}
        if (
            runtime_for_frame.get("status") == "active"
            and current_for_frame.get("mqtt_callsign") == callsign
            and self.active_frames == 0
        ):
            net_checkin_runtime.mark_response_started(callsign)
        if (
            NET_CHECKIN_RUNTIME_ENABLED
            and callsign != AI_CALLSIGN
            and net_checkin_runtime.load().get("status") == "active"
        ):
            key = (uid, callsign, protocol_base)
            with self.lock:
                # Keep this deliberately small and short-lived: it is only an
                # MQTT arrival-order arbiter, not another ASR or business queue.
                self.checkin_contenders = {
                    item_key: item for item_key, item in self.checkin_contenders.items()
                    if now - float(item.get("last_at", 0)) < 60
                }
                contender = self.checkin_contenders.setdefault(key, {
                    "uid": uid, "callsign": callsign, "protocol_base": protocol_base,
                    "first_at": now, "last_at": now, "duration_ms": 0, "qualified": False,
                })
                contender["last_at"] = now
                contender["duration_ms"] = (protocol_timestamp - protocol_base) & 0xFFFFFFFF
                if contender["duration_ms"] >= NET_CHECKIN_MIN_FIRST_CALL_MS and not contender["qualified"]:
                    contender["qualified"] = True
                    contender["qualified_at"] = now
                    contender_qualified = True
            if contender_qualified:
                queued = net_checkin_runtime.register_mqtt_candidate(callsign, now)
                if queued.get("action") == "queued":
                    self.write_state()
        with self.lock:
            if self.audio_exclusive:
                return
            if self.active_frames == 0:
                self.active_started_at = now
                self.active_protocol_base = protocol_base
                self.active_uid = uid
                self.active_callsign = callsign
                item = self.callsign_stats.setdefault(callsign, {"heard": 0, "blocked": 0, "answered": 0})
                item["heard"] = int(item.get("heard", 0)) + 1
                item["last_at"] = round(now)
                item["uid"] = uid
                callsign_policy.save_stats(self.callsign_stats)
            if self.active_protocol_base != protocol_base or self.active_uid != uid or self.active_callsign != callsign:
                return
            self.active_protocol_end = protocol_timestamp
            self.active_frames += 1
            self.active_bytes += len(payload)
            self.frames_total += 1
            self.bytes_total += len(payload)
            self.last_frame_at = now
            self.active_body_sizes.append(len(payload) - HEADER.size)
            self.active_payloads.append(payload)
            if len(self.active_body_prefixes) < 8:
                self.active_body_prefixes.append(payload[HEADER.size : HEADER.size + 32].hex())

    def finalize_if_idle(self) -> bool:
        now = time.time()
        with self.lock:
            if not self.active_frames or self.last_frame_at is None:
                return False
            active_checkin = NET_CHECKIN_RUNTIME_ENABLED and net_checkin_runtime.load().get("status") == "active"
            # FMO can pause for more than 200 ms between packets while PTT is
            # still held. Wait for a stable inbound gap before handing a
            # complete check-in turn to ASR; outbound tail timing stays separate.
            end_gap = NET_CHECKIN_ASR_SETTLE_SECONDS if active_checkin else END_GAP_SECONDS
            if now - self.last_frame_at < end_gap:
                return False
            protocol_duration = 0
            if self.active_protocol_base is not None and self.active_protocol_end is not None:
                protocol_duration = (self.active_protocol_end - self.active_protocol_base) & 0xFFFFFFFF
            continuation = bool(
                self.last_burst
                and self.last_burst.get("protocol_base") == self.active_protocol_base
                and now - float(self.last_burst.get("finalized_at", 0)) < 5
            )
            previous_frames = int(self.last_burst.get("frames", 0)) if continuation else 0
            previous_bytes = int(self.last_burst.get("bytes", 0)) if continuation else 0
            if not continuation:
                self.bursts_total += 1
            self.last_burst = {
                "ended_at": round(self.last_frame_at),
                "frames": previous_frames + self.active_frames,
                "bytes": previous_bytes + self.active_bytes,
                "duration_ms": protocol_duration,
                "protocol_base": self.active_protocol_base,
                "uid": self.active_uid,
                "callsign": self.active_callsign,
                "finalized_at": now,
                "body_sizes": self.active_body_sizes,
                "body_prefixes": self.active_body_prefixes,
            }
            source_protocol_base = self.active_protocol_base
            self.active_frames = 0
            self.active_bytes = 0
            self.active_started_at = None
            self.active_protocol_base = None
            self.active_protocol_end = None
            source_uid = self.active_uid
            source_callsign = self.active_callsign
            self.active_uid = None
            self.active_callsign = None
            self.active_body_sizes = []
            self.active_body_prefixes = []
            payloads = self.active_payloads
            generation = self.activity_generation
            self.active_payloads = []
            self.last_frame_at = None
            first_checkin_call = active_checkin and not (net_checkin_runtime.load().get("current") or {})
            checkin_ready = True
            if first_checkin_call and source_uid is not None and source_callsign and source_protocol_base is not None:
                fragment_key = (source_uid, source_callsign, source_protocol_base)
                if self.checkin_fragment_key != fragment_key:
                    self.checkin_fragment_key = fragment_key
                    self.checkin_fragment_payloads = []
                self.checkin_fragment_payloads.extend(payloads)
                if protocol_duration < NET_CHECKIN_MIN_FIRST_CALL_MS:
                    checkin_ready = False
                else:
                    payloads = self.checkin_fragment_payloads
                    self.checkin_fragment_key = None
                    self.checkin_fragment_payloads = []
            runtime_auto = control_state.enabled("auto_reply")
            control_candidate = True
            should_process = checkin_ready and AUTO_ENABLED and (runtime_auto or control_candidate) and not self.processing and not self.audio_exclusive
            if should_process:
                self.processing = True
        self.write_state()
        if should_process:
            self.start_voice_worker(self.process_voice, (payloads, generation, source_uid, source_callsign))
        return True

    def enqueue_qualified_contenders(self, current_callsign: str) -> None:
        now = time.time()
        with self.lock:
            contenders = sorted(
                (
                    dict(item) for item in self.checkin_contenders.values()
                    if item.get("qualified") and now - float(item.get("last_at", 0)) < 60
                ),
                key=lambda item: (float(item.get("first_at", 0)), float(item.get("qualified_at", 0))),
            )
        for contender in contenders:
            callsign = str(contender.get("callsign", ""))
            if callsign and callsign != current_callsign:
                net_checkin_runtime.register_mqtt_candidate(callsign, contender.get("first_at", now))

    def _record_voice(self, result: str, **details) -> None:
        with self.lock:
            self.voice_total += 1
            if result == "success":
                self.voice_success += 1
                mode = details.get("mode")
                if mode == "chat":
                    self.chat_total += 1
                elif mode == "knowledge":
                    self.knowledge_total += 1
                callsign = details.get("callsign")
                if callsign in self.callsign_stats:
                    self.callsign_stats[callsign]["answered"] = int(self.callsign_stats[callsign].get("answered", 0)) + 1
                    callsign_policy.save_stats(self.callsign_stats)
            self.last_voice = {"result": result, "at": round(time.time()), **details}
            self.processing = False
        self.write_state()

    def start_voice_worker(self, target, args):
        self.voice_worker = threading.Thread(target=target, args=args, daemon=True)
        self.voice_worker.start()

    def audio_checkin_blocked(self):
        if not NET_CHECKIN_RUNTIME_ENABLED:
            return False
        if net_checkin_runtime.load().get("status") == "active":
            return True
        return (control_state.enabled("net_checkin") and
                net_checkin_runtime.scheduled_action(datetime.now(HOURLY_TIMEZONE)) == "start")

    def check_audio_playback(self):
        # Service-owned scheduler, bounded polling; no separate cron/automation.
        if not self.audio_runtime_ready or time.monotonic() - self.last_audio_poll < 1:
            return False
        self.last_audio_poll = time.monotonic()
        with self.lock:
            blocker = ""
            busy = ""
            if not AUDIO_PLAYBACK_ENABLED or not AUTO_ENABLED:
                blocker = "发射安全门未启用"
            elif self.audio_checkin_blocked():
                blocker = "自动点名优先，跳过音频"
            elif not self.connected or self.client is None:
                blocker = "MQTT 未连接"
            elif self.audio_exclusive:
                blocker = "已有音频播放，跳过重叠计划"
            elif self.processing or self.active_frames or time.time() - self.channel_last_activity < HOURLY_IDLE_SECONDS:
                busy = "频道占用或未连续空闲 30 秒，跳过音频"
            try:
                job = audio_schedule.claim(blocker, busy=busy)
            except (OSError, ValueError, sqlite3.Error):
                self.audio_runtime_ready = False
                self.audio_scheduler_error = "音频存储异常，已关闭音频调度；修复后重启 AI 语音服务"
                return False
            if not job:
                return False
            if job.get('plan'):
                self.activity_generation += 1  # Cancel earlier ordinary voice/music/hourly output.
                job['_previous_worker'] = self.voice_worker
                self.active_frames = self.active_bytes = 0
                self.active_started_at = self.active_protocol_base = self.active_protocol_end = None
                self.active_uid = self.active_callsign = self.last_frame_at = None
                self.active_payloads = []; self.active_body_sizes = []; self.active_body_prefixes = []
            self.processing = True
            self.audio_exclusive = True
            generation = self.activity_generation
        try:
            threading.Thread(target=self.process_audio_playback, args=(job, generation), daemon=True).start()
        except Exception:
            with self.lock:
                self.processing = False
                self.audio_exclusive = False
            audio_schedule.finish(job['id'], 'failed', '播放线程启动失败')
            raise
        return True

    def prepare_audio_opening(self, text, check):
        # Keep provider I/O off the reserved playback worker so check-in/stop can
        # release that reservation without waiting for a network read timeout.
        previous = getattr(self, 'audio_intro_worker', None)
        if previous is not None and previous.is_alive():
            raise RuntimeError("前次开场请求正在取消，请稍后重试")
        done, cancelled = threading.Event(), threading.Event()
        result = {}
        def generate():
            stream = None
            try:
                pcm = bytearray()
                stream = speech.synthesize_pcm_stream(text)
                for chunk in stream:
                    if cancelled.is_set():
                        return
                    pcm.extend(chunk)
                    if len(pcm) > 45 * 32000:
                        result['error'] = "开场语音超过 45 秒，请缩短提示词"
                        return
                if not pcm or len(pcm) % 2:
                    result['error'] = "开场语音生成失败"
                else:
                    result['pcm'] = bytes(pcm)
            except Exception:
                result['error'] = "开场语音生成失败"
            finally:
                if hasattr(stream, 'close'):
                    try:
                        stream.close()
                    except Exception:
                        pass
                done.set()
        self.audio_intro_worker = threading.Thread(target=generate, daemon=True)
        self.audio_intro_worker.start()
        started = time.monotonic()
        try:
            while not done.wait(.1):
                check()
                if time.monotonic() - started > 60:
                    raise RuntimeError("开场语音生成超时")
            check()
            if result.get('error'):
                raise RuntimeError(result['error'])
            return result['pcm']
        finally:
            cancelled.set()

    def process_audio_playback(self, job, generation):
        marker_owned = False
        gallery_temp = None
        gallery_paths = {}
        lease = audio_channel_guard.Lease(CLIENT_ID)
        with self.lock:
            self.audio_exclusive = True
        try:
            def check():
                lease.refresh()
                if not AUDIO_PLAYBACK_ENABLED or not AUTO_ENABLED or not audio_schedule.is_live(job['id']):
                    raise RuntimeError("已停止或禁用音频播放")
                if self.audio_checkin_blocked():
                    raise RuntimeError("自动点名优先，音频已让位")
                with self.lock:
                    if (not self.connected or self.client is None or self.active_frames or
                            self.activity_generation != generation):
                        raise RuntimeError("频道活动或连接变化，音频已中止")

            check()
            previous = job.get('_previous_worker')
            if previous is not None:
                audio_schedule.progress(job['id'], '定时活动正在接管频道')
                deadline = time.monotonic() + 90
                while previous.is_alive():
                    check()
                    if time.monotonic() >= deadline:
                        raise RuntimeError('先前语音任务未及时退出，已停止接管')
                    time.sleep(.05)
                with self.lock:
                    self.processing = True
                check()
            sstv_gain, sstv_ids = audio_schedule.sstv_config()
            if job.get('gallery'):
                audio_schedule.progress(job['id'], '正在从 NAS 随机选图并转码')
                gallery_temp, gallery_paths, selected = nas_gallery.prepare(job['gallery'],check)
                job['assets'] = list(gallery_paths)
                sstv_ids.update(gallery_paths)
                with audio_schedule.database() as db:
                    row=db.execute('SELECT data FROM job_playlists WHERE job=?',(job['id'],)).fetchone()
                    data=json.loads(row[0]);data['gallery_selected']=selected
                    db.execute('UPDATE job_playlists SET data=? WHERE job=?',(json.dumps(data),job['id']))
                    audio_schedule.audit(db,'gallery_prepared',job['id']+': '+str(len(selected))+' 张 Robot36 · '+' / '.join(e['name'] for e in selected))
            def media_path(asset):
                return gallery_paths[asset] if asset in gallery_paths else audio_library.media_path(asset)
            assets = job.get('assets', [job['asset']])
            if any(a in sstv_ids for a in assets):
                with audio_schedule.database() as db:
                    row=db.execute('SELECT data FROM job_playlists WHERE job=?',(job['id'],)).fetchone()
                    data=json.loads(row[0]) if row else {};data['sstv_gain_db']=sstv_gain
                    db.execute('INSERT OR REPLACE INTO job_playlists VALUES(?,?)',(job['id'],json.dumps(data)))
                    audio_schedule.audit(db,'sstv_level',job['id']+f': SSTV 正文 {sstv_gain} dB')
            opening = job.get('opening_text', '')
            interval = job.get('interval_seconds', 0)
            first_gap = job.get('first_gap') is True
            # Prepare the opening through the existing Bailian-only TTS before any PTT.
            intro = b''
            if opening:
                audio_schedule.progress(job['id'], '正在准备 AI 开场提示词')
                intro = self.prepare_audio_opening(opening, check)

            transitions = []
            for index, text in enumerate(job.get('transition_texts', [])):
                audio_schedule.progress(job['id'], f'正在准备第 {index+2} 段提示语')
                transitions.append(self.prepare_audio_opening(text, check))
            closing = b''
            if job.get('closing_text'):
                audio_schedule.progress(job['id'], '正在准备 AI 结束话术')
                closing = self.prepare_audio_opening(job['closing_text'], check)

            cue = b''
            choice = job.get('chime_asset', 'off')
            if choice == 'default':
                cue = audio_chime.dingdong()
            elif choice != 'off':
                with wave.open(str(audio_library.media_path(choice)), 'rb') as wav:
                    if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000) or not 0 < wav.getnframes() <= 32000:
                        raise RuntimeError('缓存音频格式无效')
                    cue = wav.readframes(wav.getnframes())

            def wait_until(deadline):
                while True:
                    check()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:break
                    time.sleep(min(.1, remaining))

            def wait_silently(seconds):
                # No silent RF packets during gaps. Retain the worker reservation so
                # ordinary AI/music/hourly cannot insert a transmission into the program.
                steps = round(seconds * 10)
                for _ in range(steps):
                    check()
                    time.sleep(.1)
                check()

            def transmit(chunks):
                nonlocal marker_owned
                check()
                PTT_MARKER.write_text(str(int(time.time())), encoding="ascii")
                marker_owned = True
                PTT_MARKER.chmod(0o600)
                offset, state = 0, (0, 0)
                base = int(time.time() * 1000) & 0xFFFFFFFF
                media_deadline = time.monotonic()
                def padded():
                    yield bytes(round(LEAD_IN_MS / 80) * 2560)
                    yield from chunks
                    yield bytes(max(1, round(TAIL_MS / 80)) * 2560)
                for pcm in padded():
                    if not pcm:
                        continue
                    pcm += bytes((-len(pcm)) % 2560)
                    payloads, state = fmo_codec.build_radpcm_payloads(pcm, base=base, frame_offset=offset, state=state, frames_per_packet=1)
                    for payload in payloads:
                        check()
                        result = cluster_runtime.publish(self.client, TOPIC, payload, "local_only")
                        if result.rc != mqtt.MQTT_ERR_SUCCESS:
                            raise RuntimeError("MQTT 发送失败，音频已中止")
                        frames = int.from_bytes(payload[HEADER.size + 12:HEADER.size + 14], "little")
                        offset += frames * 2
                        # Absolute media clock: SQLite/control/encoding/publish overhead
                        # must be INCLUDED in each packet's duration, not added to it.
                        media_deadline += frames * 0.08
                        remaining = media_deadline - time.monotonic()
                        if remaining > 0:
                            time.sleep(remaining)
                PTT_MARKER.unlink(missing_ok=True)
                marker_owned = False
                check()

            padding = (round(LEAD_IN_MS / 80) + max(1, round(TAIL_MS / 80))) * .08
            def pcm_duration(pcm):return ((len(pcm) + 2559) // 2560) * .08 + padding
            if cue and pcm_duration(cue) > 3:
                raise RuntimeError('提示音含收发保护时间超过 3 秒，请缩短提示音')
            phases=[]
            def phase(kind,label,duration):phases.append({'kind':kind,'label':label,'duration':duration})
            if intro:
                phase('voice','正在播放 AI 开场提示词',pcm_duration(intro))
                if not first_gap:phase('gap','开场后等待 1 秒',1)
            for i, asset in enumerate(assets):
                if i or first_gap:
                    if i and i <= len(transitions):phase('voice',f'正在播放第 {i+1} 段提示语',pcm_duration(transitions[i-1]))
                    phase('gap',f'首段准备 {interval} 秒' if i == 0 else f'段间等待 {interval} 秒（下一段 {i+1}/{len(assets)}）',interval-3 if cue and interval>=3 else interval)
                    if cue and interval>=3:
                        phase('cue','正在播放提示音（距下一段 3 秒）',pcm_duration(cue))
                        phase('gap','提示音后等待下一段',max(0,3-pcm_duration(cue)))
                with wave.open(str(media_path(asset)), 'rb') as wav:
                    phase('sstv',f'正在播放第 {i+1}/{len(assets)} 段音频',((wav.getnframes()+1279)//1280)*.08+padding)
            if closing:
                phase('gap','结束话术前等待 1 秒',1)
                phase('voice','正在播放 AI 结束话术',pcm_duration(closing))
            audio_schedule.set_timeline(job['id'],phases)

            if intro:
                audio_schedule.progress(job['id'], '正在播放 AI 开场提示词')
                transmit(bytes(intro[i:i+7680]) for i in range(0, len(intro), 7680))
                if not first_gap:
                    audio_schedule.progress(job['id'],'开场后等待 1 秒')
                    wait_silently(1)  # Separate spoken opening from SSTV headers.
            for index, asset in enumerate(assets):
                check()
                if index or first_gap:
                    if index and index <= len(transitions):
                        audio_schedule.progress(job['id'], f'正在播放第 {index+1} 段提示语')
                        pcm = transitions[index-1]
                        transmit(pcm[i:i+7680] for i in range(0, len(pcm), 7680))
                    audio_schedule.progress(job['id'], f'首段准备 {interval} 秒' if index == 0 else f'段间等待 {interval} 秒（下一段 {index+1}/{len(assets)}）')
                    if cue and interval >= 3:
                        gap_end = time.monotonic() + interval
                        wait_until(gap_end - 3)
                        audio_schedule.progress(job['id'], '正在播放提示音（距下一段 3 秒）')
                        transmit(cue[i:i+7680] for i in range(0, len(cue), 7680))
                        audio_schedule.progress(job['id'],'提示音后等待下一段')
                        wait_until(gap_end)
                    else:
                        wait_until(time.monotonic() + interval) if first_gap else wait_silently(interval)
                audio_schedule.progress(job['id'], f'正在播放第 {index+1}/{len(assets)} 段音频' + (f' · SSTV {sstv_gain} dB' if asset in sstv_ids else ''))
                with wave.open(str(media_path(asset)), 'rb') as wav:
                    if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
                        raise RuntimeError("缓存音频格式无效")
                    if not 0 < wav.getnframes() <= audio_library.MAX_SECONDS * 16000:
                        raise RuntimeError("缓存音频时长无效")
                    def chunks():
                        while chunk := wav.readframes(3840):
                            yield sstv_level.scale(chunk,sstv_gain) if asset in sstv_ids else chunk
                    transmit(chunks())
            if closing:
                audio_schedule.progress(job['id'],'结束话术前等待 1 秒')
                wait_silently(1)
                audio_schedule.progress(job['id'], '正在播放 AI 结束话术')
                transmit(closing[i:i+7680] for i in range(0, len(closing), 7680))
            check()
            audio_schedule.finish(job['id'], 'completed', '音频分包发送完成（设备接收未验证）')
        except Exception as exc:
            # Only our fixed RuntimeError messages may be persisted; other exceptions can contain paths/secrets.
            safe_errors = {"先前语音任务未及时退出，已停止接管","提示音含收发保护时间超过 3 秒，请缩短提示音", "已停止或禁用音频播放", "自动点名优先，音频已让位", "频道活动或连接变化，音频已中止",
                           "缓存音频格式无效", "缓存音频时长无效", "MQTT 发送失败，音频已中止",
                           "开场语音超过 45 秒，请缩短提示词", "开场语音生成失败",
                           "前次开场请求正在取消，请稍后重试", "开场语音生成超时", "活动独占保护不可用，已停止播放"}
            message = str(exc) if str(exc) in safe_errors or isinstance(exc,nas_gallery.GalleryError) else "播放失败，请检查受控缓存、磁盘和服务状态"
            audio_schedule.finish(job['id'], 'failed', message)
        finally:
            if gallery_temp is not None:gallery_temp.cleanup()
            lease.close()
            if marker_owned:
                try:
                    PTT_MARKER.unlink(missing_ok=True)
                except OSError:
                    self.audio_runtime_ready = False
                    self.audio_scheduler_error = "PTT 状态文件清理失败，已关闭音频调度"
            with self.lock:
                self.processing = False
                self.audio_exclusive = False
            self.write_state()

    def check_hourly_announcement(self, now: datetime | None = None) -> bool:
        if not HOURLY_ENABLED or not control_state.enabled("hourly_announcement"):
            return False
        local_now = now.astimezone(HOURLY_TIMEZONE) if now else datetime.now(HOURLY_TIMEZONE)
        seconds_after_hour = local_now.minute * 60 + local_now.second
        if not HOURLY_FIRST_HOUR <= local_now.hour <= HOURLY_LAST_HOUR or seconds_after_hour >= HOURLY_GRACE_SECONDS:
            return False
        slot = local_now.strftime("%Y-%m-%dT%H:00%z")
        if NET_CHECKIN_RUNTIME_ENABLED and net_checkin_runtime.load().get("status") == "active":
            if self.hourly_state.get("last_slot") != slot:
                self.hourly_state = {
                    **self.hourly_state, "last_slot": slot, "last_attempt_at": round(time.time()),
                    "last_result": "skipped_net_checkin", "last_broadcast_hour": local_now.hour,
                }
                self._save_hourly_state()
                self.write_state()
            return False
        with self.lock:
            if self.hourly_state.get("last_slot") == slot:
                return False
            self.hourly_state = {**self.hourly_state, "last_slot": slot, "last_attempt_at": round(time.time())}
            channel_idle = time.time() - self.channel_last_activity >= HOURLY_IDLE_SECONDS
            safe = self.connected and not self.processing and not self.audio_exclusive and not self.active_frames and channel_idle and self.client is not None
            if safe:
                self.processing = True
                generation = self.activity_generation
            else:
                self.hourly_state.update({"last_result": "skipped_busy", "last_broadcast_hour": local_now.hour})
        self._save_hourly_state()
        self.write_state()
        if not safe:
            return False
        text = f"这里是 B H 一 J S S 机婶婶测试台 AI，现在是{local_now.hour}点整"
        self.start_voice_worker(self.process_hourly_announcement, (text, generation, slot, local_now.hour))
        return True

    def check_night_notice(self, now: datetime | None = None) -> bool:
        if not HOURLY_ENABLED or not control_state.enabled("hourly_announcement"):
            return False
        local_now = now.astimezone(HOURLY_TIMEZONE) if now else datetime.now(HOURLY_TIMEZONE)
        slot = night_notice_slot(local_now)
        if not slot:
            return False
        if NET_CHECKIN_RUNTIME_ENABLED and net_checkin_runtime.load().get("status") == "active":
            if self.hourly_state.get("night_last_slot") != slot:
                self.hourly_state = {
                    **self.hourly_state, "night_last_slot": slot, "last_attempt_at": round(time.time()),
                    "last_kind": "night_notice", "last_result": "skipped_net_checkin",
                }
                self._save_hourly_state()
                self.write_state()
            return False
        with self.lock:
            if self.hourly_state.get("night_last_slot") == slot:
                return False
            channel_idle = time.time() - self.channel_last_activity >= HOURLY_IDLE_SECONDS
            safe = self.connected and not self.processing and not self.audio_exclusive and not self.active_frames and channel_idle and self.client is not None
            if not safe:
                return False
            self.hourly_state = {
                **self.hourly_state,
                "night_last_slot": slot,
                "last_attempt_at": round(time.time()),
                "last_kind": "night_notice",
            }
            self.processing = True
            generation = self.activity_generation
        self._save_hourly_state()
        self.write_state()
        text = "这里是 B H 一 J S S 机婶婶测试台 AI，温馨预告：我将在零点下班休息啦，明天早上七点以后再聊。各位晚安，七十三"
        self.start_voice_worker(self.process_hourly_announcement, (text, generation, slot, local_now.hour))
        return True

    def check_net_checkin_schedule(self, now: datetime | None = None) -> bool:
        """Start or end net check-in only from the configured Beijing times."""
        if not NET_CHECKIN_RUNTIME_ENABLED or not control_state.enabled("net_checkin"):
            return False
        local_now = now.astimezone(HOURLY_TIMEZONE) if now else datetime.now(HOURLY_TIMEZONE)
        action = net_checkin_runtime.scheduled_action(local_now)
        if action is None:
            return False
        if action == "force_end":
            result = net_checkin_runtime.force_end(now=local_now.timestamp())
            remote_id = result["state"].get("remote_session_id")
            if remote_id and checkin_client.configured():
                try:
                    checkin_client.save_session(remote_id)
                except Exception:
                    pass
            net_checkin_runtime.mark_schedule_slot(local_now)
            self.write_state()
            return True
        if action == "mark_closing":
            net_checkin_runtime.mark_closing_pending(now=local_now.timestamp())
            net_checkin_runtime.mark_schedule_slot(local_now)
            self.write_state()
            return True
        if action == "start":
            # A scheduled net check-in outranks any ordinary AI reply, hourly
            # announcement, or music already being prepared/played. Advancing
            # the generation makes those workers stop at their next safe frame.
            with self.lock:
                if self.processing:
                    self.activity_generation += 1
            if music_control.load().get("status") in {"requested", "preparing", "playing"}:
                music_control.request_stop(requested_by="NET_CHECKIN")
        with self.lock:
            # active_frames protects a human transmission in progress and
            # processing protects a burst already handed to ASR/reply logic.
            # Do not require an additional quiet-gap here: frequent short FMO
            # activity can keep resetting it and permanently miss a schedule.
            safe = self.connected and not self.processing and not self.audio_exclusive and not self.active_frames and self.client is not None
            if not safe:
                return False
            self.processing = True
            generation = self.activity_generation
        self.start_voice_worker(self.process_net_checkin_schedule, (action, generation))
        return True

    def process_net_checkin_schedule(self, action: str, generation: int) -> None:
        audio_path = AUDIO_DIR / "net-checkin-schedule.wav"
        phase = "state"
        sync_error = ""
        try:
            if action == "start":
                # Do not mark the runtime active until the opening announcement
                # has actually been transmitted. Otherwise a TTS/PTT/API failure
                # leaves a ghost session that blocks ordinary ASR indefinitely.
                text = net_checkin_state.load()["opening_text"]
                result = {"action": "starting", "reply": text, "state": net_checkin_runtime.load()}
            else:
                # End is transactional: keep the runtime active/pending until
                # the closing announcement has actually been transmitted.
                current_state = net_checkin_runtime.load()
                remote_id = current_state.get("remote_session_id")
                text = net_checkin_state.load()["closing_text"]
                result = {"action": "ended", "reply": text, "state": current_state}
            text = result["reply"]
            phase = "tts_synthesize"
            speech.synthesize(text, audio_path)
            phase = "tts_decode"
            pcm = fmo_codec.wav_to_pcm16(audio_path)
            frame_bytes = fmo_codec.FRAME_SAMPLES * 2
            lead_frames = max(1, round(LEAD_IN_MS / 40))
            tail_frames = max(1, round(TAIL_MS / 40))
            pcm = b"\0" * (lead_frames * frame_bytes) + fade_in_pcm16(pcm, FADE_IN_MS) + b"\0" * (tail_frames * frame_bytes)
            # TTS can take several seconds. Traffic received while it is being
            # generated invalidates the original generation, but should not
            # make the scheduled activity fail. Wait for the current PTT to
            # finish, then adopt the latest generation at that safe boundary.
            phase = "channel_wait"
            wait_deadline = time.time() + 30
            while True:
                with self.lock:
                    channel_idle = not self.active_frames
                    if channel_idle:
                        generation = self.activity_generation
                        break
                if time.time() >= wait_deadline:
                    raise RuntimeError("channel remained busy before net check-in announcement")
                time.sleep(0.1)
            if self.client is None:
                raise RuntimeError("MQTT client unavailable before net check-in announcement")
            phase = "ptt_encode"
            phase = "ptt_build"
            payloads = fmo_codec.build_downlink_payloads(pcm, callsign=AI_CALLSIGN)
            phase = "ptt_publish"
            PTT_MARKER.write_text(str(int(time.time())), encoding="ascii")
            PTT_MARKER.chmod(0o600)
            for item in payloads:
                if action == "start" and not control_state.enabled("net_checkin"):
                    raise RuntimeError("net check-in was disabled during opening announcement")
                publish = cluster_runtime.publish(self.client, TOPIC, item, "net_checkin")
                if publish.rc != mqtt.MQTT_ERR_SUCCESS:
                    raise RuntimeError("MQTT net check-in announcement publish failed")
                frames = int.from_bytes(item[HEADER.size + 12 : HEADER.size + 14], "little")
                time.sleep(frames * 0.08)
            if action == "start":
                if not control_state.enabled("net_checkin"):
                    raise RuntimeError("net check-in was disabled before activation")
                result = net_checkin_runtime.start()
                if checkin_client.configured():
                    try:
                        settings = net_checkin_state.load()
                        remote = checkin_client.create_session(
                            planned_start_time=settings.get("start_time", ""),
                            planned_end_time=settings.get("end_time", ""),
                        )
                        result["state"] = net_checkin_runtime.set_remote_session_id(remote["id"])
                    except Exception as exc:
                        # On-air check-in remains active; sync failure is visible
                        # diagnostically but must not create another ghost state.
                        sync_error = type(exc).__name__
            else:
                result = net_checkin_runtime.end()
                if remote_id and checkin_client.configured():
                    checkin_client.save_session(remote_id)
            with self.lock:
                self.processing = False
                self.last_voice = {
                    "result": "success", "at": round(time.time()), "mode": "net_checkin_schedule",
                    "action": action, "phase": "complete", "sync_error": sync_error,
                }
            net_checkin_runtime.mark_schedule_slot(datetime.now(HOURLY_TIMEZONE))
            self.write_state()
        except Exception as exc:
            error_detail = str(exc)
            safe_error_prefixes = (
                "speech upstream HTTP error:", "speech upstream connection failed:",
                "TTS audio download failed:", "TTS returned invalid WAV audio",
                "TTS returned an untrusted audio URL", "Model Studio returned an unexpected TTS response",
                "unsupported TTS provider:", "invalid TTS text length",
                "DASHSCOPE_API_KEY is not set", "cannot derive Model Studio native endpoint",
                "channel remained busy before net check-in announcement",
                "MQTT client unavailable before net check-in announcement",
                "channel became busy during net check-in announcement",
                "net check-in was disabled during opening announcement",
                "net check-in was disabled before activation",
                "MQTT net check-in announcement publish failed",
            )
            with self.lock:
                self.processing = False
                self.last_voice = {
                    "result": "failed", "at": round(time.time()), "mode": "net_checkin_schedule",
                    "action": action, "phase": phase, "error": type(exc).__name__,
                    "error_detail": error_detail[:180] if error_detail.startswith(safe_error_prefixes) else "runtime_error",
                }
            self.write_state()
        finally:
            PTT_MARKER.unlink(missing_ok=True)
            audio_path.unlink(missing_ok=True)

    def check_net_checkin_retry(self) -> bool:
        if not NET_CHECKIN_RUNTIME_ENABLED or not control_state.enabled("net_checkin"):
            return False
        plan = net_checkin_runtime.retry_plan()
        if plan is None:
            return False
        with self.lock:
            safe = self.connected and not self.processing and not self.audio_exclusive and not self.active_frames and self.client is not None
            if not safe:
                return False
            self.processing = True
            generation = self.activity_generation
        self.start_voice_worker(self.process_net_checkin_retry, (plan, generation))
        return True

    def check_net_checkin_idle_reminder(self) -> bool:
        runtime = net_checkin_runtime.load() if NET_CHECKIN_RUNTIME_ENABLED else {}
        if (
            runtime.get("status") != "active"
            or runtime.get("current")
            or runtime.get("closing_pending")
        ):
            return False
        now = time.time()
        last_activity = max(
            float(runtime.get("started_at") or 0),
            float(self.channel_last_activity or 0),
            float(self.last_checkin_idle_reminder_at or 0),
        )
        if now - last_activity < NET_CHECKIN_IDLE_REMINDER_SECONDS:
            return False
        with self.lock:
            safe = self.connected and not self.processing and not self.audio_exclusive and not self.active_frames and self.client is not None
            if not safe:
                return False
            self.processing = True
            generation = self.activity_generation
            self.last_checkin_idle_reminder_at = now
        self.start_voice_worker(self.process_net_checkin_idle_reminder, (generation,))
        return True

    def process_net_checkin_idle_reminder(self, generation: int) -> None:
        text = "台网点名活动持续进行中，请在频率上的友台积极参与。"
        audio_path = AUDIO_DIR / "net-checkin-idle-reminder.wav"
        phase = "tts"
        try:
            speech.synthesize(text, audio_path)
            pcm = fmo_codec.wav_to_pcm16(audio_path)
            frame_bytes = fmo_codec.FRAME_SAMPLES * 2
            lead_frames = max(1, round(LEAD_IN_MS / 40))
            tail_frames = max(1, round(TAIL_MS / 40))
            pcm = b"\0" * (lead_frames * frame_bytes) + fade_in_pcm16(pcm, FADE_IN_MS) + b"\0" * (tail_frames * frame_bytes)
            with self.lock:
                safe = self.activity_generation == generation and not self.active_frames
            runtime = net_checkin_runtime.load()
            if (
                not safe
                or self.client is None
                or runtime.get("status") != "active"
                or runtime.get("current")
                or runtime.get("closing_pending")
            ):
                raise RuntimeError("channel became busy before net check-in idle reminder")
            phase = "ptt"
            payloads = fmo_codec.build_downlink_payloads(pcm, callsign=AI_CALLSIGN)
            PTT_MARKER.write_text(str(int(time.time())), encoding="ascii")
            PTT_MARKER.chmod(0o600)
            for item in payloads:
                if net_checkin_runtime.load().get("status") != "active":
                    raise RuntimeError("net check-in ended during idle reminder")
                result = cluster_runtime.publish(self.client, TOPIC, item, "net_checkin")
                if result.rc != mqtt.MQTT_ERR_SUCCESS:
                    raise RuntimeError("MQTT net check-in idle reminder publish failed")
                frames = int.from_bytes(item[HEADER.size + 12 : HEADER.size + 14], "little")
                time.sleep(frames * 0.08)
            with self.lock:
                self.processing = False
                self.last_voice = {
                    "result": "success", "at": round(time.time()),
                    "mode": "net_checkin_idle_reminder", "phase": "complete",
                }
            self.write_state()
        except Exception as exc:
            with self.lock:
                self.processing = False
                self.last_voice = {
                    "result": "failed", "at": round(time.time()),
                    "mode": "net_checkin_idle_reminder", "phase": phase,
                    "error": type(exc).__name__,
                }
            self.write_state()
        finally:
            PTT_MARKER.unlink(missing_ok=True)
            audio_path.unlink(missing_ok=True)

    def process_net_checkin_retry(self, plan: dict, generation: int) -> None:
        audio_path = AUDIO_DIR / "net-checkin-retry.wav"
        phase = "tts"
        try:
            speech.synthesize(str(plan["reply"]), audio_path)
            pcm = fmo_codec.wav_to_pcm16(audio_path)
            frame_bytes = fmo_codec.FRAME_SAMPLES * 2
            lead_frames = max(1, round(LEAD_IN_MS / 40))
            tail_frames = max(1, round(TAIL_MS / 40))
            pcm = b"\0" * (lead_frames * frame_bytes) + fade_in_pcm16(pcm, FADE_IN_MS) + b"\0" * (tail_frames * frame_bytes)
            with self.lock:
                safe = self.activity_generation == generation and not self.active_frames
            if not safe or self.client is None:
                raise RuntimeError("channel became busy before check-in retry")
            phase = "ptt"
            payloads = fmo_codec.build_downlink_payloads(pcm, callsign=AI_CALLSIGN)
            PTT_MARKER.write_text(str(int(time.time())), encoding="ascii")
            PTT_MARKER.chmod(0o600)
            for item in payloads:
                with self.lock:
                    if self.activity_generation != generation:
                        raise RuntimeError("channel became busy during check-in retry")
                publish = cluster_runtime.publish(self.client, TOPIC, item, "net_checkin")
                if publish.rc != mqtt.MQTT_ERR_SUCCESS:
                    raise RuntimeError("MQTT check-in retry publish failed")
                frames = int.from_bytes(item[HEADER.size + 12 : HEADER.size + 14], "little")
                time.sleep(frames * 0.08)
            net_checkin_runtime.commit_retry_plan(plan)
            with self.lock:
                self.processing = False
                self.last_voice = {
                    "result": "success", "at": round(time.time()), "mode": "net_checkin_retry",
                    "action": plan.get("action"), "callsign": plan.get("callsign"),
                }
            self.write_state()
        except Exception as exc:
            with self.lock:
                self.processing = False
                self.last_voice = {
                    "result": "failed", "at": round(time.time()), "mode": "net_checkin_retry",
                    "action": plan.get("action"), "callsign": plan.get("callsign"),
                    "phase": phase, "error": type(exc).__name__,
                }
            self.write_state()
        finally:
            PTT_MARKER.unlink(missing_ok=True)
            audio_path.unlink(missing_ok=True)

    def process_hourly_announcement(self, text: str, generation: int, slot: str, hour: int) -> None:
        started = time.monotonic()
        audio_path = AUDIO_DIR / "hourly-announcement.wav"
        phase = "tts"
        try:
            speech.synthesize(text, audio_path)
            pcm = fmo_codec.wav_to_pcm16(audio_path)
            if len(pcm) / 2 / fmo_codec.SAMPLE_RATE * 1000 > 20000:
                raise RuntimeError("hourly announcement exceeds 20 second limit")
            frame_bytes = fmo_codec.FRAME_SAMPLES * 2
            lead_frames = max(1, round(LEAD_IN_MS / 40))
            tail_frames = max(1, round(TAIL_MS / 40))
            pcm = b"\0" * (lead_frames * frame_bytes) + fade_in_pcm16(pcm, FADE_IN_MS) + b"\0" * (tail_frames * frame_bytes)
            with self.lock:
                safe = (
                    self.activity_generation == generation
                    and not self.active_frames
                    and time.time() - self.channel_last_activity >= HOURLY_IDLE_SECONDS
                )
            if not safe or self.client is None:
                raise RuntimeError("channel became busy before hourly announcement")
            phase = "ptt"
            PTT_MARKER.write_text(str(int(time.time())), encoding="ascii")
            PTT_MARKER.chmod(0o600)
            payloads = fmo_codec.build_downlink_payloads(pcm, callsign=AI_CALLSIGN)
            sent_frames = 0
            for item in payloads:
                with self.lock:
                    if self.activity_generation != generation:
                        raise RuntimeError("channel became busy during hourly announcement")
                result = cluster_runtime.publish(self.client, TOPIC, item, "hourly_announcement")
                if result.rc != mqtt.MQTT_ERR_SUCCESS:
                    raise RuntimeError("MQTT hourly announcement publish failed")
                frames = int.from_bytes(item[HEADER.size + 12 : HEADER.size + 14], "little")
                sent_frames += frames
                time.sleep(frames * 0.08)
            with self.lock:
                self.hourly_state.update({
                    "last_slot": slot,
                    "last_result": "success",
                    "last_broadcast_hour": hour,
                    "last_text": text,
                    "last_duration_ms": sent_frames * 40,
                    "last_completed_at": round(time.time()),
                })
                self.processing = False
            self._save_hourly_state()
            self.write_state()
        except Exception as exc:
            with self.lock:
                self.hourly_state.update({
                    "last_slot": slot,
                    "last_result": "failed",
                    "last_broadcast_hour": hour,
                    "last_error": type(exc).__name__,
                    "last_phase": phase,
                    "last_completed_at": round(time.time()),
                })
                self.processing = False
            self._save_hourly_state()
            self.write_state()
        finally:
            PTT_MARKER.unlink(missing_ok=True)
            audio_path.unlink(missing_ok=True)

    def process_voice(
        self, payloads: list[bytes], generation: int, source_uid: int | None, source_callsign: str | None,
    ) -> None:
        started = time.monotonic()
        input_wav = AUDIO_DIR / "voice-input.wav"
        reply_wav = AUDIO_DIR / "voice-reply.wav"
        stage = {}
        phase = "decode"
        transcript = ""
        reply = ""
        prepared_music = None
        participant_callsign = source_callsign
        try:
            if source_uid is None or source_callsign is None:
                raise RuntimeError("voice source is unavailable")
            frames, protocol_ms = fmo_codec.voice_frames(payloads, source_uid, source_callsign)
            media_ms = fmo_codec.media_duration_ms(frames)
            stage["codecs"] = sorted({"OPUS" if mode == 1 else "RADPCM" for mode, _ in frames})
            # FMO/RAW is shared; never choose broadcast codec by the sender.
            stage["reply_codec"] = "RADPCM"
            stage["protocol_span_ms"] = protocol_ms
            stage["media_duration_ms"] = media_ms
            # The FMO protocol timestamp can span a long-lived client/PTT
            # session with sparse packets. Bound actual decoded media duration,
            # not that wall-clock span, or a short utterance may be rejected.
            if media_ms > MAX_INPUT_MS:
                raise RuntimeError("voice input exceeds 60 second limit")
            runtime_before_asr = net_checkin_runtime.load() if NET_CHECKIN_RUNTIME_ENABLED else {}
            if runtime_before_asr.get("status") == "active":
                current_before_asr = runtime_before_asr.get("current") or {}
                completed_callsigns = {
                    str(item.get("callsign") or "").upper()
                    for item in runtime_before_asr.get("completed", [])
                }
                if (
                    current_before_asr
                    and current_before_asr.get("mqtt_callsign") not in (None, "", source_callsign.upper())
                    and source_callsign.upper() not in completed_callsigns
                ):
                    self._record_voice(
                        "ignored_busy_checkin", mode="net_checkin", callsign=source_callsign, uid=source_uid,
                        input_ms=media_ms, total_ms=round((time.monotonic() - started) * 1000), stages=stage,
                    )
                    return
                if not current_before_asr and media_ms < NET_CHECKIN_MIN_FIRST_CALL_MS:
                    self._record_voice(
                        "ignored_short_checkin", mode="net_checkin", callsign=source_callsign, uid=source_uid,
                        input_ms=media_ms, minimum_ms=NET_CHECKIN_MIN_FIRST_CALL_MS,
                        total_ms=round((time.monotonic() - started) * 1000), stages=stage,
                    )
                    return
            decoded = fmo_codec.decode_voice_to_wav(frames, input_wav)
            if all(mode == 1 for mode, _ in frames):
                stage["opus_diagnostic"] = fmo_codec.diagnose_opus_offsets(
                    payloads, source_uid, source_callsign,
                )
            if decoded["duration_ms"] < 800:
                self._record_voice(
                    "ignored_too_short", mode="voice", callsign=source_callsign, uid=source_uid,
                    input_ms=decoded["duration_ms"], minimum_ms=800,
                    total_ms=round((time.monotonic() - started) * 1000), stages=stage,
                )
                return
            metric_pcm = fmo_codec.wav_to_pcm16(input_wav)
            # Preserve the decoded, un-normalized caller audio in memory for
            # one-shot parrot replay. ASR normalization must never change it.
            raw_replay_pcm = bytes(metric_pcm)
            stage["input_rms"] = audioop.rms(metric_pcm, 2) if metric_pcm else 0
            stage["input_peak"] = audioop.max(metric_pcm, 2) if metric_pcm else 0
            stage["decoded_duration_ms"] = decoded["duration_ms"]
            normalized_pcm, input_gain = fmo_codec.normalize_quiet_pcm16(metric_pcm)
            stage["input_gain"] = round(input_gain, 2)
            if input_gain > 1:
                with wave.open(str(input_wav), "wb") as normalized_wav:
                    normalized_wav.setnchannels(1)
                    normalized_wav.setsampwidth(2)
                    normalized_wav.setframerate(fmo_codec.SAMPLE_RATE)
                    normalized_wav.writeframes(normalized_pcm)
                input_wav.chmod(0o600)
            point = time.monotonic()
            phase = "asr"
            try:
                asr, asr_retried = speech.transcribe_with_retry(input_wav)
            except RuntimeError as exc:
                detail = str(exc)
                allowed = (
                    "speech upstream HTTP error:", "speech upstream connection failed:",
                    "ASR returned empty text", "Model Studio returned an unexpected ASR response",
                    "unsupported ASR provider:",
                )
                stage["asr_retried"] = True
                stage["asr_error_detail"] = detail[:160] if detail.startswith(allowed) else "runtime_error"
                runtime = net_checkin_runtime.load() if NET_CHECKIN_RUNTIME_ENABLED else {}
                if control_state.enabled("parrot_replay") and runtime.get("status") != "active":
                    # Raw replay is useful even when ASR is temporarily empty or
                    # unavailable. Failed recognition never changes a switch.
                    asr = {"text": ""}
                    asr_retried = True
                    stage["asr_fallback_to_raw_replay"] = True
                else:
                    raise
            stage["asr_retried"] = asr_retried
            stage["asr_ms"] = round((time.monotonic() - point) * 1000)
            if self.activity_generation != generation:
                raise RuntimeError('voice task preempted before command handling')
            transcript = asr["text"].strip()
            compact_transcript = transcript.strip().strip("。！？!?，,、~～ ")
            if stage["input_rms"] < 150 and compact_transcript in LOW_INFORMATION_TRANSCRIPTS:
                self._record_voice(
                    "ignored_unintelligible", mode="voice", callsign=source_callsign, uid=source_uid,
                    transcript=transcript, input_ms=decoded["duration_ms"],
                    total_ms=round((time.monotonic() - started) * 1000), stages=stage,
                )
                return
            point = time.monotonic()
            phase = "model"
            net_result = None
            if NET_CHECKIN_RUNTIME_ENABLED:
                participant_allowed = (
                    not NET_CHECKIN_ALLOWED_CALLSIGNS
                    or source_callsign.upper() in NET_CHECKIN_ALLOWED_CALLSIGNS
                )
                if (
                    net_result is None
                    and participant_allowed
                    and net_checkin_runtime.load()["status"] == "active"
                ):
                    runtime_before = net_checkin_runtime.load()
                    current_before = runtime_before.get("current") or {}
                    # The authenticated FMO/MQTT packet header is authoritative for
                    # participant identity. ASR is intentionally limited to the
                    # spoken QTH, device and power fields; it must never replace a
                    # callsign obtained from MQTT, even when speech contains another
                    # syntactically valid callsign.
                    participant_callsign = checkin_details.callsign_from_mqtt(source_callsign)
                    if not participant_callsign:
                        raise RuntimeError("invalid MQTT callsign")
                    callsign_source = "mqtt"
                    stage["callsign_source"] = callsign_source
                    detail_values = None
                    normalized_reply = transcript.replace(" ", "")
                    is_affirmative = any(word in normalized_reply for word in ("正确", "确认", "没问题", "对的"))
                    if current_before.get("callsign") == participant_callsign.upper() and not is_affirmative:
                        profile = {}
                        if checkin_client.configured():
                            try:
                                profile = checkin_client.lookup_profile(participant_callsign)
                            except Exception as exc:
                                # Historical profile enrichment is optional.
                                # A remote console outage must not discard valid
                                # live ASR details or restart the retry counter.
                                stage["checkin_profile_error"] = type(exc).__name__
                        detail_values = checkin_details.extract(transcript, profile)
                    net_result = net_checkin_runtime.handle_participant(
                        participant_callsign, transcript, details=detail_values, mqtt_callsign=source_callsign
                    )
                    if net_result.get("action") == "ask_details":
                        self.enqueue_qualified_contenders(participant_callsign)
                        net_result["state"] = net_checkin_runtime.load()
                if net_result is not None and checkin_client.configured():
                    try:
                        action = net_result["action"]
                        state = net_result["state"]
                        if action == "started":
                            remote = checkin_client.create_session()
                            state = net_checkin_runtime.set_remote_session_id(remote["id"])
                        elif action in {"ask_details", "queued"} and state.get("remote_session_id"):
                            remote = checkin_client.add_candidate(state["remote_session_id"], participant_callsign)
                            state = net_checkin_runtime.set_remote_candidate_id(participant_callsign, remote["id"])
                        elif action in {"confirm_details", "corrected_details", "missing_details", "retry_details"} and state.get("remote_session_id"):
                            current = state.get("current") or {}
                            if current and not current.get("remote_candidate_id"):
                                remote = checkin_client.add_candidate(state["remote_session_id"], current.get("callsign", participant_callsign))
                                state = net_checkin_runtime.set_remote_candidate_id(current.get("callsign", participant_callsign), remote["id"])
                                current = state.get("current") or {}
                            if current.get("remote_candidate_id"):
                                checkin_client.update_candidate(
                                    state["remote_session_id"], current["remote_candidate_id"],
                                    {
                                        "qth": current.get("qth", ""),
                                        "device": current.get("device", ""),
                                        "power": current.get("power", ""),
                                        "transcript": transcript,
                                        "asrConfidence": asr.get("confidence"),
                                    },
                                )
                        elif action == "confirmed" and state.get("remote_session_id"):
                            completed = state.get("completed") or []
                            candidate = completed[-1] if completed else {}
                            if candidate.get("remote_candidate_id"):
                                checkin_client.confirm_candidate(
                                    state["remote_session_id"], candidate["remote_candidate_id"]
                                )
                        elif action == "ended" and state.get("remote_session_id"):
                            checkin_client.save_session(state["remote_session_id"])
                    except Exception as exc:
                        stage["checkin_sync_error"] = type(exc).__name__
                if net_result is not None and net_result.get("action") in {"busy_current", "queued", "queue_full", "ignored_closing"}:
                    self._record_voice(
                        net_result["action"], mode="net_checkin", callsign=source_callsign, uid=source_uid,
                        transcript=transcript[:120], input_ms=media_ms or decoded["duration_ms"],
                        total_ms=round((time.monotonic() - started) * 1000), stages=stage,
                    )
                    return
            music_intent = music_control.parse_voice_command(source_callsign, transcript) if net_result is None and transcript else None
            command = control_state.parse_voice_command(source_callsign, transcript) if net_result is None and music_intent is None and transcript else None
            if net_result is not None:
                answer = {
                    "mode": "control" if net_result["action"] in {
                        "started", "ended", "disabled", "already_active", "not_active"
                    } else "net_checkin",
                    "reply": net_result["reply"],
                }
            elif music_intent:
                if music_intent["action"] == "stop":
                    music_result = music_control.request_stop(requested_by=source_callsign)
                    answer = {"mode": "control", "reply": music_result["reply"]}
                else:
                    runtime = net_checkin_runtime.load() if NET_CHECKIN_RUNTIME_ENABLED else {}
                    blockers = ["net_checkin_active"] if runtime.get("status") == "active" else []
                    music_result = music_control.request_play(
                        enabled=control_state.enabled("music_playback"),
                        requested_by=source_callsign,
                        query=music_intent.get("query", ""),
                        blocked_by=blockers,
                    )
                    if music_result["action"] == "play_requested":
                        phase = "music_prepare"
                        music_control.mark("preparing")
                        prepared_music = music_playback.prepare(
                            music_intent.get("query", ""),
                            cancelled=lambda: self.activity_generation != generation or music_control.load()["stop_requested"],
                        )
                        music_control.mark("playing")
                        answer = {"mode": "music", "reply": "NAS音乐片段"}
                    else:
                        answer = {"mode": "control", "reply": music_result["reply"]}
            elif command:
                if not command.get("noop"):
                    if command["name"] in {"auto_reply", "parrot_replay"}:
                        control_state.set_reply_mode(command.get("mode", "off"))
                    else:
                        control_state.save({command["name"]: command["value"]})
                answer = {"mode": "control", "reply": command["reply"]}
            else:
                persona_command = persona_state.parse_voice_command(source_callsign, transcript) if transcript else None
                if persona_command and control_state.enabled("persona_voice_commands"):
                    persona_state.save(persona_command["updates"])
                    answer = {"mode": "control", "reply": persona_command["reply"]}
                elif persona_command:
                    answer = {"mode": "control", "reply": "身份语音设置已禁止"}
                elif parrot_replay.should_replace_ai(
                    enabled=control_state.enabled("parrot_replay"),
                    handled_command=False,
                    priority_mode=False,
                ):
                    answer = {"mode": "parrot", "reply": ""}
                elif not control_state.enabled("auto_reply"):
                    diagnostic = {}
                    if callsign_policy.is_admin_callsign(source_callsign, persona_state.ADMIN_CALLSIGN):
                        diagnostic = {"transcript": transcript[:120], "command_match": "none"}
                    self._record_voice(
                        "ignored_disabled",
                        mode="control",
                        callsign=source_callsign,
                        uid=source_uid,
                        total_ms=round((time.monotonic() - started) * 1000),
                        stages=stage,
                        **diagnostic,
                    )
                    return
                else:
                    answer = gateway.answer(source_callsign, transcript)
            stage["model_ms"] = round((time.monotonic() - point) * 1000)
            reply = str(answer.get("reply", "")).strip()
            point = time.monotonic()
            phase = "raw_replay" if answer["mode"] == "parrot" else "tts_stream"
            if prepared_music:
                announcement = speech.synthesize_pcm_stream(f"现在播放，{prepared_music.title}")
                announcement_gap = b"\0" * (round(fmo_codec.SAMPLE_RATE * 0.2) * 2)
                stream = itertools.chain(
                    announcement,
                    (announcement_gap,),
                    music_playback.pcm_chunks(prepared_music),
                )
            elif answer["mode"] == "parrot":
                raw_replay_pcm = parrot_replay.clip_pcm(
                    raw_replay_pcm, fmo_codec.SAMPLE_RATE, min(MAX_INPUT_MS, MAX_REPLY_MS),
                )
                if not raw_replay_pcm:
                    raise RuntimeError("decoded caller audio is empty")
                stream = iter((raw_replay_pcm,))
                stage["raw_replay_bytes"] = len(raw_replay_pcm)
                stage["raw_replay_unscaled"] = True
            else:
                stream = speech.synthesize_pcm_stream(reply)
            first_chunk = next(stream)
            stage["raw_replay_ready_ms" if answer["mode"] == "parrot" else "tts_first_chunk_ms"] = round((time.monotonic() - point) * 1000)
            lead_frames = max(1, round(LEAD_IN_MS / 40))
            tail_frames = max(1, round(TAIL_MS / 40))
            with self.lock:
                safe_to_send = (
                    self.activity_generation == generation
                    and not self.active_frames
                    and time.time() - self.channel_last_activity >= END_GAP_SECONDS
                )
                if answer["mode"] == "parrot":
                    safe_to_send = safe_to_send and parrot_replay.allowed(
                        enabled=control_state.enabled("parrot_replay"),
                        generation=generation,
                        current_generation=self.activity_generation,
                        active_frames=self.active_frames,
                        connected=self.connected,
                        audio_exclusive=self.audio_exclusive,
                        net_checkin_active=(
                            NET_CHECKIN_RUNTIME_ENABLED
                            and net_checkin_runtime.load().get("status") == "active"
                        ),
                    )
            if net_result is None and NET_CHECKIN_RUNTIME_ENABLED and net_checkin_runtime.load().get("status") == "active":
                safe_to_send = False
            if net_result is not None and net_checkin_runtime.load().get("status") != "active":
                safe_to_send = False
            if not safe_to_send or self.client is None:
                raise RuntimeError("channel became busy or net check-in took priority before reply")
            phase = "ptt"
            PTT_MARKER.write_text(str(int(time.time())), encoding="ascii")
            PTT_MARKER.chmod(0o600)

            frame_bytes = fmo_codec.FRAME_SAMPLES * 2
            group_bytes = frame_bytes * 6
            base = int(time.time() * 1000) & 0xFFFFFFFF
            frame_offset = 0
            radpcm_state = (0, 0)
            pending = bytearray(b"\0" * (lead_frames * frame_bytes))
            audio_bytes = 0
            end_cue = (
                audio_cues.command_dingdong(fmo_codec.SAMPLE_RATE)
                if answer["mode"] in {"control", "music"} else b""
            )
            max_audio_bytes = max(
                0,
                (MAX_REPLY_MS - lead_frames * 40 - tail_frames * 40) * 32 - len(end_cue),
            )

            def publish_pcm(data: bytes) -> None:
                nonlocal frame_offset, radpcm_state
                if net_result is None and NET_CHECKIN_RUNTIME_ENABLED and net_checkin_runtime.load().get("status") == "active":
                    raise RuntimeError("net check-in took priority during reply")
                if net_result is not None and net_checkin_runtime.load().get("status") != "active":
                    raise RuntimeError("net check-in ended during reply")
                if prepared_music and music_control.load().get("stop_requested"):
                    raise RuntimeError("music playback was preempted")
                if answer["mode"] in {"chat", "knowledge"} and not control_state.enabled("auto_reply"):
                    raise RuntimeError("automatic reply mode was switched off")
                if answer["mode"] == "parrot":
                    with self.lock:
                        raw_allowed = parrot_replay.allowed(
                            enabled=control_state.enabled("parrot_replay"),
                            generation=generation,
                            current_generation=self.activity_generation,
                            active_frames=self.active_frames,
                            connected=self.connected,
                            audio_exclusive=self.audio_exclusive,
                            net_checkin_active=(
                                NET_CHECKIN_RUNTIME_ENABLED
                                and net_checkin_runtime.load().get("status") == "active"
                            ),
                        )
                    if not raw_allowed:
                        raise RuntimeError("raw replay was cancelled by safety gate")
                # Complete the final 80 ms frame; retain IMA recovery state.
                alignment = frame_bytes * 2
                data += bytes((-len(data)) % alignment)
                outgoing, radpcm_state = fmo_codec.build_radpcm_payloads(
                    data, base=base, frame_offset=frame_offset, state=radpcm_state,
                )
                for item in outgoing:
                    with self.lock:
                        interrupted = self.activity_generation != generation
                    if interrupted:
                        raise RuntimeError("channel became busy during reply")
                    result = cluster_runtime.publish(self.client, TOPIC, item, ("net_checkin" if net_result is not None else "local_only" if prepared_music or answer.get("mode") in {"control", "parrot"} else "ai_reply"))
                    if result.rc != mqtt.MQTT_ERR_SUCCESS:
                        raise RuntimeError("MQTT reply publish failed")
                    frames = int.from_bytes(item[HEADER.size + 12 : HEADER.size + 14], "little")
                    ticks = frames * 2
                    frame_offset += ticks
                    time.sleep(ticks * 0.04)

            first_audio = True
            for chunk in itertools.chain((first_chunk,), stream):
                if len(chunk) % 2:
                    raise RuntimeError("streaming TTS returned unaligned PCM")
                remaining = max_audio_bytes - audio_bytes
                if remaining <= 0:
                    break
                chunk = chunk[:remaining]
                if first_audio:
                    if answer["mode"] != "parrot":
                        chunk = fade_in_pcm16(chunk, FADE_IN_MS)
                    first_audio = False
                audio_bytes += len(chunk)
                pending.extend(chunk)
                while len(pending) >= group_bytes:
                    publish_pcm(bytes(pending[:group_bytes]))
                    del pending[:group_bytes]
            stage["raw_replay_stream_ms" if answer["mode"] == "parrot" else "tts_complete_ms"] = round((time.monotonic() - point) * 1000)
            pending.extend(end_cue)
            pending.extend(b"\0" * (tail_frames * frame_bytes))
            if len(pending) % frame_bytes:
                pending.extend(b"\0" * (frame_bytes - len(pending) % frame_bytes))
            while pending:
                size = min(len(pending), group_bytes)
                publish_pcm(bytes(pending[:size]))
                del pending[:size]
            duration_ms = frame_offset * 40
            stage["tts_encode_ms"] = round((time.monotonic() - point) * 1000)
            if net_result is not None:
                current_after = net_checkin_runtime.load().get("current") or {}
                if current_after.get("callsign"):
                    net_checkin_runtime.mark_prompt_sent(current_after["callsign"])
            self._record_voice(
                "success",
                mode=answer["mode"],
                callsign=source_callsign,
                uid=source_uid,
                transcript=transcript[:120],
                reply=reply[:120],
                input_ms=media_ms or decoded["duration_ms"],
                reply_ms=round(duration_ms),
                lead_in_ms=lead_frames * 40,
                fade_in_ms=FADE_IN_MS,
                tail_ms=tail_frames * 40,
                total_ms=round((time.monotonic() - started) * 1000),
                stages=stage,
            )
            if prepared_music:
                music_control.mark("idle", result="completed")
        except Exception as exc:
            self._record_voice(
                "failed",
                error=type(exc).__name__,
                phase=phase,
                callsign=source_callsign,
                uid=source_uid,
                transcript=transcript[:120],
                reply=reply[:120],
                total_ms=round((time.monotonic() - started) * 1000),
                stages=stage,
            )
            if prepared_music or music_control.load()["status"] in {"requested", "preparing", "playing", "stop_requested"}:
                music_control.mark("idle", result=type(exc).__name__)
        finally:
            if source_callsign and NET_CHECKIN_RUNTIME_ENABLED:
                net_checkin_runtime.resume_response_timeout(source_callsign)
            PTT_MARKER.unlink(missing_ok=True)
            input_wav.unlink(missing_ok=True)
            reply_wav.unlink(missing_ok=True)
            if prepared_music:
                work = prepared_music.path.parent
                prepared_music.cleanup()
                try:
                    work.rmdir()
                except OSError:
                    pass


def main() -> None:
    music_control.recover_after_restart()
    audio_ready = True
    try:
        audio_schedule.recover()
    except (OSError, ValueError, sqlite3.Error):
        audio_ready = False
    if NET_CHECKIN_RUNTIME_ENABLED:
        net_checkin_runtime.recover_after_restart()
    monitor = Monitor()
    monitor.audio_runtime_ready = audio_ready
    if not audio_ready:
        monitor.audio_scheduler_error = "音频重启恢复失败，已关闭音频调度；其他语音功能继续运行"
    client = mqtt.Client(client_id=CLIENT_ID, clean_session=True)
    if isinstance(MQTT_USERNAME, str):
        client.username_pw_set(MQTT_USERNAME, password=str(MQTT_PASSWORD) if isinstance(MQTT_PASSWORD, str) else None)
    if isinstance(MQTT_TLS, dict) and MQTT_TLS.get("enabled"):
        ca_file = MQTT_TLS.get("ca_file")
        cert_file = MQTT_TLS.get("cert_file")
        key_file = MQTT_TLS.get("key_file")
        client.tls_set(
            ca_certs=str(ca_file) if isinstance(ca_file, str) else None,
            certfile=str(cert_file) if isinstance(cert_file, str) else None,
            keyfile=str(key_file) if isinstance(key_file, str) else None,
            tls_version=None,
        )
    monitor.client = client

    def on_connect(client, _userdata, _flags, rc):
        if rc == 0:
            client.subscribe(TOPIC, qos=0)
            monitor.connected_changed(True)

    def on_disconnect(_client, _userdata, _rc):
        monitor.connected_changed(False)

    def on_message(_client, _userdata, message):
        monitor.receive(message.payload)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect_async(HOST, PORT, keepalive=30)
    client.loop_start()
    monitor.write_state()
    last_status_heartbeat = time.monotonic()
    try:
        while True:
            monitor.finalize_if_idle()
            monitor.check_net_checkin_schedule()
            monitor.check_net_checkin_retry()
            monitor.check_net_checkin_idle_reminder()
            monitor.check_audio_playback()
            monitor.check_hourly_announcement()
            monitor.check_night_notice()
            if time.monotonic() - last_status_heartbeat >= 2:
                monitor.write_state()
                last_status_heartbeat = time.monotonic()
            time.sleep(0.05)
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
