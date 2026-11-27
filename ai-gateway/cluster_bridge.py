#!/usr/bin/env python3
"""FMO one-hop federation v1. Disabled means no MQTT clients or connections.
Both servers run this adapter; never bridge FMO/RAW directly in both directions.
"""
import base64,hashlib,hmac,json,os,queue,re,signal,threading,time,uuid
from pathlib import Path
import cluster_state as state
import cluster_runtime as rt
import fmo_codec

KINDS={'human_voice','ai_reply','hourly_announcement','net_checkin'}
MAX_AUDIO=2048

def canonical(d):return json.dumps(d,sort_keys=True,separators=(',',':')).encode()
def topic(target,origin):return 'FMO/CLUSTER/v1/'+target+'/in/'+origin

def packet(payload):
    if not isinstance(payload,bytes) or not 64<len(payload)<=MAX_AUDIO:raise ValueError('packet size')
    identity,base,_,_=fmo_codec.parse_packet(payload)
    return str(identity[1])+':'+str(base)

class Router:
    def __init__(self,cfg,credentials,send_peer,send_local,clock=time.time):
        self.cfg=cfg;self.credentials=credentials;self.send_peer=send_peer;self.send_local=send_local;self.clock=clock
        self.uid=cfg['local']['uid'];self.nodes={n['id']:n for n in cfg['nodes'] if n.get('enabled') and n['id'] in credentials}
        self.seen={};self.blocked={};self.last_local=-100;self.remote_owner=None;self.remote_at=-100;self.counts={'sent':0,'received':0,'dropped':0}
    def active(self):return self.cfg['bridge']['enabled'] and bool(self.uid)
    def local(self,payload):
        if not self.active():return
        packet(payload)
        meta=rt.metadata(payload)
        if meta and meta.get('origin')!='local':return # Never relay imported packets.
        self.last_local=self.clock()
        identity=fmo_codec.parse_packet(payload)[0]
        if not meta and (identity[1] in (0,65535) or identity[2].startswith('AI-')):return
        kind=meta.get('kind') if meta else 'human_voice'
        if kind not in KINDS:return # Unclassified system/music/control stays local.
        for id_,n in self.nodes.items():
            if not rt.allows(self.cfg,kind,id_):continue
            msg={'v':1,'id':uuid.uuid4().hex,'origin':self.uid,'target':n['profile']['uid'],'kind':kind,'at':self.clock(),'payload':base64.b64encode(payload).decode()}
            msg['mac']=hmac.new(bytes.fromhex(self.credentials[id_]['shared_key']),canonical(msg),hashlib.sha256).hexdigest()
            if self.send_peer(id_,topic(msg['target'],self.uid),canonical(msg)):self.counts['sent']+=1
    def inbound(self,mqtt_topic,raw,retained=False):
        if not self.active() or retained or len(raw)>4096:return False
        try:
            msg=json.loads(raw)
            if not isinstance(msg,dict) or set(msg)!={'v','id','origin','target','kind','at','payload','mac'}:return False
            n=next((n for n in self.nodes.values() if n['profile']['uid']==msg['origin']),None)
            if not n or msg['target']!=self.uid or mqtt_topic!=topic(self.uid,msg['origin']):return False
            if msg['v']!=1 or msg['kind'] not in KINDS or not isinstance(msg['id'],str) or not re.fullmatch('[a-f0-9]{32}',msg['id']):return False
            if type(msg['at']) not in (float,int) or not abs(self.clock()-msg['at'])<=3:return False
            mac=msg.pop('mac');expected=hmac.new(bytes.fromhex(self.credentials[n['id']]['shared_key']),canonical(msg),hashlib.sha256).hexdigest()
            if not isinstance(mac,str) or not hmac.compare_digest(mac,expected):return False
            if not rt.allows(self.cfg,msg['kind'],n['id']):return False
            self.seen={k:t for k,t in self.seen.items() if self.clock()-t<10}
            key=(msg['origin'],msg['id'])
            if key in self.seen or len(self.seen)>=4096:return False
            payload=base64.b64decode(msg['payload'],validate=True);stream=packet(payload)
            # Authenticate and validate before keeping any replay state.
            self.seen[key]=self.clock()
            owner=(msg['origin'],stream)
            self.blocked={k:t for k,t in self.blocked.items() if self.clock()-t<90}
            if owner in self.blocked:return False
            if self.clock()-self.last_local<1 or (self.remote_owner!=owner and self.clock()-self.remote_at<1) or rt.MAINTENANCE.exists() or rt.PTT.exists():
                if len(self.blocked)<1024:self.blocked[owner]=self.clock()
                return False
            self.remote_owner=owner;self.remote_at=self.clock()
            rt.metadata(payload,{'origin':n['id'],'kind':msg['kind'],'allow_ai':msg['kind']=='human_voice' and rt.allows(self.cfg,'ai_trigger',n['id'])})
            if not self.send_local(payload):return False
            self.counts['received']+=1
            return True
        except (ValueError,TypeError,KeyError,StopIteration,OverflowError):return False

class Service:
    def __init__(self):
        self.stop=False;self.clients={};self.local_client=None;self.events=queue.Queue(maxsize=256);self.router=None;self.states={};self.version=None
    def close(self):
        for c in list(self.clients.values())+([self.local_client] if self.local_client else []):
            c.disconnect();c.loop_stop()
        self.clients={};self.local_client=None;self.router=None;self.states={}
        while not self.events.empty():
            try:self.events.get_nowait()
            except queue.Empty:break
    def enqueue(self,client,userdata,msg):
        if len(msg.payload)>4096:return
        try:self.events.put_nowait((time.time(),msg.topic,bytes(msg.payload),msg.retain))
        except queue.Full:pass
    def configure(self,cfg,credentials):
        self.close()
        if not cfg['bridge']['enabled']:return
        # The running voice process must understand origin/classification metadata.
        status=json.loads(rt.MONITOR.read_text())
        if status.get('cluster_hook_version')!=1 or time.time()-rt.MONITOR.stat().st_mtime>15:raise RuntimeError('monitor_not_ready')
        import paho.mqtt.client as mqtt
        import gateway_config
        local=gateway_config.selected_mqtt_server()
        # Same server MQTT connection parameters as the voice gateway, different client ID.
        c=mqtt.Client(client_id='FMO-BRIDGE-LOCAL-'+cfg['local']['uid'],clean_session=True)
        if local.get('username'):c.username_pw_set(local['username'],local.get('password'))
        tls=local.get('tls') or {}
        if tls.get('enabled'):c.tls_set(ca_certs=tls.get('ca_file'),certfile=tls.get('cert_file'),keyfile=tls.get('key_file'))
        def connected(client,userdata,flags,rc):
            self.states['local']='connected' if rc==0 else 'auth_failed'
            if rc==0:
                client.subscribe([(local['topic'],0),('FMO/CLUSTER/v1/'+cfg['local']['uid']+'/in/+',0)])
        c.on_connect=connected;c.on_message=self.enqueue
        c.on_disconnect=lambda *args:self.states.update(local='disconnected')
        self.local_client=c
        self.router=Router(cfg,credentials,self.peer_send,lambda p:self.raw_send(local['topic'],p))
        for id_,node in self.router.nodes.items():
            p=node['profile'];secret=credentials[id_]
            remote=mqtt.Client(client_id=p['bridge_identity'] or 'FMO-BRIDGE-'+cfg['local']['uid']+'-'+p['uid'],clean_session=True)
            remote.username_pw_set(secret['username'],secret['password'])
            if p['tls']:remote.tls_set() # OS trust store, hostname verification remains enabled.
            remote.reconnect_delay_set(min_delay=2,max_delay=30)
            remote.on_connect=lambda client,u,f,rc,id_=id_:self.states.update({id_:'connected' if rc==0 else 'auth_failed'})
            remote.on_disconnect=lambda *args,id_=id_:self.states.update({id_:'disconnected'})
            self.clients[id_]=remote;self.states[id_]='connecting'
            remote.connect_async(p['host'],p['port'],keepalive=20);remote.loop_start()
        c.connect_async(local['host'],local['port'],keepalive=20);c.loop_start()
    def peer_send(self,id_,name,payload):
        c=self.clients.get(id_)
        if not c or not c.is_connected():return False
        return c.publish(name,payload,qos=0,retain=False).rc==0
    def raw_send(self,name,payload):
        c=self.local_client
        return bool(c and c.is_connected() and c.publish(name,payload,qos=0,retain=False).rc==0)
    def run(self):
        while not self.stop:
            try:
                cfg=state.load();credentials=rt.secrets();version=(state.revision(cfg),hashlib.sha256(canonical(credentials)).hexdigest())
                if cfg['bridge']['enabled'] and (not rt.MONITOR.exists() or time.time()-rt.MONITOR.stat().st_mtime>15):raise RuntimeError('monitor_not_ready')
                if version!=self.version:
                    self.configure(cfg,credentials);self.version=version
                # Re-read config at least once per loop, including while traffic arrives.
                try:
                    at,name,payload,retained=self.events.get(timeout=.1)
                    if self.router and time.time()-at<1 and not retained:
                        if name.startswith('FMO/CLUSTER/'):
                            if not self.router.inbound(name,payload,retained):self.router.counts['dropped']+=1
                        else:self.router.local(payload)
                except queue.Empty:pass
                except (ValueError,TypeError,KeyError):pass
                now=time.time()
                if now-getattr(self,'last_status',0)>1:
                    nodes={n['id']:(self.states.get(n['id'],'waiting') if n.get('enabled') and n['id'] in credentials else 'credentials_missing' if n.get('enabled') else 'disabled') for n in cfg['nodes']}
                    rt.atomic_json(rt.STATUS,{'at':now,'revision':state.revision(cfg),'enabled':bool(self.router),'state':'enabled' if self.router else 'disabled','local':self.states.get('local','disabled'),'nodes':nodes,'counts':self.router.counts if self.router else {}})
                    self.last_status=now
            except Exception:
                self.close();self.version=None
                rt.atomic_json(rt.STATUS,{'at':time.time(),'revision':state.revision(cfg) if 'cfg' in locals() else '', 'enabled':False,'state':'configuration_error','nodes':{}})
                time.sleep(1)
        self.close()

if __name__=='__main__':
    os.umask(0o077)
    service=Service()
    def stop(*args):service.stop=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    service.run()
