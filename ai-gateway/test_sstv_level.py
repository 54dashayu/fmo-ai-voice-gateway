import array,math,unittest
import sstv_level as level
import fmo_codec
class LevelTests(unittest.TestCase):
 def test_scale_length_frequency_and_zero(self):
  pcm=array.array('h',(round(30000*math.sin(2*math.pi*1900*i/16000)) for i in range(16000))).tobytes()
  out=level.scale(pcm,-12);self.assertEqual(len(out),len(pcm));self.assertEqual(level.scale(pcm,0),pcm)
  a=array.array('h');a.frombytes(pcm);b=array.array('h');b.frombytes(out)
  ratio=(sum(x*x for x in b)/sum(x*x for x in a))**.5
  self.assertAlmostEqual(ratio,10**(-12/20),places=5)
  self.assertEqual(sum(a[i]<0<=a[i+1] for i in range(len(a)-1)),sum(b[i]<0<=b[i+1] for i in range(len(b)-1)))
 def test_codec_roundtrip(self):
  pcm=array.array('h',(round(30000*math.sin(2*math.pi*1900*i/16000)) for i in range(12800))).tobytes()
  def rms(db):
   packets,_=fmo_codec.build_radpcm_payloads(level.scale(pcm,db),base=100,frames_per_packet=1)
   frames,_=fmo_codec.voice_frames(packets,65535,'AI-FMO')
   decoded=fmo_codec.decode_radpcm_pcm([p for codec,p in frames]);a=array.array('h');a.frombytes(decoded);return (sum(x*x for x in a)/len(a))**.5
  self.assertAlmostEqual(20*math.log10(rms(-12)/rms(0)),-12,delta=.3)
 def test_validation_and_types(self):
  for value in [True,-25,1,-12.5,'-12']:
   with self.assertRaises(ValueError):level.validate(value)
  self.assertFalse(level.is_sstv({'source':'本地: Robot36.wav'}));self.assertTrue(level.is_sstv({'source':'SSTV: 图','source_id':'abc'}));self.assertFalse(level.is_sstv({'source':'SSTV: 图','source_id':'abc','is_sstv':False}))
