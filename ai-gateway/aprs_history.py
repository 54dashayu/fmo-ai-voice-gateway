"""On-demand aprs.fi adapter. Disabled until a server-only API key is configured.

Call only for a user's active location query, not background collection.
Never log the request URL (it contains the credential).
"""
import json
import math
import os
import threading
import time
import urllib.parse
import urllib.request
import http.client
import socket
import ssl

_lock = threading.Lock()
_cache = {}
_retry_after = 0


def _request(query):
    headers = {'User-Agent': os.getenv('FMO_HTTP_USER_AGENT', 'FMO-AI-Voice-Gateway/0.2')}
    relay_port = os.getenv('FMO_APRS_FI_RELAY_PORT', '')
    if not relay_port:
        req = urllib.request.Request('https://api.aprs.fi/api/get?' + query, headers=headers)
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.loads(response.read(262145))
    # The relay only carries TCP. Authenticate aprs.fi's certificate and SNI here.
    conn = http.client.HTTPSConnection('api.aprs.fi', timeout=3)
    try:
        raw = socket.create_connection(('127.0.0.1', int(relay_port)), timeout=2)
        try:
            conn.sock = ssl.create_default_context().wrap_socket(raw, server_hostname='api.aprs.fi')
        except Exception:
            raw.close()
            raise
        conn.request('GET', '/api/get?' + query, headers=headers)
        response = conn.getresponse()
        if response.status != 200:
            raise ValueError('provider unavailable')
        return json.loads(response.read(262145))
    finally:
        conn.close()


def lookup(callsign):
    global _retry_after
    from aprs_positions import normalize_callsign

    call = normalize_callsign(callsign)
    key = os.getenv('FMO_APRS_FI_API_KEY', '').strip()
    allowed = set(os.getenv('FMO_APRS_FI_ALLOWED_CALLSIGNS', '').split(','))
    if os.getenv('FMO_APRS_FI_ENABLED', 'false').lower() != 'true':
        return None
    if not key or not call or call.split('-')[0] not in allowed:
        return None
    # Serialise requests, reuse recent results, and back off on provider failure.
    if not _lock.acquire(timeout=1.5):
        return None
    try:
        now = time.time()
        cached = _cache.get(call)
        if cached and cached[0] > now:
            return dict(cached[1]) if cached[1] else None
        if now < _retry_after:
            return None
        names = [call] if '-' in call else [call] + [f'{call}-{n}' for n in range(1, 16)]
        query = urllib.parse.urlencode({'name': ','.join(names), 'what': 'loc', 'apikey': key, 'format': 'json'})
        try:
            data = _request(query)
            if data.get('result') != 'ok':
                _retry_after = now + 60
                return None
            position = None
            for entry in data.get('entries', []):
                name = str(entry.get('name', '')).upper()
                if name not in names:
                    continue
                lat, lon = float(entry['lat']), float(entry['lng'])
                received = int(entry['lasttime'])
                if not (math.isfinite(lat) and math.isfinite(lon) and abs(lat) <= 90 and abs(lon) <= 180 and 0 < received <= now + 300):
                    continue
                if position and received <= position['receivedAt']:
                    continue
                position = {'callsign': name, 'latitude': lat, 'longitude': lon,
                            'receivedAt': received, 'source': 'aprs.fi'}
            if len(_cache) >= 1000:
                _cache.clear()
            _cache[call] = (now + 300, position)
            return dict(position) if position else None
        except (OSError, ValueError, TypeError, KeyError, http.client.HTTPException):
            # Do not expose provider exceptions or credential-bearing URLs.
            _retry_after = now + 60
            return None
    finally:
        _lock.release()
