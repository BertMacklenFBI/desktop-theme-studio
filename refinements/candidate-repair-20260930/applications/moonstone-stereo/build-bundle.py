#!/usr/bin/python3
"""Stage a portable authored receiver; source media action strings are retained."""
from pathlib import Path
import hashlib,json,re,shutil,os
ROOT=Path('/home/bertmacklen/Documents/desktop-theme-studio')
HERE=Path(__file__).resolve().parent
SOURCE=ROOT/'presets/moonstone-stereo/applications/receiver'
BUNDLE=HERE/'bundle'
CONFIG=BUNDLE/'config'
CONFIG.mkdir(parents=True,exist_ok=True)
provenance={}
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def copy(source,dest):
    dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
    provenance[str(dest.relative_to(BUNDLE))]={'source':str(source),'source_sha256':digest(source)}
for name in ['eww.yuck','saimoom.yuck','carbon.yuck','eww.scss','audio_meter.py']:
    copy(SOURCE/name,CONFIG/name)
for directory in ['materials','assets']:
    for p in sorted((SOURCE/directory).resolve().rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts:copy(p,CONFIG/directory/p.relative_to((SOURCE/directory).resolve()))
for name in ['backend.py','ui','theme-sync.py','theme-watch.py']:
    copy((SOURCE/'scripts').resolve()/name,CONFIG/'scripts'/name)
palette=json.loads((SOURCE/'expected-watcher-palette.json').read_text())
(CONFIG/'themes').mkdir(exist_ok=True)
(CONFIG/'themes/palettes.json').write_text(json.dumps({'Moonstone Stereo':palette},indent=2)+'\n')
prefix=str(SOURCE)+'/'; shared=str(Path.home()/'Documents/eww-graphite-brass/config')+'/'
for name in ['eww.yuck','saimoom.yuck','carbon.yuck','eww.scss']:
    p=CONFIG/name;t=p.read_text().replace(prefix,'').replace(shared,'')
    p.write_text(t)
# The Cava reader resolves its static meter faces from __file__; all24 faces
# and knob/material artwork are actual package files, never external links.
launcher='''#!/bin/bash
set -euo pipefail
root=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
config="$root/config"
eww="${EWW_BINARY:-eww}"
case "${1:-show}" in
 -h|--help|help) echo 'Moonstone receiver: show hide toggle close WINDOW dashboard controls calendar notes timer audio music system dock bar launchers status reload stop'; exit 0 ;;
 start|show) exec "$eww" -c "$config" open clock ;;
 hide) exec "$eww" -c "$config" close-all ;;
 toggle) if "$eww" -c "$config" active-windows | grep -q '^clock:'; then exec "$0" hide; else exec "$0" show; fi ;;
 close) exec "$eww" -c "$config" close "${2:?Window name required}" ;;
 dashboard|controls|calendar|notes|timer|audio|music|system|dock|bar|launchers) exec "$eww" -c "$config" open --toggle "$1" ;;
 status) exec "$eww" -c "$config" active-windows ;;
 reload) exec "$eww" -c "$config" reload ;;
 stop) exec "$eww" -c "$config" kill ;;
 *) echo 'Unknown receiver command' >&2; exit 2 ;;
esac
'''
(BUNDLE/'bin').mkdir(exist_ok=True);(BUNDLE/'bin/eww-widgets').write_text(launcher);(BUNDLE/'bin/eww-widgets').chmod(0o755)
for p in (CONFIG/'scripts').iterdir():p.chmod(0o755)
(CONFIG/'audio_meter.py').chmod(0o755)
actions=lambda t:sorted(re.findall(r'"(scripts/(?:backend\.py action|ui) [^"]+)"',t))
for name in ['saimoom.yuck','carbon.yuck']:
    assert actions((SOURCE/name).read_text())==actions((CONFIG/name).read_text()),name
assert not any(p.is_symlink() for p in BUNDLE.rglob('*'))
external=[]
for name in ['eww.yuck','saimoom.yuck','carbon.yuck','eww.scss']:
    for value in re.findall(r'"(/home/[^"\n]+)"',(CONFIG/name).read_text()):external.append(value)
assert not external,external
files={str(p.relative_to(BUNDLE)):digest(p) for p in sorted(BUNDLE.rglob('*')) if p.is_file() and p.name!='bundle.json'}
manifest={'schema':1,'profile':'moonstone-stereo','theme':'Moonstone Stereo','entry_config':'config',
 'launcher':'bin/eww-widgets','files':files,'copy_provenance':provenance,
 'portable_external_file_dependencies':[],'system_dependencies':['eww','python3','GTK3/GTK-layer support','normal backend command utilities'],
 'production_action_strings_preserved':True,'material_and_asset_bytes_preserved':True,
 'composition':{'panels':[78,76],'clock_receiver':[1220,290],'anchor':'bottom center','y':-130},
 'audio_scope':'Reads an already existing Cava spectrum; no capture process starts. Missing/stale/idle/signal and PREVIEW / IDLE are distinct; low/high bands are not stereo channels or calibrated dB.',
 'runtime_boundary':'No daemon watcher is launched by this build. Root handles deployment, focus/Escape/restore integration and private GUI.'}
(BUNDLE/'bundle.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
print(json.dumps({'bundle':str(BUNDLE),'manifest_sha256':digest(BUNDLE/'bundle.json'),'files':len(files),'external_dependencies':external,'source_actions_preserved':True},indent=2))
