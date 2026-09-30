#!/usr/bin/env python3
"""Reversible source-only receiver refinement; coordinator manages window selection/reload."""
import argparse,base64,importlib.util,json,sys,re,hashlib
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('appearance_adapter',HERE.parent/'adapter.py');engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
CONFIG=Path.home()/'Documents/eww-graphite-brass/config'
LAUNCHER=Path.home()/'Documents/eww-graphite-brass/bin/eww-widgets'
DASH=Path.home()/'Documents/Codex/2026-09-05/le/outputs/nocturne-studio/music-widget/ui/graphite-brass.css'
def plan():
    actions=[]
    contract=json.loads((HERE/'current-contract.json').read_text())
    for name in ['eww.yuck','saimoom.yuck','carbon.yuck','eww.scss']:
        target=CONFIG/name
        if target.is_symlink() or not target.is_file():raise RuntimeError('Expected regular receiver source: '+str(target))
        before=target.read_bytes();after=(HERE/name).read_bytes()
        if before==after:continue
        accepted=engine.digest(before)==contract['source_sha256'][name]
        if name=='eww.scss':
            pattern=r'// THEME_VARIABLES_START.*?// THEME_VARIABLES_END'
            text=before.decode()
            if len(re.findall(pattern,text,re.S))!=1:raise RuntimeError('Unknown receiver watcher variables')
            normalized=re.sub(pattern,'__DTS_PALETTE_VARIABLES__',text,flags=re.S)
            accepted=accepted or engine.digest(normalized.encode())==contract['normalized_scss_sha256']
            accepted=accepted or normalized==re.sub(pattern,'__DTS_PALETTE_VARIABLES__',after.decode(),flags=re.S)
        if not accepted:raise RuntimeError('Unknown receiver source revision: '+str(target))
        actions.append({'kind':'file','path':str(target),'before':base64.b64encode(before).decode(),'after':base64.b64encode(after).decode()})
    block='\n'+(HERE/'dashboard-receiver.css').read_text()
    if block not in DASH.read_text():actions.append({'kind':'text','path':str(DASH),'before':'','after':block,'count':1})
    launcher_text=LAUNCHER.read_text()
    normalized_launcher=launcher_text.replace(' show) "${flow[@]}" open clock ;;',' show) "${flow[@]}" open dashboard ;;')
    if engine.digest(normalized_launcher.encode()) not in {contract['launcher_sha256'],contract['historical_launcher_sha256']}:
        raise RuntimeError('Unexpected Eww launcher layout; refusing broad script rewrite.')
    for before,after in [
        (' start|show) "$eww" -c "$config" open-many clock music system launchers ;;',
         ' start|show) "$eww" -c "$config" open clock ;;'),
        (' hide) "$eww" -c "$config" close clock music system launchers ;;',
         ' hide) "$eww" -c "$config" close clock ;;'),
        # Current guarded launcher has separate start/show and flow helpers.
        # Preserve help, daemon guard, watcher, close/focus and all tool branches.
        (' show) "${flow[@]}" open dashboard ;;',
         ' show) "${flow[@]}" open clock ;;'),
    ]:
        if before.startswith(' start|show)') and ' show) "${flow[@]}" open ' in launcher_text:
            continue
        if before.startswith(' hide) "$eww"') and ' hide) "${flow[@]}" hide ;;' in launcher_text:
            continue
        if before.startswith(' show) "${flow[@]}"') and ' start|show) "$eww"' in launcher_text:
            continue
        if launcher_text.count(before)==1:
            actions.append({'kind':'text','path':str(LAUNCHER),'before':before,'after':after,'count':1})
        elif launcher_text.count(after)!=1:
            raise RuntimeError('Unexpected Eww launcher layout; refusing broad script rewrite.')
    protected=[CONFIG/'scripts'/name for name in ['backend.py','ui','theme-sync.py','theme-watch.py']]
    return {'theme':'Moonstone Stereo','refinement':'original receiver composition','source_revision':contract['revision'],'actions':actions,'protected_hashes':{str(p):engine.digest(p.read_bytes()) for p in protected if p.is_file()},'touched_paths':list(dict.fromkeys(a['path'] for a in actions)),'window_selection':{'close':['music','system','launchers','bar'],'open':['clock']},'status':'planned'}
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
