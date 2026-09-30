#!/usr/bin/python3
"""Private fixture state only. No system, media, socket, network or bus APIs."""
import json,os,sys,time
from pathlib import Path

def root():
    value=os.environ.get('DTS_PRIVATE_ROOT','')
    p=Path(value)
    if not value or not p.is_absolute() or not p.resolve().is_relative_to(Path('/tmp')):
        raise RuntimeError('DTS_PRIVATE_ROOT must name an owned temporary directory')
    return p
def load():return json.loads((root()/'state.json').read_text())
def save(value):
    p=root()/'state.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(value));tmp.replace(p)
def action(args):
    s=load();kind=args[0];rest=args[1:]
    if kind in ['volume','mic','brightness']:s[kind]=max(0,min(100,int(float(rest[0]))))
    elif kind in ['mute','mic-mute','wifi','dnd']:
        key={'mute':'muted','mic-mute':'mic_muted','wifi':'wifi','dnd':'notifications'}[kind];s[key]=not s.get(key,False)
    elif kind=='timer':
        value=rest[0]
        if value=='toggle':s['timer']['running']=not s['timer']['running']
        elif value=='reset':s['timer'].update(running=False,minutes=25)
        else:s['timer']['minutes']=max(5,min(180,s['timer']['minutes']+int(value)))
        s['timer']['text']=str(s['timer']['minutes'])+':00'
    elif kind=='media':
        value=rest[0]
        if value=='toggle':s['media']['playing']=not s['media']['playing']
        elif value=='seek':s['media']['progress']=max(0,min(100,float(rest[1])))
        elif value in ['next','previous']:
            s['media']['title']='Owned fixture track '+value;s['media']['progress']=0
        else:raise RuntimeError('Unknown private media action')
    elif kind=='workspace':
        value=int(rest[0])
        if value not in range(4):raise RuntimeError('Unknown fixture workspace')
        for w in s['workspaces']:w['active']=w['id']==value
    else:raise RuntimeError('Unknown private backend action')
    save(s)
    with (root()/'actions.jsonl').open('a') as f:f.write(json.dumps({'kind':'backend','argv':args})+'\n')
def main():
    if sys.argv[1:2]==['action']:action(sys.argv[2:]);return
    previous=None
    try:
        while True:
            output=json.dumps(load(),separators=(',',':'))
            if output!=previous:print(output,flush=True);previous=output
            if '--once' in sys.argv:return
            time.sleep(.1)
    except (BrokenPipeError,KeyboardInterrupt):pass
if __name__=='__main__':main()
