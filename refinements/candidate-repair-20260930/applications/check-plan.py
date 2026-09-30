#!/usr/bin/python3
"""Bounded read-only current plan; never print current configuration contents."""
from pathlib import Path
import importlib.util,json,sys
ROOT=Path('/home/bertmacklen/Documents/desktop-theme-studio')
slug=sys.argv[1]
relative='refinements/ocean-silk-current/applications' if slug=='ocean-silk' else 'presets/'+slug+'/applications'
path=ROOT/relative/'adapter.py';sys.path.insert(0,str(path.parent))
spec=importlib.util.spec_from_file_location('candidate_application_adapter',path)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
state=m.plan(settings=False)
print(json.dumps({'profile':slug,'status':'PASS_READ_ONLY_CURRENT_FILES_PLAN','actions':len(state['actions']),
 'paths':len(state['touched_paths']),'settings':False,'gui':False,'writes':False,
 'protected_hashes':len(state['protected_hashes']),'action_kinds':sorted(set(a['kind'] for a in state['actions']))},indent=2))
