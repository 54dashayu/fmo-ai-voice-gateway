"""Renewable audio activity lease consumed by the broker publish hook."""
import os,time
from pathlib import Path
MARKER=Path('/run/fmo-audio-lease/active')
READY=Path('/run/fmo-audio-hook/ready')
def write_lease(value):
    temp=MARKER.with_suffix('.tmp')
    temp.write_text(f"{int(value['until']*1000)}\n{value['clientid']}\n{value['pid']}")
    temp.chmod(0o644);temp.replace(MARKER)
class Lease:
    def __init__(self,clientid):self.clientid=clientid;self.next_refresh=0;self.owned=False
    def refresh(self):
        if time.monotonic()<self.next_refresh:return
        try:
            age=time.time()-int(READY.read_text())/1000
            if not 0<=age<3 or '\n' in self.clientid:raise ValueError()
            write_lease({'pid':os.getpid(),'clientid':self.clientid,'until':time.time()+5});self.owned=True
        except Exception:raise RuntimeError('活动独占保护不可用，已停止播放') from None
        self.next_refresh=time.monotonic()+1
    def close(self):
        if self.owned:
            try:MARKER.unlink(missing_ok=True)
            except OSError:pass

def active_lease():
    try:
        until,clientid,pid=MARKER.read_text().split('\n')
        if not 0<int(until)/1000-time.time()<=6:return None
        os.kill(int(pid),0)
        return {'pid':int(pid),'clientid':clientid,'until':int(until)/1000}
    except (OSError,ValueError,KeyError,TypeError):return None

def decision(body):
    lease=active_lease()
    if lease and body.get('action')=='publish' and body.get('topic')=='FMO/RAW':
        if body.get('clientid')==lease['clientid'] and body.get('peerhost') in ('127.0.0.1','::1'):return 'ignore'
        return 'deny'
    return 'ignore'
