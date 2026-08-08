#!/usr/bin/env python3
"""Guarded FMO/RAW monitor and AI voice responder."""

from __future__ import annotations

import json
import itertools
import os
import secrets
import struct
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
from announcement_schedule import NIGHT_NOTICE_MINUTES, night_notice_slot


HEADER = struct.Struct("<IHHH12s")
import gateway_config

_selected_mqtt = gateway_config.selected_mqtt_server()
HOST = str(_selected_mqtt["host"])
PORT = int(_selected_mqtt["port"])
TOPIC = str(_selected_mqtt["topic"])
CLIENT_ID = str(_selected_mqtt.get("client_id", "FMO-AI-MONITOR-CHANGE-ME"))
MQTT_USERNAME = _selected_mqtt.get("username")
MQTT_PASSWORD = _selected_mqtt.get("password")
MQTT_TLS = _selected_mqtt.get("tls", {})
END_GAP_SECONDS = min(float(os.getenv("FMO_PTT_END_GAP_SECONDS", "0.9")), 0.999)
STATE_PATH = Path(os.getenv("FMO_MQTT_STATE_PATH", "/run/fmo-ai-mqtt/status.json"))
AUTO_ENABLED = os.getenv("FMO_VOICE_AUTO_ENABLED", "false").lower() == "true"
AUDIO_DIR = Path(os.getenv("FMO_AUDIO_DIR", "/var/lib/fmo-ai-gateway/audio"))
PTT_MARKER = Path(os.getenv("FMO_PTT_ACTIVE_PATH", "/run/fmo-ai-mqtt/ptt-active"))
MAX_INPUT_MS = min(int(os.getenv("FMO_VOICE_MAX_INPUT_MS", "60000")), 60000)
MAX_REPLY_MS = min(int(os.getenv("FMO_VOICE_MAX_REPLY_MS", "60000")), 60000)
LEAD_IN_MS = min(int(os.getenv("FMO_PTT_LEAD_IN_MS", "480")), 900)
FADE_IN_MS = min(int(os.getenv("FMO_AUDIO_FADE_IN_MS", "80")), 200)
TAIL_MS = min(int(os.getenv("FMO_PTT_TAIL_MS", "120")), 500)
AI_CALLSIGN = os.getenv("FMO_AI_CALLSIGN", "AI-FMO").strip().upper()
ANNOUNCEMENT_NAME = os.getenv("FMO_ANNOUNCEMENT_NAME", "FMO AI测试台").strip()
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
        self.processing = False
        self.client: mqtt.Client | None = None
        self.voice_total = 0
        self.voice_success = 0
        self.chat_total = 0
        self.knowledge_total = 0
        self.last_voice: dict | None = None
        self.callsign_stats = callsign_policy.load_stats()
        self.last_blocked_key: tuple[int, str, int] | None = None
        self.hourly_state = self._load_hourly_state()

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "connected": self.connected,
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
                "voice_total": self.voice_total,
                "voice_success": self.voice_success,
                "chat_total": self.chat_total,
                "knowledge_total": self.knowledge_total,
                "last_voice": self.last_voice,
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
        self.write_state()

    def receive(self, payload: bytes) -> None:
        if len(payload) < HEADER.size or len(payload) > 2048:
            return
        version, _padding1, uid, _padding2, raw_callsign = HEADER.unpack_from(payload)
        callsign = raw_callsign.rstrip(b"\0").decode("ascii", "ignore").upper()
        if version == 1 and uid != 65535:
            with self.lock:
                self.channel_last_activity = time.time()
                self.activity_generation += 1
        if version != 1 or uid in {0, 65535}:
            return
        try:
            callsign = callsign_policy.normalize(callsign)
        except ValueError:
            return
        body = payload[HEADER.size :]
        if len(body) < 42:
            return
        protocol_base, protocol_timestamp, total_size = struct.unpack_from("<III", body)
        if total_size != len(payload):
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
        with self.lock:
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
            if now - self.last_frame_at < END_GAP_SECONDS:
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
            runtime_auto = control_state.enabled("auto_reply")
            control_candidate = True
            should_process = AUTO_ENABLED and (runtime_auto or control_candidate) and not self.processing
            if should_process:
                self.processing = True
        self.write_state()
        if should_process:
            threading.Thread(
                target=self.process_voice, args=(payloads, generation, source_uid, source_callsign), daemon=True
            ).start()
        return True

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

    def check_hourly_announcement(self, now: datetime | None = None) -> bool:
        if not HOURLY_ENABLED or not control_state.enabled("hourly_announcement"):
            return False
        local_now = now.astimezone(HOURLY_TIMEZONE) if now else datetime.now(HOURLY_TIMEZONE)
        seconds_after_hour = local_now.minute * 60 + local_now.second
        if not HOURLY_FIRST_HOUR <= local_now.hour <= HOURLY_LAST_HOUR or seconds_after_hour >= HOURLY_GRACE_SECONDS:
            return False
        slot = local_now.strftime("%Y-%m-%dT%H:00%z")
        with self.lock:
            if self.hourly_state.get("last_slot") == slot:
                return False
            self.hourly_state = {**self.hourly_state, "last_slot": slot, "last_attempt_at": round(time.time())}
            channel_idle = time.time() - self.channel_last_activity >= HOURLY_IDLE_SECONDS
            safe = self.connected and not self.processing and not self.active_frames and channel_idle and self.client is not None
            if safe:
                self.processing = True
                generation = self.activity_generation
            else:
                self.hourly_state.update({"last_result": "skipped_busy", "last_broadcast_hour": local_now.hour})
        self._save_hourly_state()
        self.write_state()
        if not safe:
            return False
        text = f"这里是{ANNOUNCEMENT_NAME}，现在是{local_now.hour}点整"
        threading.Thread(
            target=self.process_hourly_announcement,
            args=(text, generation, slot, local_now.hour),
            daemon=True,
        ).start()
        return True

    def check_night_notice(self, now: datetime | None = None) -> bool:
        if not HOURLY_ENABLED or not control_state.enabled("hourly_announcement"):
            return False
        local_now = now.astimezone(HOURLY_TIMEZONE) if now else datetime.now(HOURLY_TIMEZONE)
        slot = night_notice_slot(local_now)
        if not slot:
            return False
        with self.lock:
            if self.hourly_state.get("night_last_slot") == slot:
                return False
            channel_idle = time.time() - self.channel_last_activity >= HOURLY_IDLE_SECONDS
            safe = self.connected and not self.processing and not self.active_frames and channel_idle and self.client is not None
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
        text = f"这里是{ANNOUNCEMENT_NAME}，温馨预告：我将在零点下班休息，明天早上七点以后再聊。各位晚安，七十三"
        threading.Thread(
            target=self.process_hourly_announcement,
            args=(text, generation, slot, local_now.hour),
            daemon=True,
        ).start()
        return True

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
            encoded = fmo_codec.encode_pcm_to_opus(pcm)
            payloads = fmo_codec.build_fmo_payloads(encoded, callsign=AI_CALLSIGN)
            sent_frames = 0
            for item in payloads:
                with self.lock:
                    if self.activity_generation != generation:
                        raise RuntimeError("channel became busy during hourly announcement")
                result = self.client.publish(TOPIC, item, qos=0, retain=False)
                if result.rc != mqtt.MQTT_ERR_SUCCESS:
                    raise RuntimeError("MQTT hourly announcement publish failed")
                frames = int.from_bytes(item[HEADER.size + 12 : HEADER.size + 14], "little")
                sent_frames += frames
                time.sleep(frames * 0.04)
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
        try:
            if source_uid is None or source_callsign is None:
                raise RuntimeError("voice source is unavailable")
            packets, protocol_ms = fmo_codec.opus_packets(payloads, source_uid, source_callsign)
            if protocol_ms > MAX_INPUT_MS:
                raise RuntimeError("voice input exceeds 60 second limit")
            decoded = fmo_codec.decode_opus_to_wav(packets, input_wav)
            point = time.monotonic()
            phase = "asr"
            asr = speech.transcribe(input_wav)
            stage["asr_ms"] = round((time.monotonic() - point) * 1000)
            transcript = asr["text"].strip()
            point = time.monotonic()
            phase = "model"
            command = control_state.parse_voice_command(source_callsign, transcript)
            if command:
                control_state.save({command["name"]: command["value"]})
                answer = {"mode": "control", "reply": command["reply"]}
            else:
                persona_command = persona_state.parse_voice_command(source_callsign, transcript)
                if persona_command and control_state.enabled("persona_voice_commands"):
                    persona_state.save(persona_command["updates"])
                    answer = {"mode": "control", "reply": persona_command["reply"]}
                elif persona_command:
                    answer = {"mode": "control", "reply": "身份语音设置已禁止"}
                elif not control_state.enabled("auto_reply"):
                    diagnostic = {}
                    if source_callsign == persona_state.ADMIN_CALLSIGN:
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
            reply = answer["reply"].strip()
            point = time.monotonic()
            phase = "tts_stream"
            stream = speech.synthesize_pcm_stream(reply)
            first_chunk = next(stream)
            stage["tts_first_chunk_ms"] = round((time.monotonic() - point) * 1000)
            lead_frames = max(1, round(LEAD_IN_MS / 40))
            tail_frames = max(1, round(TAIL_MS / 40))
            with self.lock:
                safe_to_send = (
                    self.activity_generation == generation
                    and not self.active_frames
                    and time.time() - self.channel_last_activity >= END_GAP_SECONDS
                )
            if not safe_to_send or self.client is None:
                raise RuntimeError("channel became busy before reply")
            phase = "ptt"
            PTT_MARKER.write_text(str(int(time.time())), encoding="ascii")
            PTT_MARKER.chmod(0o600)

            frame_bytes = fmo_codec.FRAME_SAMPLES * 2
            group_bytes = frame_bytes * 6
            base = secrets.randbelow(0xFFFFFFFF - 60001) + 1
            frame_offset = 0
            pending = bytearray(b"\0" * (lead_frames * frame_bytes))
            audio_bytes = 0
            max_audio_bytes = max(0, (MAX_REPLY_MS - lead_frames * 40 - tail_frames * 40) * 32)

            def publish_pcm(data: bytes) -> None:
                nonlocal frame_offset
                encoded = fmo_codec.encode_pcm_to_opus(data)
                outgoing = fmo_codec.build_fmo_payloads(encoded, base=base, frame_offset=frame_offset)
                for item in outgoing:
                    with self.lock:
                        interrupted = self.activity_generation != generation
                    if interrupted:
                        raise RuntimeError("channel became busy during reply")
                    result = self.client.publish(TOPIC, item, qos=0, retain=False)
                    if result.rc != mqtt.MQTT_ERR_SUCCESS:
                        raise RuntimeError("MQTT reply publish failed")
                    frames = int.from_bytes(item[HEADER.size + 12 : HEADER.size + 14], "little")
                    frame_offset += frames
                    time.sleep(frames * 0.04)

            first_audio = True
            for chunk in itertools.chain((first_chunk,), stream):
                if len(chunk) % 2:
                    raise RuntimeError("streaming TTS returned unaligned PCM")
                remaining = max_audio_bytes - audio_bytes
                if remaining <= 0:
                    break
                chunk = chunk[:remaining]
                if first_audio:
                    chunk = fade_in_pcm16(chunk, FADE_IN_MS)
                    first_audio = False
                audio_bytes += len(chunk)
                pending.extend(chunk)
                while len(pending) >= group_bytes:
                    publish_pcm(bytes(pending[:group_bytes]))
                    del pending[:group_bytes]
            stage["tts_complete_ms"] = round((time.monotonic() - point) * 1000)
            if answer["mode"] == "control":
                pending.extend(audio_cues.command_dingdong(fmo_codec.SAMPLE_RATE))
            pending.extend(b"\0" * (tail_frames * frame_bytes))
            if len(pending) % frame_bytes:
                pending.extend(b"\0" * (frame_bytes - len(pending) % frame_bytes))
            while pending:
                size = min(len(pending), group_bytes)
                publish_pcm(bytes(pending[:size]))
                del pending[:size]
            duration_ms = frame_offset * 40
            stage["tts_encode_ms"] = round((time.monotonic() - point) * 1000)
            self._record_voice(
                "success",
                mode=answer["mode"],
                callsign=source_callsign,
                uid=source_uid,
                transcript=transcript[:120],
                reply=reply[:120],
                input_ms=protocol_ms or decoded["duration_ms"],
                reply_ms=round(duration_ms),
                lead_in_ms=lead_frames * 40,
                fade_in_ms=FADE_IN_MS,
                tail_ms=tail_frames * 40,
                total_ms=round((time.monotonic() - started) * 1000),
                stages=stage,
            )
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
        finally:
            PTT_MARKER.unlink(missing_ok=True)
            input_wav.unlink(missing_ok=True)
            reply_wav.unlink(missing_ok=True)


def main() -> None:
    monitor = Monitor()
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
    client.connect(HOST, PORT, keepalive=30)
    client.loop_start()
    monitor.write_state()
    try:
        while True:
            monitor.finalize_if_idle()
            monitor.check_hourly_announcement()
            monitor.check_night_notice()
            time.sleep(0.05)
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
