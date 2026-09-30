#!/usr/bin/python3
"""Prepare a real-copy private receiver and control only its supplied Eww daemon."""
import argparse,hashlib,json,os,re,shutil,stat,subprocess,sys
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from backend import root
from player.common import context as attest_private_session, binding as actual_player_binding
PACKAGE=Path(__file__).resolve().parent.parent
SOURCE=PACKAGE/'config' if (PACKAGE/'config').is_dir() else PACKAGE
WINDOWS=['clock','dashboard','controls','calendar','notes','timer','audio','system']
def _safe_file(path):
    if path.is_symlink() or path.exists() and (not path.is_file() or path.stat().st_uid!=os.getuid() or path.stat().st_nlink!=1):raise RuntimeError('Private receiver destination is not owned and regular')
def _inventory(dest):
    value={}
    for path in sorted(dest.rglob('*')):
        if path.is_symlink() or path.stat().st_uid!=os.getuid():raise RuntimeError('Private config contains an unowned or symlinked entry')
        if path.is_dir():row={'kind':'directory','mode':stat.S_IMODE(path.stat().st_mode)}
        elif path.is_file() and path.stat().st_nlink==1:row={'kind':'file','mode':stat.S_IMODE(path.stat().st_mode),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        else:raise RuntimeError('Private config contains a nonregular or shared entry')
        value[path.relative_to(dest).as_posix()]=row
    return value
def prepare():
    attest_private_session()
    r=root();r.mkdir(parents=True,exist_ok=True);dest=r/'config'
    for name in ['state.json','actions.jsonl','private-receiver-contract.json']:_safe_file(r/name)
    if dest.is_symlink() or dest.exists() and (not dest.is_dir() or dest.stat().st_uid!=os.getuid()):raise RuntimeError('Refusing foreign or symlinked private config')
    if dest.exists():
        contract=json.loads((r/'private-receiver-contract.json').read_text())
        if (contract.get('media_type')!='actual' or contract.get('source_manifest_sha256')!=hashlib.sha256((PACKAGE/'bundle.json').read_bytes()).hexdigest()
                or contract.get('source')!=str(SOURCE) or contract.get('config_inventory')!=_inventory(dest)):
            raise RuntimeError('Refusing changed or unbound private receiver config')
        return dest
    if r.resolve()==SOURCE.resolve() or r.resolve().is_relative_to(SOURCE.resolve()):raise RuntimeError('Private root must be separate from shipped source')
    dest.mkdir(mode=0o700)
    for name in ['eww.yuck','saimoom.yuck','carbon.yuck','eww.scss','audio_meter.py']:
        shutil.copy2(SOURCE/name,dest/name)
    for name in ['materials','assets']:
        shutil.copytree(SOURCE/name,dest/name,symlinks=False)
    scripts=dest/'scripts';scripts.mkdir(mode=0o700)
    shutil.copy2(PACKAGE/'private/backend.py',scripts/'backend.py');shutil.copy2(PACKAGE/'private/ui.py',scripts/'ui')
    shutil.copytree(PACKAGE/'private/player',scripts/'player',symlinks=False)
    initial=re.search(r'\(deflisten s :initial ("(?:\\.|[^"\\])*")', (dest/'eww.yuck').read_text()).group(1)
    state=json.loads(json.loads(initial));state.update(theme_label='Moonstone Stereo · PRIVATE FIXTURE',theme_short='MS',weather='Private weather fixture',network='Private network fixture')
    state['media'].update(title='Owned private media fixture',artist='Fixture transport',duration=180,total='3:00',cover=str(dest/'assets/music.svg'))
    (r/'state.json').write_text(json.dumps(state));(r/'actions.jsonl').write_text('')
    for name in ['eww.yuck','saimoom.yuck','carbon.yuck','eww.scss']:
        p=dest/name;text=p.read_text()
        text=text.replace('/home/bertmacklen/Documents/desktop-theme-studio/presets/moonstone-stereo/applications/receiver/',str(dest)+'/')
        text=text.replace('/home/bertmacklen/Documents/eww-graphite-brass/config/assets/',str(dest/'assets')+'/')
        p.write_text(text)
    # Shipped action strings still use scripts/backend.py and scripts/ui; these
    # owned copies route them to fixture state and the private daemon.
    for p in scripts.iterdir():p.chmod(0o755)
    (r/'private-receiver-contract.json').write_text(json.dumps({'schema':1,'source':str(SOURCE),'windows':WINDOWS,'panels':{'top':78,'bottom':76},'backend':'owned mock controls plus actual owned MPRIS media','media_type':'actual','actions':'same shipped UI command strings','host_controls':False,'source_manifest_sha256':hashlib.sha256((PACKAGE/'bundle.json').read_bytes()).hexdigest(),'config_inventory':_inventory(dest)},indent=2)+'\n')
    return dest
def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','start','open','close','status','stop']);p.add_argument('window',nargs='?');a=p.parse_args()
    dest=prepare()
    if a.command=='prepare':print(dest);return
    if a.command in ['start','open']:actual_player_binding()
    binary=os.environ.get('DTS_EWW_BINARY','')
    if not binary or not Path(binary).is_file():raise RuntimeError('DTS_EWW_BINARY must identify the private Eww executable')
    env=dict(os.environ,MOONSTONE_PREVIEW='1',PYTHONDONTWRITEBYTECODE='1');command=[binary,'-c',str(dest)]
    # Only the explicit startup command may create the daemon. A client timeout
    # must not make Eww steal the socket and fork a second private daemon.
    client_command=command+['--no-daemonize']
    if a.command=='start':
        subprocess.run(command+['daemon'],env=env,check=True,timeout=10)
        subprocess.run(client_command+['open','clock'],env=env,check=True,timeout=10)
    elif a.command in ['open','close']:
        if a.window not in WINDOWS:raise RuntimeError('Unknown authored receiver window')
        subprocess.run(client_command+[a.command,a.window],env=env,check=True,timeout=10)
    else:subprocess.run(client_command+(['active-windows'] if a.command=='status' else ['kill']),env=env,check=True,timeout=10)
if __name__=='__main__':main()
