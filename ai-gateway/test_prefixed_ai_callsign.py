import unittest
from unittest.mock import patch
import os
import gateway
import control_state

class PrefixedAI(unittest.TestCase):
    def test_allowlist_and_blocklist(self):
        with patch.dict(os.environ, {'FMO_VOICE_CONTROL_CALLSIGN':'BG1ADM'}), patch.object(gateway,'enabled',return_value=True), patch.object(gateway,'allow_all_callsigns',return_value=False), patch.object(gateway,'allowed_callsigns',return_value={'BG1ADM'}), patch.object(gateway,'_provider_headers',return_value={}), patch.object(gateway.callsign_policy,'load_blacklist',return_value=set()):
            self.assertEqual(gateway.validate_input('x.bg1adm','你好'),('X.BG1ADM','你好'))
            with self.assertRaises(PermissionError): gateway.validate_input('BG1ABC','你好')
            with patch.object(gateway.callsign_policy,'load_blacklist',return_value={'X.BG1ADM'}):
                with self.assertRaises(PermissionError): gateway.validate_input('X.BG1ADM','你好')
    def test_admin_stem_privilege(self):
        with patch.object(control_state,'ADMIN_CALLSIGN','BG1ADM'):
            self.assertEqual(control_state.parse_voice_command('X.BG1ADM','我是机婶婶，我命令开启整点报时')['name'],'hourly_announcement')
