#!/usr/bin/env python3
"""Read existing Cava spectrum output only; never starts an audio analyzer."""
import argparse,json,os,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
BARS='▁▂▃▄▅▆▇█'
def frame(path,now=None,preview=False):
    now=time.time() if now is None else now
    low=high=0;status='UNAVAILABLE';source='missing-or-stale'
    if preview:status='PREVIEW / IDLE';source='preview-idle'
    else:
        try:
            p=Path(path)
            if now-p.stat().st_mtime>2.0:raise ValueError('stale Cava stream')
            data=json.loads(p.read_text());text=''.join(str(x.get('value','')) for x in data.get('item',[]) if x.get('type')=='text')
            values=[BARS.index(c) for c in text if c in BARS]
            if len(values)<2:raise ValueError('No spectrum bars')
            split=len(values)//2
            low=round(sum(values[:split])/len(values[:split])/7*100)
            high=round(sum(values[split:])/len(values[split:])/7*100)
            status='SIGNAL' if max(values)>0 else 'IDLE';source='existing-cava-spectrum'
        except (OSError,ValueError,TypeError,KeyError):pass
    def face(level):return str(HERE/'materials'/f'meter-{int(round(level/5)*5):03d}.svg')
    return {'low':low,'high':high,'status':status,'source':source,'low_face':face(low),'high_face':face(high)}
def main():
    p=argparse.ArgumentParser();p.add_argument('--once',action='store_true');p.add_argument('--source',type=Path,default=Path(os.environ.get('XDG_RUNTIME_DIR','/run/user/'+str(os.getuid())))/'cava-panel');a=p.parse_args();preview=os.environ.get('MOONSTONE_PREVIEW')=='1';previous=None
    try:
        while True:
            output=json.dumps(frame(a.source,preview=preview),separators=(',',':'))
            if output!=previous:print(output,flush=True);previous=output
            if a.once:break
            time.sleep(.125)
    except (BrokenPipeError,KeyboardInterrupt):pass
if __name__=='__main__':main()
