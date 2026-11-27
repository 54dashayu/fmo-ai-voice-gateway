"""Exercise actual local HTTP admin boundary; never start the MQTT monitor."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
import server
import audio_library


class AudioHTTPTests(unittest.TestCase):
    def test_admin_csrf_validation_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(audio_library,'ROOT',Path(tmp)/'library'),patch.object(audio_library,'INBOX',Path(tmp)/'inbox'),patch.object(server,'MQTT_STATE_PATH',str(Path(tmp)/'missing')):
            http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            host='127.0.0.1:'+str(http.server_port)
            def request(body=None,headers=None,path="/admin/audio"):
                req=urllib.request.Request('http://'+host+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json',**(headers or {})})
                try:response=urllib.request.urlopen(req,timeout=3)
                except urllib.error.HTTPError as e:response=e
                with response:return response.status,json.load(response)
            admin={'X-FMO-Admin':'1'};write={**admin,'X-FMO-Audio-Request':'1','Origin':'http://'+host}
            try:
                self.assertEqual(request()[0],403)
                self.assertEqual(request(path='/admin/audio/catalog')[0],403)
                self.assertEqual(request(path='/admin/audio/gallery')[0],403)
                with patch.object(server.sstv_catalog,'listing',return_value={'items':[],'total':0}) as listing:
                    self.assertEqual(request(headers=admin,path='/admin/audio/catalog?offset=20&q=Robot')[0],200)
                    listing.assert_called_once_with('20','Robot',refresh=False)
                self.assertEqual(request({'action':'enable','enabled':True},admin)[0],403)
                self.assertEqual(request({'action':'enable','enabled':True},{**write,'Origin':'https://evil.test'})[0],403)
                self.assertEqual(request({'action':'enable','enabled':True},write)[0],200)
                code,state=request(headers=admin);self.assertEqual(code,200);self.assertTrue(state['enabled']);self.assertFalse(state['transmit_gate'])
                self.assertEqual(request({'action':'plan','asset':'missing'},write)[0],400)
                self.assertEqual(request({'action':'enable','enabled':'true'},write)[0],400)
                self.assertEqual(request({'action':'emergency_stop'},write)[0],200)
                self.assertFalse(request(headers=admin)[1]['enabled'])
            finally:http.shutdown();http.server_close();thread.join()

if __name__=='__main__':unittest.main()
