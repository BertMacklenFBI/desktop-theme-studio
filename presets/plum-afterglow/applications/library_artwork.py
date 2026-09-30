#!/usr/bin/env python3
"""Supplemental canonical library artwork; restore before the original base receipt."""
import importlib.util,base64
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('quiet_adapter',HERE/'adapter.py')
engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
DASH=Path.home()/'Documents/Codex/2026-09-05/le/outputs/nocturne-studio/music-widget'
def plan():
    logo=HERE.parent/'artwork/menu-logo.png';target=DASH/'ui/fastfetch-logo.png'
    actions=[]
    for a in [{'kind':'file','path':str(target),'after':base64.b64encode(logo.read_bytes()).decode()},
              {'kind':'json','path':str(DASH/'appearance.json'),'keys':['theme','logo'],'after':str(target)}]:
        a['before']=engine.current(a)
        if a['before']!=a['after']:actions.append(a)
    return {'theme':'Plum Afterglow','refinement':'canonical library logo','actions':actions,'protected_hashes':{},'touched_paths':[a['path'] for a in actions],'skipped':[],'logo_sha256':engine.digest(logo.read_bytes())}
if __name__=='__main__':
    engine.plan=plan
    engine.main()
