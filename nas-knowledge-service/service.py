#!/usr/bin/env python3
"""Small NAS-side knowledge/data service; contains no AI model inference."""

from __future__ import annotations

import hashlib
import hmac
import base64
import json
import math
import os
import re
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from urllib.parse import parse_qs

DATA_DIR = Path(os.getenv("FMO_KB_DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "fmo-knowledge.sqlite3"
AUDIO_DIR = DATA_DIR / "temporary-audio"
DOCUMENT_DIR = DATA_DIR / "documents"
MAX_BODY = 2 * 1024 * 1024
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_CHUNK_BYTES = 768 * 1024
ALLOWED_EXTENSIONS = {".md", ".txt", ".pdf", ".docx", ".xlsx", ".csv"}
UPLOAD_DIR = DATA_DIR / ".uploads"

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  domain TEXT NOT NULL CHECK(domain IN ('fmo_dashboard','ham_checkin_console','amateur_radio','private')),
  title TEXT NOT NULL,
  source TEXT NOT NULL,
  version TEXT,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sections (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  heading TEXT,
  content TEXT NOT NULL,
  embedding_json TEXT NOT NULL,
  token_count INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sections_document_idx ON sections(document_id);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  callsign_hash TEXT,
  detail_json TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else -1.0


def cleanup_audio() -> int:
    ttl = int(os.getenv("FMO_KB_AUDIO_TTL_SECONDS", "3600"))
    cutoff = time.time() - ttl
    removed = 0
    for path in AUDIO_DIR.glob("*"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink()
            removed += 1
    return removed


def safe_relative(value: str, *, allow_empty: bool = True) -> Path:
    value = value.strip().replace("\\", "/").strip("/")
    if not value and allow_empty:
        return Path()
    parts = value.split("/")
    if not parts or any(not part or part in {".", ".."} or part.startswith(".") or len(part) > 120 for part in parts):
        raise ValueError("invalid knowledge path")
    return Path(*parts)


def file_entries(relative: Path) -> list[dict]:
    directory = DOCUMENT_DIR / relative
    if not directory.is_dir():
        raise ValueError("directory not found")
    with connect() as db:
        indexed = {row["source"]: int(row["sections"]) for row in db.execute(
            "SELECT d.source,COUNT(s.id) sections FROM documents d LEFT JOIN sections s ON s.document_id=d.id GROUP BY d.id"
        )}
    entries = []
    for path in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold())):
        if path.name.startswith("."):
            continue
        rel = path.relative_to(DOCUMENT_DIR).as_posix()
        stat = path.stat()
        entries.append({
            "name": path.name,
            "path": rel,
            "type": "directory" if path.is_dir() else "file",
            "size": 0 if path.is_dir() else stat.st_size,
            "modified_at": int(stat.st_mtime),
            "indexed_sections": 0 if path.is_dir() else indexed.get(rel, indexed.get(path.name, 0)),
        })
    return entries


class Handler(BaseHTTPRequestHandler):
    server_version = "FmoKnowledge/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        # Never log authorization headers or request bodies.
        print(f"{self.address_string()} {fmt % args}")

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        expected = os.getenv("FMO_KB_ADMIN_TOKEN", "")
        supplied = self.headers.get("Authorization", "")
        return bool(expected) and hmac.compare_digest(supplied, f"Bearer {expected}")

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > MAX_BODY:
            raise ValueError("invalid body length")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            cleanup_audio()
            with connect() as db:
                sections = db.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
            self.send_json(200, {"ok": True, "sections": sections, "model_inference": False})
            return
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return
        try:
            if path == "/v1/files":
                relative = safe_relative(parse_qs(urlparse(self.path).query).get("path", [""])[0])
                self.send_json(200, {"path": relative.as_posix() if relative.parts else "", "entries": file_entries(relative)})
                return
            if path == "/v1/admin/overview":
                with connect() as db:
                    documents = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                    sections = db.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
                    searches = db.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='knowledge_search'").fetchone()[0]
                    search_rows = db.execute("SELECT detail_json,created_at FROM audit_events WHERE event_type='knowledge_search' ORDER BY id DESC").fetchall()
                    hits = sum(1 for row in search_rows if int(json.loads(row["detail_json"]).get("result_count", 0)) > 0)
                    recent = search_rows[0] if search_rows else None
                files = sum(1 for item in DOCUMENT_DIR.rglob("*") if item.is_file() and not item.name.startswith("."))
                latest = None if not recent else {"at": recent["created_at"], **json.loads(recent["detail_json"])}
                self.send_json(200, {"documents": documents, "sections": sections, "files": files, "searches": searches, "hits": hits, "latest_search": latest})
                return
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.send_json(400, {"error": str(exc)})
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return
        try:
            body = self.read_json()
            path = urlparse(self.path).path
            if path == "/v1/search":
                self.search(body)
            elif path == "/v1/sections/upsert":
                self.upsert(body)
            elif path == "/v1/settings":
                self.update_setting(body)
            elif path == "/v1/files/upload":
                self.upload_file(body)
            else:
                self.send_json(404, {"error": "not found"})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})

    def search(self, body: dict) -> None:
        vector = [float(x) for x in body["embedding"]]
        domains = body.get("domains") or []
        limit = min(int(body.get("limit", 5)), int(os.getenv("FMO_KB_MAX_RESULTS", "10")))
        sql = """SELECT s.id,s.heading,s.content,s.embedding_json,d.domain,d.title,d.source,d.version
                 FROM sections s JOIN documents d ON d.id=s.document_id"""
        params: list[str] = []
        if domains:
            sql += " WHERE d.domain IN (%s)" % ",".join("?" for _ in domains)
            params.extend(domains)
        with connect() as db:
            rows = db.execute(sql, params).fetchall()
        ranked = sorted(((cosine(vector, json.loads(row["embedding_json"])), row) for row in rows), reverse=True, key=lambda x: x[0])[:limit]
        now = int(time.time())
        with connect() as db:
            db.execute(
                "INSERT INTO audit_events(event_type,callsign_hash,detail_json,created_at) VALUES(?,?,?,?)",
                ("knowledge_search", None, json.dumps({"result_count": len(ranked), "domain_count": len(domains)}, separators=(",", ":")), now),
            )
        self.send_json(200, {"results": [{"score": score, **{k: row[k] for k in ("id","domain","title","heading","content","source","version")}} for score, row in ranked]})

    def upload_file(self, body: dict) -> None:
        relative = safe_relative(str(body.get("path", "")))
        name = safe_relative(str(body["name"]), allow_empty=False)
        if len(name.parts) != 1 or name.suffix.lower() not in ALLOWED_EXTENSIONS:
            raise ValueError("unsupported file name or type")
        upload_id = str(body["upload_id"])
        if not re.fullmatch(r"[a-f0-9]{32}", upload_id):
            raise ValueError("invalid upload id")
        offset = int(body["offset"])
        total_size = int(body["total_size"])
        if offset < 0 or total_size < 1 or total_size > MAX_FILE_BYTES:
            raise ValueError("invalid upload size")
        try:
            chunk = base64.b64decode(str(body["chunk"]), validate=True)
        except (ValueError, TypeError):
            raise ValueError("invalid upload chunk") from None
        if not chunk or len(chunk) > MAX_CHUNK_BYTES or offset + len(chunk) > total_size:
            raise ValueError("invalid upload chunk size")
        UPLOAD_DIR.mkdir(mode=0o700, exist_ok=True)
        temporary = UPLOAD_DIR / upload_id
        current = temporary.stat().st_size if temporary.exists() else 0
        if current != offset:
            raise ValueError("unexpected upload offset")
        with temporary.open("ab") as stream:
            stream.write(chunk)
        completed = offset + len(chunk) == total_size
        if completed:
            target_dir = DOCUMENT_DIR / relative
            target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            target = target_dir / name.name
            os.replace(temporary, target)
            os.chmod(target, 0o600)
            with connect() as db:
                db.execute(
                    "INSERT INTO audit_events(event_type,callsign_hash,detail_json,created_at) VALUES(?,?,?,?)",
                    ("file_upload", None, json.dumps({"path": target.relative_to(DOCUMENT_DIR).as_posix(), "size": total_size}, separators=(",", ":")), int(time.time())),
                )
        self.send_json(200, {"ok": True, "completed": completed, "received": offset + len(chunk)})

    def upsert(self, body: dict) -> None:
        now = int(time.time())
        domain = str(body["domain"])
        document_id = str(body.get("document_id") or hashlib.sha256(f"{domain}:{body['source']}".encode()).hexdigest()[:24])
        section_id = str(body.get("section_id") or hashlib.sha256(f"{document_id}:{body['heading']}:{body['content']}".encode()).hexdigest()[:24])
        embedding = [float(x) for x in body["embedding"]]
        if domain not in {"fmo_dashboard","ham_checkin_console","amateur_radio","private"} or not embedding:
            raise ValueError("invalid domain or embedding")
        with connect() as db:
            db.execute("INSERT INTO documents(id,domain,title,source,version,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET domain=excluded.domain,title=excluded.title,source=excluded.source,version=excluded.version,updated_at=excluded.updated_at", (document_id,domain,str(body["title"]),str(body["source"]),body.get("version"),now))
            db.execute("INSERT INTO sections(id,document_id,heading,content,embedding_json,token_count,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET heading=excluded.heading,content=excluded.content,embedding_json=excluded.embedding_json,token_count=excluded.token_count,updated_at=excluded.updated_at", (section_id,document_id,str(body.get("heading","")),str(body["content"]),json.dumps(embedding,separators=(",",":")),int(body.get("token_count",0)),now))
        self.send_json(200, {"ok": True, "document_id": document_id, "section_id": section_id})

    def update_setting(self, body: dict) -> None:
        key = str(body["key"])
        if key not in {"ai_enabled","allowed_callsigns","wake_words","max_reply_chars","audio_ttl_seconds"}:
            raise ValueError("unsupported setting")
        with connect() as db:
            db.execute("INSERT INTO settings(key,value_json,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at", (key,json.dumps(body["value"],ensure_ascii=False),int(time.time())))
        self.send_json(200, {"ok": True})


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(mode=0o700, exist_ok=True)
    DOCUMENT_DIR.mkdir(mode=0o700, exist_ok=True)
    UPLOAD_DIR.mkdir(mode=0o700, exist_ok=True)
    for name in ("fmo-dashboard", "ham-checkin", "amateur-radio", "private"):
        (DOCUMENT_DIR / name).mkdir(mode=0o700, exist_ok=True)
    with connect():
        pass
    port = int(os.getenv("FMO_KB_PORT", "8787"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
