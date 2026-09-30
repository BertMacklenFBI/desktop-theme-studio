#!/usr/bin/python3
"""No desktop: journal chaining, conflict detection, exact restore, native snapshots."""
import base64, copy, importlib.util, json, tempfile, sys
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[1]))
s=importlib.util.spec_from_file_location('usability',HERE/'usability.py');u=importlib.util.module_from_spec(s);s.loader.exec_module(u)
s=importlib.util.spec_from_file_location('native',HERE.parents[1]/'native_visibility.py');n=importlib.util.module_from_spec(s);s.loader.exec_module(n)
with tempfile.TemporaryDirectory(prefix='quiet-usability-test-') as tmp:
    root=Path(tmp);u.CONFIG=root
    for name in ('eww.yuck','saimoom.yuck','eww.scss'):(root/name).write_bytes((HERE/name).read_bytes())
    stage=u.plan();baseline={'actions':[]}
    for action in stage['actions']:
        baseline['actions'].append(dict(action,after=action['before'],applied=True))
        u.engine.write(action,action['after']);action['attempted']=True;action['applied']=True
    original=json.dumps(baseline)
    assert u.check_baseline(baseline,stage)['projected_files']==3
    assert json.dumps(baseline)==original
    bad=copy.deepcopy(stage);bad['actions'][0]['before']='corrupt'
    try:u.check_baseline(baseline,bad)
    except RuntimeError:pass
    else:raise AssertionError('Broken chain accepted')
    target=root/'saimoom.yuck';saved=target.read_bytes();target.write_bytes(saved+b'\n;; unrelated edit')
    try:u.check_baseline(baseline,stage)
    except RuntimeError:pass
    else:raise AssertionError('Live conflict accepted')
    target.write_bytes(saved)
    u.engine.restore(stage,root/'state.json')
    assert all((root/name).read_bytes()==(HERE/name).read_bytes() for name in ('eww.yuck','saimoom.yuck','eww.scss'))
    path=root/'native.json';details={'motif':'2, 0, 0, 0, 0','pid':'123','geometry':[22,44,500,300]}
    with patch.object(n,'refinement_path',return_value=path),patch.object(n,'window_details',return_value=details):
        n.record_refinement(99)
        changed=dict(details,motif='2, 0, 1, 0, 0')
        with patch.object(n,'window_details',return_value=changed):n.record_refinement(99)
        assert json.loads(path.read_text())['windows']['99']==details
        with patch.object(n,'matching',return_value=[99]),patch.object(n.subprocess,'run') as run:
            n.restore_refinement(path)
            assert run.call_args_list[0].args[0][-1]==details['motif']
            assert run.call_args_list[1].args[0][-1]=='0,22,44,500,300'
        assert json.loads(path.read_text())['status']=='restored'
# Shared music handlers are preserved verbatim, the library is a separate route.
source=(HERE/'saimoom.yuck').read_text();new=(HERE/'usability/saimoom.yuck').read_text()
for handler in ('media seek {}','media previous','media toggle','media next'):
    assert source.count(handler)==new.count(handler)==1
assert new.count(':active {s.media.player != ""}')==3
print('PASS: immutable receipt chain, chain/live conflicts, exact file restore, native original snapshot and restore, all four existing media handlers unchanged.')
# GTK's startup XID can disappear before property reads; only BadWindow is retryable.
import subprocess
real={'motif':'2, 0, 0, 0, 0','pid':'123','geometry':[0,0,368,247]}
transient=subprocess.CalledProcessError(1,['xprop'],stderr='X Error: BadWindow (invalid Window parameter)')
with patch.object(n,'matching',side_effect=[[3],[15],[15],[15]]),patch.object(n,'window_details',side_effect=[transient,real,real,real]),patch.object(n.time,'sleep'):
    assert n.wait_ready()==[15]
with patch.object(n,'matching',return_value=[15]),patch.object(n,'window_details',side_effect=subprocess.CalledProcessError(1,['xprop'],stderr='unable to open display')):
    try:n.wait_ready()
    except subprocess.CalledProcessError:pass
    else:raise AssertionError('A genuine display error was hidden')
with patch.object(n,'matching',return_value=[]),patch.object(n.time,'monotonic',side_effect=[0,0,9]),patch.object(n.time,'sleep'):
    try:n.wait_ready(timeout=8)
    except RuntimeError as error:assert 'stable window' in str(error)
    else:raise AssertionError('Startup deadline did not fail')
print('PASS: transient XID retry, three stable real-window samples, genuine X11 errors propagated, bounded deadline.')
# EWMH move uses frame coordinates; xwininfo reports the client inside its titlebar.
from types import SimpleNamespace
area=SimpleNamespace(x=0,y=40,width=2880,height=1760)
frame,client=n.center_target(area,[2490,84,368,247],(0,0,32,0))
assert frame==(1256,780) and client==(1256,812)
print('PASS: centering includes native titlebar frame extents.')
