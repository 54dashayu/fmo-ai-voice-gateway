#!/usr/bin/env python3
"""Minimal guarded decoder for the observed FMO codec-6 raw Opus format."""

from __future__ import annotations

import ctypes
import ctypes.util
import audioop
import os
import random
import struct
import wave
import zlib
from pathlib import Path


FMO_HEADER = struct.Struct("<IHHH12s")
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


def opus_packets(payloads: list[bytes], expected_uid: int, expected_callsign: str) -> tuple[list[bytes], int]:
    packets = []
    base_timestamp = None
    end_timestamp = None
    for payload in payloads:
        version, _p1, uid, _p2, raw_callsign = FMO_HEADER.unpack_from(payload)
        callsign = raw_callsign.rstrip(b"\0").decode("ascii", "strict")
        if version != 1 or uid != expected_uid or callsign != expected_callsign:
            raise ValueError("capture contains an unexpected source")
        body = payload[FMO_HEADER.size :]
        if len(body) < AGGREGATE_FIXED_SIZE:
            raise ValueError("short aggregate")
        first, timestamp, total_size, count = struct.unpack_from("<IIIH", body)
        checksum = struct.unpack_from("<I", body, 14)[0]
        codec = struct.unpack_from("<H", body, 20)[0]
        if total_size != len(payload) or codec != 6 or zlib.crc32(body[AGGREGATE_FIXED_SIZE:]) != checksum:
            raise ValueError("aggregate validation failed")
        base_timestamp = first if base_timestamp is None else base_timestamp
        end_timestamp = timestamp
        position = AGGREGATE_FIXED_SIZE
        for _ in range(count):
            sequence, block_size = struct.unpack_from("<HI", body, position)
            if sequence < 1 or block_size < 17 or position + block_size > len(body):
                raise ValueError("invalid codec block")
            block = body[position + 6 : position + block_size]
            declared = block[3]
            if block[:3] != b"\0\0\1" or block[4:10] != b"\0=\x14\0\xe0=" or declared != len(block) - 2:
                raise ValueError("unexpected codec block metadata")
            packets.append(block[10:])
            position += block_size
        if position != len(body):
            raise ValueError("aggregate has trailing bytes")
    duration_ms = 0 if base_timestamp is None or end_timestamp is None else (end_timestamp - base_timestamp) & 0xFFFFFFFF
    return packets, duration_ms


def decode_opus_to_wav(packets: list[bytes], output: Path) -> dict:
    library = _opus_library()
    error = ctypes.c_int()
    decoder = library.opus_decoder_create(SAMPLE_RATE, 1, ctypes.byref(error))
    if not decoder or error.value != 0:
        raise RuntimeError("unable to create Opus decoder")
    pcm = bytearray()
    try:
        buffer = (ctypes.c_int16 * (FRAME_SAMPLES * 3))()
        for packet in packets:
            encoded = ctypes.create_string_buffer(packet)
            samples = library.opus_decode(decoder, encoded, len(packet), buffer, len(buffer), 0)
            if samples <= 0:
                raise ValueError("Opus decode failed")
            pcm.extend(bytes(buffer)[: samples * 2])
    finally:
        library.opus_decoder_destroy(decoder)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with wave.open(str(output), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    output.chmod(0o600)
    return {"packets": len(packets), "samples": len(pcm) // 2, "duration_ms": round(len(pcm) / 2 / SAMPLE_RATE * 1000)}


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
    outer = FMO_HEADER.pack(1, 0, uid, 0, raw_callsign)
    if base is None:
        base = random.SystemRandom().randrange(1, 0xFFFFFFFF - (len(opus) + frame_offset) * 40)
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
        timestamp = base + (frame_offset + frame_index) * 40
        fixed = struct.pack("<IIIH", base, timestamp, total_size, len(group))
        fixed += struct.pack("<IHH", zlib.crc32(blocks), 0xFF07, 6)
        fixed += b"\0" * 20
        payload = outer + fixed + blocks
        if len(payload) != total_size or len(payload) > MAX_PACKET_BYTES:
            raise ValueError("generated FMO aggregate outside guarded limits")
        payloads.append(payload)
        frame_index += len(group)
    return payloads
