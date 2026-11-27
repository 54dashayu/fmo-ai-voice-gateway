"""Cluster configuration with closed-by-default transport and node gates."""
import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path

PATH = Path(os.getenv('FMO_CLUSTER_STATE_PATH', '/var/lib/fmo-ai-gateway/cluster-management.json'))
LOCK = threading.RLock()
PROFILE_FIELDS = ('name', 'callsign', 'uid', 'host', 'port', 'tls', 'topic', 'cert_fingerprint', 'root_fingerprint', 'contact', 'auth_method', 'credential_exchange', 'bridge_identity', 'publish_topics', 'subscribe_topics', 'audio_formats')
KINDS = ('human_voice', 'ai_trigger', 'ai_reply', 'hourly_announcement', 'net_checkin')

class Conflict(ValueError): pass

def profile():
    return dict(name='', callsign='', uid='', host='', port=1883, tls=None, topic='FMO/RAW',
                cert_fingerprint='', root_fingerprint='', contact='', auth_method='pending', credential_exchange='', bridge_identity='', publish_topics='', subscribe_topics='', audio_formats='')

def defaults():
    return {'bridge':{'enabled':False}, 'local': profile(), 'nodes': [], 'scopes': {k: {'mode':'local', 'targets':[]} for k in KINDS}}

def text(value, limit=200):
    if not isinstance(value,str) or len(value)>limit or any(ord(c)<32 for c in value):
        raise ValueError('文本格式或长度不正确')
    if 'PRIVATE KEY' in value.upper() or '-----BEGIN' in value:
        raise ValueError('此处仅登记公开信息，不接收证书文件或私钥')
    return value.strip()

def clean_profile(p):
    if not isinstance(p,dict) or set(p)!=set(PROFILE_FIELDS):
        raise ValueError('接入信息字段不完整或包含不支持的字段；不要提交密码或私钥')
    out={k:text(v) for k,v in p.items() if k not in ('port','tls')}
    if type(p['port']) is not int or not 1<=p['port']<=65535:raise ValueError('端口应为 1–65535')
    if p['tls'] is not None and type(p['tls']) is not bool:raise ValueError('TLS 设置无效')
    out.update(port=p['port'],tls=p['tls'])
    if out['uid'] and (not out['uid'].isdigit() or len(out['uid'])>16):raise ValueError('UID 应为数字')
    if out['callsign'] and not re.fullmatch(r'[A-Za-z0-9./-]{1,32}',out['callsign']):raise ValueError('呼号格式错误')
    out['callsign']=out['callsign'].upper()
    if out['host'] and not re.fullmatch(r'[A-Za-z0-9.\-:]+',out['host']):raise ValueError('主机只填写域名或 IP，不含协议、路径或口令')
    if not out['topic'] or any(x in out['topic'] for x in ('#','+','\x00')):raise ValueError('语音主题应为具体主题，不能包含通配符')
    for k in ('cert_fingerprint','root_fingerprint'):
        if out[k] and not (re.fullmatch(r'[a-fA-F0-9]{64}',out[k]) or re.fullmatch(r'[A-Za-z0-9_-]{43}',out[k])):
            raise ValueError('指纹需为 SHA-256 十六进制或无填充 base64url，不是证书内容')
    if out['auth_method'] not in ('pending','dedicated','fmo_certificate'):raise ValueError('认证方式无效')
    return out

def validate(value):
    if not isinstance(value,dict) or set(value) not in ({'local','nodes','scopes'},{'bridge','local','nodes','scopes'}):raise ValueError('集群配置字段错误')
    bridge=value.get('bridge',{'enabled':False})
    if not isinstance(bridge,dict) or set(bridge)!={'enabled'} or type(bridge['enabled']) is not bool:raise ValueError('桥接总开关无效')
    local=clean_profile(value['local']);nodes=value['nodes'];scopes=value['scopes']
    if not isinstance(nodes,list) or len(nodes)>32:raise ValueError('最多登记 32 个节点')
    ids=set();endpoints=set();clean=[]
    for n in nodes:
        if not isinstance(n,dict) or set(n) not in ({'id','profile'},{'id','profile','enabled'}):raise ValueError('节点字段错误')
        id_=text(n['id'],64)
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',id_) or id_ in ids:raise ValueError('节点标识无效或重复')
        p=clean_profile(n['profile'])
        if not p['name']:raise ValueError('请填写对方服务器名称')
        if p['host']:
            endpoint=(p['host'].lower(),p['port'],p['topic'])
            if endpoint in endpoints:raise ValueError('相同地址、端口和主题已登记')
            endpoints.add(endpoint)
        enabled=n.get('enabled',False)
        if type(enabled) is not bool:raise ValueError('节点开关无效')
        if enabled and (not p['host'] or not p['uid'] or p['uid']=='0' or p['tls'] is None or p['auth_method']!='dedicated'):
            raise ValueError('启用节点需填写地址、UID、确认 TLS，并选择专用桥接身份认证')
        ids.add(id_);clean.append({'id':id_,'profile':p,'enabled':enabled})
    if not isinstance(scopes,dict) or set(scopes)!=set(KINDS):raise ValueError('范围设置不完整')
    result={}
    for k,s in scopes.items():
        if not isinstance(s,dict) or set(s)!= {'mode','targets'} or s['mode'] not in ('local','all','selected'):raise ValueError('范围选项无效')
        targets=s['targets']
        if not isinstance(targets,list) or any(not isinstance(t,str) or t not in ids for t in targets) or len(set(targets))!=len(targets):raise ValueError('范围引用了不存在或重复的节点')
        if s['mode']=='selected' and not targets:raise ValueError('指定服务器范围至少选择一个节点')
        if s['mode']!='selected' and targets:raise ValueError('只有指定范围可以携带节点列表')
        result[k]={'mode':s['mode'],'targets':targets}
    uids=[n['profile']['uid'] for n in clean if n['enabled']]
    if len(set(uids))!=len(uids) or local['uid'] in uids:raise ValueError('启用节点的 UID 必须唯一且不同于本服')
    if bridge['enabled'] and (not local['uid'] or local['uid']=='0'):raise ValueError('启用桥接需填写本服 UID')
    return {'bridge':bridge,'local':local,'nodes':clean,'scopes':result}

def load():
    if not PATH.exists():return defaults()
    return validate(json.loads(PATH.read_text(encoding='utf-8')))

def revision(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def snapshot():
    with LOCK:
        value=load()
        import cluster_runtime as runtime
        try:
            state=json.loads(runtime.STATUS.read_text())
            fresh=__import__('time').time()-state.get('at',0)<10 and state.get('revision')==revision(value)
        except (OSError,ValueError,TypeError):state={};fresh=False
        try:ready=set(runtime.secrets())
        except (OSError,ValueError):ready=set()
        running=value['bridge']['enabled'] and fresh and state.get('enabled',False)
        return {'config':value,'revision':revision(value),'transport_enabled':bool(running),
                'effective_scopes':value['scopes'] if running else {k:{'mode':'local','targets':[]} for k in KINDS},
                'runtime':state if fresh else {'state':'waiting','nodes':{}},
                'node_states':{n['id']:state.get('nodes',{}).get(n['id'],'disabled') if fresh else 'waiting' for n in value['nodes']},
                'credentials_ready':{n['id']:n['id'] in ready for n in value['nodes']}}

def save(body):
    if not isinstance(body,dict) or set(body)!= {'config','revision'}:raise ValueError('保存请求格式错误')
    value=validate(body['config'])
    with LOCK:
        if body['revision']!=revision(load()):raise Conflict('配置已被其他管理员更新，请重新加载后编辑')
        PATH.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix='.cluster-',dir=PATH.parent)
        try:
            os.fchmod(fd,0o600)
            with os.fdopen(fd,'w',encoding='utf-8') as f:
                json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
            os.replace(tmp,PATH)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
        return snapshot()
