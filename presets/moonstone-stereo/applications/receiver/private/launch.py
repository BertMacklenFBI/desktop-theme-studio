#!/usr/bin/python3
"""Prepare a real-copy private receiver and control only its supplied Eww daemon."""
import argparse,json,os,re,shutil,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from backend import root
PACKAGE=Path(__file__).resolve().parent.parent
SOURCE=PACKAGE/'config' if (PACKAGE/'config').is_dir() else PACKAGE
WINDOWS=['clock','dashboard','controls','calendar','notes','timer','audio','system']
def prepare():
    r=root();r.mkdir(parents=True,exist_ok=True);dest=r/'config'
    if dest.exists():return dest
    if r.resolve()==SOURCE.resolve() or r.resolve().is_relative_to(SOURCE.resolve()):raise RuntimeError('Private root must be separate from shipped source')
    dest.mkdir()
    for name in ['eww.yuck','saimoom.yuck','carbon.yuck','eww.scss','audio_meter.py']:
        shutil.copy2(SOURCE/name,dest/name)
    for name in ['materials','assets']:
        shutil.copytree(SOURCE/name,dest/name,symlinks=False)
    scripts=dest/'scripts';scripts.mkdir()
    shutil.copy2(PACKAGE/'private/backend.py',scripts/'backend.py');shutil.copy2(PACKAGE/'private/ui.py',scripts/'ui')
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
    (r/'private-receiver-contract.json').write_text(json.dumps({'schema':1,'source':str(SOURCE),'windows':WINDOWS,'panels':{'top':78,'bottom':76},'backend':'owned mock fixture','actions':'same shipped UI command strings','host_controls':False},indent=2)+'\n')
    return dest
def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','start','open','close','status','stop']);p.add_argument('window',nargs='?');a=p.parse_args()
    dest=prepare()
    if a.command=='prepare':print(dest);return
    binary=os.environ.get('DTS_EWW_BINARY','')
    if not binary or not Path(binary).is_file():raise RuntimeError('DTS_EWW_BINARY must identify the private Eww executable')
    env=dict(os.environ,MOONSTONE_PREVIEW='1');command=[binary,'-c',str(dest)]
    if a.command=='start':
        subprocess.run(command+['daemon'],env=env,check=True,timeout=10)
        subprocess.run(command+['open','clock'],env=env,check=True,timeout=10)
    elif a.command in ['open','close']:
        if a.window not in WINDOWS:raise RuntimeError('Unknown authored receiver window')
        subprocess.run(command+[a.command,a.window],env=env,check=True,timeout=10)
    else:subprocess.run(command+(['active-windows'] if a.command=='status' else ['kill']),env=env,check=True,timeout=10)
if __name__=='__main__':main()
