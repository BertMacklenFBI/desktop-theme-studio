#!/usr/bin/python3
"""Current candidate plans in copied homes; repeated selection changes no bytes."""
import importlib.util,json,shutil,sys,tempfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path('/home/bertmacklen/Documents/desktop-theme-studio');results=[]
for slug in ['macintosh-soft','moonstone-stereo','ocean-silk','plum-afterglow']:
    path=ROOT/('refinements/ocean-silk-current/applications/adapter.py' if slug=='ocean-silk' else 'presets/'+slug+'/applications/adapter.py')
    sys.path.insert(0,str(path.parent))
    for name in ['engine','office','render','appearance_compat']:sys.modules.pop(name,None)
    spec=importlib.util.spec_from_file_location('idempotent_'+slug.replace('-','_'),path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    current=m.plan(settings=False)
    with tempfile.TemporaryDirectory(prefix=slug+'-current-') as directory:
        home=Path(directory)/'home';home.mkdir()
        paths=set(current['touched_paths'])|set(current['protected_hashes'])
        paths.update(str(m.HOME/p) for p in ['.bashrc','.config/konsolerc','Documents/eww-graphite-brass/config/themes/palettes.json'])
        for source in paths:
            p=Path(source)
            if p.is_file() and p.is_relative_to(m.HOME):
                target=home/p.relative_to(m.HOME);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
        # Konsole active profile is a planning dependency even if unchanged.
        for p in (m.HOME/'.local/share/konsole').glob('*.profile'):
            target=home/p.relative_to(m.HOME);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
        state=m.plan(home,settings=False)
        assert all('path' in a and Path(a['path']).is_relative_to(home) for a in state['actions'])
        context=patch.object(m,'ensure_closed') if hasattr(m,'ensure_closed') else patch.object(m,'__fixture_only__',True,create=True)
        with context:
            for a in state['actions']:assert m.current(a)==a['before'];m.write(a,a['after']);a['applied']=True
            assert not m.plan(home,settings=False)['actions'],'Repeat selection changed bytes: '+slug
            restore=m.engine.restore if hasattr(m,'engine') else m.restore
            restore(state,home/'receipt.json')
            assert all(m.current(a)==a['before'] for a in state['actions'])
        results.append({'profile':slug,'repeat_plan_actions':0,'temp_roundtrip':'PASS','settings':False,'gui':False})
    sys.path.pop(0)
print(json.dumps(results,indent=2))
