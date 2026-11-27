import importlib.util,sys,threading,unittest,urllib.request,urllib.error,json,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.append('/opt/fmo-ai-gateway')
spec=importlib.util.spec_from_file_location('cluster_test_server',Path(__file__).with_name('server.py'));web=importlib.util.module_from_spec(spec);spec.loader.exec_module(web)
class Tests(unittest.TestCase):
 def test_permissions_persistence_and_no_activation(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(web.cluster_state,'PATH',Path(tmp)/'state.json'),patch.object(web.cluster_runtime,'SECRETS',Path(tmp)/'secrets.json'),patch.object(web.cluster_runtime,'STATUS',Path(tmp)/'runtime.json'):
   server=web.ThreadingHTTPServer(('127.0.0.1',0),web.Handler);t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
   host='127.0.0.1:'+str(server.server_port)
   def req(method='GET',body=None,headers=None,path='cluster'):
    h={'Content-Type':'application/json',**(headers or {})}
    try:r=urllib.request.urlopen(urllib.request.Request('http://'+host+'/admin/'+path,data=json.dumps(body).encode() if body is not None else None,headers=h,method=method))
    except urllib.error.HTTPError as e:r=e
    with r:return r.status,json.load(r)
   admin={'X-FMO-Admin':'1'};write={**admin,'X-FMO-Cluster-Request':'1','Origin':'http://'+host}
   try:
    self.assertEqual(req()[0],403);code,s=req(headers=admin);self.assertEqual(code,200)
    body={'config':s['config'],'revision':s['revision']};body['config']['local']['name']='隔离测试'
    self.assertEqual(req('POST',body,admin)[0],403)
    self.assertEqual(req('POST',body,{**write,'Origin':'https://evil.test'})[0],403)
    code,out=req('POST',body,write);self.assertEqual(code,200);self.assertFalse(out['transport_enabled'])
    self.assertEqual(req('POST',body,write)[0],409)
    self.assertEqual(req(headers=admin)[1]['config']['local']['name'],'隔离测试')
    self.assertEqual(req('POST',{'enabled':True},write)[0],400)
    self.assertEqual(req('POST',{},admin,'cluster-credentials')[0],403)
    self.assertEqual(req('POST',{},write,'cluster-credentials')[0],400)
    self.assertFalse(web.cluster_runtime.SECRETS.exists())
   finally:server.shutdown();server.server_close();t.join()
if __name__=='__main__':unittest.main()
