#!/usr/bin/env python3
"""Prepare a bounded NAS music clip without publishing MQTT or controlling PTT."""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import gateway_config
import nas_music


MAX_SOURCE_BYTES = max(1, int(os.getenv("FMO_MUSIC_MAX_SOURCE_BYTES", str(512 * 1024 * 1024))))
DOWNLOAD_CHUNK_BYTES = max(16 * 1024, min(1024 * 1024, int(os.getenv("FMO_MUSIC_DOWNLOAD_CHUNK_BYTES", str(256 * 1024)))))


@dataclass(frozen=True)
class PreparedClip:
    path: Path
    duration: float
    source_duration: float
    title: str

    def cleanup(self) -> None:
        self.path.unlink(missing_ok=True)


def _request(path: str) -> urllib.request.Request:
    config = gateway_config.knowledge()
    token = str(config.get("token", ""))
    if not token:
        raise nas_music.MusicError("NAS music authorization is unavailable")
    return urllib.request.Request(
        str(config["base_url"]).rstrip("/") + path,
        headers={"Authorization": f"Bearer {token}"},
    )


def search(query: str = "") -> list[dict]:
    suffix = "/v1/music/search?q=" + urllib.parse.quote(str(query or "")[:120])
    try:
        with urllib.request.urlopen(_request(suffix), timeout=30) as response:
            payload = json.load(response)
    except (OSError, ValueError, TypeError) as exc:
        raise nas_music.MusicError("NAS music search failed") from exc
    results = payload.get("results", []) if isinstance(payload, dict) else []
    return [item for item in results if isinstance(item, dict) and item.get("id")]


def _download(identifier: str, destination: Path, cancelled: Callable[[], bool]) -> None:
    suffix = "/v1/music/stream?id=" + urllib.parse.quote(identifier)
    written = 0
    try:
        with urllib.request.urlopen(_request(suffix), timeout=90) as response, destination.open("wb") as output:
            length = int(response.headers.get("Content-Length", "0") or 0)
            if length > MAX_SOURCE_BYTES:
                raise nas_music.MusicError("NAS music source exceeds size limit")
            while True:
                if cancelled():
                    raise nas_music.MusicError("music preparation was preempted")
                chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_SOURCE_BYTES:
                    raise nas_music.MusicError("NAS music source exceeds size limit")
                output.write(chunk)
    except nas_music.MusicError:
        raise
    except OSError as exc:
        raise nas_music.MusicError("NAS music stream failed") from exc


def prepare(query: str = "", *, seconds: float = nas_music.DEFAULT_SECONDS,
            cancelled: Callable[[], bool] = lambda: False,
            rng: random.Random | None = None) -> PreparedClip:
    results = search(query)
    if not results:
        raise nas_music.MusicError("no matching NAS music was found")
    selected = (rng or random.SystemRandom()).choice(results)
    work = Path(tempfile.mkdtemp(prefix="fmo-music-"))
    source = work / "source"
    output = work / "clip.wav"
    try:
        _download(str(selected["id"]), source, cancelled)
        if cancelled():
            raise nas_music.MusicError("music preparation was preempted")
        metadata = nas_music.probe(source)
        plan = nas_music.choose_plan(metadata, requested=min(seconds, nas_music.HARD_MAX_SECONDS), rng=rng)
        nas_music.render(plan, output)
        source.unlink(missing_ok=True)
        title = " ".join(str(selected.get("name") or "随机歌曲").split())[:40]
        return PreparedClip(output, plan.duration, plan.source_duration, title)
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise


def pcm_chunks(clip: PreparedClip, *, chunk_bytes: int = 32 * 1024) -> Iterator[bytes]:
    import wave
    with wave.open(str(clip.path), "rb") as stream:
        if stream.getnchannels() != 1 or stream.getsampwidth() != 2 or stream.getframerate() != 16000:
            raise nas_music.MusicError("prepared music clip has an invalid PCM format")
        frames = max(1, chunk_bytes // 2)
        while chunk := stream.readframes(frames):
            yield chunk
