"""SSTV body-only attenuation; never edits stored source media."""
import array
import sys

def validate(value):
    if type(value) is not int or not -24 <= value <= 0:
        raise ValueError('SSTV 电平请选择 -24 至 0 dB 的整数')
    return value

def is_sstv(asset):
    if 'is_sstv' in asset:return asset['is_sstv'] is True
    return bool(asset.get('source_id') and str(asset.get('source','')).startswith('SSTV: '))

def scale(pcm, db):
    validate(db)
    if db == 0:return pcm
    values=array.array('h');values.frombytes(pcm)
    if sys.byteorder!='little':values.byteswap()
    gain=10**(db/20)
    values=array.array('h',(round(x*gain) for x in values))
    if sys.byteorder!='little':values.byteswap()
    return values.tobytes()
