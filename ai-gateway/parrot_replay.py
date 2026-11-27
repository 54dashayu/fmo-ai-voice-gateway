"""Bounded in-memory policy for the one-shot raw voice replay service."""
from __future__ import annotations


def clip_pcm(pcm: bytes, sample_rate: int, max_ms: int) -> bytes:
    if not pcm or sample_rate <= 0 or max_ms <= 0:
        return b""
    limit = max_ms * sample_rate * 2 // 1000
    limit -= limit % 2
    return bytes(pcm[:limit - (limit % 2)])


def allowed(
    *, enabled: bool, generation: int, current_generation: int,
    active_frames: int, connected: bool, audio_exclusive: bool,
    net_checkin_active: bool,
) -> bool:
    return bool(
        enabled
        and connected
        and generation == current_generation
        and not active_frames
        and not audio_exclusive
        and not net_checkin_active
    )


def should_replace_ai(*, enabled: bool, handled_command: bool, priority_mode: bool) -> bool:
    """Ordinary input replays once; commands and higher-priority modes do not."""
    return bool(enabled and not handled_command and not priority_mode)
