#!/usr/bin/python3
"""Guarded, additive workspace/logo enhancement of an already kept workstation.

Only new optional stages are applied or reversed. Existing desktop/application/menu
receipts and their before values are never rewritten. The independent timer restores
this enhancement if it is not explicitly kept within 180 seconds.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import theme
import desktop_control as control

ROOT=Path(__file__).resolve().parent
STUDIO=ROOT.parents[1]
STAGES=('workspace.json','logo.json')
MARKER=STUDIO/'state/activation-trials/gnu-darwin-workstation-enhancement.json'


def load(path): return json.loads(Path(path).read_text())
def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def journal(path): return path.parent/'enhancement.json'
def scripts(): return {name:script for script,name in theme.OPTIONAL_STAGES if name in STAGES}


def write(path,state):
    theme.save_json(journal(path),state)
    # Shared Studio guard sees the supplemental pending transaction between commands.
    theme.save_json(MARKER,{'version':1,'preset':ROOT.name,'receipt':str(path.resolve()),
                           'status':state['status'],'journal':str(journal(path).resolve())})


def unchanged(path,state):
    for name,expected in state['preserved_receipts'].items():
        source=path.parent/name
        if not source.is_file() or digest(source)!=expected:
            raise RuntimeError('Existing receipt changed during enhancement: '+name)


def command(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=20)
    if result.returncode: raise RuntimeError(result.stdout+result.stderr)
    return result.stdout.strip()


def arm(path,state):
    command(['systemd-run','--user','--quiet','--collect','--unit='+state['unit'],
             '--on-active=180s','--timer-property=AccuracySec=1s',
             '--property=Restart=on-failure','--property=RestartSec=15s',
             '--property=TimeoutStartSec=120s','/usr/bin/python3',str(ROOT/'enhance.py'),
             'restore','--receipt',str(path.resolve()),'--automatic'])


def stop(state):
    # Stop the timer, not a possibly running recovery service that owns this call.
    command(['systemctl','--user','stop',state['unit']+'.timer'])


def stage_check(script,receipt):
    theme.call(script,'check','--state',receipt,timeout=30)


def restore(path,automatic=False):
    state=load(journal(path))
    if str(path.resolve())!=state['receipt']: raise RuntimeError('Enhancement receipt path mismatch')
    if automatic and state['status'] in ('kept','restored'):
        unchanged(path,state)
        write(path,state) # reconcile a terminal journal with an interrupted marker write
        stop(state)
        return {'status':state['status'],'timer':'ignored'}
    try: unchanged(path,state)
    except Exception as exc:
        state['status']='recovery-required'; state['errors']=[str(exc)]; write(path,state)
        return {'status':state['status'],'errors':state['errors']}
    state['status']='restoring'; write(path,state)
    errors=[]; available=scripts()
    for name in reversed(state['started']):
        receipt=path.parent/name
        if not receipt.is_file():
            errors.append('Missing attempted enhancement stage receipt: '+name); continue
        try:
            if name not in available or not available[name].is_file(): raise RuntimeError('Missing stage script: '+name)
            theme.call(available[name],'restore','--state',receipt,'--commit',timeout=45)
            stage_check(available[name],receipt)
        except Exception as exc: errors.append(name+': '+str(exc))
    try: unchanged(path,state)
    except Exception as exc: errors.append(str(exc))
    if not errors:
        # Restore the registration only after every attempted stage is reversed.
        # Restored receipts remain present so full checks still verify their before values.
        theme.save_json(path.parent/'stages.json',state['original_manifest'])
        try: theme.check(path)
        except Exception as exc: errors.append('Restored theme check: '+str(exc))
    if errors:
        # Keep attempted stages registered, even after a partially successful rollback.
        manifest=load(path.parent/'stages.json')
        for key in ('required','started'):
            manifest.setdefault(key,[])
            for name in state['started']:
                if name not in manifest[key]: manifest[key].append(name)
        theme.save_json(path.parent/'stages.json',manifest)
        state['status']='recovery-required'; state['errors']=errors; write(path,state)
        return {'status':state['status'],'errors':errors}
    state['status']='restored'; state['restored_at']=time.time(); state['errors']=[]; write(path,state)
    try: stop(state)
    except Exception as exc:
        state['status']='recovery-required'; state['errors']=['Rollback succeeded but timer stop failed: '+str(exc)]; write(path,state)
        return {'status':state['status'],'errors':state['errors']}
    return {'status':'restored','receipt':str(path),'core_unchanged':True}


def apply(path):
    if load(path).get('status')!='kept': raise RuntimeError('Enhancement requires an already kept desktop receipt')
    if journal(path).exists(): raise RuntimeError('An enhancement journal already exists; inspect or restore it before another enhancement')
    theme.check(path); control.validate_source()
    manifest_path=path.parent/'stages.json'
    if not manifest_path.is_file(): raise RuntimeError('Existing release must have a stages manifest')
    manifest=load(manifest_path); available=scripts()
    names=[name for name in STAGES if not (path.parent/name).exists()]
    if not names: raise RuntimeError('Workspace and logo enhancement stages already exist')
    for name in names:
        if name not in available or not available[name].is_file(): raise RuntimeError('Missing enhancement stage script: '+name)
        if name in manifest.get('started',[]) or name in manifest.get('required',[]):
            raise RuntimeError('Missing registered stage receipt: '+name)
    originals={item.name:digest(item) for item in path.parent.glob('*.json')
               if item.name not in ('stages.json','enhancement.json','recovery.json')}
    state={'version':1,'receipt':str(path.resolve()),'status':'applying','created_at':time.time(),
           'deadline':time.time()+180,'unit':'gnu-darwin-workstation-enhancement-'+uuid.uuid4().hex[:10],
           'original_manifest':manifest,'preserved_receipts':originals,'stages':names,'started':[],
           'guard_armed':False,'errors':[]}
    write(path,state)
    try:
        arm(path,state); state['guard_armed']=True; write(path,state)
        for name in names:
            # Both journals precede the stage subprocess, including partially failed apply.
            state['started'].append(name); write(path,state); theme.start_stage(path,name)
            theme.call(available[name],'apply','--state',path.parent/name,'--commit',timeout=45)
            unchanged(path,state)
        theme.check(path); unchanged(path,state)
        if time.time()>=state['deadline']: raise RuntimeError('Enhancement inspection deadline expired during apply')
        state['status']='pending'; write(path,state)
        return {'status':'pending','deadline':state['deadline'],'receipt':str(path),
                'new_stages':names,'keep_command':f'/usr/bin/python3 {ROOT / "enhance.py"} keep --receipt {path}',
                'core_unchanged':True}
    except Exception as exc:
        state['error']=str(exc); write(path,state)
        try: result=restore(path)
        except Exception as recovery:
            state=load(journal(path)); state['status']='recovery-required'
            state['errors']=[str(recovery)]; write(path,state)
        raise


def keep(path):
    state=load(journal(path))
    if state['receipt']!=str(path.resolve()): raise RuntimeError('Enhancement receipt path mismatch')
    if state['status']=='kept':
        theme.check(path); unchanged(path,state); write(path,state); stop(state)
        return {'status':'kept','receipt':str(path),'core_unchanged':True,'new_stages':state['stages']}
    if state['status']!='pending': raise RuntimeError('Only a pending enhancement can be kept')
    if time.time()>=state['deadline']:
        restore(path); raise RuntimeError('Enhancement expired and recovery was attempted')
    theme.check(path); unchanged(path,state)
    if time.time()>=state['deadline']:
        restore(path); raise RuntimeError('Enhancement expired during validation and recovery was attempted')
    # Persist acceptance before disarming: a crash can never strand a pending trial without its timer.
    state['status']='kept'; state['kept_at']=time.time(); write(path,state)
    stop(state)
    return {'status':'kept','receipt':str(path),'core_unchanged':True,'new_stages':state['stages']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['apply','keep','restore'])
    parser.add_argument('--receipt',type=Path)
    parser.add_argument('--automatic',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if os.geteuid()==0: raise RuntimeError('Run as the desktop user')
    if args.automatic and args.command!='restore': parser.error('--automatic is only valid with restore')
    path=(args.receipt or theme.receipt()).resolve()
    guard=theme.module('gnu_darwin_enhancement_guard',theme.RUNTIME/'guard.py')
    operation={'apply':'refine','keep':'refine-keep','restore':'rollback'}[args.command]
    with guard.operation(STUDIO,ROOT.name,operation,path):
        result=apply(path) if args.command=='apply' else keep(path) if args.command=='keep' else restore(path,args.automatic)
    print(json.dumps(result,indent=2)); return 2 if result.get('errors') else 0

if __name__=='__main__':
    try: sys.exit(main())
    except Exception as exc: print(json.dumps({'error':str(exc)}),file=sys.stderr); sys.exit(2)
