#!/usr/bin/python3
"""Private receiver fixture only: owned silent media/notes/timer/system state.

Production callbacks stay byte-identical in the shipped bundle. Preparation
substitutes this implementation only in a disposable private copy.
"""
from pathlib import Path
import json,math,os,sys,time

def guard():
    run=Path(os.environ.get('DTS_RECEIVER_PRIVATE_RUN','')).resolve()
    if os.environ.get('DTS_RECEIVER_PRIVATE')!='1' or not (run/'.private-receiver-fixture').is_file():
        raise RuntimeError('Private receiver sentinel/environment required')
    for key,host in [('DISPLAY','DTS_RECEIVER_HOST_DISPLAY'),('DBUS_SESSION_BUS_ADDRESS','DTS_RECEIVER_HOST_BUS')]:
        if not os.environ.get(key) or not os.environ.get(host) or os.environ[key]==os.environ[host]:
            raise RuntimeError('Distinct private display and bus required')
    return run

def load(run):return json.loads((run/'state/receiver.json').read_text())
def save(run,state):
    p=run/'state/receiver.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(state));tmp.replace(p)
def emit(run):
    state=load(run);timer=state['timer'];end=timer.get('end',0)
    seconds=max(0,int(end-time.time())) if end else timer['minutes']*60
    timer.update(running=bool(end and seconds),text=f'{seconds//60:02}:{seconds%60:02}')
    return state
def number(value,minimum=0,maximum=100):
    n=float(value)
    if not math.isfinite(n):raise ValueError('Finite fixture control value required')
    return max(minimum,min(maximum,round(n)))
def action(run,args):
    s=load(run);op=args[0]
    if op in ['volume','mic','brightness']:s[op]=number(args[1],5 if op=='brightness' else 0)
    elif op=='mute':s['muted']=not s['muted']
    elif op=='mic-mute':s['mic_muted']=not s.get('mic_muted',False)
    elif op=='wifi':s['wifi']=not s['wifi']
    elif op=='dnd':s['notifications']=not s['notifications']
    elif op=='workspace':
        target=int(args[1])
        if target not in range(4):raise ValueError('Unknown fixture workspace')
        for row in s['workspaces']:row['active']=row['id']==target
    elif op=='media':
        media=s['media'];verb=args[1]
        if verb=='toggle':media['playing']=not media['playing']
        elif verb in ['next','previous']:
            s['track_index']=(s.get('track_index',0)+(1 if verb=='next' else -1))%3
            media['title']=['Private fixture track one','Private fixture track two','Private fixture track three'][s['track_index']]
        elif verb=='seek':
            media['progress']=number(args[2]);media['position']=round(media['duration']*media['progress']/100)
            media['elapsed']=f"{media['position']//60}:{media['position']%60:02}"
        else:raise ValueError('Unknown fixture transport')
    elif op=='timer':
        t=s['timer'];verb=args[1]
        if verb=='toggle':t['end']=0 if t.get('end',0)>time.time() else time.time()+t['minutes']*60
        elif verb=='reset':t['end']=0
        else:t.update(minutes=number(t['minutes']+int(verb),5,180),end=0)
    elif op=='notes':s['notes']=args[1];(run/'Notes.txt').write_text(args[1])
    else:raise ValueError('Unknown fixture action')
    s['fixture_last_action']=args;s['fixture_actions']=s.get('fixture_actions',0)+1;save(run,s)

def main():
    run=guard();args=sys.argv[1:]
    if args and args[0]=='action':action(run,args[1:]);return
    if args and args[0]=='ui':
        verb=args[1]
        if verb=='edit-notes':action(run,['notes','Owned private note edited from receiver control.']);return
        if verb in ['network','appearance','settings','power','lock','screenshot','files','browser','terminal']:
            s=load(run);s['fixture_last_ui']=verb;save(run,s);return
        import subprocess
        subprocess.run([str(run/'bundle/bin/eww-widgets'),*args[1:]],check=True,timeout=10);return
    if args and args[0]=='once':print(json.dumps(emit(run)));return
    previous=None
    try:
        while True:
            output=json.dumps(emit(run),separators=(',',':'))
            if output!=previous:print(output,flush=True);previous=output
            time.sleep(.25)
    except (BrokenPipeError,KeyboardInterrupt):pass
if __name__=='__main__':main()
