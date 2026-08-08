#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import gateway_config
import persona_state


BAILIAN_PROVIDER_NAMES = {"dashi", "dashscope", "bailian", "aliyun-bailian"}


MAX_AUDIO_BYTES = 10 * 1024 * 1024
DIALECT_ALIASES = {
    "广东话": "广东话", "粤语": "广东话", "廣東話": "广东话",
    "东北话": "东北话", "東北話": "东北话",
    "河南话": "河南话", "湖南话": "湖南话", "陕西话": "陕西话",
    "山东话": "山东话", "四川话": "四川话", "安徽话": "安徽话",
}


def selected_voice() -> str:
    """Select a CosyVoice v3 system voice from the persisted public persona."""
    persona = persona_state.load()
    gender = persona.get("gender", "").lower()
    accent = persona.get("accent", "").lower()
    age_text = persona.get("age", "")
    match = __import__("re").search(r"\d{1,3}", age_text)
    age = int(match.group()) if match else None
    female = any(word in gender for word in ("女", "female", "女孩", "女生"))
    male = any(word in gender for word in ("男", "male", "男孩", "男生")) and not female
    if male and acoustic_dialect() == "东北话":
        return "longlaotie_v3"
    if female and acoustic_dialect():
        return "longanhuan_v3"
    if female and any(word in accent for word in ("台湾", "台灣", "台式")):
        return "longantai_v3"
    if female and age is not None and age <= 20:
        return "longdaiyu_v3"
    if female:
        return "longanwen_v3"
    if male and age is not None and age <= 25:
        return "longcheng_v3"
    if male:
        return "longanyang"
    return os.getenv("DASHSCOPE_TTS_VOICE", "longanyang")


def acoustic_dialect() -> str | None:
    accent = persona_state.load().get("accent", "")
    for alias, dialect in DIALECT_ALIASES.items():
        if alias in accent:
            return dialect
    return None


def selected_instruction() -> str | None:
    dialect = acoustic_dialect()
    # Only longanhuan_v3 supports free dialect instructions. Native dialect
    # voices such as longlaotie_v3 reject the instruction field.
    return f"请用{dialect}表达。" if dialect and selected_voice() == "longanhuan_v3" else None


def tts_profile() -> dict:
    persona = persona_state.load()
    accent = persona.get("accent", "")
    dialect = acoustic_dialect()
    taiwan = any(word in accent for word in ("台湾", "台灣", "台式"))
    voice = selected_voice()
    dialect_effective = bool(
        dialect and (voice == "longanhuan_v3" or (voice == "longlaotie_v3" and dialect == "东北话"))
    )
    return {
        "voice": voice,
        "requested_accent": accent,
        "acoustic_accent": dialect if dialect_effective else ("台湾口音" if taiwan else None),
        "accent_effective": bool(dialect_effective or taiwan or accent in {"普通话", "普通話", "标准普通话"}),
    }


def _tts_input(text: str, audio_format: str, sample_rate: int) -> dict:
    value = {
        "text": text,
        "voice": selected_voice(),
        "format": audio_format,
        "sample_rate": sample_rate,
        "language_hints": ["zh"],
    }
    instruction = selected_instruction()
    if instruction:
        value["instruction"] = instruction
    return value


def _provider(name: str) -> tuple[str, str, str]:
    cfg = gateway_config.provider(name)
    key_env = str(cfg.get("api_key_env", "DASHSCOPE_API_KEY"))
    key = os.getenv(key_env, "")
    if not key:
        key = str(cfg.get("api_key", ""))
    return str(cfg.get("provider", "dashi")), str(cfg.get("base_url", os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")).rstrip("/")), key


def _headers(key: str) -> dict:
    if not key:
        raise RuntimeError("DASHSCOPE_API_KEY is not set")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _compatible_base() -> str:
    return _provider("tts")[1]


def _native_base() -> str:
    configured = os.getenv("DASHSCOPE_NATIVE_BASE_URL", "").rstrip("/")
    if configured:
        return configured
    suffix = "/compatible-mode/v1"
    base = _compatible_base()
    if not base.endswith(suffix):
        raise RuntimeError("cannot derive Model Studio native endpoint")
    return base[: -len(suffix)]


def _post_json(url: str, payload: dict, timeout: float, key: str) -> dict:
    request = urllib.request.Request(url, json.dumps(payload, ensure_ascii=False).encode(), headers=_headers(key), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"speech upstream HTTP error: {exc.code}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"speech upstream connection failed: {exc.reason}") from None


def _safe_audio_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.hostname.endswith("aliyuncs.com"):
        raise RuntimeError("TTS returned an untrusted audio URL")
    return parsed._replace(scheme="https").geturl()


def synthesize(text: str, output_path: Path) -> dict:
    text = text.strip()
    if not text:
        raise ValueError("invalid TTS text length")
    provider, _base, key = _provider("tts")
    if provider not in BAILIAN_PROVIDER_NAMES:
        raise RuntimeError(f"unsupported TTS provider: {provider}")
    payload = {
        "model": str(gateway_config.provider("tts").get("model", os.getenv("DASHSCOPE_TTS_MODEL", "cosyvoice-v3-flash"))),
        "input": _tts_input(text, "wav", int(os.getenv("DASHSCOPE_TTS_SAMPLE_RATE", "24000"))),
    }
    url = f"{_native_base()}/api/v1/services/audio/tts/SpeechSynthesizer"
    data = _post_json(url, payload, 45, key)
    try:
        audio_url = _safe_audio_url(data["output"]["audio"]["url"])
        characters = int(data.get("usage", {}).get("characters", len(text)))
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("Model Studio returned an unexpected TTS response") from None
    try:
        with urllib.request.urlopen(audio_url, timeout=45) as response:
            audio = response.read(MAX_AUDIO_BYTES + 1)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"TTS audio download failed: {exc.reason}") from None
    if len(audio) > MAX_AUDIO_BYTES or not audio.startswith(b"RIFF"):
        raise RuntimeError("TTS returned invalid WAV audio")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(audio)
    os.chmod(output_path, 0o600)
    return {"characters": characters, "bytes": len(audio), "model": payload["model"], "voice": payload["input"]["voice"]}


def synthesize_pcm_stream(text: str):
    """Yield 16 kHz mono PCM chunks from Model Studio's SSE TTS response."""
    text = text.strip()
    if not text:
        raise ValueError("invalid TTS text length")
    provider, _base, key = _provider("tts")
    if provider not in BAILIAN_PROVIDER_NAMES:
        raise RuntimeError(f"unsupported TTS provider: {provider}")
    payload = {
        "model": str(gateway_config.provider("tts").get("model", os.getenv("DASHSCOPE_TTS_MODEL", "cosyvoice-v3-flash"))),
        "input": _tts_input(text, "pcm", 16000),
    }
    headers = _headers(key)
    headers["X-DashScope-SSE"] = "enable"
    request = urllib.request.Request(
        f"{_native_base()}/api/v1/services/audio/tts/SpeechSynthesizer",
        json.dumps(payload, ensure_ascii=False).encode(),
        headers=headers,
        method="POST",
    )
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            for raw_line in response:
                line = raw_line.strip()
                if not line.startswith(b"data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                    audio = event.get("output", {}).get("audio", {})
                    if audio.get("url"):
                        continue
                    encoded = audio.get("data") or ""
                    chunk = base64.b64decode(encoded, validate=True) if encoded else b""
                except (ValueError, TypeError, json.JSONDecodeError):
                    raise RuntimeError("Model Studio returned invalid streaming TTS data") from None
                if chunk:
                    total += len(chunk)
                    if total > MAX_AUDIO_BYTES:
                        raise RuntimeError("streaming TTS exceeded audio size limit")
                    yield chunk
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"speech upstream HTTP error: {exc.code}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"speech upstream connection failed: {exc.reason}") from None
    if not total:
        raise RuntimeError("streaming TTS returned no audio")


def transcribe(audio_path: Path) -> dict:
    audio = audio_path.read_bytes()
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise ValueError("invalid ASR audio size")
    provider, _base, key = _provider("asr")
    if provider not in BAILIAN_PROVIDER_NAMES:
        raise RuntimeError(f"unsupported ASR provider: {provider}")
    mime = mimetypes.guess_type(str(audio_path))[0] or "audio/wav"
    data_uri = f"data:{mime};base64,{base64.b64encode(audio).decode()}"
    payload = {
        "model": str(gateway_config.provider("asr").get("model", os.getenv("DASHSCOPE_ASR_MODEL", "qwen3-asr-flash"))),
        "messages": [{
            "role": "user",
            "content": [{"type": "input_audio", "input_audio": {"data": data_uri}}],
        }],
        "stream": False,
        "asr_options": {"language": "zh", "enable_itn": True},
    }
    data = _post_json(f"{_compatible_base()}/chat/completions", payload, 45, key)
    try:
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in content)
        transcript = str(content).strip()
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Model Studio returned an unexpected ASR response") from None
    if not transcript:
        raise RuntimeError("ASR returned empty text")
    return {"text": transcript, "model": payload["model"], "bytes": len(audio)}


def cleanup_audio(directory: Path) -> int:
    ttl = int(os.getenv("FMO_AUDIO_TTL_SECONDS", "3600"))
    cutoff = time.time() - ttl
    removed = 0
    if not directory.exists():
        return 0
    for path in directory.glob("*.wav"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink()
            removed += 1
    return removed
