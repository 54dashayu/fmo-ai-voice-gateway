#!/usr/bin/env python3
"""Chunk local Markdown, embed with Model Studio, and upsert into the NAS index."""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import gateway


def parse_document(path: Path) -> tuple[dict, list[tuple[str, str]]]:
    text = path.read_text(encoding="utf-8")
    metadata = {}
    if text.startswith("---\n"):
        raw, text = text[4:].split("\n---\n", 1)
        for line in raw.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip()
    sections = []
    heading = metadata.get("title", path.stem)
    body = []
    for line in text.splitlines():
        if line.startswith("## "):
            if body:
                sections.append((heading, "\n".join(body).strip()))
            heading, body = line[3:].strip(), []
        elif not line.startswith("# "):
            body.append(line)
    if body:
        sections.append((heading, "\n".join(body).strip()))
    return metadata, [(h, re.sub(r"\n{3,}", "\n\n", c)) for h, c in sections if c]


def upsert(payload: dict) -> None:
    token = os.getenv("FMO_KB_ADMIN_TOKEN")
    if not token:
        raise RuntimeError("FMO_KB_ADMIN_TOKEN is not set")
    base = os.getenv("FMO_KB_BASE_URL", "http://127.0.0.1:18787").rstrip("/")
    request = urllib.request.Request(
        f"{base}/v1/sections/upsert", json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"NAS upsert HTTP error: {exc.code}") from None
    if not result.get("ok"):
        raise RuntimeError("NAS rejected knowledge section")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    count = 0
    for path in sorted(args.root.rglob("*.md")):
        metadata, sections = parse_document(path)
        for heading, content in sections:
            upsert({
                "domain": metadata["domain"], "title": metadata["title"],
                "source": path.name, "version": metadata.get("version"),
                "heading": heading, "content": content,
                "embedding": gateway.embed_text(f"{metadata['title']}\n{heading}\n{content}"),
                "token_count": max(1, len(content) // 2),
            })
            count += 1
    print(json.dumps({"ok": True, "documents": len(list(args.root.rglob('*.md'))), "sections": count}))


if __name__ == "__main__":
    main()
