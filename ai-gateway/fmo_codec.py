#!/usr/bin/env python3
"""Guarded FMO V1 OPUS/RADPCM parsing; see bg5esn.com/docs/fmo-voice-codec-spec/."""

from __future__ import annotations

import ctypes
import ctypes.util
import audioop
import os
import time
import itertools
import struct
import wave
import zlib
from pathlib import Path


FMO_HEADER = struct.Struct("<HII12s")
MESSAGE_HEADER_SIZE = 64
PASSPORT_VENDOR = 0x1001
CAPTURE_RECORD = struct.Struct("<dI")
AGGREGATE_FIXED_SIZE = 42
SAMPLE_RATE = 16000
FRAME_SAMPLES = 640  # 40 ms at 16 kHz
MAX_PACKET_BYTES = 2048


def _opus_library():
    name = ctypes.util.find_library("opus")
    if not name:
        raise RuntimeError("libopus is unavailable")
    library = ctypes.CDLL(name)
    library.opus_decoder_create.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    library.opus_decoder_create.restype = ctypes.c_void_p
    library.opus_decode.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int16),
        ctypes.c_int,
        ctypes.c_int,
    ]
    library.opus_decode.restype = ctypes.c_int
    library.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
    library.opus_encoder_create.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    library.opus_encoder_create.restype = ctypes.c_void_p
    library.opus_encode.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int16),
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    library.opus_encode.restype = ctypes.c_int
    library.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
    return library


def capture_payloads(path: Path) -> list[bytes]:
    payloads = []
    with path.open("rb") as stream:
        while record := stream.read(CAPTURE_RECORD.size):
            if len(record) != CAPTURE_RECORD.size:
                raise ValueError("truncated capture record")
            _arrival, size = CAPTURE_RECORD.unpack(record)
            if size < FMO_HEADER.size or size > MAX_PACKET_BYTES:
                raise ValueError("capture packet outside guarded limits")
            payload = stream.read(size)
            if len(payload) != size:
                raise ValueError("truncated capture payload")
            payloads.append(payload)
    return payloads


def packet_identity(payload: bytes) -> tuple[int, int, str, str]:
    if not MESSAGE_HEADER_SIZE <= len(payload) <= MAX_PACKET_BYTES:
        raise ValueError("packet outside guarded limits")
    version, vendor, uid, raw = FMO_HEADER.unpack_from(payload)
    if version != 1:
        raise ValueError("unsupported FMO version")
    wire_call = raw.rstrip(b"\0").decode("ascii", "strict").upper()
    # Only matching Passport/M5/Xingzhi vendor prefixes are aliases. Do not globally
    # permit punctuation or strip arbitrary prefixes from identity strings.
    callsign = wire_call[2:] if ((vendor == PASSPORT_VENDOR and wire_call.startswith("P.")) or (vendor == 0x1791 and wire_call.startswith(("M.", "E.")))) else wire_call
    return vendor, uid, callsign, wire_call


def parse_packet(payload: bytes) -> tuple[tuple[int, int, str, str], int, int, list[tuple[int, bytes]]]:
    identity = packet_identity(payload)
    first, timestamp, total, count, crc = struct.unpack_from("<IIIHI", payload, 22)
    if total != len(payload) or not count or zlib.crc32(payload[64:]) != crc:
        raise ValueError("aggregate validation failed")
    frames = []
    position = MESSAGE_HEADER_SIZE
    previous = 0
    for _ in range(count):
        if position + 16 > len(payload):
            raise ValueError("short transport frame")
        sequence, size = struct.unpack_from("<HH", payload, position)
        if sequence <= previous or size <= 16 or position + size > len(payload):
            raise ValueError("invalid transport frame")
        mode, encoded_size = struct.unpack_from("<BH", payload, position + 8)
        if mode not in (1, 2) or encoded_size != size - 8:
            raise ValueError("invalid encoded frame")
        raw = payload[position + 16:position + size]
        if mode == 2:
            if len(raw) != 328 or raw[4] > 88 or raw[6] != 64:
                raise ValueError("invalid RADPCM frame")
        frames.append((mode, raw))
        previous = sequence
        position += size
    if position != len(payload):
        raise ValueError("aggregate has trailing bytes")
    return identity, first, timestamp, frames


def voice_frames(payloads: list[bytes], expected_uid: int, expected_callsign: str) -> tuple[list[tuple[int, bytes]], int]:
    frames = []
    source = None
    base = end = None
    for payload in payloads:
        identity, first, timestamp, items = parse_packet(payload)
        vendor, uid, callsign, wire_call = identity
        if uid != expected_uid or callsign != expected_callsign:
            raise ValueError("capture contains an unexpected source")
        if source is not None and (identity != source or first != base):
            raise ValueError("capture mixes sources or streams")
        source = identity
        base = first
        end = timestamp
        frames.extend(items)
        if media_duration_ms(frames) > 60000:
            raise ValueError("voice input exceeds 60 second limit")
    if not frames:
        raise ValueError("empty voice input")
    return frames, (end - base) & 0xFFFFFFFF


def media_duration_ms(frames: list[tuple[int, bytes]]) -> int:
    return sum(40 if mode == 1 else 80 for mode, _ in frames)


def _codec_blocks(payloads: list[bytes], expected_uid: int, expected_callsign: str) -> tuple[list[bytes], int]:
    # Preserve the existing offset-diagnostic API for OPUS only. The canonical
    # payload remains at offset 10 in this diagnostic representation.
    frames, span = voice_frames(payloads, expected_uid, expected_callsign)
    if any(mode != 1 for mode, _ in frames):
        raise ValueError("not an OPUS stream")
    blocks = []
    for payload in payloads:
        position = MESSAGE_HEADER_SIZE
        while position < len(payload):
            size = struct.unpack_from("<H", payload, position + 2)[0]
            blocks.append(payload[position + 6:position + size])
            position += size
    return blocks, span


def opus_packets(payloads: list[bytes], expected_uid: int, expected_callsign: str) -> tuple[list[bytes], int]:
    frames, span = voice_frames(payloads, expected_uid, expected_callsign)
    if any(mode != 1 for mode, _ in frames):
        raise ValueError("not an OPUS stream")
    return [raw for _, raw in frames], span


IMA_STEP_TABLE = (
    7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,
    60,66,73,80,88,97,107,118,130,143,157,173,190,209,230,253,279,
    307,337,371,408,449,494,544,598,658,724,796,876,963,1060,1166,
    1282,1411,1552,1707,1878,2066,2272,2499,2749,3024,3327,3660,
    4026,4428,4871,5358,5894,6484,7132,7845,8630,9493,10442,11487,
    12635,13899,15289,16818,18500,20350,22385,24623,27086,29794,32767,
)
IMA_INDEX_TABLE = (-1,-1,-1,-1,2,4,6,8,-1,-1,-1,-1,2,4,6,8)


def decode_radpcm_pcm(packets: list[bytes]) -> bytes:
    """Decode standard FMO RADPCM to 8 kHz PCM; each frame has a recovery point."""
    pcm = bytearray()
    last = None
    predictor = step_index = 0
    for packet in packets:
        if len(packet) != 328 or packet[4] > 88 or packet[6] != 64:
            raise ValueError("invalid RADPCM frame")
        index, recovery, initial_step = struct.unpack_from("<HhB", packet)
        if last is None or last == 0xFFFF or index == 0 or index != last + 1:
            predictor, step_index = recovery, initial_step
        for value in packet[8:]:
            for code in (value >> 4, value & 15):
                step = IMA_STEP_TABLE[step_index]
                delta = step >> 3
                if code & 1: delta += step >> 2
                if code & 2: delta += step >> 1
                if code & 4: delta += step
                predictor = max(-32768, min(32767, predictor + (-delta if code & 8 else delta)))
                step_index = max(0, min(88, step_index + IMA_INDEX_TABLE[code]))
                pcm.extend(struct.pack("<h", predictor))
        last = index
    return bytes(pcm)


def decode_voice_to_wav(frames: list[tuple[int, bytes]], output: Path) -> dict:
    if not frames or media_duration_ms(frames) > 60000:
        raise ValueError("voice duration outside guarded limits")
    pcm = bytearray()
    for mode, group in itertools.groupby(frames, key=lambda frame: frame[0]):
        packets = [raw for _, raw in group]
        if mode == 1:
            part = _decode_opus_pcm(packets)
            if len(part) != len(packets) * FRAME_SAMPLES * 2:
                raise ValueError("OPUS frames must be 40 ms")
        elif mode == 2:
            raw_pcm = decode_radpcm_pcm(packets)
            part, _ = audioop.ratecv(raw_pcm, 2, 1, 8000, SAMPLE_RATE, None)
            # ratecv omits the final interpolation sample. Extend the endpoint
            # to preserve exactly 80 ms per RADPCM frame at the ASR sample rate.
            part += part[-2:] * ((len(raw_pcm) * 2 - len(part)) // 2)
        else:
            raise ValueError("unsupported codec")
        pcm.extend(part)
    return write_pcm_wav(bytes(pcm), output, len(frames))


def _decode_opus_pcm(packets: list[bytes]) -> bytes:
    """Decode packets in memory so diagnostics never need to retain source audio."""
    library = _opus_library()
    error = ctypes.c_int()
    decoder = library.opus_decoder_create(SAMPLE_RATE, 1, ctypes.byref(error))
    if not decoder or error.value != 0:
        raise RuntimeError("unable to create Opus decoder")
    pcm = bytearray()
    try:
        buffer = (ctypes.c_int16 * (FRAME_SAMPLES * 3))()
        for packet in packets:
            if not packet:
                raise ValueError("empty Opus packet")
            encoded = ctypes.create_string_buffer(packet)
            samples = library.opus_decode(decoder, encoded, len(packet), buffer, len(buffer), 0)
            if samples <= 0:
                raise ValueError("Opus decode failed")
            pcm.extend(bytes(buffer)[: samples * 2])
    finally:
        library.opus_decoder_destroy(decoder)
    return bytes(pcm)


def diagnose_opus_offsets(
    payloads: list[bytes], expected_uid: int, expected_callsign: str,
    *, offsets: tuple[int, ...] = (8, 9, 10, 11, 12),
) -> dict:
    """Return numeric-only candidate decode metrics; never writes or returns PCM."""
    blocks, _duration_ms = _codec_blocks(payloads, expected_uid, expected_callsign)
    packet_sizes = [len(block) - 10 for block in blocks]
    candidates = {}
    for offset in offsets:
        try:
            pcm = _decode_opus_pcm([block[offset:] for block in blocks])
            candidates[str(offset)] = {
                "valid": True,
                "rms": audioop.rms(pcm, 2) if pcm else 0,
                "peak": audioop.max(pcm, 2) if pcm else 0,
                "duration_ms": round(len(pcm) / 2 / SAMPLE_RATE * 1000),
            }
        except (ValueError, RuntimeError):
            candidates[str(offset)] = {"valid": False}
    return {
        "packet_count": len(blocks),
        "packet_bytes_min": min(packet_sizes) if packet_sizes else 0,
        "packet_bytes_max": max(packet_sizes) if packet_sizes else 0,
        "packet_bytes_avg": round(sum(packet_sizes) / len(packet_sizes), 1) if packet_sizes else 0,
        "candidates": candidates,
    }


def decode_opus_to_wav(packets: list[bytes], output: Path) -> dict:
    pcm = _decode_opus_pcm(packets)
    return write_pcm_wav(pcm, output, len(packets))


def write_pcm_wav(pcm: bytes, output: Path, packets: int) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with wave.open(str(output), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    output.chmod(0o600)
    return {"packets": packets, "samples": len(pcm) // 2, "duration_ms": round(len(pcm) / 2 / SAMPLE_RATE * 1000)}


def wav_to_pcm16(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError("TTS WAV must be mono 16-bit PCM")
        rate = wav.getframerate()
        pcm = wav.readframes(wav.getnframes())
    if rate != SAMPLE_RATE:
        pcm, _state = audioop.ratecv(pcm, 2, 1, rate, SAMPLE_RATE, None)
    remainder = len(pcm) % (FRAME_SAMPLES * 2)
    if remainder:
        pcm += b"\0" * (FRAME_SAMPLES * 2 - remainder)
    return pcm


def normalize_quiet_pcm16(pcm: bytes, *, target_rms: int = 1600,
                          threshold_rms: int = 400, max_gain: float = 64.0) -> tuple[bytes, float]:
    """Apply bounded gain only to a very quiet mono PCM16 ASR input."""
    if not pcm or len(pcm) % 2:
        raise ValueError("PCM16 input is invalid")
    rms = audioop.rms(pcm, 2)
    if rms <= 0 or rms >= threshold_rms:
        return pcm, 1.0
    gain = min(float(max_gain), max(1.0, float(target_rms) / rms))
    return audioop.mul(pcm, 2, gain), gain


def encode_pcm_to_opus(pcm: bytes) -> list[bytes]:
    if not pcm or len(pcm) % (FRAME_SAMPLES * 2):
        raise ValueError("PCM is not aligned to 40 ms frames")
    library = _opus_library()
    error = ctypes.c_int()
    encoder = library.opus_encoder_create(SAMPLE_RATE, 1, 2048, ctypes.byref(error))  # OPUS_APPLICATION_VOIP
    if not encoder or error.value != 0:
        raise RuntimeError("unable to create Opus encoder")
    packets = []
    try:
        output = ctypes.create_string_buffer(4000)
        for offset in range(0, len(pcm), FRAME_SAMPLES * 2):
            frame = (ctypes.c_int16 * FRAME_SAMPLES).from_buffer_copy(pcm[offset : offset + FRAME_SAMPLES * 2])
            size = library.opus_encode(encoder, frame, FRAME_SAMPLES, output, len(output))
            if size <= 0 or size > 239:
                raise RuntimeError("Opus encoder returned an unsafe packet size")
            packets.append(bytes(output.raw[:size]))
    finally:
        library.opus_encoder_destroy(encoder)
    return packets


def build_fmo_payloads(
    opus: list[bytes], uid: int = 65535, callsign: str = "AI-FMO",
    *, base: int | None = None, frame_offset: int = 0,
) -> list[bytes]:
    if not opus or len(opus) > 1500:
        raise ValueError("reply must contain 1-1500 Opus frames")
    raw_callsign = callsign.encode("ascii")[:12].ljust(12, b"\0")
    outer = FMO_HEADER.pack(1, 0, uid, raw_callsign)
    if base is None:
        base = int(time.time() * 1000) & 0xFFFFFFFF
    payloads = []
    frame_index = 0
    while frame_index < len(opus):
        group = opus[frame_index : frame_index + 6]
        blocks = bytearray()
        for sequence, packet in enumerate(group, 1):
            declared = len(packet) + 8
            if declared > 255:
                raise ValueError("Opus packet exceeds FMO one-byte length")
            metadata = b"\0\0\1" + bytes([declared]) + b"\0=\x14\0\xe0="
            block_size = 6 + len(metadata) + len(packet)
            blocks.extend(struct.pack("<HI", sequence, block_size))
            blocks.extend(metadata)
            blocks.extend(packet)
        body_size = AGGREGATE_FIXED_SIZE + len(blocks)
        total_size = FMO_HEADER.size + body_size
        timestamp = (base + (frame_offset + frame_index) * 40) & 0xFFFFFFFF
        fixed = struct.pack("<IIIH", base, timestamp, total_size, len(group))
        fixed += struct.pack("<IHH", zlib.crc32(blocks), 0xFF07, 6)
        fixed += b"\0" * 20
        payload = outer + fixed + blocks
        if len(payload) != total_size or len(payload) > MAX_PACKET_BYTES:
            raise ValueError("generated FMO aggregate outside guarded limits")
        payloads.append(payload)
        frame_index += len(group)
    return payloads


def build_radpcm_payloads(
    pcm16: bytes, *, base: int, frame_offset: int = 0,
    state: tuple[int, int] = (0, 0), uid: int = 65535,
    callsign: str = "AI-FMO", server_uid: int = 65535, frames_per_packet: int = 3,
) -> tuple[list[bytes], tuple[int, int]]:
    """Build standard 8 kHz RADPCM from the AI's 16 kHz PCM.

    frame_offset uses the existing sender's 40 ms ticks; RADPCM consumes two
    ticks per frame. IMA state and recovery points are retained across chunks.
    """
    if frames_per_packet not in (1, 2, 3):
        raise ValueError("invalid RADPCM packet grouping")
    if not pcm16 or len(pcm16) % (FRAME_SAMPLES * 4) or frame_offset % 2:
        raise ValueError("RADPCM input must be aligned to 80 ms")
    if len(pcm16) > SAMPLE_RATE * 2 * 60:
        raise ValueError("RADPCM reply exceeds 60 seconds")
    pcm8, _ = audioop.ratecv(pcm16, 2, 1, SAMPLE_RATE, 8000, None)
    frames = []
    for offset in range(0, len(pcm8), 1280):
        index = (frame_offset // 2 + offset // 1280) & 0xFFFF
        predictor, step = state
        encoded, state = audioop.lin2adpcm(pcm8[offset:offset + 1280], 2, state)
        if len(encoded) != 320:
            raise ValueError("invalid encoded RADPCM size")
        frames.append(struct.pack("<HhBBH", index, predictor, step, 0, 320) + encoded)
    payloads = []
    for start in range(0, len(frames), frames_per_packet):
        group = frames[start:start + frames_per_packet]
        body = bytearray()
        for number, raw in enumerate(group, 1):
            voice = struct.pack("<BH5s", 2, 336, bytes(5)) + raw
            body.extend(struct.pack("<HHI", number, 344, 0) + voice)
        timestamp = (base + frame_offset * 40 + start * 80) & 0xFFFFFFFF
        header = struct.pack(
            "<HII12sIIIHIBI19s", 1, 0, uid, callsign.encode("ascii"),
            base, timestamp, 64 + len(body), len(group), zlib.crc32(body),
            7, server_uid, bytes(19),
        )
        payloads.append(header + body)
    return payloads, state


def build_downlink_payloads(pcm16: bytes, *, callsign: str = "AI-FMO") -> list[bytes]:
    """Shared-channel AI downlink: standard RADPCM for every subscriber.

    This choice is independent of the sender vendor: all subscribers receive
    the same broadcast. Preserve OPUS support for incoming legacy devices.
    """
    if not pcm16:
        return []
    if len(pcm16) % 2:
        raise ValueError("unaligned PCM16")
    alignment = FRAME_SAMPLES * 4
    pcm16 += bytes((-len(pcm16)) % alignment)
    payloads, _ = build_radpcm_payloads(
        pcm16, base=int(time.time() * 1000) & 0xFFFFFFFF, callsign=callsign,
    )
    return payloads
