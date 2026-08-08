#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import gateway
import speech


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--callsign", default=os.getenv("FMO_STATION_CALLSIGN", "N0CALL"))
    parser.add_argument("--test-text", default="这里是FMO AI测试台，请用一句话介绍你自己。")
    parser.add_argument("--output-dir", default=os.getenv("FMO_AUDIO_DIR", "/var/lib/fmo-ai-gateway/audio"))
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    speech.cleanup_audio(output_dir)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    input_wav = output_dir / f"test-input-{stamp}.wav"
    reply_wav = output_dir / f"test-reply-{stamp}.wav"

    tts_input = speech.synthesize(args.test_text, input_wav)
    asr = speech.transcribe(input_wav)
    result = gateway.answer(args.callsign, asr["text"])
    tts_reply = speech.synthesize(result["reply"], reply_wav)
    print(json.dumps({
        "test_text": args.test_text,
        "transcript": asr["text"],
        "reply": result["reply"],
        "mode": result["mode"],
        "input_audio": str(input_wav),
        "reply_audio": str(reply_wav),
        "input_tts": tts_input,
        "asr": {key: asr[key] for key in ("model", "bytes")},
        "reply_tts": tts_reply,
        "ptt": False,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
