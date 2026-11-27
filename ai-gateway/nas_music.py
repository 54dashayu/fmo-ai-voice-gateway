#!/usr/bin/env python3
"""Offline-first NAS music clip preparation.

This module never publishes MQTT or controls PTT.  It only indexes an explicitly
configured directory, selects a bounded middle window and runs ffprobe/ffmpeg.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Iterable


SUPPORTED_EXTENSIONS = frozenset({".mp3", ".flac", ".m4a", ".aac", ".wav", ".ogg", ".opus"})
DEFAULT_SECONDS = 30.0
HARD_MAX_SECONDS = 45.0
DEFAULT_FADE_SECONDS = 0.3
EDGE_FRACTION = 0.125


class MusicError(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioMetadata:
    path: Path
    duration: float
    codec: str
    sample_rate: int
    channels: int
    container: str


@dataclass(frozen=True)
class ClipPlan:
    source: Path
    source_duration: float
    start: float
    duration: float
    fade: float


@dataclass(frozen=True)
class PlaybackGate:
    enabled: bool = False
    admin_authorized: bool = False
    human_active: bool = False
    emergency_stop: bool = False
    net_checkin_active: bool = False
    ai_reply_active: bool = False
    announcement_active: bool = False

    def blockers(self) -> list[str]:
        blocked: list[str] = []
        if not self.enabled:
            blocked.append("disabled")
        if not self.admin_authorized:
            blocked.append("admin_authorization_required")
        if self.human_active:
            blocked.append("human_active")
        if self.emergency_stop:
            blocked.append("emergency_stop")
        if self.net_checkin_active:
            blocked.append("net_checkin_active")
        if self.ai_reply_active:
            blocked.append("ai_reply_active")
        if self.announcement_active:
            blocked.append("announcement_active")
        return blocked


def discover(root: Path, *, max_files: int = 10_000) -> list[Path]:
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise MusicError("configured music root is not a directory")
    found: list[Path] = []
    for path in root.rglob("*"):
        if len(found) >= max_files:
            raise MusicError("music index exceeded the configured file limit")
        try:
            if path.is_file() and not path.is_symlink() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
                found.append(path)
        except OSError:
            continue
    return sorted(found)


def probe(path: Path, *, ffprobe: str = "ffprobe") -> AudioMetadata:
    command = [
        ffprobe, "-v", "error", "-select_streams", "a:0",
        "-show_entries", "format=duration,format_name:stream=codec_name,sample_rate,channels",
        "-of", "json", "--", str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
    if result.returncode:
        raise MusicError("ffprobe could not read the selected audio file")
    try:
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
        duration = float(payload["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError
        return AudioMetadata(
            path=path,
            duration=duration,
            codec=str(stream.get("codec_name", "unknown")),
            sample_rate=int(stream.get("sample_rate", 0)),
            channels=int(stream.get("channels", 0)),
            container=str(payload["format"].get("format_name", "unknown")),
        )
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MusicError("ffprobe returned incomplete audio metadata") from exc


def choose_plan(metadata: AudioMetadata, *, requested: float = DEFAULT_SECONDS,
                fade: float = DEFAULT_FADE_SECONDS, rng: random.Random | None = None) -> ClipPlan:
    if requested <= 0:
        raise MusicError("clip duration must be positive")
    duration = min(float(requested), HARD_MAX_SECONDS, metadata.duration)
    fade = min(max(0.0, float(fade)), duration / 2)
    edge = metadata.duration * EDGE_FRACTION
    earliest = edge
    latest = metadata.duration - edge - duration
    if latest < earliest:
        earliest = max(0.0, (metadata.duration - duration) / 2)
        latest = earliest
    source = rng or random.SystemRandom()
    start = earliest if latest == earliest else source.uniform(earliest, latest)
    return ClipPlan(metadata.path, metadata.duration, start, duration, fade)


def ffmpeg_command(plan: ClipPlan, output: Path, *, input_name: str | None = None,
                   ffmpeg: str = "ffmpeg") -> list[str]:
    fade_out = max(0.0, plan.duration - plan.fade)
    filters = (
        "loudnorm=I=-18:LRA=7:TP=-2,"
        f"afade=t=in:st=0:d={plan.fade:.3f},"
        f"afade=t=out:st={fade_out:.3f}:d={plan.fade:.3f}"
    )
    return [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{plan.start:.3f}", "-t", f"{plan.duration:.3f}",
        "-i", input_name or str(plan.source), "-vn", "-ac", "1", "-ar", "16000",
        "-sample_fmt", "s16", "-af", filters, "-f", "wav", str(output),
    ]


def render(plan: ClipPlan, output: Path, *, ffmpeg: str = "ffmpeg") -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = ffmpeg_command(plan, output, ffmpeg=ffmpeg)
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
    if result.returncode:
        output.unlink(missing_ok=True)
        raise MusicError("ffmpeg failed to render the bounded clip")


def stream_to_ffmpeg(plan: ClipPlan, source: BinaryIO, output: Path, *,
                     cancelled: Callable[[], bool] = lambda: False,
                     chunk_bytes: int = 64 * 1024, ffmpeg: str = "ffmpeg") -> None:
    """Feed a source incrementally; callers can stop on human/admin preemption."""
    output.parent.mkdir(parents=True, exist_ok=True)
    command = ffmpeg_command(plan, output, input_name="pipe:0", ffmpeg=ffmpeg)
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        assert process.stdin is not None
        while process.poll() is None:
            if cancelled():
                raise MusicError("music preparation was preempted")
            chunk = source.read(chunk_bytes)
            if not chunk:
                break
            try:
                process.stdin.write(chunk)
            except BrokenPipeError:
                break
        try:
            process.stdin.close()
        except BrokenPipeError:
            pass
        if process.stderr:
            process.stderr.read()
        code = process.wait(timeout=120)
        if code:
            raise MusicError("ffmpeg failed while reading the controlled stream")
    except Exception:
        if process.poll() is None:
            process.kill()
            process.wait()
        output.unlink(missing_ok=True)
        raise
    finally:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        if process.stderr:
            process.stderr.close()


def public_metadata(metadata: AudioMetadata) -> dict:
    """Return metadata without exposing the NAS root or a possibly personal filename."""
    return {
        "duration": round(metadata.duration, 3), "codec": metadata.codec,
        "sample_rate": metadata.sample_rate, "channels": metadata.channels,
        "container": metadata.container,
    }


def _json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, default=str, sort_keys=True))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline-only NAS music clip tool; no MQTT/PTT support")
    sub = parser.add_subparsers(dest="command", required=True)
    index = sub.add_parser("index")
    index.add_argument("root", type=Path)
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("source", type=Path)
    plan_parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    render_parser = sub.add_parser("render")
    render_parser.add_argument("source", type=Path)
    render_parser.add_argument("output", type=Path)
    render_parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "index":
            files = discover(args.root)
            _json({"ok": True, "audio_files": len(files), "supported_extensions": sorted(SUPPORTED_EXTENSIONS)})
            return 0
        metadata = probe(args.source.resolve(strict=True))
        clip = choose_plan(metadata, requested=args.seconds)
        if args.command == "plan":
            _json({"ok": True, "metadata": public_metadata(metadata), "plan": {
                "start": round(clip.start, 3), "duration": round(clip.duration, 3), "fade": clip.fade,
            }, "mqtt": False, "ptt": False})
            return 0
        render(clip, args.output)
        _json({"ok": True, "output": str(args.output), "duration": round(clip.duration, 3),
               "mqtt": False, "ptt": False})
        return 0
    except (MusicError, OSError) as exc:
        _json({"ok": False, "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
