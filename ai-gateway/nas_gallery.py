"""Authenticated NAS image selection and bounded offline Robot36 preparation."""
import array
import json
import math
import random
import re
import subprocess
import tempfile
import time
import urllib.request
import wave
from pathlib import Path
import gateway_config

class GalleryError(ValueError):pass

MAX_IMAGE = 32 * 1024 * 1024

def validate(value):
    if not isinstance(value,dict) or not re.fullmatch('[0-9a-f]{64}',str(value.get('directory',''))):
        raise GalleryError('请选择 NAS 图库目录')
    if type(value.get('count')) is not int or not 1 <= value['count'] <= 10:
        raise GalleryError('随机图片张数请选择 1–10')
    if type(value.get('recursive',False)) is not bool:raise GalleryError('子目录选项无效')
    return {'directory':value['directory'],'count':value['count'],'recursive':value.get('recursive',False)}

def fetch(path, limit, check=lambda:None):
    config=gateway_config.knowledge()
    if not config.get('token'):raise GalleryError('NAS 图库鉴权尚未配置')
    req=urllib.request.Request(str(config['base_url']).rstrip('/')+path,headers={'Authorization':'Bearer '+str(config['token'])})
    try:
        check()
        deadline=time.monotonic()+30
        with urllib.request.urlopen(req,timeout=2) as response:
            data=bytearray()
            while True:
                check()
                if time.monotonic()>deadline:raise GalleryError('NAS 图库读取超时')
                chunk=response.read(min(65536,limit+1-len(data)))
                if not chunk:break
                data.extend(chunk)
                if len(data)>limit:raise GalleryError('NAS 图库文件超过限制')
        check();return bytes(data)
    except (OSError,ValueError) as exc:
        raise GalleryError('NAS 图库读取失败，请检查连接、目录或文件大小') from exc

def directories():
    return json.loads(fetch('/v1/gallery/directories',2*1024*1024))

def image_format(source):
    """Inspect only raster headers before starting a decoder; no URL/container sniffing."""
    data=source.read_bytes()
    if not 0<len(data)<=MAX_IMAGE:raise GalleryError('图片大小无效')
    codec=None;width=height=0
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data)>=33 and data[8:16]==b'\0\0\0\rIHDR':
        codec='png';width=int.from_bytes(data[16:20],'big');height=int.from_bytes(data[20:24],'big')
    elif data.startswith(b'\xff\xd8'):
        codec='mjpeg';pos=2
        while pos+4<=len(data):
            if data[pos]!=255:break
            while pos<len(data) and data[pos]==255:pos+=1
            if pos>=len(data):break
            marker=data[pos];pos+=1
            if marker in (0xd9,0xda):break
            if marker==1 or 0xd0<=marker<=0xd7:continue
            if pos+2>len(data):break
            size=int.from_bytes(data[pos:pos+2],'big')
            if size<2 or pos+size>len(data):break
            if marker in (0xc0,0xc1,0xc2):
                if size<8:break
                height=int.from_bytes(data[pos+3:pos+5],'big');width=int.from_bytes(data[pos+5:pos+7],'big');break
            pos+=size
    elif len(data)>=30 and data[:4]==b'RIFF' and data[8:12]==b'WEBP':
        codec='webp';kind=data[12:16]
        if kind==b'VP8 ' and data[23:26]==b'\x9d\x01\x2a':
            width=int.from_bytes(data[26:28],'little')&0x3fff;height=int.from_bytes(data[28:30],'little')&0x3fff
        elif kind==b'VP8L' and data[20]==0x2f:
            bits=int.from_bytes(data[21:25],'little');width=(bits&0x3fff)+1;height=((bits>>14)&0x3fff)+1
        elif kind==b'VP8X' and not data[20]&2:
            width=int.from_bytes(data[24:27],'little')+1;height=int.from_bytes(data[27:30],'little')+1
    if not codec or not 0<width<=20000 or not 0<height<=20000 or width*height>20_000_000:
        raise GalleryError('仅支持有效 PNG、JPEG、静态 WebP 图片，最多 2000 万像素')
    return codec

def encode(source,target,check):
    check()
    codec=image_format(source)
    # ffmpeg bounds image decoding and preserves aspect ratio with black padding.
    p=subprocess.Popen(['ffmpeg','-v','error','-nostdin','-threads','1','-protocol_whitelist','file,pipe','-f','image2pipe','-c:v',codec,'-i',str(source),'-vf','scale=320:240:force_original_aspect_ratio=decrease,pad=320:240:(ow-iw)/2:(oh-ih)/2','-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    try:
        deadline=time.monotonic()+15
        while True:
            check()
            if time.monotonic()>deadline:raise GalleryError('图片解码超时')
            try:
                raw,_=p.communicate(timeout=.2);break
            except subprocess.TimeoutExpired:continue
        check()
        if p.returncode or len(raw)!=320*240*3:raise GalleryError('图片无法解码')
    finally:
        if p.poll() is None:p.kill();p.wait()
    samples=array.array('h');phase=0.;remainder=0.;rate=16000
    def tone(freq,ms):
        nonlocal phase,remainder
        remainder+=rate*ms/1000;n=int(remainder);remainder-=n
        step=2*math.pi*freq/rate
        for _ in range(n):
            samples.append(round(math.sin(phase)*32767));phase=(phase+step)%(2*math.pi)
    for freq,ms in [(1900,300),(1200,10),(1900,300),(1200,30)]:tone(freq,ms)
    for bit in range(7):tone(1100 if 8>>bit&1 else 1300,30)
    tone(1100,30);tone(1200,30)
    def clamp(v):return max(0,min(255,int(v+.5)))
    for y in range(240):
        check();ys=[];cs=[]
        for x in range(320):
            r,g,b=raw[(y*320+x)*3:(y*320+x)*3+3]
            ys.append(clamp(.299*r+.587*g+.114*b))
            cs.append(clamp(.5*r-.418688*g-.081312*b+128) if y%2==0 else clamp(-.168736*r-.331264*g+.5*b+128))
        tone(1200,9);tone(1500,3)
        for v in ys:tone(1500+800*v/255,88/320)
        tone(1500 if y%2==0 else 2300,4.5);tone(1900,1.5)
        for v in cs:tone(1500+800*v/255,44/320)
    import sys
    if sys.byteorder!='little':samples.byteswap()
    with wave.open(str(target),'wb') as w:w.setparams((1,2,rate,0,'NONE',''));w.writeframes(samples.tobytes())

def prepare(spec,check):
    spec=validate(spec)
    entries=json.loads(fetch('/v1/gallery/images?directory='+spec['directory']+'&recursive='+('1' if spec['recursive'] else '0'),4*1024*1024,check))['images']
    unique={e['id']:e for e in entries if re.fullmatch('[0-9a-f]{64}',str(e.get('id','')))}
    if len(unique)<spec['count']:raise GalleryError('图库图片数量不足')
    chosen=random.SystemRandom().sample(list(unique.values()),spec['count'])
    temp=tempfile.TemporaryDirectory(prefix='fmo-gallery-');paths={}
    try:
        for e in chosen:
            check();source=Path(temp.name)/'source';source.write_bytes(fetch('/v1/gallery/image?id='+e['id'],MAX_IMAGE,check))
            target=Path(temp.name)/(e['id']+'.wav');encode(source,target,check);source.unlink();paths[e['id']]=target
        return temp,paths,[{'id':e['id'],'name':e.get('name','')[:160]} for e in chosen]
    except BaseException:temp.cleanup();raise
