#!/usr/bin/python3
"""Prepare only; fixture actions never contact Eww or the user's system."""
import hashlib,importlib.util,json,os,re,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
BUNDLE=ROOT/'refinements/candidate-repair-20260930/applications/moonstone-stereo/bundle'
manifest=json.loads((BUNDLE/'bundle.json').read_text())
for rel,record in manifest['files'].items():
    p=BUNDLE/rel
    assert not p.is_symlink() and p.is_file() and p.stat().st_nlink==1
    assert hashlib.sha256(p.read_bytes()).hexdigest()==record['sha256']
for name in ['saimoom.yuck','carbon.yuck']:
    shipped=(BUNDLE/'config'/name).read_text();source=(Path(__file__).parent/name).read_text()
    actions=lambda text:re.findall(r':(?:onclick|onchange) "([^"]*)"',text)
    assert actions(shipped)==actions(source)
with tempfile.TemporaryDirectory(prefix='moonstone-owned-actions-') as directory:
    root=Path(directory);env=dict(os.environ,DTS_PRIVATE_ROOT=str(root),PYTHONDONTWRITEBYTECODE='1')
    subprocess.run(['/usr/bin/python3','-B',str(BUNDLE/'private/launch.py'),'prepare'],env=env,check=True,capture_output=True)
    backend=root/'config/scripts/backend.py';ui=root/'config/scripts/ui'
    def act(*args):subprocess.run(['/usr/bin/python3','-B',str(backend),'action',*args],env=env,check=True)
    act('volume','42');act('mute');act('timer','toggle');act('timer','5');act('media','toggle');act('media','seek','37');act('wifi');act('dnd');act('workspace','2')
    subprocess.run(['/usr/bin/python3','-B',str(ui),'edit-notes'],env=env,check=True)
    subprocess.run(['/usr/bin/python3','-B',str(ui),'lock'],env=env,check=True)
    s=json.loads((root/'state.json').read_text())
    assert s['volume']==42 and s['muted'] and s['timer']['running'] and s['timer']['minutes']==30
    assert s['media']['playing'] and s['media']['progress']==37 and s['notes']=='Private fixture note saved.'
    assert [w['id'] for w in s['workspaces'] if w['active']]==[2]
    assert (root/'last-ui-action.txt').read_text()=='lock\n'
    assert len((root/'actions.jsonl').read_text().splitlines())==11
    for name in ['eww.yuck','saimoom.yuck','carbon.yuck','eww.scss']:assert '/home/bertmacklen/' not in (root/'config'/name).read_text()
print(json.dumps({'manifest_files':len(manifest['files']),'real_copies':True,'original_action_strings':'exact','private_audio_notes_timer_calendar_system_route':'owned fixture state and private Eww only','fixture_actions':11,'host_controls_executed':False,'gui':False}))
