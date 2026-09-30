#!/usr/bin/env python3
"""File-only isolated pipeline: base -> receiver -> actual existing watcher -> restore."""
from pathlib import Path
import tempfile,importlib.util,copy,shutil,json,re
ROOT=Path(__file__).resolve().parents[1]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
base=module('moonstone_base',ROOT/'adapter.py');receiver=module('receiver',ROOT/'receiver/refine.py');sync=module('sync',Path('/home/bertmacklen/Documents/eww-graphite-brass/config/scripts/theme-sync.py'))
p=base.plan(settings=False)
with tempfile.TemporaryDirectory(prefix='moonstone-pipeline-') as tmp:
    fixture=Path(tmp)
    def local(path):return fixture/str(path).lstrip('/')
    for a in p['actions']:
        if 'path' not in a:continue
        source=Path(a['path']);target=local(source);target.parent.mkdir(parents=True,exist_ok=True)
        if source.exists() and not target.exists():shutil.copyfile(source,target)
        a['path']=str(target)
    for a in p['actions']:
        if 'path' in a:
            assert base.current(a)==a['before'],a;base.write(a,a['after'])
    for target in [receiver.CONFIG/'eww.yuck',receiver.CONFIG/'saimoom.yuck',receiver.CONFIG/'carbon.yuck',receiver.CONFIG/'eww.scss',receiver.LAUNCHER,receiver.DASH]:
        dest=local(target);dest.parent.mkdir(parents=True,exist_ok=True)
        if not dest.exists():shutil.copyfile(target,dest)
    registry=receiver.CONFIG/'themes/palettes.json';dest=local(registry);dest.parent.mkdir(parents=True,exist_ok=True)
    if not dest.exists():shutil.copyfile(registry,dest)
    receiver.CONFIG=local(receiver.CONFIG);receiver.DASH=local(receiver.DASH);receiver.LAUNCHER=local(receiver.LAUNCHER)
    f=receiver.plan();before_files={a['path']:Path(a['path']).read_bytes() for a in f['actions']}
    for a in f['actions']:
        assert receiver.engine.current(a)==a['before'];a['attempted']=True;receiver.engine.write(a,a['after']);a['applied']=True
    before=(receiver.CONFIG/'eww.scss').read_bytes()
    sync.CONFIG=receiver.CONFIG;sync.PALETTES=receiver.CONFIG/'themes/palettes.json';sync.STYLESHEET=receiver.CONFIG/'eww.scss';sync.MUSIC_ARTWORK=receiver.CONFIG/'assets/music.svg';sync.MUSIC_ARTWORK.parent.mkdir(parents=True,exist_ok=True);sync.STATE=fixture/'watcher-state';sync.THEME_STATE=sync.STATE/'theme.json'
    sync.apply('Moonstone Stereo')
    assert (receiver.CONFIG/'eww.scss').read_bytes()==before,'Watcher rewrote receiver SCSS'
    scssaction=next(a for a in p['actions'] if a.get('path')==str(receiver.CONFIG/'eww.scss'))
    assert base.current(scssaction)==scssaction['after'],'Base marker check broken after receiver'
    assert all(receiver.engine.current(a)==a['after'] for a in f['actions'])
    receiver.engine.restore(f,fixture/'receiver-state.json')
    assert all(Path(path).read_bytes()==data for path,data in before_files.items())
print('Moonstone base -> receiver -> actual watcher CSS byte identity and narrow receiver restore pass.')
