#!/usr/bin/env python3
"""Minimal text-only FMO AI gateway.

No MQTT, ASR, TTS, or PTT transmission is implemented here. The network call is
disabled unless FMO_AI_ENABLED=true and an allowed callsign is supplied.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

import callsign_policy
import persona_state


BAILIAN_PROVIDER_NAMES = {"dashi", "dashscope", "bailian", "aliyun-bailian"}
import gateway_config


KNOWLEDGE_TERMS = (
    "fmo", "仪表盘", "点名", "主控台", "呼号", "中继", "业余无线电",
    "电台", "频率", "功率", "驻波", "天线", "mqtt", "sas", "emqx",
)
CHAT_PREFIXES = ("普通聊天", "聊天模式")
KNOWLEDGE_PREFIXES = ("知识问答", "知识模式", "查询知识库")
QSO_TERMS = (
    "cq", "73", "抄收", "抄到", "信号报告", "rst", "这里是", "请回答", "请过来",
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
    "india", "juliett", "kilo", "lima", "mike", "november", "oscar", "papa",
    "quebec", "romeo", "sierra", "tango", "uniform", "victor", "whiskey",
    "x-ray", "yankee", "zulu",
)

_STATUS_LOCK = threading.Lock()
_STATUS = {
    "started_at": int(time.time()),
    "requests_total": 0,
    "chat_total": 0,
    "knowledge_total": 0,
    "last_request_at": None,
    "last_result": "waiting",
}


def _record(mode: str, result: str) -> None:
    with _STATUS_LOCK:
        _STATUS["requests_total"] += 1
        _STATUS[f"{mode}_total"] += 1
        _STATUS["last_request_at"] = int(time.time())
        _STATUS["last_result"] = result


def status_snapshot() -> dict:
    with _STATUS_LOCK:
        return dict(_STATUS)


def enabled() -> bool:
    return os.getenv("FMO_AI_ENABLED", "false").lower() == "true"


def allowed_callsigns() -> set[str]:
    raw = os.getenv("FMO_ALLOWED_CALLSIGNS", "")
    return {item.strip().upper() for item in raw.split(",") if item.strip()}


def allow_all_callsigns() -> bool:
    return os.getenv("FMO_ALLOW_ALL_CALLSIGNS", "false").lower() == "true"


def knowledge_enabled() -> bool:
    return bool(gateway_config.knowledge()["enabled"])


def _provider_headers() -> dict:
    provider = gateway_config.provider("chat")
    provider_name = str(provider.get("provider", "dashi"))
    key_env = str(provider.get("api_key_env", "DASHSCOPE_API_KEY"))
    api_key = os.getenv(key_env)
    if api_key is None:
        api_key = str(provider.get("api_key", ""))
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is not set")
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _provider_base() -> str:
    return str(gateway_config.provider("chat").get(
        "base_url",
        os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ).rstrip("/"))


def _provider_payload_model() -> str:
    return str(gateway_config.provider("chat").get("model", os.getenv("DASHSCOPE_MODEL", "qwen-plus")))


def _provider_name() -> str:
    return str(gateway_config.provider("chat").get("provider", "dashi"))


def validate_input(callsign: str, text: str) -> tuple[str, str]:
    callsign = callsign.strip().upper()
    if not enabled():
        raise RuntimeError("AI gateway is disabled (set FMO_AI_ENABLED=true to enable text calls)")
    callsign_policy.normalize(callsign)
    if callsign in callsign_policy.load_blacklist():
        raise PermissionError(f"callsign {callsign!r} is blocked")
    if not allow_all_callsigns() and callsign not in allowed_callsigns():
        raise PermissionError(f"callsign {callsign!r} is not allowed")
    max_input = int(os.getenv("FMO_MAX_INPUT_CHARS", "500"))
    if not text.strip() or len(text) > max_input:
        raise ValueError(f"input must contain 1..{max_input} characters")
    _provider_headers()
    return callsign, text.strip()


def is_knowledge_question(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in KNOWLEDGE_TERMS)


def is_standard_qso(text: str) -> bool:
    lowered = text.lower()
    if any(term in lowered for term in QSO_TERMS):
        return True
    # Callsigns commonly contain a Chinese prefix, one digit and a suffix.
    return bool(re.search(r"\b(?:B[A-Z]|VR2|XX9)[0-9][A-Z]{1,4}(?:-[0-9]{1,2})?\b", text.upper()))


def route_mode(text: str) -> tuple[str, str]:
    """Honor an explicit spoken mode prefix, otherwise route by subject."""
    cleaned = text.strip()
    for prefix in CHAT_PREFIXES:
        if cleaned.startswith(prefix):
            query = cleaned[len(prefix):].lstrip("，,：:。 ")
            return "chat", query or "你好"
    for prefix in KNOWLEDGE_PREFIXES:
        if cleaned.startswith(prefix):
            query = cleaned[len(prefix):].lstrip("，,：:。 ")
            return "knowledge", query or "请介绍知识库中现有的内容"
    return ("knowledge" if is_knowledge_question(cleaned) else "chat"), cleaned


def build_request(
    callsign: str, text: str, knowledge: list[dict] | None = None, mode: str | None = None,
    web_search: bool = False,
) -> tuple[str, dict, dict]:
    callsign, text = validate_input(callsign, text)
    base = _provider_base()
    provider_name = _provider_name()
    if provider_name not in BAILIAN_PROVIDER_NAMES:
        raise RuntimeError(f"unsupported chat provider: {provider_name}")
    model = _provider_payload_model()
    mode = mode or ("knowledge" if knowledge else "chat")
    context = ""
    if knowledge:
        excerpts = []
        for item in knowledge[: int(os.getenv("FMO_KB_MAX_RESULTS", "3"))]:
            excerpts.append(f"来源：{item.get('title','未知')} / {item.get('heading','')}\n{item.get('content','')}")
        context = "\n\n以下是NAS知识库检索结果，只能在其有依据时使用：\n" + "\n\n".join(excerpts)
    if mode == "chat":
        instructions = (
            "你是FMO语音频道里的AI聊天助手。以自然、友好、轻松的口吻回答普通交流，"
            "可以讨论生活、科技、兴趣、情感和一般知识。遵守中国境内适用的法律法规及公开传播要求；"
            "对违法、有害或明显不适合公开传播的请求简短拒绝，并尽量提供安全替代建议。"
            "不要无故说教，不要机械重复免责声明，也不要假装自己是真人或持证电台操作员。"
        )
    else:
        instructions = (
            "你是FMO语音频道的知识问答助手。优先依据提供的NAS知识库资料回答，"
            "资料不足时明确说明，不编造软件功能、设备参数、操作步骤或法规结论。"
            "涉及发射、法规或设备安全时提醒用户核对本地规定和设备手册。"
        )
        if web_search:
            instructions += (
                "NAS知识库没有找到相关资料，本次应使用阿里百炼联网搜索结果回答。"
                "优先采用设备厂商、监管机构、标准组织等可靠来源；若结果冲突或无法核实，要明确说无法确认。"
                "语音中简短说明这是联网查询结果，不朗读网址、引用编号或冗长来源列表。"
            )
    if is_standard_qso(text):
        instructions += (
            f"对方呼号为{callsign}，本轮采用业余无线电标准通联话术。"
            f"自报时必须完整说明‘这里是{os.getenv('FMO_ANNOUNCEMENT_NAME', 'FMO AI测试台')}’，不得冒充真实持证操作员；"
            "先准确回应对方呼号，再按对方的话完成呼号交换、确认抄收或结束通联。"
            "遇到CQ时简短应答，不连续重复CQ；需要拼读呼号时使用ITU字母解释法。"
            "对方说73或明确结束时，回复73并结束，不再主动展开新话题。"
            "不得虚构RST、QTH、姓名、设备和发射功率；不得说信号清楚、信号很好、满表、59或其他射频强度结论，只能说网络语音已收到或音频可辨。"
            "用词简洁，保留请讲、抄收、完毕、73等合适的通联术语，避免像客服或普通聊天。"
        )
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    instructions + persona_state.prompt()
                    + "回答适合语音播报，简洁连贯，并确保正常语速下可在45秒内播完；短问题应短答。"
                    + context
                ),
            },
            {"role": "user", "content": text.strip()},
        ],
        "temperature": 0.2,
    }
    if web_search:
        payload["enable_search"] = True
        payload["search_options"] = {"forced_search": True, "enable_source": False}
    return f"{base}/chat/completions", payload, _provider_headers()


def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    request = urllib.request.Request(url, json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"upstream HTTP error: {exc.code}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"upstream connection failed: {exc.reason}") from None


def embed_text(text: str) -> list[float]:
    provider = gateway_config.provider("embedding")
    if str(provider.get("provider", "dashscope")) not in BAILIAN_PROVIDER_NAMES:
        raise RuntimeError("only Alibaba Cloud Model Studio embedding is supported")
    payload = {
        "model": str(provider.get("model", os.getenv("DASHSCOPE_EMBEDDING_MODEL", "text-embedding-v4"))),
        "input": text,
        "dimensions": int(os.getenv("DASHSCOPE_EMBEDDING_DIMENSIONS", "1024")),
        "encoding_format": "float",
    }
    data = _post_json(
        f"{str(provider.get('base_url', _provider_base())).rstrip('/')}/embeddings", payload, _provider_headers(),
        float(os.getenv("FMO_REQUEST_TIMEOUT_SECONDS", "20")),
    )
    try:
        return [float(value) for value in data["data"][0]["embedding"]]
    except (KeyError, IndexError, TypeError, ValueError):
        raise RuntimeError("Model Studio returned an unexpected embedding response") from None


def search_knowledge(text: str) -> list[dict]:
    if not knowledge_enabled():
        return []
    config = gateway_config.knowledge()
    token = config["token"]
    if not token:
        raise RuntimeError("FMO_KB_ADMIN_TOKEN is not set")
    payload = {"embedding": embed_text(text), "limit": config["max_results"]}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    base = config["base_url"]
    data = _post_json(f"{base}/v1/search", payload, headers, config["timeout_seconds"])
    results = data.get("results")
    if not isinstance(results, list):
        raise RuntimeError("NAS knowledge service returned an unexpected response")
    minimum_score = float(os.getenv("FMO_KB_MIN_SCORE", "0.55"))
    return [item for item in results if float(item.get("score", 0.0)) >= minimum_score]


def call_model(
    callsign: str, text: str, knowledge: list[dict] | None = None,
    mode: str | None = None, web_search: bool = False,
) -> str:
    url, payload, headers = build_request(callsign, text, knowledge, mode, web_search)
    timeout = float(os.getenv("FMO_REQUEST_TIMEOUT_SECONDS", "20"))
    data = _post_json(url, payload, headers, timeout)
    try:
        reply = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        raise RuntimeError("Model Studio returned an unexpected response shape") from None
    return reply


def answer(callsign: str, text: str) -> dict:
    validate_input(callsign, text)
    mode, routed_text = route_mode(text)
    knowledge = search_knowledge(routed_text) if mode == "knowledge" and knowledge_enabled() else []
    if mode == "knowledge" and not knowledge:
        reply = call_model(callsign, routed_text, [], mode, web_search=True)
        _record(mode, "web_search_success")
        return {
            "mode": mode,
            "reply": reply,
            "sources": [{"domain": "互联网", "title": "阿里百炼联网搜索"}],
            "fallback": "bailian_web_search",
        }
    reply = call_model(callsign, routed_text, knowledge, mode)
    _record(mode, "success")
    sources = [
        {key: item.get(key) for key in ("domain", "title", "heading", "source", "version")}
        for item in knowledge
    ]
    return {"mode": mode, "reply": reply, "sources": sources}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--callsign", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        try:
            build_request(args.callsign, args.text)
        except RuntimeError as exc:
            if "DASHSCOPE_API_KEY" in str(exc):
                print("configuration valid; API key is not set (expected for offline validation)")
                return 0
            raise
        print("configuration valid")
        return 0
    print(call_model(args.callsign, args.text))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, PermissionError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
