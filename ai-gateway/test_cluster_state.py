import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import cluster_state as c

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.p=patch.object(c,'PATH',Path(self.tmp.name)/'state.json');self.p.start();self.addCleanup(self.p.stop)
 def save(self,config):return c.save({'config':config,'revision':c.snapshot()['revision']})
 def node(self):
  p=c.profile();p.update(name='对方测试台',host='example.test',contact='TEST')
  return {'id':'peer-a','profile':p,'enabled':False}
 def test_defaults_and_persistence(self):
  s=c.snapshot();self.assertFalse(s['transport_enabled']);self.assertTrue(all(x['mode']=='local' for x in s['config']['scopes'].values()))
  cfg=s['config'];cfg['local']['name']='测试台';self.save(cfg)
  self.assertEqual(c.load()['local']['name'],'测试台');self.assertEqual(c.PATH.stat().st_mode&0o777,0o600)
 def test_scopes_never_enable_connections(self):
  cfg=c.defaults();cfg['nodes']=[self.node()]
  cfg['scopes']['ai_reply']={'mode':'selected','targets':['peer-a']}
  cfg['scopes']['hourly_announcement']={'mode':'all','targets':[]}
  s=self.save(cfg);self.assertEqual(c.load(),cfg);self.assertFalse(s['transport_enabled'])
  self.assertEqual(s['node_states']['peer-a'],'waiting')
  self.assertTrue(all(x['mode']=='local' for x in s['effective_scopes'].values()))
 def test_conflict_does_not_overwrite(self):
  s=c.snapshot();cfg=copy.deepcopy(s['config']);cfg['local']['name']='管理员一';self.save(cfg)
  with self.assertRaises(c.Conflict):c.save({'config':s['config'],'revision':s['revision']})
  self.assertEqual(c.load()['local']['name'],'管理员一')
 def test_invalid_reference_and_removal(self):
  cfg=c.defaults();cfg['nodes']=[self.node()];cfg['scopes']['ai_reply']={'mode':'selected','targets':['peer-a']};self.save(cfg)
  cfg['nodes']=[]
  with self.assertRaises(ValueError):self.save(cfg)
  cfg['scopes']['ai_reply']={'mode':'local','targets':[]};self.save(cfg);self.assertEqual(c.load()['nodes'],[])
 def test_credentials_fields_and_bad_values_rejected(self):
  for key,value in [('password','secret'),('port',0),('tls','yes'),('host','mqtt://user:pass@example.test'),('topic','FMO/#'),('cert_fingerprint','bad'),('contact','PRIVATE KEY TEST MATERIAL')]:
   cfg=c.defaults();cfg['local'][key]=value
   with self.assertRaises(ValueError,msg=key):self.save(cfg)
  self.assertFalse(c.PATH.exists())
 def test_duplicates_and_empty_selected(self):
  cfg=c.defaults();n=self.node();cfg['nodes']=[n,copy.deepcopy(n)]
  with self.assertRaises(ValueError):self.save(cfg)
  cfg['nodes'][1]['id']='peer-b'
  with self.assertRaises(ValueError):self.save(cfg)
  cfg=c.defaults();cfg['scopes']['ai_reply']['mode']='selected'
  with self.assertRaises(ValueError):self.save(cfg)
 def test_corrupt_state_fails_closed(self):
  c.PATH.write_text('{broken')
  with self.assertRaises(ValueError):c.snapshot()
  with self.assertRaises(ValueError):c.save({'config':c.defaults(),'revision':'anything'})
  self.assertEqual(c.PATH.read_text(),'{broken')
 def test_failed_write_retains_previous(self):
  self.save(c.defaults());before=c.PATH.read_bytes();cfg=c.defaults();cfg['local']['name']='new'
  with patch.object(c.os,'replace',side_effect=OSError('disk error')):
   with self.assertRaises(OSError):self.save(cfg)
  self.assertEqual(c.PATH.read_bytes(),before)
if __name__=='__main__':unittest.main()
