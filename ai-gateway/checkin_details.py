#!/usr/bin/env python3
"""Extract only the three fields used by HAM Check-in Console."""

from __future__ import annotations

import json
import re

import gateway


FIELDS = ("qth", "device", "power")
CALLSIGN_PATTERN = re.compile(r"^(?=.{3,16}$)(?=.*[A-Z])(?=.*\d)[A-Z0-9/]+$")
SPELLING_WORDS = {
    "ALFA": "A", "ALPHA": "A", "BRAVO": "B", "CHARLIE": "C", "DELTA": "D",
    "ECHO": "E", "FOXTROT": "F", "GOLF": "G", "HOTEL": "H", "INDIA": "I",
    "JULIETT": "J", "JULIET": "J", "KILO": "K", "LIMA": "L", "MIKE": "M",
    "NOVEMBER": "N", "OSCAR": "O", "PAPA": "P", "QUEBEC": "Q", "ROMEO": "R",
    "SIERRA": "S", "TANGO": "T", "UNIFORM": "U", "VICTOR": "V", "WHISKEY": "W",
    "WHISKY": "W", "XRAY": "X", "YANKEE": "Y", "ZULU": "Z",
    "ZERO": "0", "ONE": "1", "TWO": "2", "THREE": "3", "FOUR": "4",
    "FIVE": "5", "SIX": "6", "SEVEN": "7", "EIGHT": "8", "NINE": "9",
}
CHINESE_DIGITS = str.maketrans("零一二三四五六七八九", "0123456789")
CHINESE_NUMBER_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _clean(value: object) -> str:
    if isinstance(value, list):
        value = " / ".join(str(item) for item in value if str(item).strip())
    return re.sub(r"[\r\n\t]+", " ", str(value or "")).strip()[:120]


def _chinese_integer(value: str) -> int | None:
    value = str(value or "").strip()
    if not value:
        return None
    if all(char in CHINESE_NUMBER_DIGITS for char in value):
        return int("".join(str(CHINESE_NUMBER_DIGITS[char]) for char in value))
    total = 0
    current = 0
    units = {"十": 10, "百": 100, "千": 1000}
    for char in value:
        if char in CHINESE_NUMBER_DIGITS:
            current = CHINESE_NUMBER_DIGITS[char]
        elif char in units:
            total += (current or 1) * units[char]
            current = 0
        else:
            return None
    return total + current


def normalize_power(value: object) -> str:
    """Normalize check-in power to H/M/L or an explicit number followed by W."""
    text = _clean(value).upper().replace("Ｗ", "W")
    if not text:
        return ""
    number = re.search(r"(?<![A-Z0-9])(\d+(?:\.\d+)?)\s*(?:W|瓦(?:特)?)", text, re.I)
    if number:
        watts = number.group(1).rstrip("0").rstrip(".") if "." in number.group(1) else number.group(1)
        return f"{watts}W"
    chinese = re.search(r"([零〇一二两三四五六七八九十百千]+)\s*(?:个)?\s*瓦(?:特)?", text)
    if chinese:
        watts = _chinese_integer(chinese.group(1))
        if watts is not None:
            return f"{watts}W"
    compact = re.sub(r"\s+", "", text)
    if compact in {"H", "HIGH", "高", "高H", "高功率", "大功率"} or "高功率" in compact:
        return "H"
    if compact in {"M", "MID", "MEDIUM", "中", "中M", "中功率", "中等功率"} or "中功率" in compact:
        return "M"
    if compact in {"L", "LOW", "低", "低L", "低功率", "小功率"} or "低功率" in compact:
        return "L"
    return ""


def normalize(value: dict | None) -> dict:
    value = value if isinstance(value, dict) else {}
    return {
        "qth": _clean(value.get("qth")),
        "device": _clean(value.get("device")),
        "power": normalize_power(value.get("power")),
    }


def _json_object(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {}
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def callsign_from_asr(transcript: str) -> str:
    text = str(transcript or "").upper().translate(CHINESE_DIGITS).replace("X-RAY", "XRAY")
    for word, letter in SPELLING_WORDS.items():
        text = re.sub(rf"\b{word}\b", letter, text)
    compact = re.sub(r"[^A-Z0-9/]", "", text)
    matches = re.findall(r"(?:B[A-Z]|VR2|XX9)\d[A-Z0-9/]{1,8}", compact)
    for value in matches:
        if CALLSIGN_PATTERN.fullmatch(value):
            return value
    return ""


def callsign_from_mqtt(value: str) -> str:
    """Return a validated callsign from the authenticated FMO/MQTT header."""
    callsign = re.sub(r"[^A-Z0-9/\-]", "", str(value or "").upper())
    return callsign if CALLSIGN_PATTERN.fullmatch(callsign.replace("-", "/")) else ""


def extract_callsign(transcript: str, mqtt_callsign: str = "") -> dict:
    direct = callsign_from_asr(transcript)
    if direct:
        return {"callsign": direct, "source": "asr"}
    fallback = re.sub(r"[^A-Z0-9/]", "", str(mqtt_callsign or "").upper())
    prompt = (
        "从业余无线电台网点名ASR文本中识别操作者主动报告的呼号。ASR文本权重最高。"
        "呼号通常包含字母和数字，也可能使用Alfa Bravo等ICAO字母解释法。"
        "MQTT认证呼号仅在ASR无法形成有效呼号时作为兜底。"
        "只输出JSON对象，键为callsign；无法识别时输出空字符串。\n"
        f"ASR文本：{transcript}\nMQTT认证呼号参考：{fallback}"
    )
    payload = {
        "model": gateway._provider_payload_model(),
        "messages": [
            {"role": "system", "content": "你是业余无线电呼号识别器，只返回JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
    }
    data = gateway._post_json(
        f"{gateway._provider_base()}/chat/completions", payload, gateway._provider_headers(), 15,
    )
    content = data["choices"][0]["message"]["content"]
    value = re.sub(r"[^A-Z0-9/]", "", str(_json_object(str(content)).get("callsign", "")).upper())
    if CALLSIGN_PATTERN.fullmatch(value):
        return {"callsign": value, "source": "asr_model"}
    return {"callsign": fallback if CALLSIGN_PATTERN.fullmatch(fallback) else "", "source": "mqtt_fallback"}


def extract_explicit(transcript: str) -> dict:
    """Deterministic fallback for clearly labelled on-air details."""
    text = re.sub(r"\s+", " ", str(transcript or "")).strip()
    qth = ""
    device = ""
    qth_match = re.search(
        r"(?:Q\s*T(?:\s*H)?|位置|所在地)\s*(?:是|在|为)?\s*([^，。；;]+)", text, re.I,
    )
    if qth_match:
        qth = re.split(r"(?:使用的?设备|设备(?:是|为)?|高功率|中功率|低功率)", qth_match.group(1))[0].strip(" ，。")
    device_match = re.search(r"(?:使用的?设备|设备)\s*(?:是|为)?\s*([^，。；;]+)", text, re.I)
    if device_match:
        device = re.split(r"(?:Q\s*T(?:\s*H)?|位置|所在地|高功率|中功率|低功率)", device_match.group(1), flags=re.I)[0].strip(" ，。")
    return normalize({"qth": qth, "device": device, "power": normalize_power(text)})


def extract(transcript: str, profile: dict | None = None) -> dict:
    known = normalize(profile)
    prompt = (
        "从业余无线电台网点名语音识别文本中提取QTH、设备、功率。"
        "只输出JSON对象，键只能是qth、device、power；没有明确说出的值输出空字符串。"
        "呼号库给出的值是当前呼号曾经使用过的候选范围。优先在候选范围中寻找与ASR文本读音、型号片段或地名片段最接近的值；"
        "例如ASR把英文设备型号识别为相近中文音节时，可以还原为候选中的完整型号。"
        "只有存在语音或ASR片段证据时才能选择候选，不得仅因候选存在就直接填入。"
        "功率输出格式只能是H、M、L或数字加W：高功率为H，中功率为M，低功率为L；"
        "五瓦、五个瓦特等明确数值输出5W。候选只有L/H/M等档位且本次没有对应语音证据时输出空字符串。\n"
        f"当前呼号的资料库候选：{json.dumps(known, ensure_ascii=False)}\n"
        f"本次ASR文本：{transcript}"
    )
    payload = {
        "model": gateway._provider_payload_model(),
        "messages": [
            {"role": "system", "content": "你是严格的结构化信息提取器。只返回JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
    }
    explicit = extract_explicit(transcript)
    try:
        data = gateway._post_json(
            f"{gateway._provider_base()}/chat/completions",
            payload,
            gateway._provider_headers(),
            15,
        )
        content = data["choices"][0]["message"]["content"]
        model = normalize(_json_object(str(content)))
    except (OSError, RuntimeError, KeyError, IndexError, TypeError, ValueError):
        return explicit
    return {key: model.get(key) or explicit.get(key, "") for key in FIELDS}
