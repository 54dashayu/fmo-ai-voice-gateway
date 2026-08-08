#!/usr/bin/env python3
from __future__ import annotations

import math
import struct


def _bell(frequency: float, duration_ms: int, sample_rate: int) -> bytes:
    samples = round(sample_rate * duration_ms / 1000)
    output = bytearray(samples * 2)
    for index in range(samples):
        elapsed = index / sample_rate
        decay = math.exp(-7.0 * elapsed)
        attack = min(1.0, index / max(1, round(sample_rate * 0.008)))
        value = (
            math.sin(2 * math.pi * frequency * elapsed)
            + 0.32 * math.sin(2 * math.pi * frequency * 2.01 * elapsed)
            + 0.12 * math.sin(2 * math.pi * frequency * 3.96 * elapsed)
        )
        sample = round(8500 * attack * decay * value)
        struct.pack_into("<h", output, index * 2, max(-32768, min(32767, sample)))
    return bytes(output)


def command_dingdong(sample_rate: int = 16000) -> bytes:
    """Return an aligned mechanical high-low confirmation chime."""
    gap = b"\0" * (round(sample_rate * 0.09) * 2)
    lead_gap = b"\0" * (round(sample_rate * 0.08) * 2)
    pcm = lead_gap + _bell(1046.5, 360, sample_rate) + gap + _bell(784.0, 520, sample_rate)
    frame_bytes = round(sample_rate * 0.04) * 2
    if len(pcm) % frame_bytes:
        pcm += b"\0" * (frame_bytes - len(pcm) % frame_bytes)
    return pcm
