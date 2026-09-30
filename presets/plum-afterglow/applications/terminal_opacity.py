#!/usr/bin/env python3
"""Supplemental terminal transaction; separate receipt, restored before original base."""
import importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location('quiet_adapter',Path(__file__).with_name('adapter.py'))
engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
KEYS={'use-theme-transparency':'false','use-transparent-background':'false','background-transparency-percent':'0'}
def plan():
    profile=engine.run(['gsettings','get','org.gnome.Terminal.ProfilesList','default']).strip("'")
    schema=f'org.gnome.Terminal.Legacy.Profile:/org/gnome/terminal/legacy/profiles:/:{profile}/'
    actions=[]
    for key,after in KEYS.items():
        a={'kind':'gsetting','schema':schema,'key':key,'after':after}
        a['before']=engine.current(a)
        if a['before']!=after:actions.append(a)
    return {'theme':'Plum Afterglow','refinement':'opaque GNOME Terminal','actions':actions,'protected_hashes':{},'touched_paths':[],'gsettings':actions,'skipped':[]}
if __name__=='__main__':
    engine.plan=plan
    engine.main()
