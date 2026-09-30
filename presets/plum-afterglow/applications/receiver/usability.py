#!/usr/bin/python3
"""Compatibility receipt for already-integrated Plum Afterglow usability."""
import base64, importlib.util
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('quiet_receiver',HERE/'refine.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
engine=base.engine
CONFIG=base.CONFIG

def plan():
    actions=[]
    for name in ('eww.yuck','saimoom.yuck','eww.scss'):
        target=CONFIG/name
        before=target.read_bytes(); baseline=(HERE/name).read_bytes()
        if before!=baseline:
            raise RuntimeError('Receiver source differs from approved baseline: '+str(target))
        after=(HERE/'usability'/name).read_bytes()
        actions.append(dict(kind='file',path=str(target),before=base64.b64encode(before).decode(),after=base64.b64encode(after).decode()))
    return dict(theme='Plum Afterglow',refinement='music discovery and popup usability',actions=actions,protected_hashes={},touched_paths=[a['path'] for a in actions],gsettings=[],skipped=[])

def check_baseline(baseline_state, usability_state):
    """Validate the live top layer AND its exact link to the immutable baseline."""
    projected={}
    for action in usability_state['actions']:
        if action.get('applied'):
            if engine.current(action)!=action['after']:
                raise RuntimeError('Usability conflict: '+action['path'])
            projected[action['path']]=action['before']
    baseline_paths=set()
    for action in baseline_state['actions']:
        if not action.get('applied'):continue
        path=action.get('path')
        if path in projected:
            if action['kind']!='file' or projected[path]!=action['after']:
                raise RuntimeError('Usability/baseline chain mismatch: '+path)
            baseline_paths.add(path)
        elif engine.current(action)!=action['after']:
            raise RuntimeError('Receiver conflict: '+str(path))
    if set(projected)!=baseline_paths:
        raise RuntimeError('Usability layer modifies a file outside the baseline receipt')
    return {'status':'passed','projected_files':len(projected),'baseline_receipt_unchanged':True}

if __name__=='__main__':
    engine.plan=plan
    engine.main()
