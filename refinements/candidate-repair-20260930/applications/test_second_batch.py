#!/usr/bin/python3
"""New candidate application source copies, temp-home round trips only."""
import importlib.util,json,shutil,sys,tempfile
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
results=[]
for slug in ['quiet-sage','gnu-darwin-aqua','gnu-darwin-workstation']:
    path=HERE/slug/'staged/adapter.py';sys.path.insert(0,str(path.parent))
    for name in ['engine','office','render','appearance_compat']:sys.modules.pop(name,None)
    spec=importlib.util.spec_from_file_location('staged_'+slug.replace('-','_'),path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    current=m.plan(settings=False)
    with tempfile.TemporaryDirectory(prefix=slug+'-app-repair-') as directory:
        home=Path(directory)/'home';home.mkdir()
        paths=set(current['touched_paths'])|set(current['protected_hashes'])
        if slug=='quiet-sage':paths.add(str(m.HOME/'.config/konsolerc'))
        for source in paths:
            p=Path(source)
            if p.is_file() and p.is_relative_to(m.HOME):
                target=home/p.relative_to(m.HOME);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
        state=m.plan(home,settings=False)
        assert all('path' in a and Path(a['path']).is_relative_to(home) for a in state['actions'])
        protected=dict(state['protected_hashes']);shell=home/'.bashrc';before_shell=shell.read_bytes() if shell.is_file() else None
        context=patch.object(m,'ensure_closed') if hasattr(m,'ensure_closed') else patch.object(m,'__fixture_only__',True,create=True)
        with context:
            for a in state['actions']:
                assert m.current(a)==a['before'];m.write(a,a['after']);a['applied']=True
            validate=m.validate if hasattr(m,'validate') else m.validate_protected
            validate(state)
            assert not m.plan(home,settings=False)['actions'],'Appearance plan not idempotent: '+slug
            target=next(a for a in state['actions'] if a['kind']=='json')
            m.write(target,'unreviewed appearance edit')
            try:
                restore=m.engine.restore if hasattr(m,'engine') else m.restore
                restore(state,home/'receipt.json')
            except RuntimeError:pass
            else:raise AssertionError('Unknown edit accepted by restore: '+slug)
            m.write(target,target['after']);restore(state,home/'receipt.json')
            assert all(m.current(a)==a['before'] for a in state['actions'])
            validate(state)
        assert all(Path(p).is_file() for p in protected)
        if before_shell is not None:assert shell.read_bytes()==before_shell
        results.append({'profile':slug,'current_plan_actions':len(current['actions']),'temp_plan_actions':len(state['actions']),'temp_paths':len(state['touched_paths']),'roundtrip':'PASS','idempotence':'PASS','conflict':'REFUSED','shell_bytes':'unchanged','settings':False,'gui':False})
    sys.path.pop(0)
print(json.dumps(results,indent=2))
