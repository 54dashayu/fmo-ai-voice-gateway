"""Real paho clients and two bridge subprocesses against isolated MQTT simulators.
All sockets are loopback ephemeral ports. No live broker, speech API or radio.
"""
import json,os,socket,struct,subprocess,sys,tempfile,threading,time,unittest
from pathlib import Path
import paho.mqtt.client as mqtt
import cluster_state as cs
import cluster_runtime as rt
from test_passport_codec import packet,rad

def read(c,n):
 data=b''
 while len(data)<n:
  x=c.recv(n-len(data))
  if not x:raise EOFError()
  data+=x
 return data

def wire(first,data):
 n=len(data);out=bytearray([first])
 while True:
  v=n%128;n//=128;out.append(v|(128 if n else 0))
  if not n:break
 return bytes(out)+data

def match(pattern,name):
 a=pattern.split('/');b=name.split('/')
 return len(a)==len(b) and all(x=='+' or x==y for x,y in zip(a,b))

class Broker:
 def __init__(self):
  self.sock=socket.socket();self.sock.bind(('127.0.0.1',0));self.sock.listen();self.port=self.sock.getsockname()[1]
  self.clients={};self.lock=threading.RLock();self.history=[];self.done=False
  threading.Thread(target=self.accept,daemon=True).start()
 def accept(self):
  while not self.done:
   try:c,_=self.sock.accept()
   except OSError:return
   with self.lock:self.clients[c]=[]
   threading.Thread(target=self.run,args=(c,),daemon=True).start()
 def run(self,c):
  try:
   while not self.done:
    first=read(c,1)[0];size=0;mult=1
    while True:
     n=read(c,1)[0];size+=(n&127)*mult;mult*=128
     if not n&128:break
    data=read(c,size);kind=first>>4
    if kind==1:c.sendall(b'\x20\x02\x00\x00')
    elif kind==8:
     pos=2;ack=b''
     while pos<len(data):
      length=int.from_bytes(data[pos:pos+2],'big');pos+=2;topic=data[pos:pos+length].decode();pos+=length+1
      with self.lock:self.clients[c].append(topic)
      ack+=b'\x00'
     c.sendall(wire(0x90,data[:2]+ack))
    elif kind==3:
     length=int.from_bytes(data[:2],'big');name=data[2:2+length].decode();payload=data[2+length:]
     with self.lock:
      self.history.append((name,payload))
      for dest,subs in list(self.clients.items()):
       if any(match(p,name) for p in subs):
        try:dest.sendall(wire(0x30,data))
        except OSError:pass
    elif kind==12:c.sendall(b'\xd0\x00')
    elif kind==14:return
  except (EOFError,OSError):pass
  finally:
   with self.lock:self.clients.pop(c,None)
   c.close()
 def close(self):
  self.done=True;self.sock.close()
  with self.lock:
   for c in list(self.clients):
    try:c.shutdown(socket.SHUT_RDWR)
    except OSError:pass

def wait(fn,timeout=8):
 until=time.time()+timeout
 while time.time()<until:
  if fn():return
  time.sleep(.05)
 raise AssertionError('condition timed out')

class WireTests(unittest.TestCase):
 def test_bidirectional_real_clients_scopes_stop_and_no_echo(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);brokers=[Broker(),Broker()];processes=[];clients=[];heard=[[],[]]
   try:
    for i in range(2):
     p=root/str(i);p.mkdir();cfg=cs.defaults();cfg['local']['uid']=str(i+1);cfg['bridge']['enabled']=True
     peer=cs.profile();peer.update(name='isolated peer',uid=str(2-i),host='127.0.0.1',port=brokers[1-i].port,tls=False,auth_method='dedicated')
     cfg['nodes']=[{'id':'peer','profile':peer,'enabled':True}]
     cfg['scopes']['human_voice']={'mode':'all','targets':[]}
     (p/'config').write_text(json.dumps(cfg));(p/'monitor').write_text('{"cluster_hook_version":1}')
     (p/'secrets').write_text(json.dumps({'peer':{'username':'isolated','password':'isolated','shared_key':'ac'*32}}))
     env={**os.environ,'FMO_CLUSTER_STATE_PATH':str(p/'config'),'FMO_CLUSTER_SECRETS':str(p/'secrets'),'FMO_CLUSTER_LEDGER':str(p/'ledger'),'FMO_CLUSTER_STATUS':str(p/'status'),'FMO_CLUSTER_MONITOR_STATUS':str(p/'monitor'),'FMO_CLUSTER_PTT':str(p/'ptt'),'FMO_CLUSTER_MAINTENANCE':str(p/'maintenance'),'FMO_GATEWAY_CONFIG_PATH':str(p/'no-gateway-config'),'FMO_MQTT_HOST':'127.0.0.1','FMO_MQTT_PORT':str(brokers[i].port),'FMO_MQTT_TOPIC':'FMO/RAW'}
     processes.append(subprocess.Popen([sys.executable,'cluster_bridge.py'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE))
     c=mqtt.Client(client_id='wire-test-'+str(i));c.on_message=lambda c,u,m,i=i:heard[i].append(bytes(m.payload))
     c.on_connect=lambda c,u,f,rc:c.subscribe('FMO/RAW',0)
     c.connect('127.0.0.1',brokers[i].port);c.loop_start();clients.append(c)
    def ready():
     for i in range(2):
      try:s=json.loads((root/str(i)/'status').read_text())
      except (OSError,ValueError):return False
      if s.get('nodes',{}).get('peer')!='connected' or s.get('local')!='connected':return False
     return True
    wait(ready)
    a=packet([(2,rad(1))],uid=10,call='BH1JSS',vendor=0,stamp=100)
    clients[0].publish('FMO/RAW',a);wait(lambda:a in heard[1]);time.sleep(.2)
    self.assertEqual(heard[0].count(a),1);self.assertEqual(heard[1].count(a),1)
    time.sleep(1.1)
    b=packet([(2,rad(2))],uid=11,call='BG5ESN',vendor=0,stamp=200)
    clients[1].publish('FMO/RAW',b);wait(lambda:b in heard[0]);self.assertEqual(heard[1].count(b),1)
    # Mark robot output locally; ai_reply=local must prevent cross-server output.
    from unittest.mock import patch
    bot=packet([(2,rad(3))],uid=65535,call='AI-TEST',vendor=0,stamp=300)
    with patch.object(rt,'DB',root/'0'/'ledger'):rt.metadata(bot,{'origin':'local','kind':'ai_reply'})
    clients[0].publish('FMO/RAW',bot);time.sleep(.3);self.assertNotIn(bot,heard[1])
    # Disable actual daemon, wait for its sockets to close; no replay after disable.
    p=root/'0';cfg=json.loads((p/'config').read_text());cfg['bridge']['enabled']=False;rt.atomic_json(p/'config',cfg)
    wait(lambda:json.loads((p/'status').read_text()).get('state')=='disabled')
    last=packet([(2,rad(4))],uid=10,call='BH1JSS',vendor=0,stamp=400)
    clients[0].publish('FMO/RAW',last);time.sleep(.2);self.assertNotIn(last,heard[1])
   finally:
    for c in clients:c.disconnect();c.loop_stop()
    for p in processes:
     p.terminate()
     try:p.communicate(timeout=5)
     except subprocess.TimeoutExpired:p.kill();p.communicate()
    for b in brokers:b.close()
if __name__=='__main__':unittest.main()
