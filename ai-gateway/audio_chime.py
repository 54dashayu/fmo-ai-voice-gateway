"""Short pre-image cue, 16 kHz mono PCM; no I/O or transmission."""
import math
import struct

def dingdong():
    samples=[]
    for frequency in (880,660):
        for i in range(9600):
            t=i/16000
            envelope=min(1,t/.01)*math.exp(-5*t)*min(1,(.6-t)/.03)
            samples.append(round(14000*envelope*math.sin(2*math.pi*frequency*t)))
    return struct.pack('<'+'h'*len(samples),*samples)
