import json,os,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import audio_channel_guard as g
class GuardTests(unittest.TestCase):
 def test_lease_authorization_scope_and_expiry(self):
  with tempfile.TemporaryDirectory() as d,patch.object(g,'MARKER',Path(d)/'lease'):
   body={'clientid':'OTHER','peerhost':'192.0.2.1','action':'publish','topic':'FMO/RAW'}
   self.assertEqual(g.decision(body),'ignore')
   value={'pid':os.getpid(),'clientid':'AI','until':time.time()+5};g.write_lease(value)
   self.assertEqual(g.decision(body),'deny')
   self.assertEqual(g.decision({**body,'action':'subscribe'}),'ignore')
   self.assertEqual(g.decision({**body,'topic':'other'}),'ignore')
   self.assertEqual(g.decision({**body,'clientid':'AI'}),'deny')
   self.assertEqual(g.decision({**body,'clientid':'AI','peerhost':'127.0.0.1'}),'ignore')
   value['until']=time.time()-1;g.write_lease(value);self.assertEqual(g.decision(body),'ignore')
 def test_dead_process_releases_channel(self):
  with tempfile.TemporaryDirectory() as d,patch.object(g,'MARKER',Path(d)/'lease'),patch.object(g.os,'kill',side_effect=ProcessLookupError()):
   g.write_lease({'pid':123,'clientid':'AI','until':time.time()+5});self.assertIsNone(g.active_lease())
 def test_broker_heartbeat_required_and_lease_released(self):
  with tempfile.TemporaryDirectory() as d,patch.object(g,'MARKER',Path(d)/'lease'),patch.object(g,'READY',Path(d)/'ready'):
   lease=g.Lease('AI')
   with self.assertRaises(RuntimeError):lease.refresh()
   self.assertFalse(g.MARKER.exists())
   g.READY.write_text(str(int(time.time()*1000)));lease.refresh();self.assertEqual(g.active_lease()['clientid'],'AI')
   lease.close();self.assertFalse(g.MARKER.exists())
