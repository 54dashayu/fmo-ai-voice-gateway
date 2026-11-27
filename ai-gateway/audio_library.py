"""Bounded, offline-decoded media import. This module never transmits."""
from __future__ import annotations

import fcntl
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import selectors
import time
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import uuid
import wave

ROOT = Path(os.getenv('FMO_AUDIO_LIBRARY', '/var/lib/fmo-ai-gateway/playback'))
INBOX = Path(os.getenv('FMO_AUDIO_INBOX', '/var/lib/fmo-ai-gateway/audio-inbox'))
MAX_BYTES = 32 * 1024 * 1024
MAX_SECONDS = 600
MAX_CACHE = 256 * 1024 * 1024
FORMATS = 'wav,mp3,flac,ogg,aac,aiff'


class MediaError(ValueError):
    pass


def initialize():
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    INBOX.mkdir(parents=True, exist_ok=True, mode=0o700)


def local_names():
    initialize()
    return sorted(p.name for p in INBOX.iterdir()
                  if not p.is_symlink() and p.is_file() and p.stat().st_size <= MAX_BYTES)[:200]


def copy_local(name, target):
    if not isinstance(name, str) or not name or name in {'.', '..'} or Path(name).name != name or '\\' in name:
        raise MediaError('只能选择音频收件目录中的文件名')
    directory = os.open(INBOX, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= MAX_BYTES:
            raise MediaError('本地来源必须是独立普通文件，大小为 1 字节至 32 MiB')
        with target.open('wb') as output:
            remaining = MAX_BYTES + 1
            while remaining:
                chunk = source.read(min(65536, remaining))
                if not chunk:
                    break
                output.write(chunk)
                remaining -= len(chunk)
        if target.stat().st_size > MAX_BYTES:
            raise MediaError('文件超过 32 MiB')


def public_address(address):
    ip = ipaddress.ip_address(address)
    if not ip.is_global:
        return False
    if ip.version == 6:
        # Exclude translated/tunnel ranges that can embed an otherwise blocked IPv4 target.
        return ip in ipaddress.ip_network('2000::/3') and not ip.sixtofour and not ip.teredo
    return True


def public_target(url):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 or ord(c) > 126 for c in url):
        raise MediaError('URL 必须是有效的 http/https 地址')
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        host = parts.hostname
        if parts.scheme not in {'http', 'https'} or not host or parts.username or parts.password or parts.fragment:
            raise ValueError()
        if port not in {80, 443} or not re.fullmatch(r'[A-Za-z0-9.:-]+', host):
            raise ValueError()
        # Isolate DNS so a stalled resolver cannot hold an admin thread indefinitely.
        code = 'import socket,json,sys; print(json.dumps(sorted({i[4][0] for i in socket.getaddrinfo(sys.argv[1],sys.argv[2],type=socket.SOCK_STREAM)})))'
        result = subprocess.run([sys.executable, '-c', code, host, str(port)], capture_output=True, timeout=5, check=True)
        addresses = json.loads(result.stdout)
        if not addresses or any(not public_address(a) for a in addresses):
            raise ValueError()
    except (ValueError, OSError, subprocess.SubprocessError):
        raise MediaError('URL 只允许公共互联网地址及 80/443 端口，禁止内网或解析失败的目标') from None
    return host, port, addresses[0]


def download(url, target, max_bytes=MAX_BYTES):
    host, port, address = public_target(url)
    pinned = '[' + address + ']' if ':' in address else address
    # Disable proxy/config defaults, redirects and alternate protocols. Pin DNS at connect time.
    # URL stays on stdin; signed query strings never enter logs, argv or persistent state.
    config = 'url = "' + url.replace('\\', '\\\\').replace('"', '\\"') + '"\n'
    command = ['curl', '-q', '--config', '-', '--silent', '--fail', '--noproxy', '*',
               '--proto', '=http,https', '--max-redirs', '0', '--connect-timeout', '5',
               '--max-time', '30', '--max-filesize', str(max_bytes), '--resolve',
               f'{host}:{port}:{pinned}', '--output', '-', '--write-out', '%{http_code}']
    try:
        bounded_download(command, config.encode(), target, max_bytes=max_bytes)
    except (OSError, subprocess.SubprocessError, TimeoutError):
        raise MediaError('音频下载失败、超时或超过 32 MiB；请检查公网直链') from None
    return 'URL: ' + host  # No path, query or credentials retained.


def bounded_download(command, config, target, max_bytes=None):
    max_bytes = MAX_BYTES if max_bytes is None else max_bytes
    # Enforce a streaming cap even with old curl versions and chunked responses.
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        try:
            process.stdin.write(config)
            process.stdin.close()
            deadline = time.monotonic() + 35
            total = 0
            with selectors.DefaultSelector() as selector, target.open('wb') as output:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise TimeoutError()
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes + 3:
                        raise MediaError('音频下载超过 32 MiB')
                    output.write(chunk)
            if process.wait(timeout=max(.1, deadline - time.monotonic())) != 0 or total <= 3:
                raise MediaError('音频下载失败、超时或超过大小限制')
            with target.open('r+b') as stream:
                stream.seek(-3, 2)
                if stream.read(3) != b'200':
                    raise MediaError('URL 未返回音频直链；不支持重定向')
                stream.truncate(total - 3)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def convert(source, output):
    common = ['-v', 'error', '-protocol_whitelist', 'pipe', '-format_whitelist', FORMATS]
    try:
        with source.open('rb') as stream:
            probe = subprocess.run(['ffprobe', *common, '-show_entries',
                                    'stream=codec_type,duration:format=duration,format_name',
                                    '-of', 'json', 'pipe:0'], stdin=stream, capture_output=True, timeout=15, check=True)
        metadata = json.loads(probe.stdout)
        if not any(s.get('codec_type') == 'audio' for s in metadata.get('streams', [])):
            raise MediaError('文件不含支持的音频')
        duration = metadata.get('format', {}).get('duration')
        if duration is not None and (not math.isfinite(float(duration)) or float(duration) > MAX_SECONDS):
            raise MediaError('音频时长不得超过 10 分钟')
        with source.open('rb') as stream:
            subprocess.run(['ffmpeg', '-nostdin', *common, '-i', 'pipe:0', '-map', '0:a:0',
                            '-vn', '-sn', '-dn', '-threads', '1', '-ac', '1', '-ar', '16000',
                            '-c:a', 'pcm_s16le', '-t', str(MAX_SECONDS + 1), '-y', str(output)],
                           stdin=stream, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=45, check=True)
        with wave.open(str(output), 'rb') as wav:
            seconds = wav.getnframes() / wav.getframerate()
            if not 0 < seconds <= MAX_SECONDS:
                raise MediaError('音频时长必须在 0 至 10 分钟之间；不截断 SSTV')
        return round(seconds, 3)
    except MediaError:
        raise
    except (ValueError, OSError, wave.Error, subprocess.SubprocessError):
        raise MediaError('音频探测/转码失败或超时；支持 WAV、MP3、FLAC、OGG、AAC、AIFF') from None


def import_media(kind, value):
    initialize()
    with (ROOT / '.import.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise MediaError('另一个导入正在执行，请稍后重试') from None
        for stale in ROOT.glob('.import-*'):
            if stale.is_dir() and not stale.is_symlink():
                shutil.rmtree(stale)
        if sum(p.stat().st_size for p in ROOT.glob('*.wav')) > MAX_CACHE - MAX_SECONDS * 32000 - 4096:
            raise MediaError('音频缓存已满，请先删除不再使用的音频')
        identifier = uuid.uuid4().hex
        work = Path(tempfile.mkdtemp(prefix='.import-', dir=ROOT))
        try:
            source = work / 'source'
            if kind == 'local':
                copy_local(value, source)
                label = '本地: ' + value
            elif kind == 'upload':
                if not isinstance(value, dict) or not isinstance(value.get('data'), bytes) or not 0 < len(value['data']) <= MAX_BYTES:
                    raise MediaError('上传音频不能为空或超过 32 MiB')
                name = value.get('name', '')
                if not isinstance(name, str) or not 1 <= len(name) <= 200 or any(ord(c) < 32 for c in name) or '/' in name or chr(92) in name:
                    raise MediaError('文件名无效')
                source.write_bytes(value['data'])
                label = '上传: ' + name
            elif kind == 'url':
                label = download(value, source)
            else:
                raise MediaError('来源类型无效')
            duration = convert(source, work / 'audio.wav')
            destination = ROOT / (identifier + '.wav')
            os.replace(work / 'audio.wav', destination)
            destination.chmod(0o600)
            return {'id': identifier, 'source': label, 'duration': duration, 'bytes': destination.stat().st_size}
        finally:
            shutil.rmtree(work, ignore_errors=True)


def media_path(identifier):
    if not re.fullmatch('[a-f0-9]{32}', str(identifier)):
        raise MediaError('音频标识无效')
    return ROOT / (identifier + '.wav')
