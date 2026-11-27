"""Shared bridge policy and short-lived hash metadata; never stores audio."""
import hashlib,json,os,sqlite3,time
from pathlib import Path
import cluster_state
DB=Path(os.getenv('FMO_CLUSTER_LEDGER','/var/lib/fmo-ai-gateway/cluster-ledger.sqlite'))
STATUS=Path(os.getenv('FMO_CLUSTER_STATUS','/var/lib/fmo-ai-gateway/cluster-runtime.json'))
SECRETS=Path(os.getenv('FMO_CLUSTER_SECRETS','/var/lib/fmo-ai-gateway/cluster-credentials.json'))
MAINTENANCE=Path(os.getenv('FMO_CLUSTER_MAINTENANCE','/var/lib/fmo-ai-gateway/cluster-maintenance'))
PTT=Path(os.getenv('FMO_CLUSTER_PTT','/run/fmo-ai-mqtt/ptt-active'))
MONITOR=Path(os.getenv('FMO_CLUSTER_MONITOR_STATUS','/run/fmo-ai-mqtt/status.json'))

def allows(cfg,kind,node):
    scope=cfg['scopes'].get(kind,{'mode':'local','targets':[]})
    return scope['mode']=='all' or (scope['mode']=='selected' and node in scope['targets'])

def secrets():
    if not SECRETS.exists():return {}
    return json.loads(SECRETS.read_text())

def store_credentials(body):
    if not isinstance(body,dict) or set(body)!={'node_id','username','password','shared_key'}:raise ValueError('凭据字段错误')
    import re
    node=body['node_id']
    with cluster_state.LOCK:
        if node not in [n['id'] for n in cluster_state.load()['nodes']]:raise ValueError('请先保存节点')
        for k in ('username','password'):
            if not isinstance(body[k],str) or not 1<=len(body[k])<=512 or any(ord(c)<32 for c in body[k]):raise ValueError('请填写专用 MQTT 用户名和密码')
        if not isinstance(body['shared_key'],str) or not re.fullmatch('[a-fA-F0-9]{64}',body['shared_key']):raise ValueError('双方共享校验密钥需为 64 位十六进制（32 字节随机数）')
        data=secrets();data[node]={k:body[k] for k in ('username','password','shared_key')}
        atomic_json(SECRETS,data)
    return {'ok':True,'message':'凭据已保存，不回显；连接状态由总开关与节点开关控制'}

def atomic_json(path,value):
    import tempfile
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.cluster-',dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w') as f:json.dump(value,f);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def metadata(payload,value=None):
    if value is None and not DB.exists():return None
    DB.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(DB,timeout=1) as db:
        os.chmod(DB,0o600)
        db.execute('CREATE TABLE IF NOT EXISTS frames (hash TEXT PRIMARY KEY, at REAL, info TEXT)')
        key=hashlib.sha256(payload).hexdigest();now=time.time()
        if value is not None:
            db.execute('DELETE FROM frames WHERE at < ?', (now-15,))
            db.execute('INSERT OR REPLACE INTO frames VALUES (?,?,?)',(key,now,json.dumps(value)))
            db.execute('DELETE FROM frames WHERE hash IN (SELECT hash FROM frames ORDER BY at DESC LIMIT -1 OFFSET 8192)')
        else:
            row=db.execute('SELECT info FROM frames WHERE hash=? AND at>?',(key,now-15)).fetchone()
            return json.loads(row[0]) if row else None

def incoming(payload):
    try:
        meta=metadata(payload)
        if meta and meta.get('origin')!='local' and meta.get('allow_ai'):
            cfg=cluster_state.load()
            meta['allow_ai']=cfg['bridge']['enabled'] and any(n['id']==meta['origin'] and n.get('enabled') for n in cfg['nodes']) and allows(cfg,'ai_trigger',meta['origin'])
        return meta
    except (OSError,ValueError,sqlite3.Error):return {'origin':'unavailable','allow_ai':False}

def publish(client,topic,payload,kind):
    if MAINTENANCE.exists():raise RuntimeError('voice maintenance')
    try:
        cfg=cluster_state.load()
        if cfg['bridge']['enabled']:metadata(payload,{'origin':'local','kind':kind})
    except (OSError,ValueError,sqlite3.Error):
        # Do not put unclassified robot output on a topic being bridged.
        raise RuntimeError('cluster classification unavailable') from None
    return client.publish(topic,payload,qos=0,retain=False)
