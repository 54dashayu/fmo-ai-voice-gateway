import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import mqtt_monitor as m
from test_passport_codec import packet,rad

class MonitorTests(unittest.TestCase):
 def test_remote_bot_counts_busy_without_asr_or_reply(self):
  with patch.object(m.callsign_policy,'load_stats',return_value={}),patch.object(m.Monitor,'_load_hourly_state',return_value={}):monitor=m.Monitor()
  p=packet([(2,rad(1))],uid=65535,call='AI-TEST',stamp=100)
  with patch.object(m.cluster_runtime,'incoming',return_value={'origin':'peer','kind':'ai_reply','allow_ai':False}):monitor.receive(p)
  self.assertEqual(monitor.active_frames,0);self.assertEqual(monitor.activity_generation,1);self.assertGreater(monitor.channel_last_activity,0)
 def test_remote_human_scope_block_still_busy(self):
  with patch.object(m.callsign_policy,'load_stats',return_value={}),patch.object(m.Monitor,'_load_hourly_state',return_value={}):monitor=m.Monitor()
  p=packet([(2,rad(1))],uid=55,call='BH1JSS',stamp=100)
  with patch.object(m.cluster_runtime,'incoming',return_value={'origin':'peer','kind':'human_voice','allow_ai':False}):monitor.receive(p)
  self.assertEqual(monitor.active_frames,0);self.assertEqual(monitor.activity_generation,1)
 def test_allowed_remote_human_enters_existing_voice_pipeline(self):
  with patch.object(m.callsign_policy,'load_stats',return_value={}),patch.object(m.Monitor,'_load_hourly_state',return_value={}):monitor=m.Monitor()
  p=packet([(2,rad(1))],uid=55,call='BH1JSS',stamp=100)
  with patch.object(m.cluster_runtime,'incoming',return_value={'origin':'peer','kind':'human_voice','allow_ai':True}),patch.object(m.callsign_policy,'load_blacklist',return_value=set()),patch.object(m.callsign_policy,'save_stats'),patch.object(m,'NET_CHECKIN_RUNTIME_ENABLED',False):monitor.receive(p)
  self.assertEqual(monitor.active_frames,1);self.assertEqual(monitor.active_payloads,[p])
 def test_local_publish_path_classifies_all_five_output_sites(self):
  import ast
  tree=ast.parse(Path('mqtt_monitor.py').read_text())
  hooks=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id=='cluster_runtime' and n.func.attr=='publish']
  self.assertEqual(len(hooks),5)
  self.assertEqual([n.args[3].value for n in hooks if isinstance(n.args[3],ast.Constant)].count('hourly_announcement'),1)
if __name__=='__main__':unittest.main()
