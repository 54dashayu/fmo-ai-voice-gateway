import copy,json,os,socket,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import cluster_bridge as b
import cluster_runtime as rt
import cluster_state as cs
from test_passport_codec import packet,rad

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  for module,name in [(rt,'DB'),(rt,'STATUS'),(rt,'SECRETS'),(rt,'MAINTENANCE'),(rt,'PTT'),(cs,'PATH')]:
   p=patch.object(module,name,Path(self.tmp.name)/name);p.start();self.addCleanup(p.stop)
  self.cfg=cs.defaults();self.cfg['local']['uid']='1';self.cfg['bridge']['enabled']=True
  self.peer=cs.profile();self.peer.update(uid='2',host='localhost',tls=False,name='B',auth_method='dedicated')
  self.cfg['nodes']=[{'id':'b','profile':self.peer,'enabled':True}]
  for k in self.cfg['scopes']:self.cfg['scopes'][k]={'mode':'all','targets':[]}
  self.key={'b':{'username':'test','password':'test','shared_key':'ab'*32}}
  self.out=[];self.raw=[];self.now=1000.
  self.router=b.Router(self.cfg,self.key,lambda *args:self.out.append(args) or True,lambda p:self.raw.append(p) or True,clock=lambda:self.now)
  self.audio=packet([(2,rad(1))],uid=10,call='BH1JSS',vendor=0,stamp=100)
 def remote(self,kind='human_voice'):
  cfg=copy.deepcopy(self.cfg);cfg['local']['uid']='2';cfg['nodes'][0]['profile']['uid']='1'
  outgoing=[]
  r=b.Router(cfg,self.key,lambda *x:outgoing.append(x) or True,lambda _:True,clock=lambda:self.now)
  rt.metadata(self.audio,{'origin':'local','kind':kind})
  r.local(self.audio);return outgoing[0][1:]
 def test_disabled_no_forward(self):
  self.cfg['bridge']['enabled']=False;self.router.local(self.audio);self.assertEqual(self.out,[])
 def test_scopes_classification(self):
  self.cfg['scopes']['ai_reply']={'mode':'local','targets':[]}
  rt.metadata(self.audio,{'origin':'local','kind':'ai_reply'});self.router.local(self.audio);self.assertEqual(self.out,[])
  self.cfg['scopes']['ai_reply']={'mode':'selected','targets':['b']};self.router.local(self.audio);self.assertEqual(len(self.out),1)
  rt.metadata(self.audio,{'origin':'local','kind':'hourly_announcement'});self.cfg['scopes']['hourly_announcement']={'mode':'local','targets':[]};self.router.local(self.audio);self.assertEqual(len(self.out),1)
 def test_roundtrip_exact_bytes_and_no_relay(self):
  topic,msg=self.remote();self.assertTrue(self.router.inbound(topic,msg));self.assertEqual(self.raw,[self.audio])
  self.router.local(self.audio);self.assertEqual(self.out,[])
  self.assertFalse(self.router.inbound(topic,msg));self.assertEqual(len(self.raw),1)
 def test_remote_bot_never_triggers_ai(self):
  topic,msg=self.remote('ai_reply');self.assertTrue(self.router.inbound(topic,msg))
  self.assertFalse(rt.metadata(self.audio)['allow_ai'])
 def test_ai_trigger_separate_and_receiver_policy(self):
  topic,msg=self.remote();self.cfg['scopes']['ai_trigger']={'mode':'local','targets':[]}
  self.assertTrue(self.router.inbound(topic,msg));self.assertFalse(rt.metadata(self.audio)['allow_ai'])
  self.raw.clear();topic,msg=self.remote();self.cfg['scopes']['human_voice']={'mode':'local','targets':[]}
  self.assertFalse(self.router.inbound(topic,msg));self.assertEqual(self.raw,[])
 def test_tamper_stale_retained_wrong_topic(self):
  topic,msg=self.remote()
  self.assertFalse(self.router.inbound(topic,msg,True));self.assertFalse(self.router.inbound(topic+'x',msg))
  d=json.loads(msg);d['payload']=d['payload'][:-2]+'aa';self.assertFalse(self.router.inbound(topic,json.dumps(d).encode()))
  self.now+=4;self.assertFalse(self.router.inbound(topic,msg));self.assertEqual(self.raw,[])
 def test_channel_busy_drops_no_late_queue(self):
  topic,msg=self.remote();self.router.last_local=self.now;self.assertFalse(self.router.inbound(topic,msg));self.now+=2
  self.assertFalse(self.router.inbound(topic,msg));self.assertEqual(self.raw,[])
 def test_local_hook_records_before_publish(self):
  cs.save({'config':self.cfg,'revision':cs.snapshot()['revision']})
  client=Mock();client.publish.side_effect=lambda *a,**k:self.assertEqual(rt.metadata(a[1])['kind'],'hourly_announcement')
  rt.publish(client,'FMO/RAW',self.audio,'hourly_announcement');client.publish.assert_called_once()
 def test_credentials_not_returned(self):
  cs.save({'config':self.cfg,'revision':cs.snapshot()['revision']})
  out=rt.store_credentials({'node_id':'b',**self.key['b']})
  self.assertNotIn('password',out);s=cs.snapshot();self.assertTrue(s['credentials_ready']['b']);self.assertNotIn('ab'*32,json.dumps(s));self.assertEqual(rt.SECRETS.stat().st_mode&0o777,0o600)
 def test_no_socket_or_client_when_service_disabled(self):
  s=b.Service()
  with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')):
   s.configure(cs.defaults(),{});self.assertIsNone(s.local_client);self.assertEqual(s.clients,{})
 def test_migration_and_node_validation(self):
  d=cs.defaults();del d['bridge'];self.assertFalse(cs.validate(d)['bridge']['enabled'])
  self.cfg['nodes'][0]['profile']['tls']=None
  with self.assertRaises(ValueError):cs.validate(self.cfg)
if __name__=='__main__':unittest.main()
