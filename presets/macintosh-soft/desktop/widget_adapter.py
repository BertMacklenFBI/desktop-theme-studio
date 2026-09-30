#!/usr/bin/python3
"""Macintosh Soft widget transaction. Plan/import never write live files or start processes."""
from pathlib import Path
import base64,hashlib,importlib.util,json,sys,re
from datetime import datetime,timezone
HERE=Path(__file__).resolve().parent
PRESET=HERE.parent
LIVE=Path.home()/'Documents/eww-graphite-brass/config'
ENGINE=PRESET/'applications/engine.py'
spec=importlib.util.spec_from_file_location('macintosh_widget_engine',ENGINE)
E=importlib.util.module_from_spec(spec);sys.dont_write_bytecode=True;spec.loader.exec_module(E)

def plan(live=LIVE):
    historical=json.loads((HERE/'widget-baseline.json').read_text())['sha256']
    contract=json.loads((HERE/'widget-current-contract.json').read_text())
    baseline=contract['sha256']
    for name,expected in contract['dependency_sha256'].items():
        dependency=live/'scripts'/name
        if not dependency.is_file() or dependency.is_symlink() or E.digest(dependency.read_bytes())!=expected:
            raise RuntimeError('Widget dependency revision drift: '+str(dependency)+'; review before a new plan')
    actions=[];proof=[]
    for name in ['eww.yuck','saimoom.yuck','carbon.yuck','eww.scss','eww-widgets']:
        dest=live/name if name!='eww-widgets' else live.parent/'bin/eww-widgets'
        original=LIVE/name if name!='eww-widgets' else LIVE.parent/'bin/eww-widgets'
        if not dest.is_file() or dest.is_symlink():raise RuntimeError('Expected regular current widget source: '+str(dest))
        before=dest.read_bytes();after=(HERE/'widgets'/name).read_bytes()
        if name in ['saimoom.yuck','carbon.yuck']:
            commands=lambda raw:sorted(re.findall(r'"(scripts/(?:backend\.py|ui)[^"]*)"',raw.decode()))
            if commands(before)!=commands(after):raise RuntimeError('Widget action handler contract drift: '+str(dest))
        if before==after:continue
        accepted=E.digest(before) in {baseline[str(original)],historical[str(original)]}
        if name=='eww.scss':
            pattern=r'// THEME_VARIABLES_START.*?// THEME_VARIABLES_END'
            text=before.decode();target=after.decode()
            if len(re.findall(pattern,text,re.S))!=1:raise RuntimeError('Unknown watcher variables layout: '+str(dest))
            normalized=E.digest(re.sub(pattern,'__DTS_PALETTE_VARIABLES__',text,flags=re.S).encode())
            accepted=accepted or normalized==contract['normalized_scss_sha256'][str(original)]
            # Watcher-selected variables may change; authored target style/body
            # must still be exact. This does not advance historical baselines.
            accepted=accepted or re.sub(pattern,'__DTS_PALETTE_VARIABLES__',text,flags=re.S)==re.sub(pattern,'__DTS_PALETTE_VARIABLES__',target,flags=re.S)
        if not accepted:raise RuntimeError('Widget baseline drift: '+str(dest)+'; review current source before rebuilding, never rebase a receipt')
        action={'kind':'file','path':str(dest),'before':base64.b64encode(before).decode(),'after':base64.b64encode(after).decode()}
        actions.append(action)
        proof.append({'path':str(dest),'before_sha256':E.digest(before),'after_sha256':E.digest(after),'before_base64':action['before']})
    palette=json.loads((HERE/'widgets/palette.json').read_text())
    a={'kind':'json','path':str(live/'themes/palettes.json'),'keys':['Macintosh Soft'],'after':palette}
    a['before']=E.current(a)
    if a['before']!=a['after']:actions.append(a)
    # Read-only behavior dependencies. No shell/music command is rewritten by this adapter.
    protected=[live/'scripts/backend.py',live/'scripts/ui',live/'scripts/theme-sync.py',live/'scripts/theme-watch.py']
    audit=PRESET.parents[1]/'audits/applications.json'
    if audit.exists():
        protected.extend(Path(p) for p in json.loads(audit.read_text()).get('protected_files',[]) if 'eww-graphite-brass' not in p)
    return {'theme':'Macintosh Soft','created_at':datetime.now(timezone.utc).isoformat(),'actions':actions,'touched_paths':sorted({a['path'] for a in actions}),'protected_edits':proof,'intentional_protected_appearance_edits':[p['path'] for p in proof],'protected_hashes':{str(p):E.digest(p.read_bytes()) for p in protected if p.is_file()},'skipped':[],'indirect_watcher_paths':[str(live/'assets/music.svg'),str(live.parent/'state/theme.json')],'notes':['No daemon start/reload/close is performed. Coordinator reloads after full guarded apply.','Exact Cinnamon GTK theme Macintosh Soft resolves directly to the new registry key; watcher aliases are unnecessary.','Apply widget source before selecting the new live theme; a stale old stylesheet after watcher mutation fails the captured baseline precondition.','scripts/backend.py and all music/audio action handlers are unchanged.','Current full bytes are journaled before each attempted write; restore refuses unknown subsequent edits.']}

if __name__=='__main__':
    E.plan=plan
    E.main()
