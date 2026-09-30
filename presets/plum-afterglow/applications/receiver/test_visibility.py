#!/usr/bin/env python3
"""Exercise the patched real launcher with inert Eww/sync/watcher fixture scripts."""
import importlib.util,tempfile,subprocess,os
from pathlib import Path
s=importlib.util.spec_from_file_location('refine',Path(__file__).with_name('refine.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
p=m.plan();text=m.LAUNCHER.read_text()
for a in p['actions']:
    if a['path']==str(m.LAUNCHER):text=text.replace(a['before'],a['after'])
with tempfile.TemporaryDirectory(prefix='quiet-visibility-') as td:
    root=Path(td);(root/'bin').mkdir();(root/'config/scripts').mkdir(parents=True);(root/'state').mkdir()
    launcher=root/'bin/eww-widgets';launcher.write_text(text);launcher.chmod(0o755)
    stub='''#!/usr/bin/python3
import os,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1];args=sys.argv[3:];p=root/'state/open'
windows=p.read_text().splitlines() if p.exists() else []
if args[0]=='ping':print('pong')
elif args[0]=='active-windows':print('\\n'.join(w+': '+w for w in windows))
elif args[0]=='open':p.write_text(args[-1]+'\\n')
elif args[0]=='close-all':p.write_text('')
else:raise SystemExit('Unexpected Eww call '+repr(args))
'''
    (root/'bin/eww').write_text(stub);(root/'bin/eww').chmod(0o755)
    for name in ('theme-sync.py','theme-watch.py'):
        path=root/'config/scripts'/name;path.write_text('#!/bin/sh\nexit 0\n');path.chmod(0o755)
    def run(action):subprocess.run([str(launcher),action],check=True,timeout=5)
    opened=root/'state/open'
    run('start');assert not opened.exists()
    run('show');assert opened.read_text()=='dashboard\n'
    run('toggle');assert opened.read_text()==''
    run('toggle');assert opened.read_text()=='dashboard\n'
    opened.write_text('controls\nnotes\n');run('hide');assert opened.read_text()==''
    opened.write_text('music\n');run('toggle');assert opened.read_text()==''
print('Launcher fixture: start idle, show dashboard, hide all, toggle dashboard/other tools pass.')
