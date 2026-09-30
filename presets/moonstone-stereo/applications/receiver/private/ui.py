#!/usr/bin/python3
"""Same frontend commands, private Eww windows and fixture notes only."""
import json,os,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from backend import root,load,save
WINDOWS={'clock','dashboard','controls','calendar','notes','timer','audio','music','system','dock','bar','launchers'}
def main():
    args=sys.argv[1:];kind=args[0] if args else 'dashboard';r=root()
    with (r/'actions.jsonl').open('a') as f:f.write(json.dumps({'kind':'ui','argv':args})+'\n')
    if kind=='edit-notes':
        s=load();s['notes']='Private fixture note saved.';save(s);return
    # Controls that normally launch host programs are represented by an owned
    # acknowledgement receipt. No external application or host control runs.
    if kind in {'network','appearance','screenshot','lock','settings','power','files','browser','terminal'}:
        (r/'last-ui-action.txt').write_text(kind+'\n');return
    binary=os.environ.get('DTS_EWW_BINARY','')
    if not binary or not Path(binary).is_file():raise RuntimeError('Private Eww binary must be supplied')
    command=[binary,'-c',str(r/'config')]
    if kind=='close':
        if len(args)!=2 or args[1] not in WINDOWS:raise RuntimeError('Unknown private close window')
        command+=['close',args[1]]
    elif kind=='hide':command+=['close',*sorted(WINDOWS)]
    elif kind in WINDOWS:command+=['open','--toggle',kind]
    else:raise RuntimeError('Unknown private UI command')
    subprocess.run(command,check=True,timeout=10)
if __name__=='__main__':main()
