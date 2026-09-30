#!/usr/bin/python3
"""Current launcher/source round trip, drift refusal and preserved handler bytes."""
import importlib.util,json,shutil,tempfile
from pathlib import Path
spec=importlib.util.spec_from_file_location('receiver',Path(__file__).with_name('refine.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with tempfile.TemporaryDirectory(prefix='moonstone-refine-') as directory:
    root=Path(directory);config=root/'config';config.mkdir();launcher=root/'bin/eww-widgets';launcher.parent.mkdir();dash=root/'ui/graphite-brass.css';dash.parent.mkdir()
    for name in ['eww.yuck','saimoom.yuck','carbon.yuck','eww.scss']:shutil.copy2(m.CONFIG/name,config/name)
    shutil.copytree(m.CONFIG/'scripts',config/'scripts',ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(m.LAUNCHER,launcher);shutil.copy2(m.DASH,dash)
    m.CONFIG=config;m.LAUNCHER=launcher;m.DASH=dash
    state=m.plan();before={a['path']:Path(a['path']).read_bytes() for a in state['actions']}
    for a in state['actions']:
        assert m.engine.current(a)==a['before'];m.engine.write(a,a['after']);a['applied']=True
    assert not m.plan()['actions'],'Receiver plan must be idempotent'
    m.engine.restore(state,root/'receipt.json')
    assert all(Path(p).read_bytes()==data for p,data in before.items())
    source=launcher.read_text();launcher.write_text(source+'\n# unreviewed command\n')
    try:m.plan()
    except RuntimeError as e:assert 'launcher layout' in str(e)
    else:raise AssertionError('Unknown launcher edit accepted')
    launcher.write_text(source);(config/'carbon.yuck').write_text('unknown source')
    try:m.plan()
    except RuntimeError as e:assert 'source revision' in str(e)
    else:raise AssertionError('Unknown widget source accepted')
print(json.dumps({'receiver_roundtrip':'PASS','idempotence':'PASS','unknown_launcher':'REFUSED','unknown_frontend':'REFUSED','source_revision':'candidate-repair-20260930','gui':False,'host_writes':False}))
