"""Read-only SSTV V1 adapter for an explicitly configured public catalog."""
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.parse import urlencode
import audio_library as media

BASE = os.getenv('FMO_SSTV_CATALOG_API_URL', '').rstrip('/')
FILES = os.getenv('FMO_SSTV_CATALOG_FILES_URL', '').rstrip('/') + '/'
_lock = threading.Lock()
_cache = {}


def fetch(suffix):
    if not BASE or FILES == '/':
        raise media.MediaError('SSTV 公共图库未配置')
    with tempfile.TemporaryDirectory(prefix='fmo-sstv-catalog-') as directory:
        target = Path(directory)/'catalog.json'
        media.download(BASE + suffix, target, max_bytes=2*1024*1024)
        try:
            data = json.loads(target.read_bytes())
            if data.get('apiVersion') != '1':
                raise ValueError()
            return data
        except (ValueError, TypeError, AttributeError):
            raise media.MediaError('SSTV 声音库响应格式不兼容') from None


def clean(item):
    if not isinstance(item, dict) or not re.fullmatch('[a-zA-Z0-9_-]{1,100}', str(item.get('id',''))):
        raise media.MediaError('SSTV 素材标识无效')
    output = {key: item.get(key) for key in ('id','title','mode','duration','wavBytes','wavUrl','imageUrl')}
    for key, folder, extension in [('wavUrl','audio','wav'),('imageUrl','images','png')]:
        if not re.fullmatch(re.escape(FILES + folder + '/') + r'[A-Za-z0-9_-]+\.' + extension, str(output[key])):
            raise media.MediaError('SSTV 素材文件地址无效')
    output['title'] = str(output['title'])[:120]
    return output


def listing(offset=0, query='', refresh=False):
    try:
        offset = int(offset)
        if not 0 <= offset <= 10000:
            raise ValueError()
    except (ValueError, TypeError):
        raise media.MediaError('目录页码无效') from None
    query = str(query)[:80]
    key = (offset, query)
    requested_at = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached and ((not refresh and time.monotonic() - cached[0] < 60) or cached[0] >= requested_at):
            return cached[1]
        data = fetch('?' + urlencode({'limit':20, 'offset':offset, 'q':query}))
        try:
            result = {'items':[clean(item) for item in data['items'][:20]], 'total':int(data['total']), 'offset':offset, 'limit':20}
        except (KeyError, TypeError, ValueError):
            raise media.MediaError('SSTV 声音库目录格式无效') from None
        if len(_cache) >= 32:
            _cache.clear()
        _cache[key] = (time.monotonic(), result)
        return result


def import_item(identifier):
    if not re.fullmatch('[a-zA-Z0-9_-]{1,100}', str(identifier)):
        raise media.MediaError('SSTV 素材标识无效')
    item = clean(fetch('/' + identifier).get('item'))
    asset = media.import_media('url', item['wavUrl'])
    asset.update(source='SSTV: ' + item['title'], source_id=item['id'], source_url=item['wavUrl'], mode=item['mode'])
    return asset
