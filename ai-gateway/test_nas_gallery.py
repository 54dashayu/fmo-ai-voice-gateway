import json,tempfile,unittest,wave,struct,zlib
from unittest.mock import Mock
from pathlib import Path
from unittest.mock import patch
import nas_gallery as g
def png(w=1,h=1):
 def chunk(k,b):return struct.pack('>I',len(b))+k+b+struct.pack('>I',zlib.crc32(k+b))
 return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0\xff\0\0'))+chunk(b'IEND',b'')
class GalleryTests(unittest.TestCase):
 def test_validation(self):
  for count in [0,11,True,'3']:
   with self.assertRaises(ValueError):g.validate({'directory':'a'*64,'count':count})
 def test_random_and_cleanup(self):
  entries=[{'id':str(i)*64,'name':str(i)} for i in range(5)]
  def fetch(url,*args):return json.dumps({'images':entries}).encode() if '/images?' in url else b'image'
  with patch.object(g,'fetch',side_effect=fetch),patch.object(g,'encode',side_effect=lambda a,b,c:b.write_bytes(b'wav')):
   temp,paths,selected=g.prepare({'directory':'a'*64,'count':3},lambda:None)
   self.assertEqual(len(paths),3);root=Path(temp.name);temp.cleanup();self.assertFalse(root.exists())
 def test_shortage(self):
  with patch.object(g,'fetch',return_value=b'{"images":[]}'),patch.object(g,'encode') as enc:
   with self.assertRaises(ValueError):g.prepare({'directory':'a'*64,'count':3},lambda:None)
   enc.assert_not_called()
 def test_encoder(self):
  with tempfile.TemporaryDirectory() as d:
   src=Path(d)/'in.ppm';out=Path(d)/'out.wav';src.write_bytes(png());g.encode(src,out,lambda:None)
   with wave.open(str(out)) as w:
    self.assertEqual((w.getnchannels(),w.getsampwidth(),w.getframerate()),(1,2,16000));self.assertAlmostEqual(w.getnframes()/16000,36.91,places=3)

 def test_malicious_and_oversized_rejected_before_decoder(self):
  with tempfile.TemporaryDirectory() as d,patch.object(g.subprocess,'Popen') as spawn:
   src=Path(d)/'fake.png'
   for payload in [b'#EXTM3U\nhttp://example.invalid/private',b'ffconcat version 1.0\nfile /etc/passwd',png(10000,10000)]:
    src.write_bytes(payload)
    with self.assertRaises(g.GalleryError):g.encode(src,Path(d)/'out',lambda:None)
   spawn.assert_not_called()
 def test_cancellation_kills_decoder(self):
  with tempfile.TemporaryDirectory() as d:
   src=Path(d)/'image';src.write_bytes(png());proc=Mock();proc.poll.return_value=None
   check=Mock(side_effect=[None,RuntimeError('cancelled')])
   with patch.object(g.subprocess,'Popen',return_value=proc) as spawn:
    with self.assertRaises(RuntimeError):g.encode(src,Path(d)/'out',check)
   proc.kill.assert_called_once();proc.wait.assert_called_once()
   args=spawn.call_args.args[0];self.assertIn('image2pipe',args);self.assertIn('file,pipe',args);self.assertIn('png',args)
