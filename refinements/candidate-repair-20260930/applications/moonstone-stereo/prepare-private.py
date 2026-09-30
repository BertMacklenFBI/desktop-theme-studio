#!/usr/bin/python3
"""Prepare only a new caller-owned private receiver fixture; launches no GUI."""
from pathlib import Path
import argparse,hashlib,json,os,re,shutil
HERE=Path(__file__).resolve().parent
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--host-display',required=True);parser.add_argument('--host-bus',required=True)
    a=parser.parse_args();run=a.run.resolve()
    if run.exists():raise RuntimeError('Use a new private receiver directory')
    manifest=json.loads((HERE/'bundle/bundle.json').read_text())
    actual={str(p.relative_to(HERE/'bundle')) for p in (HERE/'bundle').rglob('*') if p.is_file() and p.name!='bundle.json'}
    if actual!=set(manifest['files']) or any(p.is_symlink() for p in (HERE/'bundle').rglob('*')):
        raise RuntimeError('Shipped receiver inventory changed')
    for relative,entry in manifest['files'].items():
        expected=entry['sha256'] if isinstance(entry,dict) else entry
        p=HERE/'bundle'/relative
        if p.is_symlink() or digest(p)!=expected:raise RuntimeError('Shipped receiver source changed: '+relative)
        if isinstance(entry,dict) and p.stat().st_mode&0o777!=entry['mode']:raise RuntimeError('Shipped receiver mode changed: '+relative)
    run.mkdir(mode=0o700,parents=True);(run/'.private-receiver-fixture').write_text('Moonstone owned private fixture\n')
    shutil.copytree(HERE/'bundle',run/'bundle')
    (run/'state').mkdir(mode=0o700)
    config=run/'bundle/config'
    yuck=(config/'eww.yuck').read_text();match=re.search(r'\(deflisten s :initial ("(?:[^"\\]|\\.)*")',yuck)
    state=json.loads(json.loads(match.group(1)))
    state.update(hostname='Private Fixture Host',network='Private fixture network',weather='Weather unavailable — private fixture',theme_label='Moonstone Stereo',theme_short='MS',notes='Owned private notes. No host notes are read.',fixture=True)
    state['media'].update(title='Private fixture track one',artist='Owned silent media fixture',album='Private test album',player='private-owned-fixture',playing=False,duration=180,total='3:00',cover=str(config/'assets/music.svg'))
    state['timer'].update(end=0)
    (run/'state/receiver.json').write_text(json.dumps(state));(run/'Notes.txt').write_text(state['notes'])
    shutil.copy2(HERE/'private_backend.py',config/'scripts/backend.py');(config/'scripts/backend.py').chmod(0o755)
    # This copy's callbacks preserve original verbs but target guarded fixtures.
    ui='#!/usr/bin/python3\nimport runpy,sys\nsys.argv=[sys.argv[0],"ui",*sys.argv[1:]]\nrunpy.run_path(__file__.rsplit("/",1)[0]+"/backend.py",run_name="__main__")\n'
    (config/'scripts/ui').write_text(ui);(config/'scripts/ui').chmod(0o755)
    env={'DTS_RECEIVER_PRIVATE':'1','DTS_RECEIVER_PRIVATE_RUN':str(run),'DTS_RECEIVER_HOST_DISPLAY':a.host_display,'DTS_RECEIVER_HOST_BUS':a.host_bus,'MOONSTONE_PREVIEW':'1'}
    (run/'private-environment.json').write_text(json.dumps(env,indent=2)+'\n')
    receipt={'schema':1,'bundle_manifest_sha256':digest(HERE/'bundle/bundle.json'),'run':str(run),'private_replacements':['config/scripts/backend.py','config/scripts/ui'],
      'source_callbacks_preserved':True,'private_state_scope':'Owned silent transport, notes, timer, controls and system fixture; no host data/actions.',
      'environment_file':str(run/'private-environment.json'),'gui_launched':False,
      'composition':manifest.get('composition',{'panels':[manifest['panels']['top'],manifest['panels']['bottom']], 'clock_receiver':[1220,290],'anchor':'bottom center','y':-130}),
      'unused_preserved_private_helpers':[str(p.relative_to(HERE/'bundle')) for p in sorted((HERE/'bundle/private').glob('*'))]}
    (run/'preparation.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n');print(json.dumps(receipt,indent=2))
if __name__=='__main__':main()
