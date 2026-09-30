#!/usr/bin/env python3
"""Reversible source-only receiver refinement; coordinator manages window selection/reload."""
import argparse,base64,importlib.util,json,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('appearance_adapter',HERE.parent/'adapter.py');engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
CONFIG=Path.home()/'Documents/eww-graphite-brass/config'
LAUNCHER=Path.home()/'Documents/eww-graphite-brass/bin/eww-widgets'
DASH=Path.home()/'Documents/Codex/2026-09-05/le/outputs/nocturne-studio/music-widget/ui/graphite-brass.css'
def plan():
    actions=[]
    for name in ['eww.yuck','saimoom.yuck','carbon.yuck','eww.scss']:
        target=CONFIG/name
        actions.append({'kind':'file','path':str(target),'before':base64.b64encode(target.read_bytes()).decode(),'after':base64.b64encode((HERE/name).read_bytes()).decode()})
    launcher=LAUNCHER.read_text()
    replacements=[
        (' start|show) "$eww" -c "$config" open clock ;;',
         ' start) : ;;\n show) "$eww" -c "$config" open dashboard ;;'),
        (' hide) "$eww" -c "$config" close clock ;;',
         ' hide) "$eww" -c "$config" close-all ;;'),
        ("grep -q '^clock:'", "grep -q ':'"),
    ]
    for before,after in replacements:
        if launcher.count(before)==1:
            actions.append({'kind':'text','path':str(LAUNCHER),'before':before,'after':after,'count':1})
        elif launcher.count(after)!=1:
            raise RuntimeError('Unexpected launcher visibility definition; refusing a broad rewrite')
    return {'theme':'Plum Afterglow','refinement':'on-demand Plum Afterglow tools','actions':actions,'protected_hashes':{},'touched_paths':list(dict.fromkeys(a['path'] for a in actions)),'window_selection':{'close':['clock','dashboard','controls','calendar','notes','timer','audio','music','system','launchers','dock','bar'],'open':[]},'status':'planned'}
def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['plan','apply','restore','check']);p.add_argument('--state',type=Path);p.add_argument('--commit',action='store_true');a=p.parse_args()
    if a.command=='plan':
        state=plan();print(json.dumps({k:v for k,v in state.items() if k!='actions'},indent=2));return
    if not a.state:p.error('--state required')
    if a.command=='apply':
        if a.state.exists():raise RuntimeError('Use a new state path')
        state=plan()
        if not a.commit:print(json.dumps({'preview':True,'paths':state['touched_paths']},indent=2));return
        engine.journal(a.state,state)
        try:
            for op in state['actions']:
                if engine.current(op)!=op['before']:raise RuntimeError('Source conflict: '+op['path'])
                op['attempted']=True;engine.journal(a.state,state);engine.write(op,op['after']);op['applied']=True;engine.journal(a.state,state)
            state['status']='applied';engine.journal(a.state,state)
        except Exception as exc:
            state['error']=str(exc)
            try:engine.restore(state,a.state);state['status']='rolled-back'
            except Exception as err:state['status']='recovery-required';state['recovery_error']=str(err)
            engine.journal(a.state,state);raise
    elif a.command=='restore':
        state=engine.load(a.state)
        if not a.commit:print('Preview: restore original receiver source changes');return
        engine.restore(state,a.state)
    else:
        state=engine.load(a.state);bad=[op['path'] for op in state['actions'] if op.get('applied') and engine.current(op)!=op['after']]
        print(json.dumps({'status':state['status'],'conflicts':bad},indent=2))
        if bad:sys.exit(1)
if __name__=='__main__':main()
